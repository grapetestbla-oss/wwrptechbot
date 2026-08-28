# Настройка VPN-ноды (второй ВПС): Marzban + Xray VLESS Reality

Reality маскирует трафик под обычный HTTPS к реальному чужому сайту — именно это
позволяет работать там, где включают белые списки и режут стандартные VPN.

## 1. Установка Marzban

```bash
sudo bash -c "$(curl -sL https://github.com/Gozargah/Marzban-scripts/raw/master/marzban.sh)" @ install
sudo marzban cli admin create --sudo     # логин/пароль для панели -> в .env бота
```

Панель по умолчанию: `http://IP:8000/dashboard`. Закройте её TLS-сертификатом
(`marzban.sh` умеет ставить certbot) — бот ходит в API по HTTPS.

## 2. Ключи Reality

```bash
docker exec -it marzban-marzban-1 xray x25519   # приватный + публичный ключ
openssl rand -hex 8                             # shortId
```

## 3. Инбаунд в `/var/lib/marzban/xray_config.json`

```json
{
  "inbounds": [
    {
      "tag": "VLESS TCP REALITY",
      "listen": "0.0.0.0",
      "port": 443,
      "protocol": "vless",
      "settings": { "clients": [], "decryption": "none" },
      "streamSettings": {
        "network": "tcp",
        "security": "reality",
        "realitySettings": {
          "show": false,
          "dest": "www.microsoft.com:443",
          "xver": 0,
          "serverNames": ["www.microsoft.com"],
          "privateKey": "ПРИВАТНЫЙ_КЛЮЧ_ИЗ_ШАГА_2",
          "shortIds": ["SHORT_ID_ИЗ_ШАГА_2"]
        }
      },
      "sniffing": { "enabled": true, "destOverride": ["http", "tls", "quic"] }
    }
  ],
  "outbounds": [{ "protocol": "freedom", "tag": "DIRECT" }]
}
```

Перезапуск: `marzban restart`. Имя тега (`VLESS TCP REALITY`) должно совпадать
со значением `MARZBAN_INBOUNDS` в `.env`.

### Выбор `dest` / `serverNames`
Берите крупный сайт, который:
- не заблокирован в стране пользователей и не входит в чужие белые списки;
- поддерживает TLS 1.3 и HTTP/2;
- физически близок к вашей ноде (меньше подозрений по RTT).

Хорошие кандидаты: `www.microsoft.com`, `www.samsung.com`, `dl.google.com`.

## 4. Гигиена ноды

- Порт 443 отдан Xray; SSH перенесите на нестандартный порт и закройте паролем-ключом.
- `ufw allow 443`, `ufw allow <ssh-порт>`, остальное закрыть; порт панели — только
  для IP основного ВПС: `ufw allow from <IP_основного_ВПС> to any port 8000`.
- Включите BBR: `echo "net.core.default_qdisc=fq" >> /etc/sysctl.conf`,
  `echo "net.ipv4.tcp_congestion_control=bbr" >> /etc/sysctl.conf`, `sysctl -p`.

## 5. Как бот управляет нодой

Одно устройство пользователя = один аккаунт Marzban с именем `tv_<tgid>_<n>`.
Лимит устройств соблюдается на стороне Xray, а не только в интерфейсе:
лишние аккаунты бэкенд удаляет, при бане и истечении срока — переводит в `disabled`.
