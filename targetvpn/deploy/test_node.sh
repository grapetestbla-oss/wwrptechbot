#!/usr/bin/env bash
# Проверка ноды без телефона.
#
# Поднимаем клиент Xray прямо на сервере и пробуем выйти в интернет через
# собственный инбаунд — сначала через 127.0.0.1 (проверяем ключи и Reality),
# затем через внешний адрес (проверяем сетевой путь).
#
#   bash deploy/test_node.sh                 # ключ берётся из базы TargetVPN
#   bash deploy/test_node.sh 'vless://...'   # или передайте ключ вручную
set -uo pipefail

APP_DIR=/opt/targetvpn
OWNER_ID=${TVPN_OWNER_ID:-7824168810}
SOCKS_PORT=10899
WORK=/tmp/targetvpn-test
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

say()  { printf "\n\033[1;36m==> %s\033[0m\n" "$*"; }
ok()   { printf "\033[1;32m✅ %s\033[0m\n" "$*"; }
bad()  { printf "\033[1;31m❌ %s\033[0m\n" "$*"; }
warn() { printf "\033[1;33m!! %s\033[0m\n" "$*"; }

mkdir -p "$WORK"
XRAY_PID=""
cleanup() { [[ -n "$XRAY_PID" ]] && kill "$XRAY_PID" 2>/dev/null; }
trap cleanup EXIT

# --- 1. Ключ -------------------------------------------------------------
KEY=${1:-}
if [[ -z "$KEY" && -x "$APP_DIR/.venv/bin/python" ]]; then
  say "Берём ключ из базы TargetVPN"
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
  warn "Скопируйте его в мини-аппе и запустите: bash deploy/test_node.sh 'vless://…'"
  exit 1
fi

UUID=$(sed -E 's|vless://([^@]+)@.*|\1|' <<<"$KEY")
HOSTPORT=$(sed -E 's|vless://[^@]+@([^?]+).*|\1|' <<<"$KEY")
HOST=${HOSTPORT%%:*}
PORT=${HOSTPORT##*:}
param() { sed -E "s/.*[?&]$1=([^&#]*).*/\1/" <<<"$KEY" | head -1; }
SNI=$(param sni); PBK=$(param pbk); SID=$(param sid); FP=$(param fp); FP=${FP:-chrome}
echo "Сервер: $HOST:$PORT · sni: $SNI"

CONTAINER=$(docker ps --format '{{.Names}}' | grep -m1 marzban || true)
[[ -n "$CONTAINER" ]] || { bad "Контейнер Marzban не найден"; exit 1; }

# --- 2. Ключи Reality ----------------------------------------------------
SERVER_PBK=$(grep -o '"publicKey"[^,]*' /var/lib/marzban/xray_config.json 2>/dev/null \
  | head -1 | sed -E 's/.*:\s*"([^"]*)".*/\1/')
if [[ -n "$SERVER_PBK" && "$SERVER_PBK" != "$PBK" ]]; then
  bad "Публичный ключ в ключе и в конфигурации ноды не совпадают"
  echo "  в ключе:  $PBK"
  echo "  на ноде:  $SERVER_PBK"
  exit 1
fi
ok "Публичный ключ совпадает с конфигурацией ноды"

# shortId: если его нет в списке ноды, сервер молча рвёт соединение.
SERVER_SIDS=$(sed -n '/shortIds/,/]/p' /var/lib/marzban/xray_config.json 2>/dev/null \
  | grep -o '"[0-9a-fA-F]\+"' | tr -d '"' | tr '\n' ' ')
if [[ -n "$SERVER_SIDS" ]]; then
  if grep -qw "$SID" <<<"$SERVER_SIDS"; then
    ok "shortId из ключа есть в конфигурации ноды"
  else
    bad "shortId из ключа отсутствует на ноде"
    echo "  в ключе: $SID"
    echo "  на ноде: $SERVER_SIDS"
  fi
fi

# Ключ в нашей базе мог устареть: сравним с тем, что панель выдаёт сейчас.
say "Сравниваем с ключом из панели Marzban"
CREDS=/opt/targetvpn/.node-credentials
if [[ -f "$CREDS" ]]; then
  # shellcheck disable=SC1090
  source "$CREDS"
  PANEL_PORT=$(sed -n 's/^UVICORN_PORT[ =]*//p' /opt/marzban/.env 2>/dev/null | tr -d ' "' | head -1)
  PANEL_PORT=${PANEL_PORT:-8000}
  TOKEN=$(curl -s --max-time 10 -X POST "http://127.0.0.1:${PANEL_PORT}/api/admin/token" \
    -d "username=${PANEL_USER}&password=${PANEL_PASS}" | sed -E 's/.*"access_token":"([^"]*)".*/\1/')
  if [[ -n "$TOKEN" && "$TOKEN" != *"{"* ]]; then
    MARZBAN_USER=$(sed -E 's|.*#||' <<<"$KEY" | sed -E 's/%20.*//; s/.*\(//' )
    REMOTE_USER=${TVPN_REMOTE_USER:-tv_${OWNER_ID}_1}
    LIVE=$(curl -s --max-time 10 -H "Authorization: Bearer $TOKEN" \
      "http://127.0.0.1:${PANEL_PORT}/api/user/${REMOTE_USER}" \
      | grep -o 'vless://[^"]*' | head -1)
    if [[ -n "$LIVE" ]]; then
      LIVE_UUID=$(sed -E 's|vless://([^@]+)@.*|\1|' <<<"$LIVE")
      LIVE_SID=$(sed -E 's/.*[?&]sid=([^&#]*).*/\1/' <<<"$LIVE")
      LIVE_PBK=$(sed -E 's/.*[?&]pbk=([^&#]*).*/\1/' <<<"$LIVE")
      if [[ "$LIVE_UUID" == "$UUID" && "$LIVE_SID" == "$SID" && "$LIVE_PBK" == "$PBK" ]]; then
        ok "Ключ в базе совпадает с тем, что выдаёт панель"
      else
        bad "Ключ в базе устарел — панель выдаёт другой"
        echo "  панель: uuid=${LIVE_UUID:0:8}… sid=$LIVE_SID"
        echo "  база:   uuid=${UUID:0:8}… sid=$SID"
        warn "Дальше проверяем ключом из панели."
        KEY=$LIVE
        UUID=$LIVE_UUID; SID=$LIVE_SID; PBK=$LIVE_PBK
      fi
    else
      warn "Панель не отдала ключ для ${REMOTE_USER}"
    fi
  else
    warn "Не удалось получить токен панели"
  fi
else
  warn "Нет $CREDS — пропускаем сверку с панелью"
fi

# --- 2.1 Пара ключей Reality ---------------------------------------------
# publicKey в конфиге — просто текст, сервер работает по privateKey.
# Если пара разошлась, клиента отвергнут, а поле в файле останется прежним.
say "Сверяем пару ключей Reality на самой ноде"
PRIV=$(grep -o '"privateKey"[^,]*' /var/lib/marzban/xray_config.json 2>/dev/null \
  | head -1 | sed -E 's/.*:\s*"([^"]*)".*/\1/')
if [[ -n "$PRIV" ]]; then
  DERIVED=$(docker exec "$CONTAINER" xray x25519 -i "$PRIV" 2>/dev/null \
    | sed -n 's/^Public key: //p' | tr -d '\r')
  if [[ -z "$DERIVED" ]]; then
    warn "Не удалось вычислить публичный ключ из приватного (другая версия xray)"
  elif [[ "$DERIVED" == "$PBK" ]]; then
    ok "Приватный ключ ноды соответствует ключу в подписке"
  else
    bad "Пара ключей Reality разошлась — клиента отвергают всегда"
    echo "  из privateKey следует: $DERIVED"
    echo "  клиентам выдаётся:     $PBK"
    warn "Лечится перевыпуском инбаунда:"
    warn "  rm /var/lib/marzban/xray_config.json && bash deploy/install_node.sh"
    exit 1
  fi
fi

# --- 2.2 Есть ли пользователь в работающем ядре --------------------------
if [[ -n "${TOKEN:-}" ]]; then
  say "Проверяем, попал ли пользователь в работающее ядро"
  RUNNING=$(curl -s --max-time 10 -H "Authorization: Bearer $TOKEN" \
    "http://127.0.0.1:${PANEL_PORT}/api/core/config")
  if grep -q "$UUID" <<<"$RUNNING"; then
    ok "UUID найден в конфигурации работающего ядра"
  elif [[ -n "$RUNNING" && "$RUNNING" != *"detail"* ]]; then
    bad "UUID отсутствует в работающем ядре — сервер не знает этого клиента"
    warn "Marzban не применил пользователя работающему ядру."
    warn "Лечится одной кнопкой: Админка -> Локации -> ваша локация ->"
    warn "«Пересинхронизировать с ядром»."
    warn "Или прямо здесь:"
    warn "  curl -s -X POST -H \"Authorization: Bearer \$TOKEN\" \\"
    warn "    http://127.0.0.1:${PANEL_PORT}/api/core/restart"
  else
    warn "Панель не отдала конфигурацию ядра — пропускаем проверку"
  fi
fi

# --- 3. Сайт-прикрытие Reality -------------------------------------------
say "Проверяем сайт маскировки ($SNI:443) с самого сервера"
if timeout 10 bash -c "cat < /dev/null > /dev/tcp/${SNI}/443" 2>/dev/null; then
  ok "Сайт маскировки доступен — Reality сможет проксировать рукопожатие"
else
  bad "Сервер не может открыть ${SNI}:443"
  warn "Reality без доступа к dest не работает. Смените dest на доступный сайт:"
  warn "  TVPN_REALITY_DEST=dl.google.com bash deploy/install_node.sh"
  exit 1
fi

# --- 4. Клиент Xray на хосте ---------------------------------------------
XRAY_BIN=$(docker exec "$CONTAINER" sh -c 'command -v xray' 2>/dev/null | tr -d '\r')
[[ -n "$XRAY_BIN" ]] || XRAY_BIN=/usr/local/bin/xray
docker cp "$CONTAINER:$XRAY_BIN" "$WORK/xray" >/dev/null 2>&1 || {
  bad "Не удалось достать бинарник Xray из контейнера"; exit 1; }
chmod +x "$WORK/xray"

run_through() {
  local address=$1 label=$2
  cat > "$WORK/client.json" <<EOF
{
  "log": { "loglevel": "info" },
  "inbounds": [{ "listen": "127.0.0.1", "port": ${SOCKS_PORT}, "protocol": "socks",
    "settings": { "udp": true, "auth": "noauth" } }],
  "outbounds": [{ "protocol": "vless",
    "settings": { "vnext": [{ "address": "${address}", "port": ${PORT},
      "users": [{ "id": "${UUID}", "encryption": "none" }] }] },
    "streamSettings": { "network": "tcp", "security": "reality",
      "realitySettings": { "serverName": "${SNI}", "fingerprint": "${FP}",
        "publicKey": "${PBK}", "shortId": "${SID}" } } }]
}
EOF
  "$WORK/xray" run -c "$WORK/client.json" > "$WORK/xray.log" 2>&1 &
  XRAY_PID=$!
  sleep 3

  local out
  out=$(curl -sS --max-time 20 -x "socks5h://127.0.0.1:${SOCKS_PORT}" \
    https://1.1.1.1/cdn-cgi/trace 2>&1)
  # Пауза, чтобы ядро успело записать причину отказа в лог.
  sleep 2
  kill -TERM "$XRAY_PID" 2>/dev/null; wait "$XRAY_PID" 2>/dev/null; XRAY_PID=""

  if grep -q "ip=" <<<"$out"; then
    ok "$label: трафик прошёл"
    grep -E "^(ip|loc)=" <<<"$out" | sed 's/^/    /'
    return 0
  fi

  bad "$label: трафик не прошёл"
  echo "  curl: ${out:0:160}"
  echo "  лог ядра:"
  grep -viE "^$" "$WORK/xray.log" | tail -8 | sed 's/^/    /'
  return 1
}

say "Проверяем часы сервера — Reality завязан на время"
if command -v timedatectl >/dev/null 2>&1; then
  timedatectl | grep -E "Time zone|synchronized|Universal" | sed 's/^/  /'
fi

say "Проверка через 127.0.0.1 — это про ключи и Reality"
LOCAL_OK=0
run_through "127.0.0.1" "Локально" && LOCAL_OK=1

say "Проверка через внешний адрес — это про сеть и файрвол"
PUBLIC_OK=0
run_through "$HOST" "Снаружи" && PUBLIC_OK=1

# --- 5. Итог -------------------------------------------------------------
say "Итог"
if [[ $LOCAL_OK -eq 1 && $PUBLIC_OK -eq 1 ]]; then
  ok "Нода полностью исправна. Причина у клиента на телефоне."
elif [[ $LOCAL_OK -eq 1 ]]; then
  bad "Ключи и Reality в порядке, но снаружи не пройти."
  warn "Похоже на фильтрацию у провайдера или блокировку порта ${PORT}."
  warn "Попробуйте перенести Xray на 443: TVPN_XRAY_PORT=443 (мини-апп тогда на другой порт)."
else
  bad "Не проходит даже локально — дело в конфигурации инбаунда."
  warn "Полные логи ядра: docker logs $CONTAINER --tail 60"
fi
rm -rf "$WORK"
