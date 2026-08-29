"""Сквозная проверка бэкенда без живой ноды и Telegram (DEMO_MODE).

Запуск:  python scripts/smoke_test.py
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

BOT_TOKEN = "123456:TEST-TOKEN"
OWNER_ID = 7824168810
DB_PATH = ROOT / "smoke.db"

os.environ.update(
    BOT_TOKEN=BOT_TOKEN,
    DEMO_MODE="1",
    OWNER_ID=str(OWNER_ID),
    DATABASE_URL=f"sqlite+aiosqlite:///{DB_PATH}",
    INTERNAL_SECRET="smoke-secret",
    JWT_SECRET="smoke-jwt",
    PUBLIC_BASE_URL="http://localhost:8000",
    BOT_USERNAME="targetvpn_bot",
)

import httpx  # noqa: E402
from app.main import app  # noqa: E402


def make_init_data(tg_id: int, username: str) -> str:
    user = json.dumps({"id": tg_id, "first_name": "Test", "username": username,
                       "language_code": "ru"}, separators=(",", ":"))
    pairs = {"auth_date": str(int(time.time())), "query_id": "AAA", "user": user}
    check = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(pairs)


async def _as_coro(value):
    return value


def check(label: str, condition: bool, extra: str = "") -> None:
    mark = "✅" if condition else "❌"
    print(f"{mark} {label}" + (f" — {extra}" if extra else ""))
    if not condition:
        raise SystemExit(1)


async def main() -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()

    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            # --- обычный пользователь ---
            r = await c.post("/api/auth", json={"initData": make_init_data(555001, "user1")})
            check("auth пользователя", r.status_code == 200, r.text[:120])
            token = r.json()["token"]
            auth = {"Authorization": f"Bearer {token}"}

            r = await c.post("/api/auth", json={"initData": make_init_data(555001, "u") + "x"})
            check("подделанная initData отклонена", r.status_code == 401)

            r = await c.get("/api/state", headers=auth)
            state = r.json()
            check("state без подписки", state["subscription"] is None and state["trial_available"])

            r = await c.get("/api/plans", headers=auth)
            plans = r.json()
            check("тарифы отдаются", len(plans) >= 4, f"{len(plans)} шт.")
            check("цены не выше 200₽/мес",
                  all(p["price_rub"] <= 200 for p in plans if p["duration_hours"] <= 744))

            r = await c.post("/api/trial", headers=auth)
            trial = r.json()
            check("пробная подписка на 24ч / 3 устройства",
                  r.status_code == 200 and trial["devices"] == 3
                  and 23 * 3600 < trial["seconds_left"] <= 24 * 3600)

            r = await c.post("/api/trial", headers=auth)
            check("повторный триал запрещён", r.status_code == 400)

            devices = []
            for i in range(3):
                r = await c.post("/api/devices", headers=auth,
                                 json={"name": f"Device {i}", "platform": "android"})
                check(f"устройство {i + 1} создано", r.status_code == 200, r.text[:120])
                devices.append(r.json())
            check("ключ vless выдан", devices[0]["config_url"].startswith("vless://"))

            r = await c.post("/api/devices", headers=auth,
                             json={"name": "Лишнее", "platform": "ios"})
            check("лимит устройств соблюдается", r.status_code == 400, r.json().get("detail", ""))

            sub_token = state["sub_url"].rsplit("/", 1)[-1]
            r = await c.get(f"/sub/{sub_token}")
            check("ссылка-подписка работает", r.status_code == 200 and len(r.text) > 40)

            r = await c.post(f"/api/devices/{devices[0]['id']}/refresh", headers=auth)
            check("перевыпуск ключа", r.status_code == 200)
            r = await c.delete(f"/api/devices/{devices[2]['id']}", headers=auth)
            check("удаление устройства", r.status_code == 200)

            # --- владелец / админка ---
            r = await c.post("/api/auth", json={"initData": make_init_data(OWNER_ID, "owner")})
            owner_token = r.json()["token"]
            check("владелец получает роль owner", r.json()["user"]["role"] == "owner")
            oauth = {"Authorization": f"Bearer {owner_token}"}

            r = await c.get("/api/admin/stats", headers=oauth)
            check("статистика админки", r.status_code == 200, json.dumps(r.json())[:120])

            r = await c.get("/api/admin/stats", headers=auth)
            check("обычный юзер не пускается в админку", r.status_code == 403)

            r = await c.post("/api/admin/plans", headers=oauth, json={
                "code": "promo7", "title": "Неделя", "description": "7 дней, 2 устройства",
                "price_rub": 69, "duration_hours": 168, "devices": 2, "sort_order": 5})
            check("создание тарифа", r.status_code == 200, r.text[:150])
            new_plan_id = r.json()["id"]

            r = await c.post("/api/admin/plans", headers=oauth, json={
                "id": new_plan_id, "code": "promo7", "title": "Неделя", "price_rub": 79,
                "duration_hours": 168, "devices": 2})
            check("изменение цены тарифа", r.json()["price_rub"] == 79)

            r = await c.post("/api/admin/grant", headers=oauth,
                             json={"tg_id": 555001, "plan_id": new_plan_id})
            check("выдача подписки админом", r.status_code == 200, r.text[:150])

            r = await c.get("/api/admin/users?q=555001", headers=oauth)
            check("поиск пользователя", r.json() and r.json()[0]["tg_id"] == 555001)

            r = await c.post("/api/admin/ban", headers=oauth,
                             json={"tg_id": 555001, "banned": True, "reason": "тест"})
            check("блокировка пользователя", r.status_code == 200)
            r = await c.get("/api/state", headers=auth)
            check("забаненный не имеет доступа к API", r.status_code == 403)
            r = await c.get(f"/sub/{sub_token}")
            check("подписка забаненного отключена", r.status_code == 404)

            r = await c.post("/api/admin/ban", headers=oauth,
                             json={"tg_id": 555001, "banned": False})
            check("разблокировка", r.status_code == 200)
            r = await c.get("/api/state", headers=auth)
            check("доступ вернулся", r.status_code == 200)

            r = await c.post("/api/admin/ban", headers=oauth,
                             json={"tg_id": OWNER_ID, "banned": True})
            check("владельца заблокировать нельзя", r.status_code == 400)

            r = await c.post("/api/admin/promos", headers=oauth,
                             json={"code": "target25", "discount_percent": 25, "bonus_days": 3})
            check("создание промокода", r.status_code == 200)
            r = await c.post("/api/promo/check", headers=auth,
                             json={"code": "TARGET25", "plan_id": new_plan_id})
            check("промокод даёт скидку", abs(r.json()["price_rub"] - 59.25) < 0.01, r.text)

            r = await c.post("/api/admin/role", headers=auth, json={"tg_id": 555002, "role": "admin"})
            check("роли меняет только владелец", r.status_code == 403)

            # --- мультинода ---
            r = await c.get("/api/admin/nodes", headers=oauth)
            check("нода по умолчанию создана из .env", len(r.json()) == 1 and r.json()[0]["is_default"])

            r = await c.post("/api/admin/nodes", headers=oauth, json={
                "code": "nl", "title": "Нидерланды", "flag": "🇳🇱", "country": "Netherlands",
                "url": "https://nl.example.com:8000", "username": "admin", "password": "pass",
                "is_active": True, "is_default": True, "sort_order": 10})
            check("создание локации", r.status_code == 200, r.text[:150])
            nl_id = r.json()["id"]

            r = await c.get("/api/admin/nodes", headers=oauth)
            defaults = [n for n in r.json() if n["is_default"]]
            check("нода по умолчанию только одна", len(defaults) == 1 and defaults[0]["id"] == nl_id)

            r = await c.post("/api/admin/nodes", headers=oauth, json={
                "code": "bad", "title": "Кривая", "url": "https://x", "username": "a",
                "inbounds_json": "{не json}"})
            check("некорректный JSON инбаундов отклонён", r.status_code == 400)

            r = await c.get("/api/nodes", headers=auth)
            check("пользователь видит список локаций", len(r.json()) == 2, r.text[:120])

            # Расширяем лимит, чтобы проверить выдачу во второй локации.
            await c.post("/api/admin/grant", headers=oauth,
                         json={"tg_id": 555001, "hours": 48, "devices": 5,
                               "title": "Тестовый расширенный"})
            r = await c.post("/api/devices", headers=auth,
                             json={"name": "NL key", "platform": "windows", "node_id": nl_id})
            check("устройство создаётся в выбранной локации",
                  r.status_code == 200 and r.json()["node_title"] == "Нидерланды", r.text[:150])
            check("ключ ведёт на хост выбранной ноды", "nl.example.com" in r.json()["config_url"])
            nl_device = r.json()["id"]

            r = await c.post("/api/devices", headers=auth,
                             json={"name": "Ghost", "platform": "ios", "node_id": 999})
            check("несуществующая локация отклонена", r.status_code == 400)

            r = await c.delete(f"/api/admin/nodes/{nl_id}", headers=oauth)
            check("отключение локации", r.status_code == 200)
            r = await c.get("/api/nodes", headers=auth)
            check("отключённая локация не предлагается", len(r.json()) == 1)
            r = await c.get("/api/state", headers=auth)
            check("ключи на отключённой локации живы",
                  any(d["id"] == nl_device for d in r.json()["devices"]))

            # --- LZT Market ---
            from app.services import billing as _billing
            check("LZT выключен без токена", not _billing.lzt_enabled())
            check("комментарий перевода уникален", _billing.lzt_comment(42) == "TVPN42")
            comment, amount = _billing._lzt_extract(
                {"data": {"comment": "TVPN42"}, "incoming_sum": "149.00"})
            check("разбор входящего перевода", comment == "TVPN42" and amount == 149.0)
            r = await c.post("/api/purchase", headers=auth,
                             json={"plan_id": new_plan_id, "method": "lzt"})
            check("оплата LZT недоступна без настройки", r.status_code == 400)

            # Полный цикл LZT: счёт -> входящий перевод -> активация подписки.
            from app.config import settings as _cfg
            _cfg.lzt_token, _cfg.lzt_user_id, _cfg.lzt_username = "test", 1, "targetvpn"
            r = await c.post("/api/purchase", headers=auth,
                             json={"plan_id": new_plan_id, "method": "lzt"})
            check("счёт LZT создан", r.status_code == 200 and r.json()["comment"].startswith("TVPN"),
                  r.text[:150])
            lzt_payment = r.json()

            from app.db import SessionLocal as _Session
            transfers = [{"data": {"comment": lzt_payment["comment"]},
                          "incoming_sum": lzt_payment["amount_native"]}]
            _billing.lzt_fetch_incoming = lambda limit=50: _as_coro(transfers)
            async with _Session() as s2:
                activated = await _billing.lzt_check_pending(s2)
            check("перевод найден и подписка активирована", activated == 1, f"{activated}")

            r = await c.get(f"/api/payments/{lzt_payment['payment_id']}", headers=auth)
            check("платёж LZT помечен оплаченным", r.json()["paid"])

            async with _Session() as s2:
                check("повторный перевод не задваивает активацию",
                      await _billing.lzt_check_pending(s2) == 0)

            r = await c.post("/api/purchase", headers=auth,
                             json={"plan_id": new_plan_id, "method": "lzt"})
            short = r.json()
            _billing.lzt_fetch_incoming = lambda limit=50: _as_coro(
                [{"data": {"comment": short["comment"]}, "incoming_sum": 1}])
            async with _Session() as s2:
                check("недоплата не активирует подписку",
                      await _billing.lzt_check_pending(s2) == 0)
            _cfg.lzt_token, _cfg.lzt_user_id = "", 0

            # --- запуск без ноды: ничего не продаём ---
            r = await c.get("/api/admin/nodes", headers=oauth)
            for node in r.json():
                if node["is_active"]:
                    await c.delete(f"/api/admin/nodes/{node['id']}", headers=oauth)
            r = await c.get("/api/state", headers=auth)
            check("без локаций сервис помечен как незапущенный",
                  r.json()["nodes_ready"] is False and r.json()["trial_available"] is False)
            r = await c.post("/api/purchase", headers=auth,
                             json={"plan_id": new_plan_id, "method": "stars"})
            check("без локаций оплата заблокирована", r.status_code == 400, r.text[:100])
            # Свободный слот есть — значит откажет именно отсутствие локаций.
            await c.post("/api/admin/grant", headers=oauth,
                         json={"tg_id": 555001, "hours": 48, "devices": 9, "title": "Тест слотов"})
            r = await c.post("/api/devices", headers=auth, json={"name": "X", "platform": "ios"})
            check("без локаций устройство не создаётся",
                  r.status_code == 400 and "окаци" in r.json()["detail"], r.text[:120])
            r = await c.post("/api/admin/nodes", headers=oauth, json={
                "code": "main", "id": 1, "title": "Основная локация",
                "url": "https://demo.node.local:8000", "username": "admin", "password": "p",
                "is_active": True, "is_default": True})
            check("локация возвращена в строй", r.status_code == 200)
            r = await c.get("/api/state", headers=auth)
            check("сервис снова готов", r.json()["nodes_ready"] is True)

            r = await c.get("/api/state", headers=auth)
            check("список способов оплаты отдаётся",
                  r.json()["payment_methods"] == ["stars"], r.json()["payment_methods"])

            r = await c.post("/api/admin/broadcast", headers=oauth, json={"text": "Привет"})
            check("рассылка ставится в очередь", r.json()["queued"] >= 2)

            r = await c.get("/internal/notifications",
                            headers={"X-Internal-Secret": "smoke-secret"})
            check("бот забирает уведомления", r.status_code == 200 and len(r.json()) >= 2)
            r = await c.get("/internal/notifications", headers={"X-Internal-Secret": "wrong"})
            check("внутренний ключ проверяется", r.status_code == 403)

            r = await c.post("/internal/tick", headers={"X-Internal-Secret": "smoke-secret"})
            check("фоновая проверка сроков", r.status_code == 200)

    DB_PATH.unlink(missing_ok=True)
    print("\nВсе проверки пройдены.")


if __name__ == "__main__":
    asyncio.run(main())
