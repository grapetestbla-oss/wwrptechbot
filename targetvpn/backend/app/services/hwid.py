"""Привязка приложения TargetVPN к устройству по HWID."""
from __future__ import annotations

import hashlib
import logging
import random
import secrets
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import BindCode, Device, Node, User, utcnow
from . import settings_store, subs

log = logging.getLogger("hwid")

ALPHABET = "ACDEFGHJKLMNPQRSTUVWXYZ23456789"  # без похожих символов


class HwidError(RuntimeError):
    pass


def normalize(raw: str) -> str:
    """HWID приходит от клиента, поэтому в базе храним только его хеш."""
    value = (raw or "").strip()
    if len(value) < 8:
        raise HwidError("Некорректный идентификатор устройства")
    return hashlib.sha256(value.encode()).hexdigest()


def mask(hwid: str | None) -> str:
    return f"{hwid[:6]}…{hwid[-4:]}" if hwid else ""


async def issue_bind_code(session: AsyncSession, user: User, node_id: int | None = None) -> BindCode:
    """Код, который пользователь вводит в приложении. Живёт недолго и одноразовый."""
    sub = await subs.active_subscription(session, user)
    if sub is None:
        raise HwidError("Нужна активная подписка")

    # Старые неиспользованные коды гасим, чтобы действовал только последний.
    for old in (await session.execute(select(BindCode).where(
            BindCode.user_id == user.id, BindCode.is_used.is_(False)))).scalars().all():
        old.is_used = True

    ttl = await settings_store.get_int(session, "bind_code_ttl_min", 15)
    for _ in range(20):
        code = "".join(random.choice(ALPHABET) for _ in range(6))
        exists = (await session.execute(select(BindCode).where(
            BindCode.code == code))).scalar_one_or_none()
        if exists is None:
            break
    else:
        raise HwidError("Не удалось сгенерировать код, попробуйте ещё раз")

    bind = BindCode(code=code, user_id=user.id, node_id=node_id,
                    expires_at=utcnow() + timedelta(minutes=ttl))
    session.add(bind)
    await session.commit()
    await session.refresh(bind)
    return bind


async def bind_device(session: AsyncSession, code: str, raw_hwid: str, name: str = "",
                      model: str = "", app_version: str = "") -> tuple[Device, User]:
    """Приложение обменивает код + HWID на постоянный токен доступа."""
    hwid = normalize(raw_hwid)
    bind = (await session.execute(select(BindCode).where(
        BindCode.code == (code or "").strip().upper()))).scalar_one_or_none()
    if bind is None or bind.is_used:
        raise HwidError("Код не найден или уже использован")
    if subs.aware(bind.expires_at) < utcnow():
        raise HwidError("Срок действия кода истёк, получите новый")

    user = (await session.execute(select(User).where(User.id == bind.user_id))).scalar_one()
    if user.is_banned:
        raise HwidError("Доступ заблокирован")
    sub = await subs.active_subscription(session, user)
    if sub is None:
        raise HwidError("Подписка неактивна")

    # Этот же HWID уже привязан к устройству пользователя — просто выдаём новый токен.
    existing = (await session.execute(select(Device).where(
        Device.user_id == user.id, Device.hwid == hwid))).scalar_one_or_none()
    if existing is not None:
        existing.client_token = secrets.token_urlsafe(32)
        existing.app_version = app_version[:32]
        existing.last_seen_at = utcnow()
        bind.is_used = True
        await session.commit()
        await session.refresh(existing)
        return existing, user

    # HWID, занятый чужим аккаунтом, повторно не привязываем.
    taken = (await session.execute(select(Device).where(
        Device.hwid == hwid, Device.user_id != user.id))).scalar_one_or_none()
    if taken is not None:
        raise HwidError("Это устройство уже привязано к другому аккаунту")

    # Свободный слот: сначала берём устройство без HWID, иначе создаём новое.
    free = (await session.execute(select(Device).where(
        Device.user_id == user.id, Device.hwid.is_(None),
        Device.is_active.is_(True)).order_by(Device.id))).scalars().first()
    if free is None:
        free = await subs.add_device(session, user, name or "Приложение", "android", bind.node_id)

    free.hwid = hwid
    free.hwid_bound_at = utcnow()
    free.client_token = secrets.token_urlsafe(32)
    free.client_model = model[:64]
    free.app_version = app_version[:32]
    free.last_seen_at = utcnow()
    free.platform = "android"
    if name:
        free.name = name[:64]
    bind.is_used = True
    await session.commit()
    await session.refresh(free)
    return free, user


async def device_by_token(session: AsyncSession, token: str, raw_hwid: str) -> tuple[Device, User]:
    """Аутентификация приложения: токен обязан совпасть с привязанным HWID."""
    if not token:
        raise HwidError("Приложение не привязано")
    device = (await session.execute(select(Device).where(
        Device.client_token == token))).scalar_one_or_none()
    if device is None:
        raise HwidError("Привязка отозвана, введите новый код")
    if device.hwid != normalize(raw_hwid):
        raise HwidError("Устройство не совпадает с привязанным. Нужна отвязка HWID")

    user = (await session.execute(select(User).where(User.id == device.user_id))).scalar_one()
    if user.is_banned:
        raise HwidError("Доступ заблокирован")
    device.last_seen_at = utcnow()
    await session.commit()
    return device, user


async def unbind(session: AsyncSession, device: Device, *, reason: str = "paid") -> None:
    """Снимает привязку: устройство остаётся, приложение придётся привязать заново."""
    device.hwid = None
    device.hwid_bound_at = None
    device.client_token = None
    device.client_model = ""
    device.unbind_count += 1
    await session.flush()
    log.info("HWID отвязан у устройства %s (%s)", device.id, reason)


async def node_title(session: AsyncSession, device: Device) -> str:
    if not device.node_id:
        return ""
    node = (await session.execute(select(Node).where(Node.id == device.node_id))).scalar_one_or_none()
    return node.title if node else ""
