"""Настройки, которые меняются из админки и живут в базе, а не в .env."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Setting, utcnow

DEFAULTS: dict[str, str] = {
    # Цена одной отвязки HWID, рублей.
    "unbind_price_rub": "50",
    # Ссылка на APK и его версия — показываются в мини-аппе.
    "apk_url": "",
    "apk_version": "",
    # Сколько минут живёт код привязки приложения.
    "bind_code_ttl_min": "15",
}


async def get(session: AsyncSession, key: str) -> str:
    row = (await session.execute(select(Setting).where(Setting.key == key))).scalar_one_or_none()
    if row is not None:
        return row.value
    return DEFAULTS.get(key, "")


async def get_float(session: AsyncSession, key: str, fallback: float = 0.0) -> float:
    try:
        return float(await get(session, key))
    except (TypeError, ValueError):
        return fallback


async def get_int(session: AsyncSession, key: str, fallback: int = 0) -> int:
    return int(await get_float(session, key, fallback))


async def set_value(session: AsyncSession, key: str, value: str) -> None:
    row = (await session.execute(select(Setting).where(Setting.key == key))).scalar_one_or_none()
    if row is None:
        session.add(Setting(key=key, value=value))
    else:
        row.value = value
        row.updated_at = utcnow()
    await session.flush()


async def all_values(session: AsyncSession) -> dict[str, str]:
    rows = (await session.execute(select(Setting))).scalars().all()
    values = dict(DEFAULTS)
    values.update({row.key: row.value for row in rows})
    return values
