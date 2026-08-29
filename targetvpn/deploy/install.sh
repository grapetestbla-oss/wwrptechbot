#!/usr/bin/env bash
# Установка TargetVPN на основной ВПС (Ubuntu 22.04/24.04, Debian 12).
# Запуск от root:  bash deploy/install.sh
set -euo pipefail

APP_DIR=/opt/targetvpn
APP_USER=targetvpn
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

say()  { printf "\n\033[1;36m==> %s\033[0m\n" "$*"; }
warn() { printf "\033[1;33m!! %s\033[0m\n" "$*"; }
die()  { printf "\033[1;31mОшибка: %s\033[0m\n" "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "запустите от root (sudo bash deploy/install.sh)"

# --- 1. Ввод параметров ---------------------------------------------------
# Повторный запуск (обновление) не переспрашивает — берём всё из готового .env.
REUSE=0
if [[ -f "$APP_DIR/.env" ]]; then
  REUSE=1
  DOMAIN=$(sed -n 's#^PUBLIC_BASE_URL=https\?://##p' "$APP_DIR/.env" | tr -d '\r')
  say "Найден $APP_DIR/.env — обновляем установку ($DOMAIN), настройки не трогаем"
fi

if [[ $REUSE -eq 0 ]]; then
read -rp "Домен для Mini App (например vpn.example.com; пусто — использовать IP через nip.io): " DOMAIN
read -rp "Токен бота от @BotFather: " BOT_TOKEN
BOT_USERNAME=$(curl -fsS "https://api.telegram.org/bot${BOT_TOKEN}/getMe" 2>/dev/null \
  | sed -n 's/.*"username":"\([^"]*\)".*/\1/p' || true)
if [[ -n "$BOT_USERNAME" ]]; then
  echo "Бот определён: @${BOT_USERNAME}"
else
  read -rp "Username бота без @ (Telegram не ответил, введите вручную): " BOT_USERNAME
fi
read -rp "Ваш Telegram ID (владелец сервиса) [7824168810]: " OWNER_ID
read -rp "Ссылка на поддержку [https://t.me/${BOT_USERNAME}]: " SUPPORT_URL
read -rp "Выпустить TLS-сертификат Let's Encrypt? [Y/n]: " WANT_TLS

OWNER_ID=${OWNER_ID:-7824168810}
SUPPORT_URL=${SUPPORT_URL:-https://t.me/${BOT_USERNAME}}
WANT_TLS=${WANT_TLS:-Y}
[[ -n "$BOT_TOKEN" ]] || die "токен бота обязателен"

if [[ -z "$DOMAIN" ]]; then
  PUBLIC_IP=$(curl -fsS https://api.ipify.org || hostname -I | awk '{print $1}')
  DOMAIN="${PUBLIC_IP//./-}.nip.io"
  warn "Домен не указан, используем $DOMAIN (nip.io резолвится в ваш IP)"
fi
fi  # конец блока первичной настройки

# --- 2. Пакеты ------------------------------------------------------------
say "Ставим системные пакеты"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -yqq python3 python3-venv python3-pip nginx curl git ufw >/dev/null

# --- 3. Пользователь и файлы ---------------------------------------------
say "Готовим $APP_DIR"
id -u "$APP_USER" &>/dev/null || useradd --system --create-home --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
mkdir -p "$APP_DIR"
if [[ "$REPO_DIR" != "$APP_DIR" ]]; then
  cp -r "$REPO_DIR"/. "$APP_DIR"/
fi
cd "$APP_DIR"

say "Виртуальное окружение и зависимости"
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r backend/requirements.txt -r bot/requirements.txt

# --- 4. Конфигурация ------------------------------------------------------
if [[ $REUSE -eq 1 ]]; then
  say "Конфигурация сохранена без изменений (бэкап .env.bak)"
  cp .env .env.bak
else
  say "Генерируем .env"
  JWT_SECRET=$(openssl rand -hex 32)
  INTERNAL_SECRET=$(openssl rand -hex 32)
  cat > .env <<EOF
BOT_TOKEN=${BOT_TOKEN}
BOT_USERNAME=${BOT_USERNAME}
WEBAPP_URL=https://${DOMAIN}/app/
SUPPORT_URL=${SUPPORT_URL}
OWNER_ID=${OWNER_ID}

DATABASE_URL=sqlite+aiosqlite:///${APP_DIR}/data/targetvpn.db
API_BASE_URL=http://127.0.0.1:8000
PUBLIC_BASE_URL=https://${DOMAIN}
INTERNAL_SECRET=${INTERNAL_SECRET}
JWT_SECRET=${JWT_SECRET}
CORS_ORIGINS=https://${DOMAIN}

# Заполните, когда будет готов ВПС под VPN (или добавьте локацию в админке).
MARZBAN_URL=
MARZBAN_USERNAME=
MARZBAN_PASSWORD=
MARZBAN_VERIFY_SSL=true
MARZBAN_INBOUNDS={"vless": ["VLESS TCP REALITY"]}
MARZBAN_PREFIX=tv
DEMO_MODE=false

CRYPTOBOT_TOKEN=
CRYPTOBOT_ASSET=USDT
RUB_PER_USDT=100
RUB_PER_STAR=1.6

LZT_TOKEN=
LZT_USER_ID=
LZT_USERNAME=
LZT_POLL_INTERVAL=60

TRIAL_ENABLED=true
REFERRAL_BONUS_DAYS=7
EOF
fi
mkdir -p "$APP_DIR/data"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
chmod 600 "$APP_DIR/.env"

# --- 5. systemd -----------------------------------------------------------
say "Регистрируем сервисы systemd"
install -m 644 deploy/targetvpn-api.service /etc/systemd/system/
install -m 644 deploy/targetvpn-bot.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable targetvpn-api targetvpn-bot >/dev/null
systemctl restart targetvpn-api targetvpn-bot

# --- 6. nginx -------------------------------------------------------------
if [[ $REUSE -eq 1 ]]; then
  say "nginx уже настроен — пропускаем"
else
say "Настраиваем nginx для $DOMAIN"
sed "s/vpn.example.com/${DOMAIN}/g" deploy/nginx.conf > /etc/nginx/sites-available/targetvpn
# До выпуска сертификата оставляем только HTTP, иначе nginx не стартует.
cat > /etc/nginx/sites-available/targetvpn <<EOF
server {
    listen 80;
    server_name ${DOMAIN};
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF
ln -sf /etc/nginx/sites-available/targetvpn /etc/nginx/sites-enabled/targetvpn
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
fi

# --- 7. Файрвол -----------------------------------------------------------
say "Открываем порты"
ufw allow OpenSSH >/dev/null 2>&1 || true
ufw allow 80/tcp  >/dev/null 2>&1 || true
ufw allow 443/tcp >/dev/null 2>&1 || true
yes | ufw enable >/dev/null 2>&1 || true

# --- 8. TLS ---------------------------------------------------------------
if [[ $REUSE -eq 0 && "$WANT_TLS" =~ ^[YyДд]?$ ]]; then
  say "Выпускаем сертификат Let's Encrypt"
  apt-get install -yqq certbot python3-certbot-nginx >/dev/null
  if certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos \
       --register-unsafely-without-email --redirect; then
    systemctl reload nginx
  else
    warn "Сертификат выпустить не удалось. Проверьте, что домен указывает на этот сервер,"
    warn "и повторите: certbot --nginx -d $DOMAIN"
  fi
fi

# --- 9. Проверка ----------------------------------------------------------
say "Проверяем конфигурацию"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/python" "$APP_DIR/scripts/check_config.py" || true

cat <<EOF

Готово. Дальше:
  1. В @BotFather: /newapp -> URL https://${DOMAIN}/app/
     и Bot Settings -> Menu Button -> тот же URL.
  2. Когда будет ВПС под VPN — поставьте Marzban (deploy/NODE_SETUP.md)
     и добавьте локацию в админке Mini App (вкладка «Локации»).
     До этого момента оплата и пробный доступ намеренно отключены.

Полезное:
  systemctl status targetvpn-api targetvpn-bot
  journalctl -u targetvpn-bot -f
  $APP_DIR/.venv/bin/python $APP_DIR/scripts/check_config.py
EOF
