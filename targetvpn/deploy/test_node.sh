#!/usr/bin/env bash
# Проверка ноды без телефона: поднимаем клиент Xray прямо на сервере,
# подключаемся к собственному инбаунду и пробуем выйти в интернет.
#
#   bash deploy/test_node.sh                    # ключ берётся из базы TargetVPN
#   bash deploy/test_node.sh 'vless://...'      # или передайте ключ вручную
set -uo pipefail

APP_DIR=/opt/targetvpn
OWNER_ID=${TVPN_OWNER_ID:-7824168810}
SOCKS_PORT=10899
CONFIG=/var/lib/marzban/tv_test_client.json

say()  { printf "\n\033[1;36m==> %s\033[0m\n" "$*"; }
ok()   { printf "\033[1;32m✅ %s\033[0m\n" "$*"; }
bad()  { printf "\033[1;31m❌ %s\033[0m\n" "$*"; }
warn() { printf "\033[1;33m!! %s\033[0m\n" "$*"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

KEY=${1:-}
if [[ -z "$KEY" && -x "$APP_DIR/.venv/bin/python" ]]; then
  say "Берём ключ из базы TargetVPN"
  # Запускаем из каталога сервиса (там .env и база), но скрипт берём из этого
  # клона: в /opt/targetvpn может лежать сборка постарше.
  for candidate in "$SCRIPT_DIR/../scripts/print_key.py" "$APP_DIR/scripts/print_key.py"; do
    [[ -f "$candidate" ]] || continue
    KEY=$(cd "$APP_DIR" && "$APP_DIR/.venv/bin/python" "$candidate" "$OWNER_ID" 2>&1 | tail -1)
    [[ "$KEY" == vless://* ]] && break
    warn "Не вышло получить ключ: $KEY"
    KEY=""
  done
fi

if [[ -z "$KEY" ]]; then
  bad "Ключ не найден."
  warn "Скопируйте его в мини-аппе (Устройства → Ключ → «Скопировать ключ») и запустите:"
  warn "  bash deploy/test_node.sh 'vless://…'"
  exit 1
fi
echo "Ключ: ${KEY:0:60}…"

CONTAINER=$(docker ps --format '{{.Names}}' | grep -m1 marzban || true)
[[ -n "$CONTAINER" ]] || { bad "Контейнер Marzban не найден"; exit 1; }

# --- разбираем ссылку ---
UUID=$(sed -E 's|vless://([^@]+)@.*|\1|' <<<"$KEY")
HOSTPORT=$(sed -E 's|vless://[^@]+@([^?]+).*|\1|' <<<"$KEY")
HOST=${HOSTPORT%%:*}
PORT=${HOSTPORT##*:}
query=${KEY#*\?}
param() { sed -E "s/.*[?&]$1=([^&#]*).*/\1/" <<<"$KEY" | head -1; }
SNI=$(param sni); PBK=$(param pbk); SID=$(param sid); FP=$(param fp)
FP=${FP:-chrome}

echo "Сервер: $HOST:$PORT, sni: $SNI"
echo "pbk из ключа: $PBK"

SERVER_PBK=$(grep -o '"publicKey"[^,]*' /var/lib/marzban/xray_config.json 2>/dev/null \
  | head -1 | sed -E 's/.*:\s*"([^"]*)".*/\1/')
if [[ -n "$SERVER_PBK" ]]; then
  if [[ "$SERVER_PBK" == "$PBK" ]]; then
    ok "Публичный ключ совпадает с конфигурацией ноды"
  else
    bad "Ключи не совпадают! В конфиге ноды: $SERVER_PBK"
    warn "Клиент не сможет пройти рукопожатие Reality — нужно перевыпустить ключи."
  fi
fi

# --- клиентская конфигурация для проверки ---
cat > "$CONFIG" <<EOF
{
  "log": { "loglevel": "warning" },
  "inbounds": [{ "listen": "127.0.0.1", "port": ${SOCKS_PORT}, "protocol": "socks",
    "settings": { "udp": true, "auth": "noauth" } }],
  "outbounds": [{ "protocol": "vless",
    "settings": { "vnext": [{ "address": "${HOST}", "port": ${PORT},
      "users": [{ "id": "${UUID}", "encryption": "none" }] }] },
    "streamSettings": { "network": "tcp", "security": "reality",
      "realitySettings": { "serverName": "${SNI}", "fingerprint": "${FP}",
        "publicKey": "${PBK}", "shortId": "${SID}" } } }]
}
EOF

say "Запускаем тестовый клиент внутри контейнера"
docker exec -d "$CONTAINER" xray run -c /var/lib/marzban/tv_test_client.json
sleep 4

say "Пробуем выйти в интернет через собственный сервер"
RESULT=$(curl -s --max-time 20 -x "socks5h://127.0.0.1:${SOCKS_PORT}" https://1.1.1.1/cdn-cgi/trace 2>&1)
if grep -q "ip=" <<<"$RESULT"; then
  ok "Туннель работает. Выходной адрес:"
  grep -E "^(ip|loc)=" <<<"$RESULT"
else
  bad "Через туннель ничего не прошло."
  echo "$RESULT" | head -3
  warn "Смотрите логи ядра: docker logs $CONTAINER --tail 40 | grep -i -E 'reality|fail|error'"
fi

say "Убираем тестовый клиент"
docker exec "$CONTAINER" pkill -f tv_test_client.json 2>/dev/null || true
rm -f "$CONFIG"
