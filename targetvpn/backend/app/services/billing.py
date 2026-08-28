from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import (Payment, PaymentStatus, Plan, PromoCode, PromoRedemption,
                      User, utcnow)
from .subs import aware, grant_subscription, notify

log = logging.getLogger("billing")


class BillingError(RuntimeError):
    pass


def stars_amount(price_rub: float) -> int:
    """Рубли -> звёзды Telegram по настраиваемому курсу (округляем вверх)."""
    return max(1, math.ceil(price_rub / max(settings.rub_per_star, 0.01)))


def crypto_amount(price_rub: float) -> float:
    return round(max(price_rub / max(settings.rub_per_usdt, 0.01), 0.01), 2)


async def apply_promo(session: AsyncSession, user: User, code: str,
                      price_rub: float) -> tuple[float, PromoCode | None, int]:
    """Возвращает (цена со скидкой, промокод, бонусные часы)."""
    if not code:
        return price_rub, None, 0
    promo = (await session.execute(
        select(PromoCode).where(PromoCode.code == code.strip().upper()))).scalar_one_or_none()
    if promo is None or not promo.is_active:
        raise BillingError("Промокод не найден")
    if promo.expires_at and aware(promo.expires_at) < utcnow():
        raise BillingError("Срок действия промокода истёк")
    if promo.max_uses and promo.used_count >= promo.max_uses:
        raise BillingError("Промокод уже использован максимальное число раз")
    used = (await session.execute(select(PromoRedemption).where(
        PromoRedemption.promo_id == promo.id,
        PromoRedemption.user_id == user.id))).scalar_one_or_none()
    if used:
        raise BillingError("Вы уже применяли этот промокод")
    discounted = round(price_rub * (100 - promo.discount_percent) / 100, 2)
    return discounted, promo, promo.bonus_days * 24


async def redeem_promo(session: AsyncSession, user: User, promo: PromoCode | None) -> None:
    if promo is None:
        return
    promo.used_count += 1
    session.add(PromoRedemption(promo_id=promo.id, user_id=user.id))
    await session.flush()


# --- CryptoBot ---

async def cryptobot_create_invoice(session: AsyncSession, user: User, plan: Plan,
                                   price_rub: float, promo_code: str = "") -> Payment:
    if not settings.cryptobot_token:
        raise BillingError("Оплата криптой временно недоступна")
    amount = crypto_amount(price_rub)
    payment = Payment(user_id=user.id, plan_id=plan.id, provider="cryptobot",
                      amount_rub=price_rub, amount_native=amount,
                      currency=settings.cryptobot_asset,
                      payload=json.dumps({"promo": promo_code}))
    session.add(payment)
    await session.flush()

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            f"{settings.cryptobot_api}/createInvoice",
            headers={"Crypto-Pay-API-Token": settings.cryptobot_token},
            json={
                "asset": settings.cryptobot_asset,
                "amount": str(amount),
                "description": f"TargetVPN · {plan.title}",
                "payload": str(payment.id),
                "allow_comments": False,
                "allow_anonymous": False,
                "expires_in": 3600,
                "paid_btn_name": "openBot",
                "paid_btn_url": f"https://t.me/{settings.bot_username}" if settings.bot_username else None,
            },
        )
    data = resp.json()
    if not data.get("ok"):
        raise BillingError(f"CryptoBot отклонил счёт: {data.get('error')}")
    result = data["result"]
    payment.external_id = str(result["invoice_id"])
    payment.payload = json.dumps({"promo": promo_code, "url": result["bot_invoice_url"]})
    await session.commit()
    return payment


def verify_cryptobot_signature(body: bytes, signature: str) -> bool:
    if not settings.cryptobot_token or not signature:
        return False
    secret = hashlib.sha256(settings.cryptobot_token.encode()).digest()
    calculated = hmac.new(secret, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(calculated, signature)


# --- Telegram Stars ---

async def stars_create_invoice_link(user: User, plan: Plan, price_rub: float,
                                    payment_id: int) -> str:
    if not settings.bot_token:
        raise BillingError("Оплата звёздами временно недоступна")
    amount = stars_amount(price_rub)
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            f"https://api.telegram.org/bot{settings.bot_token}/createInvoiceLink",
            json={
                "title": f"TargetVPN · {plan.title}",
                "description": plan.description or f"Подписка на {plan.duration_hours // 24} дн., "
                                                   f"устройств: {plan.devices}",
                "payload": f"tvpn:{payment_id}",
                "currency": "XTR",
                "prices": [{"label": plan.title, "amount": amount}],
            },
        )
    data = resp.json()
    if not data.get("ok"):
        raise BillingError(f"Telegram отклонил счёт: {data.get('description')}")
    return data["result"]


async def complete_payment(session: AsyncSession, payment: Payment) -> None:
    """Единая точка выдачи подписки после успешной оплаты (любой провайдер)."""
    if payment.status == PaymentStatus.paid:
        return
    user = (await session.execute(select(User).where(User.id == payment.user_id))).scalar_one()
    plan = (await session.execute(select(Plan).where(Plan.id == payment.plan_id))).scalar_one_or_none()
    if plan is None:
        raise BillingError("Тариф больше не существует, свяжитесь с поддержкой")

    meta = json.loads(payment.payload or "{}")
    bonus_hours = 0
    promo_code = meta.get("promo") or ""
    if promo_code:
        promo = (await session.execute(select(PromoCode).where(
            PromoCode.code == promo_code.upper()))).scalar_one_or_none()
        if promo:
            bonus_hours = promo.bonus_days * 24
            await redeem_promo(session, user, promo)

    payment.status = PaymentStatus.paid
    payment.paid_at = utcnow()
    await session.flush()

    sub = await grant_subscription(session, user, plan, hours=plan.duration_hours + bonus_hours)

    # Реферальный бонус — разово, за первую оплату приглашённого.
    if user.referrer_id and settings.referral_bonus_days:
        prior = (await session.execute(select(Payment).where(
            Payment.user_id == user.id, Payment.status == PaymentStatus.paid))).scalars().all()
        if len(prior) <= 1:
            ref = (await session.execute(select(User).where(
                User.tg_id == user.referrer_id))).scalar_one_or_none()
            if ref:
                await grant_subscription(session, ref, None,
                                         hours=settings.referral_bonus_days * 24,
                                         devices=1, title="Реферальный бонус")
                await notify(session, ref.tg_id,
                             f"🎉 Ваш реферал оплатил подписку — вам начислено "
                             f"{settings.referral_bonus_days} дней.")

    until = aware(sub.expires_at).astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
    await notify(session, user.tg_id,
                 f"✅ Оплата получена. Тариф «{plan.title}» активен до {until}.\n"
                 f"Устройств доступно: {sub.devices}. Откройте приложение и подключайтесь.")
    await session.commit()
