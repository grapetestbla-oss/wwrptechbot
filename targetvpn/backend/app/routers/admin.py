from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_session
from ..marzban import MarzbanError, client_for, marzban
from ..models import (AdminLog, Device, Node, Notification, Payment,
                      PaymentStatus, Plan, PromoCode, Role, Subscription, User,
                      utcnow)
from ..schemas import (AdminUserOut, BalanceRequest, BanRequest, BroadcastRequest,
                       GrantRequest,
                       NodeAdminOut, NodeUpsert, PlanOut, PlanUpsert, PromoUpsert,
                       RoleRequest, SettingsUpsert, StatsOut, UnbindRequest)
from ..security import current_admin, current_owner
from ..services import billing
from ..services import hwid as hwid_service
from ..services import settings_store, subs
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

    nodes = (await session.execute(select(Node).where(Node.is_active.is_(True)))).scalars().all()
    online = 0
    for node in nodes:
        try:
            await client_for(node).system_stats()
            online += 1
        except (MarzbanError, OSError):
            continue

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
        node_online=bool(nodes) and online == len(nodes),
        nodes_total=len(nodes),
        nodes_online=online,
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
            trial_used=user.trial_used, devices=devices, balance_rub=user.balance_rub,
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


@router.post("/balance")
async def change_balance(payload: BalanceRequest, admin: User = Depends(current_admin),
                         session: AsyncSession = Depends(get_session)):
    """Начисление или списание баланса. Баланс тратится на тарифы и отвязки."""
    user = await _get_user(session, payload.tg_id)
    if payload.amount == 0:
        raise HTTPException(400, "Сумма не может быть нулевой")
    balance = await billing.top_up_balance(session, user, payload.amount, payload.reason)
    await subs.log_admin(session, admin.tg_id, "balance", str(user.tg_id),
                         f"{payload.amount:+.0f} ₽ -> {balance:.0f} ₽ {payload.reason}".strip())
    await session.commit()
    return {"ok": True, "balance_rub": balance}


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
             "is_active": p.is_active,
             "expires_at": p.expires_at.isoformat() if p.expires_at else None} for p in rows]


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
    promo.expires_at = (utcnow() + timedelta(days=payload.expires_in_days)
                        if payload.expires_in_days > 0 else None)
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


# --- Ноды (локации) ---

@router.get("/nodes", response_model=list[NodeAdminOut])
async def list_nodes(check: bool = False, admin: User = Depends(current_admin),
                     session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(Node).order_by(Node.sort_order, Node.id))).scalars().all()
    result = []
    for node in rows:
        devices = (await session.execute(select(func.count(Device.id)).where(
            Device.node_id == node.id, Device.is_active.is_(True)))).scalar_one()
        online = None
        if check:
            try:
                await client_for(node).system_stats()
                online = True
            except (MarzbanError, OSError):
                online = False
        result.append(NodeAdminOut(
            id=node.id, code=node.code, title=node.title, flag=node.flag, country=node.country,
            is_default=node.is_default, url=node.url, username=node.username,
            verify_ssl=node.verify_ssl, inbounds_json=node.inbounds_json,
            is_active=node.is_active, sort_order=node.sort_order, devices=devices, online=online))
    return result


@router.post("/nodes", response_model=NodeAdminOut)
async def upsert_node(payload: NodeUpsert, admin: User = Depends(current_admin),
                      session: AsyncSession = Depends(get_session)):
    import json as _json
    try:
        _json.loads(payload.inbounds_json)
    except ValueError:
        raise HTTPException(400, "Инбаунды должны быть корректным JSON")

    if payload.id:
        node = (await session.execute(select(Node).where(Node.id == payload.id))).scalar_one_or_none()
        if node is None:
            raise HTTPException(404, "Локация не найдена")
    else:
        if (await session.execute(select(Node).where(Node.code == payload.code))).scalar_one_or_none():
            raise HTTPException(400, "Локация с таким кодом уже есть")
        node = Node(code=payload.code)
        session.add(node)

    data = payload.model_dump(exclude={"id"})
    # Пустой пароль при редактировании означает «оставить прежний».
    if payload.id and not payload.password:
        data.pop("password")
    for field, value in data.items():
        setattr(node, field, value)

    if node.is_default:
        for other in (await session.execute(select(Node))).scalars().all():
            if other.code != node.code:
                other.is_default = False

    await subs.log_admin(session, admin.tg_id, "node_upsert", payload.code, payload.title)
    await session.commit()
    await session.refresh(node)
    return NodeAdminOut(id=node.id, code=node.code, title=node.title, flag=node.flag,
                        country=node.country, is_default=node.is_default, url=node.url,
                        username=node.username, verify_ssl=node.verify_ssl,
                        inbounds_json=node.inbounds_json, is_active=node.is_active,
                        sort_order=node.sort_order)


@router.delete("/nodes/{node_id}")
async def disable_node(node_id: int, admin: User = Depends(current_admin),
                       session: AsyncSession = Depends(get_session)):
    node = (await session.execute(select(Node).where(Node.id == node_id))).scalar_one_or_none()
    if node is None:
        raise HTTPException(404, "Локация не найдена")
    # Мягкое отключение: выданные ключи остаются, новые устройства сюда не попадают.
    node.is_active = False
    node.is_default = False
    await subs.log_admin(session, admin.tg_id, "node_disable", node.code)
    await session.commit()
    return {"ok": True}


# --- Настройки сервиса ---

@router.get("/settings")
async def read_settings(admin: User = Depends(current_admin),
                        session: AsyncSession = Depends(get_session)):
    return await settings_store.all_values(session)


@router.post("/settings")
async def write_settings(payload: SettingsUpsert, admin: User = Depends(current_admin),
                         session: AsyncSession = Depends(get_session)):
    changes = {k: v for k, v in payload.model_dump().items() if v is not None}
    if "unbind_price_rub" in changes and changes["unbind_price_rub"] < 0:
        raise HTTPException(400, "Цена отвязки не может быть отрицательной")
    for key, value in changes.items():
        await settings_store.set_value(session, key, str(value))
    await subs.log_admin(session, admin.tg_id, "settings", "",
                         ", ".join(f"{k}={v}" for k, v in changes.items()))
    await session.commit()
    return await settings_store.all_values(session)


# --- Привязки устройств ---

@router.get("/devices/{tg_id}")
async def user_devices(tg_id: int, admin: User = Depends(current_admin),
                       session: AsyncSession = Depends(get_session)):
    user = await _get_user(session, tg_id)
    rows = (await session.execute(select(Device).where(
        Device.user_id == user.id).order_by(Device.id))).scalars().all()
    return [{"id": d.id, "name": d.name, "platform": d.platform,
             "hwid": hwid_service.mask(d.hwid), "hwid_bound": d.hwid is not None,
             "model": d.client_model, "app_version": d.app_version,
             "unbind_count": d.unbind_count, "is_active": d.is_active,
             "last_seen_at": d.last_seen_at.isoformat() if d.last_seen_at else None}
            for d in rows]


@router.post("/unbind")
async def admin_unbind(payload: UnbindRequest, admin: User = Depends(current_admin),
                       session: AsyncSession = Depends(get_session)):
    """Бесплатная отвязка HWID администратором (например, по обращению в поддержку)."""
    device = (await session.execute(select(Device).where(
        Device.id == payload.device_id))).scalar_one_or_none()
    if device is None:
        raise HTTPException(404, "Устройство не найдено")
    if device.hwid is None:
        raise HTTPException(400, "Устройство не привязано")
    user = (await session.execute(select(User).where(User.id == device.user_id))).scalar_one()
    await hwid_service.unbind(session, device, reason="admin")
    await subs.notify(session, user.tg_id,
                      f"🔓 Администратор отвязал устройство «{device.name}». "
                      "Можно привязать приложение заново.")
    await subs.log_admin(session, admin.tg_id, "unbind", str(user.tg_id), device.name)
    await session.commit()
    return {"ok": True}
