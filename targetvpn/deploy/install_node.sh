#!/usr/bin/env bash
# Установка VPN-ноды: Marzban + Xray VLESS Reality.
#
# Можно ставить на тот же сервер, где крутится бот, или на отдельный ВПС.
# Скрипт идемпотентный: повторный запуск чинит конфигурацию, не ломая ключи.
#
#   bash deploy/install_node.sh
#   TVPN_XRAY_PORT=443 TVPN_NODE_TITLE="Германия" bash deploy/install_node.sh
set -euo pipefail

XRAY_PORT=${TVPN_XRAY_PORT:-8443}
NODE_CODE=${TVPN_NODE_CODE:-main}
NODE_TITLE=${TVPN_NODE_TITLE:-Основная локация}
NODE_FLAG=${TVPN_NODE_FLAG:-🌍}
REALITY_DEST=${TVPN_REALITY_DEST:-www.microsoft.com}
APP_DIR=/opt/targetvpn
MARZBAN_DIR=/opt/marzban
LOG=/var/log/targetvpn-node-install.log

say()  { printf "\n\033[1;36m==> %s\033[0m\n" "$*"; }
warn() { printf "\033[1;33m!! %s\033[0m\n" "$*"; }
die()  { printf "\033[1;31mОшибка: %s\033[0m\n" "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "запустите от root"

container_name() {
  docker ps --format '{{.Names}}' 2>/dev/null | grep -m1 marzban || true
}

# --- 1. Marzban ------------------------------------------------------------
if [[ -z "$(container_name)" ]]; then
  say "Ставим Marzban (лог: $LOG)"
  curl -fsSL https://github.com/Gozargah/Marzban-scripts/raw/master/marzban.sh -o /tmp/marzban.sh \
    || die "не удалось скачать установщик Marzban"
  # Установщик в конце показывает логи и не завершается сам — ограничиваем время
  # и дальше проверяем результат по состоянию контейнера.
  timeout 900 bash /tmp/marzban.sh install </dev/null >"$LOG" 2>&1 || true

  say "Ждём запуска панели"
  for _ in $(seq 1 60); do
    [[ -n "$(container_name)" ]] && break
    sleep 3
  done
  [[ -n "$(container_name)" ]] || { tail -20 "$LOG"; die "Marzban не запустился, смотрите $LOG"; }
else
  say "Marzban уже установлен — обновляем конфигурацию"
fi

CONTAINER=$(container_name)
PANEL_PORT=$(sed -n 's/^UVICORN_PORT[ =]*//p' "$MARZBAN_DIR/.env" 2>/dev/null | tr -d ' "' | head -1)
PANEL_PORT=${PANEL_PORT:-8000}
echo "Контейнер: $CONTAINER, порт панели: $PANEL_PORT"

# --- 2. Ключи Reality ------------------------------------------------------
CONFIG=/var/lib/marzban/xray_config.json
if grep -q '"privateKey"' "$CONFIG" 2>/dev/null && grep -q 'VLESS TCP REALITY' "$CONFIG" 2>/dev/null; then
  say "Инбаунд Reality уже настроен — ключи не трогаем"
  KEEP_CONFIG=1
else
  say "Генерируем ключи Reality"
  KEYS=$(docker exec "$CONTAINER" xray x25519 2>/dev/null || true)
  PRIVATE_KEY=$(echo "$KEYS" | sed -n 's/^Private key: //p')
  PUBLIC_KEY=$(echo "$KEYS" | sed -n 's/^Public key: //p')
  SHORT_ID=$(openssl rand -hex 8)
  [[ -n "$PRIVATE_KEY" ]] || die "не удалось получить ключи Reality (xray x25519)"
  KEEP_CONFIG=0
fi

# --- 3. Инбаунд ------------------------------------------------------------
if [[ "$KEEP_CONFIG" == "0" ]]; then
  say "Пишем конфигурацию Xray (VLESS Reality на порту ${XRAY_PORT})"
  [[ -f "$CONFIG" ]] && cp "$CONFIG" "${CONFIG}.bak.$(date +%s)"
  cat > "$CONFIG" <<EOF
{
  "log": { "loglevel": "warning" },
  "inbounds": [
    {
      "tag": "VLESS TCP REALITY",
      "listen": "0.0.0.0",
      "port": ${XRAY_PORT},
      "protocol": "vless",
      "settings": { "clients": [], "decryption": "none" },
      "streamSettings": {
        "network": "tcp",
        "security": "reality",
        "realitySettings": {
          "show": false,
          "dest": "${REALITY_DEST}:443",
          "xver": 0,
          "serverNames": ["${REALITY_DEST}"],
          "privateKey": "${PRIVATE_KEY}",
          "publicKey": "${PUBLIC_KEY}",
          "shortIds": ["${SHORT_ID}"]
        }
      },
      "sniffing": { "enabled": true, "destOverride": ["http", "tls", "quic"] }
    }
  ],
  "outbounds": [
    { "protocol": "freedom", "tag": "DIRECT" },
    { "protocol": "blackhole", "tag": "BLOCK" }
  ],
  "routing": {
    "rules": [
      { "type": "field", "ip": ["geoip:private"], "outboundTag": "BLOCK" },
      { "type": "field", "protocol": ["bittorrent"], "outboundTag": "BLOCK" }
    ]
  }
}
EOF
  say "Перезапускаем Marzban"
  (cd "$MARZBAN_DIR" && docker compose restart >/dev/null 2>&1) || marzban restart -n >/dev/null 2>&1 || true
  sleep 10
  CONTAINER=$(container_name)
fi

# --- 4. Администратор панели ----------------------------------------------
# Пароль панели храним на диске: иначе каждый повторный запуск заводил бы
# новый пароль и перезаписывал администратора.
CRED_FILE=/opt/targetvpn/.node-credentials
if [[ -f "$CRED_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$CRED_FILE"
fi
PANEL_USER=${TVPN_PANEL_USER:-${PANEL_USER:-tvadmin}}
PANEL_PASS=${TVPN_PANEL_PASS:-${PANEL_PASS:-$(openssl rand -hex 16)}}

panel_token_ok() {
  curl -sS --max-time 10 -X POST "http://127.0.0.1:${PANEL_PORT}/api/admin/token" \
    -d "username=${PANEL_USER}&password=${PANEL_PASS}" 2>/dev/null | grep -q access_token
}

say "Заводим администратора панели"
if panel_token_ok; then
  echo "Администратор ${PANEL_USER} уже существует"
else
  # У разных версий Marzban отличается CLI, поэтому пробуем по очереди.
  docker exec "$CONTAINER" marzban-cli admin create --sudo \
    --username "$PANEL_USER" --password "$PANEL_PASS" >/dev/null 2>&1 || true

  if ! panel_token_ok; then
    docker exec "$CONTAINER" python3 -c "
from app.db import GetDB, crud
from app.models.admin import AdminCreate
with GetDB() as db:
    existing = crud.get_admin(db, '${PANEL_USER}')
    if existing:
        crud.remove_admin(db, existing)
    crud.create_admin(db, AdminCreate(username='${PANEL_USER}', password='${PANEL_PASS}', is_sudo=True))
" >/dev/null 2>&1 || true
  fi

  if panel_token_ok; then
    echo "Создан администратор ${PANEL_USER}"
    mkdir -p "$(dirname "$CRED_FILE")"
    printf 'PANEL_USER=%s\nPANEL_PASS=%s\n' "$PANEL_USER" "$PANEL_PASS" > "$CRED_FILE"
    chmod 600 "$CRED_FILE"
  else
    warn "Автоматически создать администратора не вышло."
    warn "Сделайте вручную:  marzban cli admin create --sudo"
    warn "затем добавьте локацию в админке Mini App (адрес http://127.0.0.1:${PANEL_PORT})."
    PANEL_USER=""
  fi
fi

# --- 5. Файрвол ------------------------------------------------------------
say "Открываем порт ${XRAY_PORT}, панель наружу закрываем"
ufw allow "${XRAY_PORT}"/tcp >/dev/null 2>&1 || true
ufw deny "${PANEL_PORT}"/tcp >/dev/null 2>&1 || true

# --- 6. Регистрация локации в TargetVPN -----------------------------------
if [[ -n "$PANEL_USER" && -x "$APP_DIR/.venv/bin/python" && -f "$APP_DIR/scripts/add_node.py" ]]; then
  say "Регистрируем локацию в TargetVPN"
  if ( cd "$APP_DIR" && sudo -u targetvpn "$APP_DIR/.venv/bin/python" scripts/add_node.py \
        --code "$NODE_CODE" --title "$NODE_TITLE" --flag "$NODE_FLAG" \
        --url "http://127.0.0.1:${PANEL_PORT}" \
        --username "$PANEL_USER" --password "$PANEL_PASS" \
        --no-verify-ssl --default ); then
    systemctl restart targetvpn-api 2>/dev/null || true
  else
    warn "Не удалось зарегистрировать локацию — добавьте её в админке вручную"
  fi
elif [[ -z "$PANEL_USER" ]]; then
  warn "Локация не зарегистрирована: нет доступа к панели"
else
  warn "TargetVPN на этой машине не найден — добавьте локацию в админке вручную"
fi

cat <<EOF

Нода готова.

  Порт Xray:       ${XRAY_PORT} (VLESS Reality, маскировка под ${REALITY_DEST})
  Панель Marzban:  http://127.0.0.1:${PANEL_PORT} (наружу закрыта)
  Логин панели:    ${PANEL_USER:-не создан}
  Пароль панели:   ${PANEL_PASS}

Сохраните пароль — он нужен, если будете подключать ноду к боту на другом сервере.
EOF
