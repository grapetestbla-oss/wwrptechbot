"""Регистрация VPN-ноды в базе TargetVPN из командной строки.

    python scripts/add_node.py --code main --title "Германия" --flag 🇩🇪 \
        --url https://127.0.0.1:8000 --username admin --password secret --default
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import Node  # noqa: E402


async def main() -> None:
    parser = argparse.ArgumentParser(description="Добавить или обновить локацию TargetVPN")
    parser.add_argument("--code", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--url", required=True, help="адрес панели Marzban")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--flag", default="🌍")
    parser.add_argument("--country", default="")
    parser.add_argument("--inbounds", default='{"vless": ["VLESS TCP REALITY"]}')
    parser.add_argument("--no-verify-ssl", action="store_true",
                        help="панель на самоподписанном сертификате или на 127.0.0.1")
    parser.add_argument("--default", action="store_true", help="выдавать эту локацию по умолчанию")
    args = parser.parse_args()

    await init_db()
    async with SessionLocal() as session:
        node = (await session.execute(select(Node).where(
            Node.code == args.code))).scalar_one_or_none()
        created = node is None
        if node is None:
            node = Node(code=args.code)
            session.add(node)

        node.title = args.title
        node.flag = args.flag
        node.country = args.country
        node.url = args.url.rstrip("/")
        node.username = args.username
        node.password = args.password
        node.verify_ssl = not args.no_verify_ssl
        node.inbounds_json = args.inbounds
        node.is_active = True
        if args.default:
            node.is_default = True
            for other in (await session.execute(select(Node))).scalars().all():
                if other.code != args.code:
                    other.is_default = False
        await session.commit()

    print(f"{'Добавлена' if created else 'Обновлена'} локация «{args.title}» ({args.code})")


if __name__ == "__main__":
    asyncio.run(main())
