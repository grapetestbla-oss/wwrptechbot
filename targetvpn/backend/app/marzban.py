from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

import httpx

from .config import settings

log = logging.getLogger("marzban")


class MarzbanError(RuntimeError):
    pass


class MarzbanClient:
    """Тонкий асинхронный клиент панели Marzban (Xray/VLESS Reality на VPN-ВПС).

    Одно устройство пользователя = один аккаунт на ноде. Так лимит устройств
    реально соблюдается на стороне Xray, а не только в интерфейсе.
    """

    def __init__(self) -> None:
        self._token: str | None = None
        self._token_exp: float = 0.0
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return bool(settings.marzban_url) and not settings.demo_mode

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=settings.marzban_url.rstrip("/"),
                                 verify=settings.marzban_verify_ssl, timeout=20.0)

    async def _auth_header(self) -> dict[str, str]:
        async with self._lock:
            if self._token and time.time() < self._token_exp:
                return {"Authorization": f"Bearer {self._token}"}
            async with self._client() as client:
                resp = await client.post("/api/admin/token", data={
                    "username": settings.marzban_username,
                    "password": settings.marzban_password,
                })
            if resp.status_code != 200:
                raise MarzbanError(f"Не удалось авторизоваться в Marzban: {resp.text[:200]}")
            self._token = resp.json()["access_token"]
            self._token_exp = time.time() + 45 * 60
            return {"Authorization": f"Bearer {self._token}"}

    async def _request(self, method: str, path: str, **kwargs) -> Any:
        headers = await self._auth_header()
        async with self._client() as client:
            resp = await client.request(method, path, headers=headers, **kwargs)
        if resp.status_code == 401:
            self._token = None
            headers = await self._auth_header()
            async with self._client() as client:
                resp = await client.request(method, path, headers=headers, **kwargs)
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            raise MarzbanError(f"Marzban {method} {path} -> {resp.status_code}: {resp.text[:300]}")
        return resp.json() if resp.content else {}

    # --- Пользователи ноды ---

    async def get_user(self, username: str) -> dict | None:
        if not self.enabled:
            return _demo_user(username)
        return await self._request("GET", f"/api/user/{username}")

    async def create_user(self, username: str, expire_ts: int, traffic_gb: int = 0,
                          note: str = "") -> dict:
        if not self.enabled:
            return _demo_user(username)
        payload = {
            "username": username,
            "proxies": {p: {} for p in settings.marzban_inbounds},
            "inbounds": settings.marzban_inbounds,
            "expire": expire_ts,
            "data_limit": traffic_gb * 1024 ** 3 if traffic_gb else 0,
            "data_limit_reset_strategy": "no_reset",
            "status": "active",
            "note": note,
        }
        existing = await self._request("GET", f"/api/user/{username}")
        if existing:
            return await self.update_user(username, expire_ts=expire_ts, traffic_gb=traffic_gb,
                                          status="active")
        return await self._request("POST", "/api/user", json=payload)

    async def update_user(self, username: str, expire_ts: int | None = None,
                          traffic_gb: int | None = None, status: str | None = None) -> dict:
        if not self.enabled:
            return _demo_user(username)
        payload: dict[str, Any] = {}
        if expire_ts is not None:
            payload["expire"] = expire_ts
        if traffic_gb is not None:
            payload["data_limit"] = traffic_gb * 1024 ** 3 if traffic_gb else 0
        if status is not None:
            payload["status"] = status
        return await self._request("PUT", f"/api/user/{username}", json=payload)

    async def delete_user(self, username: str) -> None:
        if not self.enabled:
            return
        try:
            await self._request("DELETE", f"/api/user/{username}")
        except MarzbanError as exc:  # аккаунт уже мог быть удалён вручную
            log.warning("Не удалось удалить %s: %s", username, exc)

    async def reset_user_traffic(self, username: str) -> None:
        if not self.enabled:
            return
        await self._request("POST", f"/api/user/{username}/reset")

    async def system_stats(self) -> dict:
        if not self.enabled:
            return {"demo": True}
        return await self._request("GET", "/api/system") or {}


def _demo_user(username: str) -> dict:
    """Фейковый ответ для DEMO_MODE — чтобы разрабатывать без живой ноды."""
    uid = uuid.uuid5(uuid.NAMESPACE_DNS, username)
    link = (f"vless://{uid}@demo.targetvpn.node:443?type=tcp&security=reality"
            f"&sni=www.microsoft.com&fp=chrome&pbk=DEMOPUBLICKEY&sid=ab12#TargetVPN-{username}")
    return {
        "username": username,
        "status": "active",
        "used_traffic": 0,
        "data_limit": 0,
        "links": [link],
        "subscription_url": f"/sub/demo-{username}",
    }


marzban = MarzbanClient()
