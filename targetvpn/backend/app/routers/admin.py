from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_session
from ..marzban import MarzbanError, marzban
from ..models import (AdminLog, Device, Notification, Payment, PaymentStatus,
                      Plan, PromoCode, Role, Subscription, User, utcnow)
from ..schemas import (AdminUserOut, BanRequest, BroadcastRequest, GrantRequest,
                       PlanOut, PlanUpsert, PromoUpsert, RoleRequest, StatsOut)
from ..security import current_admin, current_owner
from ..services import subs
from .api import plan_out

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/stats", response_model=StatsOut)
async def stats(admin: User = Depends(current_admin), session: AsyncSession = Depends(get_session)):
    now = utcnow()
    month_ago = now - timedelta(days=30)
    day_ago = now - timedelta(days=1)

    async def scalar(stmt):
        return (await session.execute(stmt)).scalar_one() or 0

    active_subs = (await session.execute(select(Subscription).where(
        Subscription.is_active.is_(True)))).scalars().all()
    active_subs = [s for s in active_subs if subs.aware(s.expires_at) > now]

    node_online = True
    try:
        await marzban.system_stats()
    except MarzbanError:
        node_online = False

    return StatsOut(
        users_total=await scalar(select(func.count(User.id))),
        users_active=await scalar(select(func.count(User.id)).where(User.last_seen_at >= month_ago)),
        users_banned=await scalar(select(func.count(User.id)).where(User.is_banned.is_(True))),
        subs_active=len(active_subs),
        trials_active=len([s for s in active_subs if s.is_trial]),
        devices_active=await scalar(select(func.count(Device.id)).where(Device.is_active.is_(True))),
        revenue_total=float(await scalar(select(func.coalesce(func.sum(Payment.amount_rub), 0))
                                         .where(Payment.status == PaymentStatus.paid))),
        revenue_month=float(await scalar(select(func.coalesce(func.sum(Payment.amount_rub), 0))
                                         .where(Payment.status == PaymentStatus.paid,
                                                Payment.paid_at >= month_ago))),
        payments_total=await scalar(select(func.count(Payment.id))
                                    .where(Payment.status == PaymentStatus.paid)),
        new_users_today=await scalar(select(func.count(User.id)).where(User.created_at >= day_ago)),
        node_online=node_online,
    )


# --- Тарифы ---

@router.get("/plans", response_model=list[PlanOut])
async def all_plans(admin: User = Depends(current_admin),
                    session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(Plan).order_by(Plan.sort_order, Plan.id))).scalars().all()
    return [plan_out(p) for p in rows]


@router.post("/plans", response_model=PlanOut)
async def upsert_plan(payload: PlanUpsert, admin: User = Depends(current_admin),
                      session: AsyncSession = Depends(get_session)):
    if payload.id:
        plan = (await session.execute(select(Plan).where(Plan.id == payload.id))).scalar_one_or_none()
        if plan is None:
            raise HTTPException(404, "Тариф не найден")
    else:
        exists = (await session.execute(select(Plan).where(
            Plan.code == payload.code))).scalar_one_or_none()
        if exists:
            raise HTTPException(400, "Тариф с таким кодом уже есть")
        plan = Plan(code=payload.code)
        session.add(plan)

    for field, value in payload.model_dump(exclude={"id"}).items():
        setattr(plan, field, value)
    await subs.log_admin(session, admin.tg_id, "plan_upsert", payload.code,
                         f"{payload.title} / {payload.price_rub}₽")
    await session.commit()
    await session.refresh(plan)
    return plan_out(plan)


@router.delete("/plans/{plan_id}")
async def delete_plan(plan_id: int, admin: User = Depends(current_admin),
                      session: AsyncSession = Depends(get_session)):
    plan = (await session.execute(select(Plan).where(Plan.id == plan_id))).scalar_one_or_none()
    if plan is None:
        raise HTTPException(404, "Тариф не найден")
    plan.is_active = False  # мягкое удаление: активные подписки не ломаем
    await subs.log_admin(session, admin.tg_id, "plan_disable", plan.code)
    await session.commit()
    return {"ok": True}


# --- Пользователи ---

@router.get("/users", response_model=list[AdminUserOut])
async def list_users(q: str = Query(default=""), limit: int = 50, offset: int = 0,
                     admin: User = Depends(current_admin),
                     session: AsyncSession = Depends(get_session)):
    stmt = select(User).order_by(User.created_at.desc()).limit(min(limit, 200)).offset(offset)
    if q:
        like = f"%{q.strip().lstrip('@')}%"
        conditions = [User.username.ilike(like), User.first_name.ilike(like)]
        if q.strip().isdigit():
            conditions.append(User.tg_id == int(q.strip()))
        stmt = stmt.where(or_(*conditions))
    users = (await session.execute(stmt)).scalars().all()

    result = []
    for user in users:
        sub = await subs.active_subscription(session, user)
        devices = (await session.execute(select(func.count(Device.id)).where(
            Device.user_id == user.id, Device.is_active.is_(True)))).scalar_one()
        result.append(AdminUserOut(
            tg_id=user.tg_id, username=user.username, first_name=user.first_name,
            role=user.role.value, is_banned=user.is_banned, ban_reason=user.ban_reason,
            trial_used=user.trial_used, devices=devices,
            plan_title=sub.plan_title if sub else None,
            expires_at=sub.expires_at if sub else None, created_at=user.created_at))
    return result


async def _get_user(session: AsyncSession, tg_id: int) -> User:
    user = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
    if user is None:
        raise HTTPException(404, "Пользователь не найден (он должен хотя бы раз открыть бота)")
    return user


@router.post("/grant")
async def grant(payload: GrantRequest, admin: User = Depends(current_admin),
                session: AsyncSession = Depends(get_session)):
    user = await _get_user(session, payload.tg_id)
    plan = None
    if payload.plan_id:
        plan = (await session.execute(select(Plan).where(
            Plan.id == payload.plan_id))).scalar_one_or_none()
        if plan is None:
            raise HTTPException(404, "Тариф не найден")
    if plan is None and not payload.hours:
        raise HTTPException(400, "Укажите тариф или количество часов")

    sub = await subs.grant_subscription(session, user, plan, hours=payload.hours,
                                        devices=payload.devices, title=payload.title)
    await subs.notify(session, user.tg_id,
                      f"🎁 Администратор выдал подписку «{sub.plan_title}» "
                      f"до {subs.aware(sub.expires_at).strftime('%d.%m.%Y %H:%M')} UTC.")
    await subs.log_admin(session, admin.tg_id, "grant", str(user.tg_id), sub.plan_title)
    await session.commit()
    return {"ok": True, "expires_at": sub.expires_at.isoformat()}


@router.post("/revoke")
async def revoke(payload: GrantRequest, admin: User = Depends(current_admin),
                 session: AsyncSession = Depends(get_session)):
    user = await _get_user(session, payload.tg_id)
    await subs.revoke_subscription(session, user)
    await subs.notify(session, user.tg_id, "⚠️ Ваша подписка отозвана администратором.")
    await subs.log_admin(session, admin.tg_id, "revoke", str(user.tg_id))
    await session.commit()
    return {"ok": True}


@router.post("/ban")
async def ban(payload: BanRequest, admin: User = Depends(current_admin),
              session: AsyncSession = Depends(get_session)):
    user = await _get_user(session, payload.tg_id)
    if user.role == Role.owner:
        raise HTTPException(400, "Владельца заблокировать нельзя")
    if user.tg_id == admin.tg_id:
        raise HTTPException(400, "Себя блокировать нельзя")
    user.is_banned = payload.banned
    user.ban_reason = payload.reason if payload.banned else None
    if payload.banned:
        await subs.disable_devices(session, user)
        await subs.notify(session, user.tg_id,
                          f"🚫 Доступ заблокирован. {payload.reason}".strip())
    else:
        await subs.sync_devices(session, user)
        await subs.notify(session, user.tg_id, "✅ Доступ разблокирован.")
    await subs.log_admin(session, admin.tg_id, "ban" if payload.banned else "unban",
                         str(user.tg_id), payload.reason)
    await session.commit()
    return {"ok": True}


@router.post("/role")
async def set_role(payload: RoleRequest, owner: User = Depends(current_owner),
                   session: AsyncSession = Depends(get_session)):
    user = await _get_user(session, payload.tg_id)
    if user.tg_id == settings.owner_id:
        raise HTTPException(400, "Роль владельца изменить нельзя")
    if payload.role not in ("user", "admin"):
        raise HTTPException(400, "Допустимые роли: user, admin")
    user.role = Role(payload.role)
    await subs.log_admin(session, owner.tg_id, "role", str(user.tg_id), payload.role)
    await session.commit()
    return {"ok": True}


@router.post("/broadcast")
async def broadcast(payload: BroadcastRequest, admin: User = Depends(current_admin),
                    session: AsyncSession = Depends(get_session)):
    users = (await session.execute(select(User).where(User.is_banned.is_(False)))).scalars().all()
    count = 0
    for user in users:
        if payload.only_active and not await subs.active_subscription(session, user):
            continue
        session.add(Notification(tg_id=user.tg_id, text=payload.text))
        count += 1
    await subs.log_admin(session, admin.tg_id, "broadcast", "", f"{count} получателей")
    await session.commit()
    return {"ok": True, "queued": count}


# --- Промокоды ---

@router.get("/promos")
async def list_promos(admin: User = Depends(current_admin),
                      session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(PromoCode).order_by(PromoCode.id.desc()))).scalars().all()
    return [{"id": p.id, "code": p.code, "discount_percent": p.discount_percent,
             "bonus_days": p.bonus_days, "max_uses": p.max_uses, "used_count": p.used_count,
             "is_active": p.is_active} for p in rows]


@router.post("/promos")
async def upsert_promo(payload: PromoUpsert, admin: User = Depends(current_admin),
                       session: AsyncSession = Depends(get_session)):
    code = payload.code.strip().upper()
    promo = (await session.execute(select(PromoCode).where(
        PromoCode.code == code))).scalar_one_or_none()
    if promo is None:
        promo = PromoCode(code=code)
        session.add(promo)
    promo.discount_percent = max(0, min(100, payload.discount_percent))
    promo.bonus_days = max(0, payload.bonus_days)
    promo.max_uses = max(0, payload.max_uses)
    promo.is_active = payload.is_active
    await subs.log_admin(session, admin.tg_id, "promo", code)
    await session.commit()
    return {"ok": True, "id": promo.id}


@router.delete("/promos/{promo_id}")
async def delete_promo(promo_id: int, admin: User = Depends(current_admin),
                       session: AsyncSession = Depends(get_session)):
    await session.execute(delete(PromoCode).where(PromoCode.id == promo_id))
    await subs.log_admin(session, admin.tg_id, "promo_delete", str(promo_id))
    await session.commit()
    return {"ok": True}


@router.get("/logs")
async def logs(limit: int = 50, admin: User = Depends(current_admin),
               session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(AdminLog).order_by(AdminLog.id.desc())
                                  .limit(min(limit, 200)))).scalars().all()
    return [{"id": r.id, "admin": r.admin_tg_id, "action": r.action, "target": r.target,
             "details": r.details, "at": r.created_at.isoformat()} for r in rows]


@router.get("/payments")
async def payments(limit: int = 50, admin: User = Depends(current_admin),
                   session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(Payment).order_by(Payment.id.desc())
                                  .limit(min(limit, 200)))).scalars().all()
    out = []
    for p in rows:
        user = (await session.execute(select(User).where(User.id == p.user_id))).scalar_one_or_none()
        out.append({"id": p.id, "tg_id": user.tg_id if user else None, "provider": p.provider,
                    "amount_rub": p.amount_rub, "status": p.status.value,
                    "at": (p.paid_at or p.created_at).isoformat()})
    return out
