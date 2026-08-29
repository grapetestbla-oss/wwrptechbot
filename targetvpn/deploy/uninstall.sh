#!/usr/bin/env bash
# Полное удаление TargetVPN с основного ВПС.
#
#   bash deploy/uninstall.sh            # с бэкапом базы и .env в /root
#   TVPN_BACKUP=0 bash deploy/uninstall.sh   # без бэкапа
#
# Трогает только объекты TargetVPN. Caddy, nginx и другие сайты на сервере
# остаются работать: удаляется лишь наш блок конфигурации.
set -euo pipefail

APP_DIR=/opt/targetvpn
SRC_DIR=/opt/targetvpn-src
APP_USER=targetvpn
BACKUP=${TVPN_BACKUP:-1}

say()  { printf "\n\033[1;36m==> %s\033[0m\n" "$*"; }
warn() { printf "\033[1;33m!! %s\033[0m\n" "$*"; }

[[ $EUID -eq 0 ]] || { echo "Запустите от root"; exit 1; }

# --- 1. Бэкап -------------------------------------------------------------
if [[ "$BACKUP" == "1" && -d "$APP_DIR" ]]; then
  BACKUP_FILE="/root/targetvpn-backup-$(date +%F-%H%M).tar.gz"
  say "Сохраняем базу и конфиг в $BACKUP_FILE"
  tar czf "$BACKUP_FILE" -C "$APP_DIR" data .env 2>/dev/null \
    && echo "Готово: $BACKUP_FILE" \
    || warn "Нечего сохранять (нет data/.env)"
fi

# --- 2. Сервисы -----------------------------------------------------------
say "Останавливаем сервисы"
for unit in targetvpn-api targetvpn-bot; do
  systemctl stop "$unit" 2>/dev/null || true
  systemctl disable "$unit" 2>/dev/null || true
  rm -f "/etc/systemd/system/${unit}.service"
done
systemctl daemon-reload 2>/dev/null || true
systemctl reset-failed 2>/dev/null || true

# --- 3. Конфигурация веб-сервера -----------------------------------------
if [[ -f /etc/caddy/conf.d/targetvpn.caddy ]]; then
  say "Убираем сайт из Caddy"
  rm -f /etc/caddy/conf.d/targetvpn.caddy
  # Строку import оставляем, только если каталог опустел — она безвредна,
  # но чистим, чтобы не осталось следов от нашей установки.
  if [[ -d /etc/caddy/conf.d ]] && [[ -z "$(ls -A /etc/caddy/conf.d 2>/dev/null)" ]]; then
    sed -i '/^import conf\.d\/\*\.caddy$/d' /etc/caddy/Caddyfile 2>/dev/null || true
    rmdir /etc/caddy/conf.d 2>/dev/null || true
  fi
  if caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1; then
    systemctl reload caddy 2>/dev/null || true
  else
    warn "Caddyfile не проходит проверку — посмотрите его вручную"
  fi
fi

if [[ -e /etc/nginx/sites-enabled/targetvpn || -e /etc/nginx/sites-available/targetvpn ]]; then
  say "Убираем сайт из nginx"
  rm -f /etc/nginx/sites-enabled/targetvpn /etc/nginx/sites-available/targetvpn
  nginx -t >/dev/null 2>&1 && systemctl reload nginx 2>/dev/null || \
    warn "nginx не перезагружен — проверьте конфиг вручную"
fi

# --- 4. Файлы и пользователь ---------------------------------------------
say "Удаляем файлы проекта"
rm -rf "$APP_DIR" "$SRC_DIR"
id -u "$APP_USER" &>/dev/null && userdel "$APP_USER" 2>/dev/null || true

# --- 5. Что осталось ------------------------------------------------------
say "Проверяем, что ничего не осталось"
LEFT=0
for path in "$APP_DIR" "$SRC_DIR" /etc/systemd/system/targetvpn-api.service \
            /etc/systemd/system/targetvpn-bot.service /etc/caddy/conf.d/targetvpn.caddy; do
  [[ -e "$path" ]] && { echo "осталось: $path"; LEFT=1; }
done
[[ $LEFT -eq 0 ]] && echo "Чисто."

cat <<EOF

TargetVPN удалён с этого сервера.
${BACKUP_FILE:+Бэкап: ${BACKUP_FILE} — скопируйте его на новый ВПС, если нужны данные.}

Что НЕ трогалось и осталось работать: Caddy/nginx с остальными сайтами,
их сертификаты, системные пакеты (python3, git, ufw).

Отдельно, если больше не нужны:
  - A-запись домена, которую вы заводили под мини-апп;
  - в @BotFather: /myapps -> удалить Web App, и Bot Settings -> Menu Button -> убрать.
EOF
