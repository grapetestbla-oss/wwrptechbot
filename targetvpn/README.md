# TargetVPN — VPN-сервис в Telegram Mini App

Подписочный VPN на Xray **VLESS + Reality**: витрина и оплата живут в Telegram
Mini App, ключи автоматически выдаются на второй ВПС через API Marzban.

```
Пользователь ──▶ Telegram-бот ──▶ Mini App (витрина, оплата, ключи, админка)
                                    │
                          FastAPI-бэкенд (основной ВПС)
                                    │  Marzban REST API
                          VPN-нода (второй ВПС): Xray, VLESS Reality
```

## Что уже работает

- **Подписки.** Пробная — 24 часа / 3 устройства / бесплатно, один раз на аккаунт.
  Платные (цены редактируются): Старт 99 ₽ · 1 устройство, Стандарт 149 ₽ · 3,
  Максимум 199 ₽ · 5, Стандарт 3 мес. 399 ₽.
- **Лимит устройств соблюдается физически:** каждое устройство — отдельный аккаунт
  на ноде. Кончилась подписка или прилетел бан — аккаунты уходят в `disabled`.
- **Оплата:** Telegram Stars и CryptoBot (USDT/TON), промокоды со скидкой и бонусными
  днями, рефералка (+7 дней за первую оплату приглашённого).
- **Админка внутри Mini App** (владелец `7824168810`, права несъёмные): статистика и
  выручка, редактор тарифов и цен, поиск пользователей, выдача и отзыв подписок,
  баны с причиной, назначение админов, промокоды, рассылка, журнал действий.
- **Ключи:** vless-ссылка на каждое устройство + общая ссылка-подписка
  (`/sub/<token>`) для v2rayNG, Hiddify, Streisand, V2Box — клиент сам обновляет
  конфиги. Это же основа для будущего фирменного клиента TargetVPN.
- **Фон:** каждые 5 минут гасятся просроченные подписки, за 24 часа до конца
  пользователю уходит напоминание.

## Структура

```
backend/            FastAPI: API мини-аппа, админка, вебхуки, ссылка-подписка
  app/routers/      api.py (витрина) · admin.py (админка) · payments.py (оплата, /sub)
  app/services/     subs.py (подписки и устройства) · billing.py (платежи)
  app/marzban.py    клиент панели на VPN-ноде
bot/bot.py          aiogram-бот: вход в Mini App, оплата звёздами, уведомления
miniapp/            Mini App без сборки: index.html · styles.css · app.js · admin.js
deploy/             systemd, nginx, docker-compose, NODE_SETUP.md
scripts/smoke_test.py  сквозная проверка всех сценариев без ноды и Telegram
```

## Запуск

### 1. VPN-нода (второй ВПС)
Следуйте `deploy/NODE_SETUP.md` — Marzban, инбаунд Reality, файрвол.

### 2. Основной ВПС

```bash
git clone <repo> /opt/targetvpn && cd /opt/targetvpn/targetvpn
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt -r bot/requirements.txt
cp .env.example .env && nano .env          # токены, доступы к ноде, домен
.venv/bin/uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
.venv/bin/python bot/bot.py                # во втором терминале
```

Продакшн: `deploy/nginx.conf` + `certbot`, затем

```bash
cp deploy/targetvpn-*.service /etc/systemd/system/
systemctl enable --now targetvpn-api targetvpn-bot
```

Или через Docker: `docker compose -f deploy/docker-compose.yml up -d --build`.

### 3. Настройка бота в @BotFather

1. `/newapp` → выберите бота → URL мини-аппа `https://ваш-домен/app/`.
2. `/mybots` → Bot Settings → Menu Button → тот же URL.
3. Для Stars ничего подключать не нужно; для крипты — создайте приложение в
   @CryptoBot (Crypto Pay → Create App), токен в `CRYPTOBOT_TOKEN`, вебхук на
   `https://ваш-домен/payments/cryptobot/webhook`.

### 4. Проверка

```bash
.venv/bin/python scripts/smoke_test.py     # 30+ проверок в демо-режиме, без ноды
```

`DEMO_MODE=true` позволяет крутить весь сервис локально: ключи генерируются
фейковые, нода не нужна.

## Безопасность

- Вход в Mini App — по подписи `initData` (HMAC от токена бота), окно 24 часа;
  дальше короткоживущий JWT.
- Админские ручки проверяют роль на сервере, а не по флагу из фронта; смена ролей
  доступна только владельцу, его самого нельзя разжаловать или забанить.
- Бот ходит в бэкенд по `X-Internal-Secret`; вебхук CryptoBot проверяется по HMAC.
- Ссылка-подписка выдаётся по случайному токену и мгновенно перестаёт работать
  при бане или истечении срока.

## Что дальше

- **LZT Market** — платежи через их API: добавляется провайдером в
  `backend/app/services/billing.py` рядом с `cryptobot_*` (провайдер `lzt` в модели
  `Payment` уже предусмотрен) плюс кнопка в `openPayment()` мини-аппа.
- **Клиент TargetVPN** — бэкенд уже отдаёт стандартную ссылку-подписку с заголовками
  `profile-title` и `subscription-userinfo`, поэтому фирменный клиент (свой или форк
  Hiddify) подключается к готовому API без изменений на сервере.
- Несколько нод и выбор локации: `Device.remote_username` уже уникален, достаточно
  добавить таблицу нод и выбирать клиент Marzban по стране.
