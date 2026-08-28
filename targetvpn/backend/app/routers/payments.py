from __future__ import annotations

import base64
import json
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import Device, Notification, Payment, PaymentStatus, User
from ..security import require_internal
from ..services import billing, subs

log = logging.getLogger("payments")
router = APIRouter(tags=["payments"])


@router.post("/payments/cryptobot/webhook")
async def cryptobot_webhook(request: Request,
                            crypto_pay_api_signature: str = Header(default="", alias="crypto-pay-api-signature"),
                            session: AsyncSession = Depends(get_session)):
    body = await request.body()
    if not billing.verify_cryptobot_signature(body, crypto_pay_api_signature):
        raise HTTPException(403, "Неверная подпись webhook")

    data = json.loads(body)
    if data.get("update_type") != "invoice_paid":
        return {"ok": True}

    invoice = data.get("payload", {})
    payment_id = invoice.get("payload")
    payment = (await session.execute(select(Payment).where(
        Payment.id == int(payment_id)))).scalar_one_or_none() if payment_id else None
    if payment is None:
        log.warning("CryptoBot: неизвестный платёж %s", payment_id)
        return {"ok": True}
    try:
        await billing.complete_payment(session, payment)
    except billing.BillingError as exc:
        log.error("Не удалось активировать подписку: %s", exc)
    return {"ok": True}


@router.post("/internal/payments/stars", dependencies=[Depends(require_internal)])
async def stars_paid(payload: dict, session: AsyncSession = Depends(get_session)):
    """Вызывается ботом после successful_payment (Telegram Stars)."""
    payment_id = int(payload.get("payment_id", 0))
    payment = (await session.execute(select(Payment).where(
        Payment.id == payment_id))).scalar_one_or_none()
    if payment is None:
        raise HTTPException(404, "Платёж не найден")
    payment.external_id = payload.get("charge_id") or payment.external_id
    try:
        await billing.complete_payment(session, payment)
    except billing.BillingError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True}


@router.get("/internal/notifications", dependencies=[Depends(require_internal)])
async def pending_notifications(limit: int = 50, session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(Notification).where(
        Notification.is_sent.is_(False)).order_by(Notification.id).limit(limit))).scalars().all()
    for row in rows:
        row.is_sent = True
    await session.commit()
    return [{"tg_id": r.tg_id, "text": r.text} for r in rows]


@router.post("/internal/tick", dependencies=[Depends(require_internal)])
async def tick(session: AsyncSession = Depends(get_session)):
    changed = await subs.expire_pass(session)
    return {"ok": True, "changed": changed}


@router.get("/sub/{token}", response_class=PlainTextResponse)
async def subscription_feed(token: str, session: AsyncSession = Depends(get_session)):
    """Ссылка-подписка для клиентов (v2rayNG / Hiddify / Streisand / TargetVPN)."""
    user = (await session.execute(select(User).where(User.sub_token == token))).scalar_one_or_none()
    if user is None or user.is_banned:
        raise HTTPException(404, "Подписка не найдена")
    sub = await subs.active_subscription(session, user)
    if sub is None:
        raise HTTPException(403, "Подписка неактивна")
    devices = (await session.execute(select(Device).where(
        Device.user_id == user.id, Device.is_active.is_(True)).order_by(Device.id))).scalars().all()
    links = [d.config_url for d in devices if d.config_url]
    payload = base64.b64encode("\n".join(links).encode()).decode()
    headers = {
        "profile-title": base64.b64encode("TargetVPN".encode()).decode(),
        "profile-update-interval": "6",
        "subscription-userinfo": (f"upload=0; download={int(sum(d.used_traffic for d in devices))}; "
                                  f"total={sub.traffic_gb * 1024 ** 3}; "
                                  f"expire={int(subs.aware(sub.expires_at).timestamp())}"),
    }
    return PlainTextResponse(payload, headers=headers)
