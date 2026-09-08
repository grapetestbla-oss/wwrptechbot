package targetcore

import (
	"strings"
	"testing"

	"github.com/xtls/xray-core/infra/conf/serial"
)

// Конфигурация ноды ровно в том виде, как её пишет deploy/install_node.sh.
const serverConfig = `{
  "log": { "loglevel": "warning" },
  "inbounds": [
    {
      "tag": "VLESS TCP REALITY",
      "listen": "0.0.0.0",
      "port": 8443,
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
          "privateKey": "QOFCbmZ5tPZnFPQ2pFTfhL3lJ5F0d6oWJTaCsvJ8XW8",
          "publicKey": "5nsYvvqbbZYst338tC8tlwbY7NOyDq8X20xTjrYjOHU",
          "shortIds": ["5b830b742b92b05a"]
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
      { "type": "field", "ip": ["10.0.0.0/8","192.168.0.0/16"], "outboundTag": "BLOCK" },
      { "type": "field", "protocol": ["bittorrent"], "outboundTag": "BLOCK" }
    ]
  }
}`

func TestServerConfigParses(t *testing.T) {
	if _, err := serial.LoadJSONConfig(strings.NewReader(serverConfig)); err != nil {
		t.Fatalf("ядро отвергло конфигурацию ноды: %v", err)
	}
}
