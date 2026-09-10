"""Сброс ссылок на сборки к значениям по умолчанию.

Раньше ссылки прописывались вручную (publish_build.py клал файлы на сам
сервер), и эти записи в базе перебивают новые значения по умолчанию, которые
ведут на последний релиз GitHub. Скрипт удаляет записи, после чего снова
работают значения из settings_store.DEFAULTS.

    python scripts/reset_downloads.py            # показать, что изменится
    python scripts/reset_downloads.py --apply    # применить
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import delete, select  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import Setting  # noqa: E402
from app.services import settings_store  # noqa: E402

KEYS = [key for _p, key, _v, _e, _t in settings_store.DOWNLOADS]
KEYS += [ver for _p, _u, ver, _e, _t in settings_store.DOWNLOADS]


async def main() -> None:
    parser = argparse.ArgumentParser(description="Сбросить ссылки на сборки")
    parser.add_argument("--apply", action="store_true", help="применить, а не показать")
    args = parser.parse_args()

    await init_db()
    async with SessionLocal() as session:
        rows = (await session.execute(
            select(Setting).where(Setting.key.in_(KEYS)))).scalars().all()
        if not rows:
            print("Своих ссылок нет — уже используются значения по умолчанию.")
        for row in rows:
            default = settings_store.DEFAULTS.get(row.key, "")
            print(f"{row.key}:\n  сейчас:      {row.value or '(пусто)'}\n"
                  f"  станет:      {default or '(пусто)'}")
        if not args.apply:
            if rows:
                print("\nЭто предпросмотр. Чтобы применить: --apply")
            return
        await session.execute(delete(Setting).where(Setting.key.in_(KEYS)))
        await session.commit()

    async with SessionLocal() as session:
        print("\nКнопки скачивания теперь:")
        for item in await settings_store.downloads(session):
            print(f"  {item['emoji']} {item['title']}: {item['url']}")


if __name__ == "__main__":
    asyncio.run(main())
