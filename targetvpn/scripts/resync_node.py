"""Пересинхронизация локации с работающим ядром — из командной строки.

    python scripts/resync_node.py          # все активные локации
    python scripts/resync_node.py main     # только локация с этим кодом
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import Node  # noqa: E402
from app.services import subs  # noqa: E402


async def main() -> None:
    code = sys.argv[1] if len(sys.argv) > 1 else ""

    async with SessionLocal() as session:
        stmt = select(Node).where(Node.is_active.is_(True), Node.url != "")
        if code:
            stmt = stmt.where(Node.code == code)
        nodes = (await session.execute(stmt)).scalars().all()

        if not nodes:
            raise SystemExit("Подходящих локаций не нашлось")

        for node in nodes:
            print(f"Локация «{node.title}» ({node.code})…")
            result = await subs.resync_node(session, node)
            print(f"  устройств: {result['devices']}, выдано заново: {result['restored']}, "
                  f"ошибок: {result['failed']}, "
                  f"ядро перезапущено: {'да' if result['core_restarted'] else 'нет'}")


if __name__ == "__main__":
    asyncio.run(main())
