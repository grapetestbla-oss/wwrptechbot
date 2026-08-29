from __future__ import annotations

import secrets
from collections.abc import AsyncIterator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import (AsyncSession, async_sessionmaker,
                                    create_async_engine)

from .config import settings
from .models import Base, Node, Plan, Role, User

engine = create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


# Стартовый набор тарифов. Всё редактируется из админки Mini App,
# здесь только значения по умолчанию при первом запуске.
DEFAULT_PLANS = [
    dict(code="trial", title="Пробный", description="24 часа бесплатно, 3 устройства",
         price_rub=0, duration_hours=24, devices=3, traffic_gb=0, is_trial=True,
         sort_order=0, emoji="🎁"),
    dict(code="start", title="Старт", description="1 устройство на месяц",
         price_rub=99, duration_hours=720, devices=1, traffic_gb=0,
         sort_order=10, emoji="⚡"),
    dict(code="standard", title="Стандарт", description="3 устройства на месяц",
         price_rub=149, old_price_rub=199, duration_hours=720, devices=3, traffic_gb=0,
         is_popular=True, sort_order=20, emoji="🔥"),
    dict(code="max", title="Максимум", description="5 устройств на месяц",
         price_rub=199, duration_hours=720, devices=5, traffic_gb=0,
         sort_order=30, emoji="👑"),
    dict(code="standard90", title="Стандарт 3 мес.", description="3 устройства на 90 дней",
         price_rub=399, old_price_rub=447, duration_hours=2160, devices=3, traffic_gb=0,
         sort_order=40, emoji="💎"),
]


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as session:
        existing = set((await session.execute(select(Plan.code))).scalars().all())
        for data in DEFAULT_PLANS:
            if data["code"] not in existing:
                session.add(Plan(**data))

        nodes = (await session.execute(select(Node.id))).scalars().all()
        if not nodes and (settings.marzban_url or settings.demo_mode):
            # Первый запуск: нода берётся из .env, дальше управляется из админки.
            import json
            session.add(Node(
                code="main", title="Основная локация", flag="🌍",
                url=settings.marzban_url or "https://demo.node.local:8000",
                username=settings.marzban_username,
                password=settings.marzban_password, verify_ssl=settings.marzban_verify_ssl,
                inbounds_json=json.dumps(settings.marzban_inbounds, ensure_ascii=False),
                is_active=True, is_default=True, sort_order=0))

        owner = (await session.execute(
            select(User).where(User.tg_id == settings.owner_id))).scalar_one_or_none()
        if owner is None:
            session.add(User(tg_id=settings.owner_id, role=Role.owner, first_name="Owner",
                             sub_token=secrets.token_urlsafe(24)))
        elif owner.role != Role.owner:
            owner.role = Role.owner
        await session.commit()
