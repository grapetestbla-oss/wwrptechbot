# Развёртывание на основном ВПС

Порядок такой: сначала поднимаем бота и Mini App, потом (когда будет второй ВПС)
добавляем VPN-локацию. До появления локации оплата и пробный доступ намеренно
отключены — сервис честно показывает «идёт запуск», деньги ни с кого не берутся.

## Что нужно заранее

- ВПС с Ubuntu 22.04/24.04 или Debian 12, root-доступ, 1 ГБ RAM хватает.
- Домен, A-запись которого указывает на IP этого ВПС. Домена нет — скрипт
  подставит `<ваш-ип>.nip.io`, сертификат на него выпускается нормально.
  Telegram открывает Mini App только по HTTPS, поэтому сертификат обязателен.
- Токен бота от @BotFather.

## Установка

```bash
apt update && apt install -y git
git clone -b claude/telegram-vpn-mini-app-jn457e \
  https://github.com/grapetestbla-oss/wwrptechbot.git /opt/targetvpn-src
cd /opt/targetvpn-src/targetvpn
bash deploy/install.sh
```

**Важно про ветку:** код лежит в ветке `claude/telegram-vpn-mini-app-jn457e`,
в `main` его пока нет. Без флага `-b` склонируется пустой `main` и команда
`cd .../targetvpn` выдаст «No such file or directory». Если смёржите ветку в
`main` — флаг больше не нужен.

Скрипт спросит домен, токен бота и ваш Telegram ID, дальше сделает всё сам:
поставит зависимости, создаст пользователя `targetvpn` и `/opt/targetvpn`,
сгенерирует `.env` со случайными секретами, поднимет systemd-сервисы,
настроит nginx, откроет порты и выпустит сертификат Let's Encrypt.
В конце запустится проверка конфигурации.

Username бота определяется автоматически по токену — вводить не нужно.

## После установки: настройка в @BotFather

1. `/newapp` → выбрать бота → загрузить иконку/описание →
   **Web App URL**: `https://ваш-домен/app/`
2. `/mybots` → бот → **Bot Settings → Menu Button** → тот же URL.

Теперь `/start` в боте открывает Mini App. Админка видна сразу: ваш ID прописан
владельцем, права снять нельзя.

## Когда придёт ВПС под VPN

1. Поставьте на нём Marzban и инбаунд Reality по `deploy/NODE_SETUP.md`.
2. Откройте порт панели только для основного ВПС:
   `ufw allow from <IP основного ВПС> to any port 8000`.
3. В Mini App → **Админка → Локации → Новая локация**: название, флаг, адрес
   панели, логин и пароль. Отметьте «выдавать по умолчанию».
4. Сервис включится сам: появятся тарифы, пробный доступ и оплата.

Прописывать ноду в `.env` не нужно — там она только для самого первого запуска.

## Эксплуатация

```bash
systemctl status targetvpn-api targetvpn-bot     # состояние
journalctl -u targetvpn-bot -f                   # логи бота
journalctl -u targetvpn-api -f                   # логи API
/opt/targetvpn/.venv/bin/python /opt/targetvpn/scripts/check_config.py   # диагностика
```

Проверка показывает: жив ли токен, открывается ли Mini App снаружи, не остались
ли дефолтные секреты, отвечают ли локации, какие способы оплаты включены.

### Обновление

Обновляемся из того же клона, откуда ставили (`/opt/targetvpn-src`). Повторный
запуск установщика ничего не переспрашивает и не трогает `.env`, nginx и
сертификат — только обновляет код, зависимости и перезапускает сервисы:

```bash
cd /opt/targetvpn-src && git pull origin claude/telegram-vpn-mini-app-jn457e
cd targetvpn && bash deploy/install.sh
```

Клон потерялся — просто склонируйте заново той же командой из раздела
«Установка», `.env` в `/opt/targetvpn` останется нетронутым.

### Бэкап

Вся база — один файл `/opt/targetvpn/data/targetvpn.db`. Достаточно копировать
его вместе с `.env`:

```bash
tar czf ~/targetvpn-backup-$(date +%F).tar.gz -C /opt/targetvpn data .env
```

## Подключение оплаты

- **Telegram Stars** работают сразу, ничего подключать не нужно.
- **CryptoBot**: @CryptoBot → Crypto Pay → Create App → токен в `CRYPTOBOT_TOKEN`,
  вебхук на `https://ваш-домен/payments/cryptobot/webhook`.
- **LZT Market**: `LZT_TOKEN`, `LZT_USER_ID`, `LZT_USERNAME` в `.env`.

После правки `.env`: `systemctl restart targetvpn-api targetvpn-bot`.
В мини-аппе показываются только реально настроенные способы.

## Безопасность

- `.env` лежит с правами `600` у пользователя `targetvpn` — токен бота и пароли
  панели в репозиторий не попадают.
- Если токен где-то засветился — `/revoke` у @BotFather, новый токен в `.env`,
  перезапуск сервисов.
- Секреты `JWT_SECRET` и `INTERNAL_SECRET` генерируются при установке случайно;
  менять их вручную не нужно (после смены `JWT_SECRET` у всех разлогинится
  Mini App — это безопасно).
