from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl

import jwt
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .db import get_session
from .models import Role, User, utcnow

INIT_DATA_TTL = 24 * 3600  # окно валидности initData из Telegram


def verify_init_data(init_data: str) -> dict:
    """Проверяет подпись Telegram WebApp initData и возвращает разобранные поля."""
    if not init_data:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "initData отсутствует")
    if not settings.bot_token:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "BOT_TOKEN не настроен")

    try:
        pairs = dict(parse_qsl(init_data, strict_parsing=True))
    except ValueError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "initData повреждена")

    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "initData без подписи")

    check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret_key = hmac.new(b"WebAppData", settings.bot_token.encode(), hashlib.sha256).digest()
    calculated = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated, received_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверная подпись initData")

    auth_date = int(pairs.get("auth_date", "0"))
    if auth_date and time.time() - auth_date > INIT_DATA_TTL:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "initData просрочена")

    user_raw = pairs.get("user")
    if not user_raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "initData без пользователя")
    pairs["user"] = json.loads(user_raw)
    return pairs


def create_token(tg_id: int) -> str:
    payload = {
        "sub": str(tg_id),
        "exp": datetime.now(timezone.utc) + timedelta(hours=settings.jwt_ttl_hours),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> int:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        return int(payload["sub"])
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Сессия истекла, откройте приложение заново")


async def get_or_create_user(session: AsyncSession, tg_user: dict,
                             referrer_id: int | None = None) -> User:
    tg_id = int(tg_user["id"])
    user = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
    if user is None:
        user = User(
            tg_id=tg_id,
            username=tg_user.get("username"),
            first_name=tg_user.get("first_name"),
            language=tg_user.get("language_code") or "ru",
            sub_token=secrets.token_urlsafe(24),
            role=Role.owner if tg_id == settings.owner_id else Role.user,
            referrer_id=referrer_id if referrer_id and referrer_id != tg_id else None,
        )
        session.add(user)
        await session.flush()
    else:
        user.username = tg_user.get("username") or user.username
        user.first_name = tg_user.get("first_name") or user.first_name
        user.last_seen_at = utcnow()
        if tg_id == settings.owner_id and user.role != Role.owner:
            user.role = Role.owner
    await session.commit()
    await session.refresh(user)
    return user


async def current_user(authorization: str = Header(default=""),
                       session: AsyncSession = Depends(get_session)) -> User:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Нет авторизации")
    tg_id = decode_token(authorization[7:])
    user = (await session.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Пользователь не найден")
    if user.is_banned:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            f"Доступ заблокирован. {user.ban_reason or ''}".strip())
    user.last_seen_at = utcnow()
    await session.commit()
    return user


async def current_admin(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостаточно прав")
    return user


async def current_owner(user: User = Depends(current_user)) -> User:
    if user.role != Role.owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Только для владельца")
    return user


def require_internal(x_internal_secret: str = Header(default="")) -> None:
    """Аутентификация служебных вызовов от бота к бэкенду."""
    if not hmac.compare_digest(x_internal_secret, settings.internal_secret):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Неверный внутренний ключ")
