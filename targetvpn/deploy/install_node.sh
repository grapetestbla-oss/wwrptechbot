#!/usr/bin/env bash
# Установка VPN-ноды: Marzban + Xray VLESS Reality.
#
# Можно ставить на тот же сервер, где крутится бот, или на отдельный ВПС.
# После установки нода автоматически регистрируется в базе TargetVPN,
# если она есть на этой же машине.
#
#   bash deploy/install_node.sh
#   TVPN_XRAY_PORT=443 TVPN_NODE_TITLE="Германия" bash deploy/install_node.sh
set -euo pipefail

XRAY_PORT=${TVPN_XRAY_PORT:-8443}
NODE_CODE=${TVPN_NODE_CODE:-main}
NODE_TITLE=${TVPN_NODE_TITLE:-Основная локация}
NODE_FLAG=${TVPN_NODE_FLAG:-🌍}
# Сайт, под который маскируется Reality. Должен держать TLS 1.3 и быть
# доступен из страны пользователей.
REALITY_DEST=${TVPN_REALITY_DEST:-www.microsoft.com}
PANEL_PORT=${TVPN_PANEL_PORT:-8000}
APP_DIR=/opt/targetvpn

say()  { printf "\n\033[1;36m==> %s\033[0m\n" "$*"; }
warn() { printf "\033[1;33m!! %s\033[0m\n" "$*"; }
die()  { printf "\033[1;31mОшибка: %s\033[0m\n" "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "запустите от root"

if ss -ltn 2>/dev/null | grep -q ":${XRAY_PORT}\b"; then
  die "порт ${XRAY_PORT} уже занят — задайте другой через TVPN_XRAY_PORT"
fi

# --- 1. Marzban ------------------------------------------------------------
if ! command -v marzban >/dev/null 2>&1; then
  say "Ставим Marzban (панель управления Xray)"
  bash -c "$(curl -sL https://github.com/Gozargah/Marzban-scripts/raw/master/marzban.sh)" @ install \
    || die "не удалось установить Marzban"
else
  say "Marzban уже установлен — обновляем конфигурацию"
fi

PANEL_USER=${TVPN_PANEL_USER:-tvadmin}
PANEL_PASS=${TVPN_PANEL_PASS:-$(openssl rand -hex 16)}

# --- 2. Ключи Reality ------------------------------------------------------
say "Генерируем ключи Reality"
CONTAINER=$(docker ps --format '{{.Names}}' | grep -m1 marzban || true)
[[ -n "$CONTAINER" ]] || die "контейнер Marzban не найден — проверьте: marzban status"

KEYS=$(docker exec "$CONTAINER" xray x25519)
PRIVATE_KEY=$(echo "$KEYS" | sed -n 's/^Private key: //p')
PUBLIC_KEY=$(echo "$KEYS" | sed -n 's/^Public key: //p')
SHORT_ID=$(openssl rand -hex 8)
[[ -n "$PRIVATE_KEY" ]] || die "не удалось получить ключи Reality"

# --- 3. Инбаунд ------------------------------------------------------------
say "Пишем конфигурацию Xray (VLESS Reality на порту ${XRAY_PORT})"
CONFIG=/var/lib/marzban/xray_config.json
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
marzban restart -n >/dev/null 2>&1 || marzban restart >/dev/null 2>&1 || true
sleep 8

# --- 4. Администратор панели ----------------------------------------------
say "Создаём администратора панели"
if docker exec -e MARZBAN_USERNAME="$PANEL_USER" -e MARZBAN_PASSWORD="$PANEL_PASS" \
     "$CONTAINER" marzban-cli admin create --sudo --username "$PANEL_USER" \
     --password "$PANEL_PASS" >/dev/null 2>&1; then
  echo "Создан: $PANEL_USER"
else
  warn "Не удалось создать администратора автоматически."
  warn "Сделайте вручную: marzban cli admin create --sudo"
  warn "и пропишите логин/пароль в админке Mini App -> Локации."
fi

# --- 5. Файрвол ------------------------------------------------------------
say "Открываем порт ${XRAY_PORT}"
ufw allow "${XRAY_PORT}"/tcp >/dev/null 2>&1 || true
# Панель наружу не выставляем: бэкенд ходит на 127.0.0.1.
ufw deny "${PANEL_PORT}"/tcp >/dev/null 2>&1 || true

# --- 6. Регистрация локации в TargetVPN -----------------------------------
if [[ -x "$APP_DIR/.venv/bin/python" && -f "$APP_DIR/scripts/add_node.py" ]]; then
  say "Регистрируем локацию в TargetVPN"
  ( cd "$APP_DIR" && sudo -u targetvpn "$APP_DIR/.venv/bin/python" scripts/add_node.py \
      --code "$NODE_CODE" --title "$NODE_TITLE" --flag "$NODE_FLAG" \
      --url "http://127.0.0.1:${PANEL_PORT}" \
      --username "$PANEL_USER" --password "$PANEL_PASS" \
      --no-verify-ssl --default ) && systemctl restart targetvpn-api || \
    warn "Не удалось зарегистрировать локацию — добавьте её вручную в админке"
else
  warn "TargetVPN на этой машине не найден — добавьте локацию в админке вручную"
fi

cat <<EOF

Нода готова.

  Порт Xray:        ${XRAY_PORT} (VLESS Reality, маскировка под ${REALITY_DEST})
  Панель Marzban:   http://127.0.0.1:${PANEL_PORT}  (наружу закрыта)
  Логин панели:     ${PANEL_USER}
  Пароль панели:    ${PANEL_PASS}

Сохраните пароль: он понадобится, если будете подключать эту ноду к боту
на другом сервере. Проверка: /opt/targetvpn/.venv/bin/python /opt/targetvpn/scripts/check_config.py
EOF
