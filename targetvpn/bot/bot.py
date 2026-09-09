from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import httpx
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (InlineKeyboardButton, InlineKeyboardMarkup,
                           LabeledPrice, Message, PreCheckoutQuery, WebAppInfo)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.config import settings  # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bot")

router = Router()

# Системы в порядке популярности; iOS обслуживается через Happ, остальные — клиентом.
OS_CHOICES = [
    ("android", "🤖", "Android"),
    ("ios", "🍏", "iPhone / iPad"),
    ("windows", "🪟", "Windows"),
    ("linux", "🐧", "Linux"),
    ("macos", "💻", "macOS"),
]

HAPP_LINKS = [
    ("App Store", "https://apps.apple.com/app/happ-proxy-utility/id6504287215"),
    ("Сайт Happ", "https://www.happ.su/"),
]

INSTALL_HINTS = {
    "android": "Скачайте .apk и разрешите установку из этого источника.",
    "windows": "Запустите .exe — при первом подключении Windows спросит разрешение на VPN.",
    "linux": "Сделайте файл исполняемым: <code>chmod +x TargetVPN.AppImage</code>.",
    "macos": "Откройте .dmg и перетащите TargetVPN в «Программы».",
}

WELCOME = (
    "<b>TargetVPN</b> — быстрый доступ в интернет без блокировок.\n\n"
    "• Протокол VLESS Reality — трафик неотличим от обычного HTTPS\n"
    "• Работает там, где режут VPN и включают белые списки\n"
    "• До 5 устройств на одной подписке\n"
    "• Пробный доступ: <b>24 часа, 3 устройства, бесплатно</b>\n\n"
    "Открывайте приложение — там тарифы, ключи и статус подписки."
)


def main_kb() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="🚀 Открыть TargetVPN",
                                  web_app=WebAppInfo(url=settings.webapp_url))],
            [InlineKeyboardButton(text="📥 Скачать / подключиться",
                                  callback_data="downloads")]]
    if settings.support_url:
        rows.append([InlineKeyboardButton(text="💬 Поддержка", url=settings.support_url)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def internal(method: str, path: str, **kwargs):
    async with httpx.AsyncClient(base_url=settings.api_base_url, timeout=20.0) as client:
        resp = await client.request(method, path,
                                    headers={"X-Internal-Secret": settings.internal_secret},
                                    **kwargs)
    resp.raise_for_status()
    return resp.json() if resp.content else None


@router.message(CommandStart())
async def start(message: Message, command: CommandObject):
    # Реферальная ссылка вида ?start=ref123456 разбирается на бэкенде из initData.
    await message.answer(WELCOME, reply_markup=main_kb())


@router.message(Command("app", "vpn", "menu"))
async def open_app(message: Message):
    await message.answer("Приложение TargetVPN:", reply_markup=main_kb())


@router.message(Command("download", "install"))
async def download(message: Message):
    """Первый шаг: выбор системы — от неё зависит и клиент, и способ подключения."""
    rows = [[InlineKeyboardButton(text=f"{emoji} {title}", callback_data=f"dl:{os_id}")]
            for os_id, emoji, title in OS_CHOICES]
    rows.append([InlineKeyboardButton(text="🚀 Открыть TargetVPN",
                                      web_app=WebAppInfo(url=settings.webapp_url))])
    await message.answer(
        "<b>Какая у вас система?</b>\n\n"
        "На Android, Windows и Linux ставится наш клиент TargetVPN, "
        "на iPhone подключаемся через Happ — Apple не пропускает наш клиент в App Store.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


async def send_ios(message: Message) -> None:
    rows = [[InlineKeyboardButton(text=f"⬇️ Установить Happ · {title}", url=url)]
            for title, url in HAPP_LINKS]
    rows.append([InlineKeyboardButton(text="🚀 Подключить в приложении",
                                      web_app=WebAppInfo(url=settings.webapp_url))])
    await message.answer(
        "<b>🍏 iPhone и iPad</b>\n\n"
        "Apple не пропускает наш клиент в App Store, поэтому подключаемся через "
        "<b>Happ</b> — бесплатное приложение.\n\n"
        "1. Установите Happ по кнопке ниже\n"
        "2. Откройте TargetVPN и нажмите «Подключить через Happ» — ключ добавится сам",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


async def send_build(message: Message, os_id: str) -> None:
    try:
        builds = await internal("GET", "/internal/downloads") or []
    except Exception:  # noqa: BLE001 - бэкенд мог перезапускаться
        log.exception("Не удалось получить ссылки на сборки")
        builds = []

    build = next((b for b in builds if b["platform"] == os_id), None)
    title = dict((o[0], o[2]) for o in OS_CHOICES).get(os_id, os_id)
    if build is None:
        return await message.answer(
            f"Сборка для {title} ещё не выложена.\n"
            "Пока подключайтесь по ключу из мини-аппа — он работает "
            "в Happ, v2rayNG и Hiddify.",
            reply_markup=main_kb())

    rows = [[InlineKeyboardButton(
        text=f"{build['emoji']} Скачать TargetVPN"
             + (f" · {build['version']}" if build.get("version") else ""),
        url=build["url"])],
        [InlineKeyboardButton(text="🚀 Открыть TargetVPN",
                              web_app=WebAppInfo(url=settings.webapp_url))]]
    await message.answer(
        f"<b>{build['emoji']} {title}</b>\n\n"
        f"{INSTALL_HINTS.get(os_id, '')}\n\n"
        "После установки откройте мини-приложение, скопируйте ссылку-подписку "
        "и вставьте её в клиент — дальше только кнопка подключения.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.message(Command("help"))
async def help_cmd(message: Message):
    await message.answer(
        "Всё управление — в мини-приложении: тарифы, оплата, ключи, устройства.\n"
        "Если ключ перестал работать — нажмите «Перевыпустить» у устройства.\n"
        "Скачать клиент под свою систему: /download\n"
        f"Поддержка: {settings.support_url or 'скоро'}",
        reply_markup=main_kb())


@router.message(Command("id"))
async def whoami(message: Message):
    await message.answer(f"Ваш Telegram ID: <code>{message.from_user.id}</code>")


@router.message(Command("admin"))
async def admin_cmd(message: Message):
    if message.from_user.id != settings.owner_id:
        # Полная проверка прав — на бэкенде; здесь только быстрый ответ владельцу.
        return await message.answer("Раздел доступен администраторам в приложении.")
    await message.answer(
        "Админ-панель находится в мини-приложении — вкладка «Админка».\n"
        "Там: тарифы и цены, выдача подписок, блокировки, промокоды, статистика.",
        reply_markup=main_kb())


@router.callback_query(F.data == "downloads")
async def downloads_callback(query):
    await query.answer()
    await download(query.message)


@router.callback_query(F.data.startswith("dl:"))
async def download_os(query):
    await query.answer()
    os_id = query.data.split(":", 1)[1]
    if os_id == "ios":
        return await send_ios(query.message)
    await send_build(query.message, os_id)


# --- Оплата звёздами ---

@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def successful_payment(message: Message):
    payload = message.successful_payment.invoice_payload or ""
    if not payload.startswith("tvpn:"):
        return
    payment_id = payload.split(":", 1)[1]
    try:
        await internal("POST", "/internal/payments/stars", json={
            "payment_id": int(payment_id),
            "charge_id": message.successful_payment.telegram_payment_charge_id,
        })
    except Exception:  # noqa: BLE001
        log.exception("Не удалось подтвердить платёж %s", payment_id)
        await message.answer("Оплата прошла, но активация задержалась. "
                             "Мы уже разбираемся — напишите в поддержку, если доступ не появится.")
        return
    await message.answer("✅ Оплата получена, подписка активирована. Открывайте приложение!",
                         reply_markup=main_kb())


async def notifications_worker(bot: Bot) -> None:
    """Разбирает очередь уведомлений бэкенда и доставляет их пользователям."""
    while True:
        try:
            items = await internal("GET", "/internal/notifications", params={"limit": 50}) or []
            for item in items:
                try:
                    await bot.send_message(item["tg_id"], item["text"], reply_markup=main_kb())
                except Exception as exc:  # noqa: BLE001 - юзер мог заблокировать бота
                    log.warning("Не доставлено %s: %s", item["tg_id"], exc)
                await asyncio.sleep(0.05)
        except Exception:  # noqa: BLE001
            log.exception("Ошибка очереди уведомлений")
        await asyncio.sleep(10)


async def main() -> None:
    if not settings.bot_token:
        raise SystemExit("BOT_TOKEN не задан в .env")
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    asyncio.create_task(notifications_worker(bot))
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
