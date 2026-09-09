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


def check_env_parsing() -> None:
    """Регрессия: pydantic-settings разбирал списки и словари из .env как JSON
    и падал на обычной строке вроде CORS_ORIGINS=https://site.tld."""
    import tempfile

    from app.config import Settings

    cases = [
        ("CORS_ORIGINS=https://a.tld\nMARZBAN_INBOUNDS={\"vless\": [\"X\"]}\n",
         ["https://a.tld"], {"vless": ["X"]}),
        ("CORS_ORIGINS=https://a.tld, https://b.tld\n"
         "MARZBAN_INBOUNDS='{\"vless\": [\"Y\"]}'\n",
         ["https://a.tld", "https://b.tld"], {"vless": ["Y"]}),
        ("CORS_ORIGINS=*\n", ["*"], {"vless": ["VLESS TCP REALITY"]}),
        # Ровно то, что пишет установщик: часть значений пустая.
        ("CORS_ORIGINS=https://a.tld\n"
         "MARZBAN_URL=\nMARZBAN_USERNAME=\nMARZBAN_PASSWORD=\n"
         "MARZBAN_INBOUNDS='{\"vless\": [\"VLESS TCP REALITY\"]}'\n"
         "CRYPTOBOT_TOKEN=\nLZT_TOKEN=\nLZT_USER_ID=\nLZT_USERNAME=\n"
         "RUB_PER_USDT=100\nTRIAL_ENABLED=true\nDEMO_MODE=false\n",
         ["https://a.tld"], {"vless": ["VLESS TCP REALITY"]}),
    ]
    for body, origins, inbounds in cases:
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as handle:
            handle.write("BOT_TOKEN=1:AA\n" + body)
            path = handle.name
        settings = Settings(_env_file=path)
        check(f"настройки читаются из .env ({body.splitlines()[0][:28]}…)",
              settings.cors_origins == origins and settings.marzban_inbounds == inbounds,
              f"{settings.cors_origins} / {settings.marzban_inbounds}")
        Path(path).unlink()


async def main() -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()

    check_env_parsing()

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
            r = await c.post("/api/admin/promos", headers=oauth, json={
                "code": "expired", "discount_percent": 50, "expires_in_days": 0})
            r = await c.get("/api/admin/promos", headers=oauth)
            check("промокоды перечисляются со сроком",
                  any(p["code"] == "TARGET25" and p["expires_at"] is None for p in r.json()),
                  r.text[:150])

            r = await c.post("/api/admin/promos", headers=oauth, json={
                "code": "week", "discount_percent": 10, "expires_in_days": 7})
            r = await c.get("/api/admin/promos", headers=oauth)
            week = [p for p in r.json() if p["code"] == "WEEK"][0]
            check("срок действия промокода сохраняется", week["expires_at"] is not None,
                  str(week))

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

            r = await c.post(f"/api/admin/nodes/{nl_id}/resync", headers=oauth)
            check("пересинхронизация локации",
                  r.status_code == 200 and "restored" in r.json(), r.text[:150])

            r = await c.post("/api/admin/nodes/999/resync", headers=oauth)
            check("пересинхронизация несуществующей локации отклонена", r.status_code == 404)

            r = await c.post(f"/api/admin/nodes/{nl_id}/resync", headers=auth)
            check("пересинхронизация закрыта от обычных юзеров", r.status_code == 403)

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

            # --- привязка приложения по HWID ---
            r = await c.post("/api/bind-code", headers=auth)
            check("код привязки выдан", r.status_code == 200 and len(r.json()["code"]) == 6,
                  r.text[:120])
            bind_code = r.json()["code"]

            r = await c.post("/api/client/bind", json={
                "code": bind_code, "hwid": "android-id-1234567890abcdef",
                "name": "Pixel", "model": "Pixel 8", "app_version": "1.0.0"})
            check("приложение привязалось",
                  r.status_code == 200 and r.json()["config"].startswith("vless://"), r.text[:150])
            client_token = r.json()["token"]
            client_device = r.json()["device_id"]
            capp = {"Authorization": f"Bearer {client_token}", "X-HWID": "android-id-1234567890abcdef"}

            r = await c.post("/api/client/bind", json={
                "code": bind_code, "hwid": "android-id-1234567890abcdef"})
            check("код одноразовый", r.status_code == 400, r.text[:100])

            r = await c.get("/api/client/state", headers=capp)
            check("клиент видит подписку", r.status_code == 200 and r.json()["active"], r.text[:120])

            r = await c.get("/api/client/config", headers={
                "Authorization": f"Bearer {client_token}", "X-HWID": "other-device-0000000000"})
            check("чужой HWID с тем же токеном отклонён", r.status_code == 401, r.text[:120])

            r = await c.get("/api/client/config", headers={
                "Authorization": "Bearer forged-token-0000", "X-HWID": "android-id-1234567890abcdef"})
            check("поддельный токен отклонён", r.status_code == 401)

            # Вторая локация, чтобы проверить именно переключение.
            await c.post("/api/admin/nodes", headers=oauth, json={
                "code": "fi", "title": "Финляндия", "flag": "🇫🇮",
                "url": "https://fi.demo.local:8000", "username": "admin", "password": "p"})
            r = await c.get("/api/client/regions", headers=capp)
            check("приложение видит список регионов",
                  r.status_code == 200 and any(x["is_current"] for x in r.json()), r.text[:150])
            other_region = [x for x in r.json() if not x["is_current"]]

            if other_region:
                target = other_region[0]
                r = await c.post("/api/client/region", headers=capp, json={"node_id": target["id"]})
                check("смена региона из приложения",
                      r.status_code == 200 and r.json()["location"] == target["title"], r.text[:150])
                r = await c.get("/api/client/state", headers=capp)
                check("новая локация в статусе", r.json()["location"] == target["title"])

            # Та же смена локации, но из мини-аппа.
            r = await c.get("/api/nodes", headers=auth)
            fi = [n for n in r.json() if n["code"] == "fi"][0]
            r = await c.get("/api/state", headers=auth)
            some_device = r.json()["devices"][0]["id"]
            r = await c.post(f"/api/devices/{some_device}/region", headers=auth,
                             json={"node_id": fi["id"]})
            check("смена локации из мини-аппа",
                  r.status_code == 200 and r.json()["node_title"] == "Финляндия", r.text[:150])

            r = await c.post("/api/client/region", headers=capp, json={"node_id": 999})
            check("несуществующий регион отклонён", r.status_code == 404)

            r = await c.get("/api/state", headers=auth)
            bound = [d for d in r.json()["devices"] if d["hwid_bound"]]
            check("мини-апп показывает привязку", len(bound) == 1 and "…" in bound[0]["hwid"],
                  str(bound)[:120])
            check("цена отвязки по умолчанию 50₽", r.json()["unbind_price_rub"] == 50.0)

            # --- платная отвязка ---
            _cfg.lzt_token, _cfg.lzt_user_id, _cfg.lzt_username = "test", 1, "targetvpn"
            r = await c.post("/api/unbind", headers=auth,
                             json={"device_id": client_device, "method": "lzt"})
            check("счёт на отвязку создан",
                  r.status_code == 200 and r.json()["amount_rub"] == 50.0, r.text[:150])
            unbind_payment = r.json()

            r = await c.get("/api/client/state", headers=capp)
            check("до оплаты привязка держится", r.status_code == 200)

            _billing.lzt_fetch_incoming = lambda limit=50: _as_coro(
                [{"data": {"comment": unbind_payment["comment"]},
                  "incoming_sum": unbind_payment["amount_native"]}])
            async with _Session() as s2:
                check("оплата отвязки проведена", await _billing.lzt_check_pending(s2) == 1)
            _cfg.lzt_token, _cfg.lzt_user_id = "", 0

            r = await c.get("/api/client/state", headers=capp)
            check("после оплаты старый токен мёртв", r.status_code == 401, r.text[:120])

            r = await c.post("/api/bind-code", headers=auth)
            r = await c.post("/api/client/bind", json={
                "code": r.json()["code"], "hwid": "new-phone-abcdef1234567890", "name": "iPhone"})
            check("новое устройство привязалось после оплаты", r.status_code == 200, r.text[:150])
            new_token = r.json()["token"]

            # --- админ: цена и бесплатная отвязка ---
            r = await c.post("/api/admin/settings", headers=oauth, json={"unbind_price_rub": 75})
            check("админ меняет цену отвязки", r.json()["unbind_price_rub"] == "75.0", r.text[:120])
            r = await c.get("/api/state", headers=auth)
            check("новая цена видна в мини-аппе", r.json()["unbind_price_rub"] == 75.0)
            await c.post("/api/admin/settings", headers=oauth, json={"unbind_price_rub": 50})

            r = await c.get(f"/api/admin/devices/555001", headers=oauth)
            check("админ видит привязки", any(d["hwid_bound"] for d in r.json()), r.text[:150])
            bound_id = [d["id"] for d in r.json() if d["hwid_bound"]][0]

            r = await c.post("/api/admin/unbind", headers=oauth, json={"device_id": bound_id})
            check("админ отвязывает бесплатно", r.status_code == 200)
            r = await c.get("/api/client/state", headers={
                "Authorization": f"Bearer {new_token}", "X-HWID": "new-phone-abcdef1234567890"})
            check("после админской отвязки токен мёртв", r.status_code == 401)

            r = await c.post("/api/admin/settings", headers=auth, json={"apk_url": "http://x"})
            check("настройки закрыты от обычных юзеров", r.status_code == 403)

            # --- оплата картой через Telegram Payments ---
            check("сумма в копейках", _billing.card_amount(149) == 14900,
                  str(_billing.card_amount(149)))
            check("тестовый токен распознаётся",
                  not _billing.card_enabled(), "без токена оплата картой выключена")

            r = await c.post("/api/purchase", headers=auth,
                             json={"plan_id": new_plan_id, "method": "card"})
            check("без токена провайдера оплата картой отклонена", r.status_code == 400,
                  r.text[:120])

            _cfg.payment_provider_token = "1877036958:TEST:xxxxxxxx"
            check("режим теста виден по метке", _billing.card_is_test())
            _billing.card_create_invoice_link = lambda *a, **kw: _as_coro(
                "https://t.me/invoice/test123")
            r = await c.get("/api/state", headers=auth)
            check("карта появилась в способах оплаты",
                  "card" in r.json()["payment_methods"], str(r.json()["payment_methods"]))

            r = await c.post("/api/purchase", headers=auth,
                             json={"plan_id": new_plan_id, "method": "card"})
            check("счёт картой создан",
                  r.status_code == 200 and r.json()["invoice_link"].startswith("https://t.me/"),
                  r.text[:150])
            card_payment = r.json()["payment_id"]

            # Бот подтверждает оплату так же, как для звёзд.
            r = await c.post("/internal/payments/stars",
                             headers={"X-Internal-Secret": "smoke-secret"},
                             json={"payment_id": card_payment, "charge_id": "test-charge"})
            check("подтверждение платежа картой активирует подписку", r.status_code == 200,
                  r.text[:120])
            r = await c.get(f"/api/payments/{card_payment}", headers=auth)
            check("платёж картой помечен оплаченным", r.json()["paid"])
            _cfg.payment_provider_token = ""

            # --- баланс: начисление админом и оплата с него ---
            r = await c.post("/api/admin/balance", headers=oauth,
                             json={"tg_id": 555001, "amount": 300, "reason": "бонус"})
            check("админ начисляет баланс", r.json()["balance_rub"] == 300.0, r.text[:120])

            r = await c.get("/api/state", headers=auth)
            check("баланс виден пользователю", r.json()["user"]["balance_rub"] == 300.0)
            check("способ оплаты с баланса появился",
                  "balance" in r.json()["payment_methods"], str(r.json()["payment_methods"]))

            r = await c.post("/api/purchase", headers=auth,
                             json={"plan_id": new_plan_id, "method": "balance"})
            check("оплата тарифа с баланса",
                  r.status_code == 200 and r.json()["activated"], r.text[:150])
            r = await c.get("/api/state", headers=auth)
            check("баланс уменьшился на цену тарифа",
                  r.json()["user"]["balance_rub"] == 221.0, str(r.json()["user"]["balance_rub"]))

            r = await c.post("/api/admin/balance", headers=oauth,
                             json={"tg_id": 555001, "amount": -1000})
            check("баланс не уходит в минус", r.json()["balance_rub"] == 0.0, r.text[:120])

            r = await c.post("/api/purchase", headers=auth,
                             json={"plan_id": new_plan_id, "method": "balance"})
            check("без денег оплата с баланса отклонена", r.status_code == 400, r.text[:120])

            r = await c.post("/api/admin/balance", headers=auth, json={"tg_id": 555001, "amount": 5})
            check("баланс меняет только админ", r.status_code == 403)

            # --- страница подключения ---
            r = await c.get("/api/state", headers=auth)
            connect_token = r.json()["sub_url"].rsplit("/", 1)[-1]
            r = await c.get(f"/connect/{connect_token}")
            check("страница подключения открывается", r.status_code == 200, r.text[:120])
            check("Happ получает сам ключ vless, а не выдуманную схему",
                  "vless://" in r.text and "happ://add" not in r.text, r.text[:200])
            check("для остальных клиентов подставлена подписка",
                  "v2rayng://install-config?url=" in r.text and "/sub/" in r.text)
            check("есть ссылки на установку Happ", "apps.apple.com" in r.text)
            r = await c.get("/connect/неизвестный-токен")
            check("чужой токен на странице подключения отклонён", r.status_code == 404)

            # --- кнопки скачивания под три платформы ---
            r = await c.get("/api/state", headers=auth)
            check("без ссылок кнопок скачивания нет", r.json()["downloads"] == [])

            r = await c.post("/api/admin/settings", headers=oauth, json={
                "apk_url": "https://cdn.example/targetvpn.apk", "apk_version": "1.0.0",
                "windows_url": "https://cdn.example/targetvpn.exe"})
            check("админ задаёт ссылки на сборки", r.status_code == 200)

            r = await c.get("/api/state", headers=auth)
            downloads = r.json()["downloads"]
            check("показываются только заполненные платформы",
                  [d["platform"] for d in downloads] == ["android", "windows"], str(downloads)[:160])
            check("версия попадает в кнопку",
                  downloads[0]["version"] == "1.0.0" and downloads[0]["emoji"] == "🤖")

            # Файл из каталога раздачи должен отдаваться самим сервером.
            from app.main import DOWNLOADS_DIR
            probe = DOWNLOADS_DIR / "probe.apk"
            probe.write_bytes(b"PK\x03\x04 build")
            r = await c.get("/downloads/probe.apk")
            check("сборки раздаются с сервера",
                  r.status_code == 200 and r.content.startswith(b"PK"), r.text[:80])
            probe.unlink()

            r = await c.get("/internal/downloads", headers={"X-Internal-Secret": "smoke-secret"})
            check("бот получает те же ссылки", len(r.json()) == 2, r.text[:120])

            r = await c.get("/api/client/version")
            builds = r.json()["builds"]
            check("приложение видит сборки по платформам",
                  builds["android"]["version"] == "1.0.0" and "windows" in builds, r.text[:160])

            await c.post("/api/admin/settings", headers=oauth,
                         json={"apk_url": "", "windows_url": ""})

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
