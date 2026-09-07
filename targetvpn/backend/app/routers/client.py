"""API мобильного клиента TargetVPN (Android APK).

Аутентификация: токен, выданный при привязке, плюс заголовок X-HWID.
Токен без совпадающего HWID недействителен — перенести подписку копированием
файлов приложения нельзя, нужна платная отвязка.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import utcnow
from ..services import hwid as hwid_service
from ..services import subs
from ..services.settings_store import get as get_setting

router = APIRouter(prefix="/api/client", tags=["client"])


class BindRequest(BaseModel):
    code: str
    hwid: str
    name: str = "Телефон"
    model: str = ""
    app_version: str = ""


class BindResponse(BaseModel):
    token: str
    device_id: int
    device_name: str
    config: str
    location: str = ""
    expires_at: str
    seconds_left: int


class ClientState(BaseModel):
    active: bool
    device_name: str
    location: str = ""
    plan_title: str = ""
    expires_at: str = ""
    seconds_left: int = 0
    config: str = ""
    message: str = ""


async def _auth(authorization: str, x_hwid: str, session: AsyncSession):
    token = authorization[7:] if authorization.startswith("Bearer ") else ""
    try:
        return await hwid_service.device_by_token(session, token, x_hwid)
    except hwid_service.HwidError as exc:
        raise HTTPException(401, str(exc))


@router.post("/bind", response_model=BindResponse)
async def bind(payload: BindRequest, session: AsyncSession = Depends(get_session)):
    try:
        device, user = await hwid_service.bind_device(
            session, payload.code, payload.hwid, payload.name, payload.model,
            payload.app_version)
    except hwid_service.HwidError as exc:
        raise HTTPException(400, str(exc))

    sub = await subs.active_subscription(session, user)
    if sub is None:
        raise HTTPException(400, "Подписка неактивна")
    expires = subs.aware(sub.expires_at)
    await subs.notify(session, user.tg_id,
                      f"📱 Приложение привязано к устройству «{device.name}». "
                      "Если понадобится сменить телефон — отвяжите HWID в мини-аппе.")
    await session.commit()
    return BindResponse(
        token=device.client_token, device_id=device.id, device_name=device.name,
        config=device.config_url, location=await hwid_service.node_title(session, device),
        expires_at=expires.isoformat(),
        seconds_left=max(0, int((expires - utcnow()).total_seconds())))


@router.get("/state", response_model=ClientState)
async def state(authorization: str = Header(default=""), x_hwid: str = Header(default=""),
                session: AsyncSession = Depends(get_session)):
    device, user = await _auth(authorization, x_hwid, session)
    sub = await subs.active_subscription(session, user)
    if sub is None:
        return ClientState(active=False, device_name=device.name,
                           message="Подписка закончилась. Продлите её в Telegram-боте.")
    expires = subs.aware(sub.expires_at)
    return ClientState(
        active=device.is_active, device_name=device.name,
        location=await hwid_service.node_title(session, device),
        plan_title=sub.plan_title, expires_at=expires.isoformat(),
        seconds_left=max(0, int((expires - utcnow()).total_seconds())),
        config=device.config_url if device.is_active else "",
        message="" if device.is_active else "Устройство отключено, обратитесь в поддержку.")


@router.get("/config")
async def config(authorization: str = Header(default=""), x_hwid: str = Header(default=""),
                 session: AsyncSession = Depends(get_session)):
    """Актуальный ключ подключения. Приложение запрашивает его перед стартом VPN."""
    device, user = await _auth(authorization, x_hwid, session)
    sub = await subs.active_subscription(session, user)
    if sub is None or not device.is_active:
        raise HTTPException(403, "Подписка неактивна")
    return {"config": device.config_url,
            "location": await hwid_service.node_title(session, device),
            "expires_at": subs.aware(sub.expires_at).isoformat()}


@router.get("/version")
async def version(session: AsyncSession = Depends(get_session)):
    """Приложение проверяет, не вышла ли новая версия."""
    return {"version": await get_setting(session, "apk_version"),
            "url": await get_setting(session, "apk_url")}
