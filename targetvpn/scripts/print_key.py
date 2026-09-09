"""Печатает ключ подключения пользователя — для диагностики ноды.

    python scripts/print_key.py 7824168810
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import Device, User  # noqa: E402


async def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Укажите Telegram ID: python scripts/print_key.py 7824168810")
    tg_id = int(sys.argv[1])

    async with SessionLocal() as session:
        user = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
        if user is None:
            raise SystemExit(f"Пользователь {tg_id} не найден")
        device = (await session.execute(select(Device).where(
            Device.user_id == user.id, Device.is_active.is_(True))
            .order_by(Device.id))).scalars().first()
        if device is None or not device.config_url:
            raise SystemExit("У пользователя нет активных устройств с ключом")
        print(device.config_url)


if __name__ == "__main__":
    asyncio.run(main())
