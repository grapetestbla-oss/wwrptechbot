from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .db import SessionLocal, init_db
from .routers import admin, api, payments
from .services.billing import lzt_check_pending, lzt_enabled
from .services.subs import expire_pass

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("targetvpn")

MINIAPP_DIR = Path(__file__).resolve().parents[2] / "miniapp"


async def expiry_worker() -> None:
    """Каждые 5 минут гасит просроченные подписки и ставит напоминания в очередь."""
    while True:
        try:
            async with SessionLocal() as session:
                changed = await expire_pass(session)
                if changed:
                    log.info("Обработано подписок по сроку: %s", changed)
        except Exception:  # noqa: BLE001 - воркер не должен умирать
            log.exception("Ошибка фоновой проверки подписок")
        await asyncio.sleep(300)


async def lzt_worker() -> None:
    """Опрашивает входящие переводы LZT Market и активирует оплаченные подписки."""
    while True:
        await asyncio.sleep(settings.lzt_poll_interval)
        try:
            async with SessionLocal() as session:
                activated = await lzt_check_pending(session)
                if activated:
                    log.info("LZT Market: активировано подписок: %s", activated)
        except Exception:  # noqa: BLE001 - воркер не должен умирать
            log.exception("Ошибка опроса LZT Market")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    tasks = [asyncio.create_task(expiry_worker())]
    if lzt_enabled():
        tasks.append(asyncio.create_task(lzt_worker()))
    log.info("TargetVPN backend запущен (demo_mode=%s)", settings.demo_mode)
    yield
    for task in tasks:
        task.cancel()


app = FastAPI(title="TargetVPN API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api.router)
app.include_router(admin.router)
app.include_router(payments.router)

if MINIAPP_DIR.exists():
    app.mount("/app", StaticFiles(directory=MINIAPP_DIR, html=True), name="miniapp")


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/")
async def root():
    index = MINIAPP_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    return {"service": "TargetVPN", "docs": "/docs"}
