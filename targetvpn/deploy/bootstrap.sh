#!/usr/bin/env bash
# Установка TargetVPN одной командой: бот, Mini App и VPN-нода.
#
#   curl -fsSL .../deploy/bootstrap.sh | TVPN_DOMAIN=... TVPN_BOT_TOKEN=... bash
#
# Переменные:
#   TVPN_DOMAIN      домен для Mini App (обязательно)
#   TVPN_BOT_TOKEN   токен бота от @BotFather (обязательно)
#   TVPN_OWNER_ID    Telegram ID владельца (по умолчанию 7824168810)
#   TVPN_WITH_NODE   1 (по умолчанию) — ставить VPN-ноду на этот же сервер
#   TVPN_XRAY_PORT   порт Xray, по умолчанию 8443 (443 занят мини-аппом)
#   TVPN_NODE_TITLE  название локации, например "Швеция"
#   TVPN_SKIP_DNS    1 — не проверять, что домен указывает на этот сервер
set -euo pipefail

BRANCH=${TVPN_BRANCH:-claude/telegram-vpn-mini-app-jn457e}
REPO=${TVPN_REPO:-https://github.com/grapetestbla-oss/wwrptechbot.git}
SRC=${TVPN_SRC:-/opt/targetvpn-src}

say()  { printf "\n\033[1;36m==> %s\033[0m\n" "$*"; }
warn() { printf "\033[1;33m!! %s\033[0m\n" "$*"; }
die()  { printf "\033[1;31mОшибка: %s\033[0m\n" "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "запустите от root"

# Обновление уже установленного сервиса: домен и токен берём из его .env,
# заново передавать переменные не нужно.
INSTALLED_ENV=/opt/targetvpn/.env
if [[ -f "$INSTALLED_ENV" ]]; then
  : "${TVPN_DOMAIN:=$(sed -n 's#^PUBLIC_BASE_URL=https\?://##p' "$INSTALLED_ENV" | tr -d '\r')}"
  : "${TVPN_BOT_TOKEN:=$(sed -n 's/^BOT_TOKEN=//p' "$INSTALLED_ENV" | tr -d '\r')}"
  [[ -n "$TVPN_DOMAIN" ]] && say "Обновляем установку ($TVPN_DOMAIN)"
fi

[[ -n "${TVPN_DOMAIN:-}" ]] || die "не задан TVPN_DOMAIN"
[[ -n "${TVPN_BOT_TOKEN:-}" ]] || die "не задан TVPN_BOT_TOKEN"

export TVPN_OWNER_ID=${TVPN_OWNER_ID:-7824168810}
export TVPN_XRAY_PORT=${TVPN_XRAY_PORT:-8443}

say "Ставим git"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git curl dnsutils >/dev/null

# --- Домен должен указывать сюда, иначе сертификат не выпустится -----------
if [[ "${TVPN_SKIP_DNS:-0}" != "1" ]]; then
  say "Проверяем DNS для $TVPN_DOMAIN"
  SERVER_IP=$(curl -fsS --max-time 15 https://api.ipify.org || hostname -I | awk '{print $1}')
  DOMAIN_IPS=$(getent ahostsv4 "$TVPN_DOMAIN" 2>/dev/null | awk '{print $1}' | sort -u | tr '\n' ' ')
  if [[ -z "$DOMAIN_IPS" ]]; then
    die "у домена $TVPN_DOMAIN нет A-записи. Добавьте A -> $SERVER_IP и повторите"
  fi
  if ! grep -qw "$SERVER_IP" <<<"$DOMAIN_IPS"; then
    warn "домен указывает на: $DOMAIN_IPS"
    warn "а этот сервер: $SERVER_IP"
    die "поправьте A-запись на $SERVER_IP (или запустите с TVPN_SKIP_DNS=1)"
  fi
  echo "DNS в порядке: $TVPN_DOMAIN -> $SERVER_IP"
fi

# --- Код -------------------------------------------------------------------
say "Забираем код из ветки $BRANCH"
rm -rf "$SRC"
git clone -q --depth 1 -b "$BRANCH" "$REPO" "$SRC" || die "не удалось склонировать репозиторий"
cd "$SRC/targetvpn"

# --- Бот и Mini App --------------------------------------------------------
say "Устанавливаем бота и Mini App"
bash deploy/install.sh </dev/null

# --- VPN-нода --------------------------------------------------------------
if [[ "${TVPN_WITH_NODE:-1}" == "1" ]]; then
  say "Устанавливаем VPN-ноду (Marzban + Xray Reality)"
  bash deploy/install_node.sh </dev/null
else
  warn "VPN-нода пропущена (TVPN_WITH_NODE=0)"
fi

cat <<EOF

Установка завершена.

Осталось в @BotFather:
  /newapp -> ваш бот -> Web App URL: https://${TVPN_DOMAIN}/app/
  /mybots -> Bot Settings -> Menu Button -> тот же URL

Проверка в любой момент:
  /opt/targetvpn/.venv/bin/python /opt/targetvpn/scripts/check_config.py
EOF
