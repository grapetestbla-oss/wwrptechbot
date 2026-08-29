#!/usr/bin/env bash
# Установка TargetVPN на основной ВПС (Ubuntu 22.04/24.04, Debian 12).
#
# Интерактивно:      bash deploy/install.sh
# Без вопросов:      TVPN_DOMAIN=vpn.example.com TVPN_BOT_TOKEN=123:AA... \
#                    TVPN_OWNER_ID=7824168810 bash deploy/install.sh
#
# Повторный запуск обновляет код и не трогает .env, веб-сервер и сертификаты.
set -euo pipefail

APP_DIR=/opt/targetvpn
APP_USER=targetvpn
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

say()  { printf "\n\033[1;36m==> %s\033[0m\n" "$*"; }
warn() { printf "\033[1;33m!! %s\033[0m\n" "$*"; }
die()  { printf "\033[1;31mОшибка: %s\033[0m\n" "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "запустите от root (sudo bash deploy/install.sh)"

# --- 1. Параметры ---------------------------------------------------------
DOMAIN=${TVPN_DOMAIN:-}
BOT_TOKEN=${TVPN_BOT_TOKEN:-}
OWNER_ID=${TVPN_OWNER_ID:-7824168810}
SUPPORT_URL=${TVPN_SUPPORT_URL:-}
WANT_TLS=${TVPN_TLS:-Y}

REUSE=0
if [[ -f "$APP_DIR/.env" ]]; then
  REUSE=1
  DOMAIN=$(sed -n 's#^PUBLIC_BASE_URL=https\?://##p' "$APP_DIR/.env" | tr -d '\r')
  say "Найден $APP_DIR/.env — обновляем установку ($DOMAIN), настройки не трогаем"
fi

if [[ $REUSE -eq 0 ]]; then
  if [[ -z "$BOT_TOKEN" ]]; then
    read -rp "Токен бота от @BotFather: " BOT_TOKEN
  fi
  [[ -n "$BOT_TOKEN" ]] || die "токен бота обязателен (TVPN_BOT_TOKEN)"

  if [[ -z "$DOMAIN" ]]; then
    read -rp "Домен для Mini App (пусто — IP через nip.io): " DOMAIN
  fi
  if [[ -z "$DOMAIN" ]]; then
    PUBLIC_IP=$(curl -fsS https://api.ipify.org || hostname -I | awk '{print $1}')
    DOMAIN="${PUBLIC_IP//./-}.nip.io"
    warn "Домен не указан, используем $DOMAIN"
  fi

  BOT_USERNAME=$(curl -fsS "https://api.telegram.org/bot${BOT_TOKEN}/getMe" 2>/dev/null \
    | sed -n 's/.*"username":"\([^"]*\)".*/\1/p' || true)
  [[ -n "$BOT_USERNAME" ]] && echo "Бот определён: @${BOT_USERNAME}" \
    || read -rp "Username бота без @ (Telegram не ответил): " BOT_USERNAME
  SUPPORT_URL=${SUPPORT_URL:-https://t.me/${BOT_USERNAME}}
fi

# --- 2. Кто держит 80/443 -------------------------------------------------
WEB=nginx
if systemctl is-active --quiet caddy 2>/dev/null; then
  WEB=caddy
  say "На сервере работает Caddy — встраиваемся в него, nginx не ставим"
elif ss -ltnp 2>/dev/null | grep -qE ':(80|443)\b' && ! systemctl is-active --quiet nginx 2>/dev/null; then
  warn "Порты 80/443 заняты сторонним процессом:"
  ss -ltnp | grep -E ':(80|443)\b' || true
  warn "Веб-сервер не настраиваю — проксируйте сами на http://127.0.0.1:8000"
  WEB=manual
fi

# --- 3. Пакеты ------------------------------------------------------------
say "Ставим системные пакеты"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
PKGS="python3 python3-venv python3-pip curl git ufw"
[[ "$WEB" == "nginx" ]] && PKGS="$PKGS nginx"
apt-get install -yqq $PKGS >/dev/null

# --- 4. Пользователь и файлы ---------------------------------------------
say "Готовим $APP_DIR"
id -u "$APP_USER" &>/dev/null || useradd --system --create-home --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
mkdir -p "$APP_DIR"
[[ "$REPO_DIR" != "$APP_DIR" ]] && cp -r "$REPO_DIR"/. "$APP_DIR"/
cd "$APP_DIR"

say "Виртуальное окружение и зависимости"
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r backend/requirements.txt -r bot/requirements.txt

# --- 5. Конфигурация ------------------------------------------------------
if [[ $REUSE -eq 1 ]]; then
  say "Конфигурация сохранена без изменений (бэкап .env.bak)"
  cp .env .env.bak
else
  say "Генерируем .env"
  cat > .env <<EOF
BOT_TOKEN=${BOT_TOKEN}
BOT_USERNAME=${BOT_USERNAME}
WEBAPP_URL=https://${DOMAIN}/app/
SUPPORT_URL=${SUPPORT_URL}
OWNER_ID=${OWNER_ID}

DATABASE_URL=sqlite+aiosqlite:///${APP_DIR}/data/targetvpn.db
API_BASE_URL=http://127.0.0.1:8000
PUBLIC_BASE_URL=https://${DOMAIN}
INTERNAL_SECRET=$(openssl rand -hex 32)
JWT_SECRET=$(openssl rand -hex 32)
CORS_ORIGINS=https://${DOMAIN}

# Заполняется автоматически при добавлении локации в админке.
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

# --- 6. systemd -----------------------------------------------------------
say "Регистрируем сервисы systemd"
install -m 644 deploy/targetvpn-api.service /etc/systemd/system/
install -m 644 deploy/targetvpn-bot.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable targetvpn-api targetvpn-bot >/dev/null
systemctl restart targetvpn-api targetvpn-bot

# --- 7. Веб-сервер --------------------------------------------------------
if [[ $REUSE -eq 1 ]]; then
  say "Веб-сервер уже настроен — пропускаем"
elif [[ "$WEB" == "caddy" ]]; then
  say "Добавляем сайт в Caddy (сертификат он выпустит сам)"
  cp /etc/caddy/Caddyfile "/etc/caddy/Caddyfile.bak.$(date +%s)" 2>/dev/null || true
  mkdir -p /etc/caddy/conf.d
  cat > /etc/caddy/conf.d/targetvpn.caddy <<EOF
${DOMAIN} {
    encode zstd gzip
    reverse_proxy 127.0.0.1:8000
}
EOF
  grep -q 'import conf.d/\*.caddy' /etc/caddy/Caddyfile 2>/dev/null \
    || printf '\nimport conf.d/*.caddy\n' >> /etc/caddy/Caddyfile
  if caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1; then
    systemctl reload caddy
  else
    warn "Caddyfile не прошёл проверку — возможно, ${DOMAIN} уже описан в конфиге."
    warn "Проверьте: caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile"
    rm -f /etc/caddy/conf.d/targetvpn.caddy
  fi
elif [[ "$WEB" == "nginx" ]]; then
  say "Настраиваем nginx для $DOMAIN"
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

  if [[ "$WANT_TLS" =~ ^[YyДд]?$ ]]; then
    say "Выпускаем сертификат Let's Encrypt"
    apt-get install -yqq certbot python3-certbot-nginx >/dev/null
    certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos \
      --register-unsafely-without-email --redirect || \
      warn "Сертификат не выпущен. Проверьте DNS и повторите: certbot --nginx -d $DOMAIN"
    systemctl reload nginx
  fi
fi

# --- 8. Файрвол -----------------------------------------------------------
say "Открываем порты"
ufw allow OpenSSH >/dev/null 2>&1 || true
ufw allow 80/tcp  >/dev/null 2>&1 || true
ufw allow 443/tcp >/dev/null 2>&1 || true
yes | ufw enable  >/dev/null 2>&1 || true

# --- 9. Проверка ----------------------------------------------------------
say "Проверяем конфигурацию"
sleep 3
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/python" "$APP_DIR/scripts/check_config.py" || true

cat <<EOF

Готово. Дальше:
  1. @BotFather: /newapp -> URL https://${DOMAIN}/app/
     и Bot Settings -> Menu Button -> тот же URL.
  2. Когда будет ВПС под VPN — Marzban по deploy/NODE_SETUP.md,
     затем добавьте локацию в админке Mini App.
     До этого оплата и пробный доступ намеренно отключены.

Полезное:
  systemctl status targetvpn-api targetvpn-bot
  journalctl -u targetvpn-bot -f
  $APP_DIR/.venv/bin/python $APP_DIR/scripts/check_config.py
EOF
