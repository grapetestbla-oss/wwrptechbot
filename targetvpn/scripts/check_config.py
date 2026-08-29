"""Проверка готовности установки: токен, домен, ноды, платежи.

Запуск на сервере:  /opt/targetvpn/.venv/bin/python scripts/check_config.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import httpx  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.marzban import MarzbanError, client_for  # noqa: E402
from app.models import Node, Plan, User  # noqa: E402

OK, WARN, BAD = "✅", "⚠️ ", "❌"
problems: list[str] = []


def line(mark: str, text: str, detail: str = "") -> None:
    print(f"{mark} {text}" + (f" — {detail}" if detail else ""))
    if mark == BAD:
        problems.append(text)


async def check_bot() -> None:
    if not settings.bot_token:
        return line(BAD, "BOT_TOKEN не задан")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"https://api.telegram.org/bot{settings.bot_token}/getMe")
        data = resp.json()
    except httpx.HTTPError as exc:
        return line(BAD, "Telegram недоступен", str(exc)[:80])
    if not data.get("ok"):
        return line(BAD, "Токен бота отклонён Telegram", str(data.get("description"))[:80])
    bot = data["result"]
    line(OK, "Токен бота рабочий", f"@{bot.get('username')}")
    if settings.bot_username and settings.bot_username.lstrip("@") != bot.get("username"):
        line(WARN, "BOT_USERNAME в .env не совпадает с реальным",
             f"{settings.bot_username} vs {bot.get('username')}")


async def check_webapp() -> None:
    url = settings.webapp_url
    if not url.startswith("https://"):
        return line(BAD, "WEBAPP_URL должен быть https — Telegram не откроет Mini App", url)
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(url)
    except httpx.HTTPError as exc:
        return line(BAD, "Mini App недоступен снаружи", str(exc)[:100])
    if resp.status_code == 200 and "TargetVPN" in resp.text:
        line(OK, "Mini App отвечает", url)
    else:
        line(BAD, f"Mini App вернул {resp.status_code}", url)


async def check_secrets() -> None:
    weak = [name for name, value in (("JWT_SECRET", settings.jwt_secret),
                                     ("INTERNAL_SECRET", settings.internal_secret))
            if value.startswith("change-me") or len(value) < 24]
    if weak:
        line(BAD, "Слабые секреты в .env", ", ".join(weak))
    else:
        line(OK, "Секреты заданы")


async def check_db() -> None:
    try:
        await init_db()
        async with SessionLocal() as session:
            plans = (await session.execute(select(Plan))).scalars().all()
            owner = (await session.execute(select(User).where(
                User.tg_id == settings.owner_id))).scalar_one_or_none()
    except Exception as exc:  # noqa: BLE001
        return line(BAD, "База недоступна", str(exc)[:120])
    line(OK, "База готова", f"тарифов: {len(plans)}")
    line(OK if owner and owner.role.value == "owner" else BAD,
         "Владелец с админ-правами", str(settings.owner_id))


async def check_nodes() -> None:
    async with SessionLocal() as session:
        nodes = (await session.execute(select(Node))).scalars().all()
    ready = [n for n in nodes if n.is_active and n.url]
    if not ready:
        return line(WARN, "Локаций пока нет — оплата и пробный доступ отключены",
                    "добавьте ноду в админке, когда будет ВПС под VPN")
    for node in ready:
        try:
            await client_for(node).system_stats()
            line(OK, f"Локация «{node.title}» отвечает", node.url)
        except (MarzbanError, OSError) as exc:
            line(BAD, f"Локация «{node.title}» недоступна", str(exc)[:100])


async def check_payments() -> None:
    methods = []
    if settings.bot_token:
        methods.append("Telegram Stars")
    if settings.cryptobot_token:
        methods.append("CryptoBot")
    if settings.lzt_token and settings.lzt_user_id:
        methods.append("LZT Market")
    line(OK if methods else WARN, "Способы оплаты", ", ".join(methods) or "только Stars")


async def main() -> None:
    print("Проверка TargetVPN\n")
    for check in (check_bot, check_webapp, check_secrets, check_db, check_nodes, check_payments):
        await check()
    if problems:
        print(f"\nНужно исправить: {len(problems)}")
        raise SystemExit(1)
    print("\nВсё в порядке.")


if __name__ == "__main__":
    asyncio.run(main())
