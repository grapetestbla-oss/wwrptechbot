from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_session
from ..models import Device, Payment, PaymentStatus, Plan, User, utcnow
from ..schemas import (AuthRequest, AuthResponse, DeviceCreate, DeviceOut,
                       PlanOut, PromoCheck, PurchaseRequest, PurchaseResponse,
                       StateOut, SubscriptionOut, UserOut)
from ..security import (create_token, current_user, get_or_create_user,
                        verify_init_data)
from ..services import billing, subs

log = logging.getLogger("api")
router = APIRouter(prefix="/api", tags=["app"])


def user_out(user: User, referrals: int = 0) -> UserOut:
    link = (f"https://t.me/{settings.bot_username}?start=ref{user.tg_id}"
            if settings.bot_username else "")
    return UserOut(tg_id=user.tg_id, username=user.username, first_name=user.first_name,
                   role=user.role.value, is_banned=user.is_banned, trial_used=user.trial_used,
                   referrals=referrals, ref_link=link)


def plan_out(plan: Plan) -> PlanOut:
    return PlanOut(
        id=plan.id, code=plan.code, title=plan.title, description=plan.description,
        emoji=plan.emoji, price_rub=plan.price_rub, old_price_rub=plan.old_price_rub,
        duration_hours=plan.duration_hours, devices=plan.devices, traffic_gb=plan.traffic_gb,
        is_trial=plan.is_trial, is_popular=plan.is_popular, is_active=plan.is_active,
        sort_order=plan.sort_order,
        price_stars=billing.stars_amount(plan.price_rub) if plan.price_rub else 0,
        price_crypto=billing.crypto_amount(plan.price_rub) if plan.price_rub else 0.0,
    )


def device_out(device: Device) -> DeviceOut:
    return DeviceOut(id=device.id, name=device.name, platform=device.platform,
                     config_url=device.config_url,
                     used_traffic_gb=round(device.used_traffic / 1024 ** 3, 2),
                     is_active=device.is_active, created_at=device.created_at)


@router.post("/auth", response_model=AuthResponse)
async def auth(payload: AuthRequest, session: AsyncSession = Depends(get_session)):
    data = verify_init_data(payload.init_data)
    referrer = None
    start_param = payload.start_param or data.get("start_param") or ""
    if start_param.startswith("ref"):
        try:
            referrer = int(start_param[3:])
        except ValueError:
            referrer = None
    user = await get_or_create_user(session, data["user"], referrer)
    if user.is_banned:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            f"Доступ заблокирован. {user.ban_reason or ''}".strip())
    return AuthResponse(token=create_token(user.tg_id), user=user_out(user))


@router.get("/state", response_model=StateOut)
async def state(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    sub = await subs.active_subscription(session, user)
    devices = (await session.execute(
        select(Device).where(Device.user_id == user.id).order_by(Device.id))).scalars().all()
    referrals = (await session.execute(
        select(func.count(User.id)).where(User.referrer_id == user.tg_id))).scalar_one()

    sub_out = None
    if sub:
        seconds_left = int((subs.aware(sub.expires_at) - utcnow()).total_seconds())
        sub_out = SubscriptionOut(
            id=sub.id, plan_title=sub.plan_title, devices=sub.devices,
            devices_used=len([d for d in devices if d.is_active]),
            traffic_gb=sub.traffic_gb, expires_at=sub.expires_at,
            seconds_left=max(0, seconds_left), is_trial=sub.is_trial)

    trial_plan = (await session.execute(select(Plan).where(
        Plan.is_trial.is_(True), Plan.is_active.is_(True)))).scalars().first()
    return StateOut(
        user=user_out(user, referrals),
        subscription=sub_out,
        devices=[device_out(d) for d in devices],
        sub_url=f"{settings.public_base_url.rstrip('/')}/sub/{user.sub_token}",
        support_url=settings.support_url,
        trial_available=bool(settings.trial_enabled and trial_plan and not user.trial_used
                             and sub is None),
    )


@router.get("/plans", response_model=list[PlanOut])
async def plans(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(
        select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.sort_order, Plan.price_rub)
    )).scalars().all()
    return [plan_out(p) for p in rows if not p.is_trial or not user.trial_used]


@router.post("/trial", response_model=SubscriptionOut)
async def activate_trial(user: User = Depends(current_user),
                         session: AsyncSession = Depends(get_session)):
    if not settings.trial_enabled:
        raise HTTPException(400, "Пробный период отключён")
    if user.trial_used:
        raise HTTPException(400, "Пробный период уже использован")
    if await subs.active_subscription(session, user):
        raise HTTPException(400, "У вас уже есть активная подписка")
    plan = (await session.execute(select(Plan).where(
        Plan.is_trial.is_(True), Plan.is_active.is_(True)))).scalars().first()
    if plan is None:
        raise HTTPException(400, "Пробный тариф недоступен")
    sub = await subs.grant_subscription(session, user, plan, extend=False)
    return SubscriptionOut(id=sub.id, plan_title=sub.plan_title, devices=sub.devices,
                           devices_used=0, traffic_gb=sub.traffic_gb, expires_at=sub.expires_at,
                           seconds_left=int((subs.aware(sub.expires_at) - utcnow()).total_seconds()),
                           is_trial=True)


@router.post("/devices", response_model=DeviceOut)
async def create_device(payload: DeviceCreate, user: User = Depends(current_user),
                        session: AsyncSession = Depends(get_session)):
    try:
        device = await subs.add_device(session, user, payload.name, payload.platform)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return device_out(device)


@router.delete("/devices/{device_id}")
async def delete_device(device_id: int, user: User = Depends(current_user),
                        session: AsyncSession = Depends(get_session)):
    try:
        await subs.remove_device(session, user, device_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return {"ok": True}


@router.post("/devices/{device_id}/refresh", response_model=DeviceOut)
async def refresh_device(device_id: int, user: User = Depends(current_user),
                         session: AsyncSession = Depends(get_session)):
    device = (await session.execute(select(Device).where(
        Device.id == device_id, Device.user_id == user.id))).scalar_one_or_none()
    if device is None:
        raise HTTPException(404, "Устройство не найдено")
    try:
        device = await subs.refresh_device(session, device)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return device_out(device)


@router.post("/promo/check")
async def promo_check(payload: PromoCheck, user: User = Depends(current_user),
                      session: AsyncSession = Depends(get_session)):
    plan = (await session.execute(select(Plan).where(Plan.id == payload.plan_id))).scalar_one_or_none()
    if plan is None:
        raise HTTPException(404, "Тариф не найден")
    try:
        price, promo, bonus_hours = await billing.apply_promo(session, user, payload.code,
                                                              plan.price_rub)
    except billing.BillingError as exc:
        raise HTTPException(400, str(exc))
    return {"price_rub": price, "discount_percent": promo.discount_percent if promo else 0,
            "bonus_days": bonus_hours // 24}


@router.post("/purchase", response_model=PurchaseResponse)
async def purchase(payload: PurchaseRequest, user: User = Depends(current_user),
                   session: AsyncSession = Depends(get_session)):
    plan = (await session.execute(select(Plan).where(
        Plan.id == payload.plan_id, Plan.is_active.is_(True)))).scalar_one_or_none()
    if plan is None:
        raise HTTPException(404, "Тариф не найден")
    if plan.is_trial:
        raise HTTPException(400, "Пробный тариф активируется отдельной кнопкой")

    try:
        price, promo, _ = await billing.apply_promo(session, user, payload.promo_code,
                                                    plan.price_rub)
    except billing.BillingError as exc:
        raise HTTPException(400, str(exc))
    promo_code = promo.code if promo else ""

    if payload.method == "cryptobot":
        try:
            payment = await billing.cryptobot_create_invoice(session, user, plan, price, promo_code)
        except billing.BillingError as exc:
            raise HTTPException(400, str(exc))
        meta = json.loads(payment.payload or "{}")
        return PurchaseResponse(payment_id=payment.id, method="cryptobot",
                                invoice_url=meta.get("url", ""), amount_rub=price,
                                amount_native=payment.amount_native, currency=payment.currency)

    if payload.method == "stars":
        payment = Payment(user_id=user.id, plan_id=plan.id, provider="stars",
                          amount_rub=price, amount_native=billing.stars_amount(price),
                          currency="XTR", payload=json.dumps({"promo": promo_code}))
        session.add(payment)
        await session.flush()
        try:
            link = await billing.stars_create_invoice_link(user, plan, price, payment.id)
        except billing.BillingError as exc:
            raise HTTPException(400, str(exc))
        payment.external_id = link
        await session.commit()
        return PurchaseResponse(payment_id=payment.id, method="stars", invoice_link=link,
                                amount_rub=price, amount_native=payment.amount_native,
                                currency="XTR")

    raise HTTPException(400, "Неизвестный способ оплаты")


@router.get("/payments/{payment_id}")
async def payment_status(payment_id: int, user: User = Depends(current_user),
                         session: AsyncSession = Depends(get_session)):
    payment = (await session.execute(select(Payment).where(
        Payment.id == payment_id, Payment.user_id == user.id))).scalar_one_or_none()
    if payment is None:
        raise HTTPException(404, "Платёж не найден")
    return {"status": payment.status.value, "paid": payment.status == PaymentStatus.paid}
