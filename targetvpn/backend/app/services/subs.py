from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..marzban import MarzbanError, client_for, marzban
from ..models import (AdminLog, Device, Node, Notification, Plan, Subscription,
                      User, utcnow)

log = logging.getLogger("subs")


def aware(dt: datetime | None) -> datetime | None:
    """SQLite отдаёт naive datetime — приводим к UTC-aware, чтобы сравнения не падали."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def active_subscription(session: AsyncSession, user: User) -> Subscription | None:
    rows = (await session.execute(
        select(Subscription)
        .where(Subscription.user_id == user.id, Subscription.is_active.is_(True))
        .order_by(Subscription.expires_at.desc())
    )).scalars().all()
    now = utcnow()
    for sub in rows:
        if aware(sub.expires_at) > now:
            return sub
    return None


async def grant_subscription(session: AsyncSession, user: User, plan: Plan | None = None, *,
                             hours: int | None = None, devices: int | None = None,
                             traffic_gb: int | None = None, title: str | None = None,
                             is_trial: bool = False, extend: bool = True) -> Subscription:
    """Выдаёт или продлевает подписку и синхронизирует устройства с нодой."""
    hours = hours if hours is not None else (plan.duration_hours if plan else 720)
    devices = devices if devices is not None else (plan.devices if plan else 1)
    traffic_gb = traffic_gb if traffic_gb is not None else (plan.traffic_gb if plan else 0)
    title = title or (plan.title if plan else "Индивидуальный")
    is_trial = is_trial or bool(plan and plan.is_trial)

    current = await active_subscription(session, user)
    now = utcnow()

    if current and extend and current.plan_id == (plan.id if plan else None):
        # Тот же тариф — просто добавляем время.
        current.expires_at = aware(current.expires_at) + timedelta(hours=hours)
        current.devices = max(current.devices, devices)
        current.notified_expiring = False
        sub = current
    else:
        if current:
            current.is_active = False
        base = aware(current.expires_at) if current and extend else now
        base = max(base or now, now)
        sub = Subscription(
            user_id=user.id,
            plan_id=plan.id if plan else None,
            plan_title=title,
            devices=devices,
            traffic_gb=traffic_gb,
            started_at=now,
            expires_at=base + timedelta(hours=hours),
            is_active=True,
            is_trial=is_trial,
        )
        session.add(sub)

    if is_trial:
        user.trial_used = True

    await session.flush()
    await sync_devices(session, user, sub)
    await session.commit()
    await session.refresh(sub)
    return sub


async def revoke_subscription(session: AsyncSession, user: User) -> None:
    subs = (await session.execute(
        select(Subscription).where(Subscription.user_id == user.id,
                                   Subscription.is_active.is_(True)))).scalars().all()
    for sub in subs:
        sub.is_active = False
    await disable_devices(session, user)
    await session.commit()


async def node_client(session: AsyncSession, device: Device):
    """Клиент панели той ноды, на которой живёт устройство."""
    if device.node_id:
        node = (await session.execute(
            select(Node).where(Node.id == device.node_id))).scalar_one_or_none()
        if node:
            return client_for(node)
    return marzban


async def pick_node(session: AsyncSession, node_id: int | None = None) -> Node | None:
    """Выбранная локация, иначе нода по умолчанию, иначе первая активная."""
    stmt = select(Node).where(Node.is_active.is_(True)).order_by(
        Node.is_default.desc(), Node.sort_order, Node.id)
    if node_id:
        node = (await session.execute(select(Node).where(
            Node.id == node_id, Node.is_active.is_(True)))).scalar_one_or_none()
        if node is None:
            raise ValueError("Локация недоступна")
        return node
    return (await session.execute(stmt)).scalars().first()


def remote_username(user: User, index: int) -> str:
    return f"{settings.marzban_prefix}_{user.tg_id}_{index}"


async def sync_devices(session: AsyncSession, user: User,
                       sub: Subscription | None = None) -> list[Device]:
    """Приводит аккаунты на ноде в соответствие с активной подпиской."""
    sub = sub or await active_subscription(session, user)
    devices = (await session.execute(
        select(Device).where(Device.user_id == user.id).order_by(Device.id))).scalars().all()

    if sub is None or user.is_banned:
        await disable_devices(session, user)
        return devices

    expire_ts = int(aware(sub.expires_at).timestamp())
    per_device_gb = sub.traffic_gb

    # Лишние устройства (после смены тарифа на меньший) — отключаем.
    for extra in devices[sub.devices:]:
        if extra.is_active:
            client = await node_client(session, extra)
            await client.delete_user(extra.remote_username)
            extra.is_active = False
            extra.config_url = ""

    for device in devices[: sub.devices]:
        try:
            client = await node_client(session, device)
            data = await client.create_user(device.remote_username, expire_ts, per_device_gb,
                                            note=f"tg:{user.tg_id}")
            _apply_remote(device, data, client.url)
            device.is_active = True
        except MarzbanError as exc:
            log.error("Синхронизация %s не удалась: %s", device.remote_username, exc)
    await session.flush()
    return devices


async def disable_devices(session: AsyncSession, user: User) -> None:
    devices = (await session.execute(
        select(Device).where(Device.user_id == user.id))).scalars().all()
    for device in devices:
        try:
            client = await node_client(session, device)
            await client.update_user(device.remote_username, status="disabled")
        except MarzbanError as exc:
            log.warning("Не удалось отключить %s: %s", device.remote_username, exc)
        device.is_active = False
    await session.flush()


async def add_device(session: AsyncSession, user: User, name: str, platform: str,
                     node_id: int | None = None) -> Device:
    sub = await active_subscription(session, user)
    if sub is None:
        raise ValueError("Нет активной подписки")
    used = (await session.execute(
        select(Device).where(Device.user_id == user.id, Device.is_active.is_(True)))).scalars().all()
    if len(used) >= sub.devices:
        raise ValueError(f"Достигнут лимит устройств для тарифа: {sub.devices}")

    # Ищем свободный индекс, чтобы не пересоздавать удалённые имена.
    taken = {d.remote_username for d in (await session.execute(
        select(Device).where(Device.user_id == user.id))).scalars().all()}
    index = 1
    while remote_username(user, index) in taken:
        index += 1

    node = await pick_node(session, node_id)
    device = Device(user_id=user.id, name=name[:64] or f"Устройство {index}",
                    platform=platform, remote_username=remote_username(user, index),
                    node_id=node.id if node else None)
    session.add(device)
    await session.flush()

    client = client_for(node) if node else marzban
    data = await client.create_user(device.remote_username,
                                    int(aware(sub.expires_at).timestamp()),
                                    sub.traffic_gb, note=f"tg:{user.tg_id}")
    _apply_remote(device, data, client.url)
    await session.commit()
    await session.refresh(device)
    return device


async def remove_device(session: AsyncSession, user: User, device_id: int) -> None:
    device = (await session.execute(
        select(Device).where(Device.id == device_id,
                             Device.user_id == user.id))).scalar_one_or_none()
    if device is None:
        raise ValueError("Устройство не найдено")
    client = await node_client(session, device)
    await client.delete_user(device.remote_username)
    await session.delete(device)
    await session.commit()


async def refresh_device(session: AsyncSession, device: Device) -> Device:
    """Перевыпускает ключ устройства (например, если конфиг утёк)."""
    client = await node_client(session, device)
    await client.delete_user(device.remote_username)
    user = (await session.execute(select(User).where(User.id == device.user_id))).scalar_one()
    sub = await active_subscription(session, user)
    if sub is None:
        raise ValueError("Нет активной подписки")
    data = await client.create_user(device.remote_username,
                                    int(aware(sub.expires_at).timestamp()),
                                    sub.traffic_gb, note=f"tg:{user.tg_id}")
    _apply_remote(device, data, client.url)
    await session.commit()
    return device


def _apply_remote(device: Device, data: dict | None, base_url: str = "") -> None:
    if not data:
        return
    links = data.get("links") or []
    device.config_url = links[0] if links else device.config_url
    sub_url = data.get("subscription_url") or ""
    if sub_url and sub_url.startswith("/"):
        sub_url = (base_url or settings.marzban_url).rstrip("/") + sub_url
    device.remote_sub_url = sub_url
    device.used_traffic = float(data.get("used_traffic") or 0)
    device.synced_at = utcnow()


async def notify(session: AsyncSession, tg_id: int, text: str) -> None:
    session.add(Notification(tg_id=tg_id, text=text))
    await session.flush()


async def log_admin(session: AsyncSession, admin_tg_id: int, action: str, target: str = "",
                    details: str = "") -> None:
    session.add(AdminLog(admin_tg_id=admin_tg_id, action=action, target=target, details=details))
    await session.flush()


async def expire_pass(session: AsyncSession) -> int:
    """Фоновая проверка: гасит просроченные подписки, шлёт напоминания."""
    now = utcnow()
    subs = (await session.execute(
        select(Subscription).where(Subscription.is_active.is_(True)))).scalars().all()
    changed = 0
    for sub in subs:
        expires = aware(sub.expires_at)
        user = (await session.execute(select(User).where(User.id == sub.user_id))).scalar_one()
        if expires <= now:
            sub.is_active = False
            await disable_devices(session, user)
            await notify(session, user.tg_id,
                         "⌛️ Подписка TargetVPN закончилась. Продлите её в приложении, "
                         "чтобы вернуть доступ.")
            changed += 1
        elif not sub.notified_expiring and expires - now <= timedelta(hours=24):
            sub.notified_expiring = True
            hours_left = max(1, int((expires - now).total_seconds() // 3600))
            await notify(session, user.tg_id,
                         f"⏰ Подписка «{sub.plan_title}» закончится через {hours_left} ч. "
                         "Продлите заранее, чтобы не потерять ключи.")
            changed += 1
    await session.commit()
    return changed
