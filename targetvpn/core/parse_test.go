package targetcore

import (
	"strings"
	"testing"

	"github.com/xtls/xray-core/infra/conf/serial"
)

// Конфигурация ровно в том виде, как её собирает XrayConfig.kt.
const generated = `{
 "log":{"loglevel":"warning"},
 "dns":{"servers":["1.1.1.1","8.8.8.8"],"queryStrategy":"UseIPv4"},
 "inbounds":[{"tag":"socks-in","listen":"127.0.0.1","port":10808,"protocol":"socks",
   "settings":{"udp":true,"auth":"noauth","ip":"127.0.0.1"},
   "sniffing":{"enabled":true,"destOverride":["http","tls","quic"]}}],
 "outbounds":[
  {"tag":"proxy","protocol":"vless",
   "settings":{"vnext":[{"address":"77.110.96.66","port":8443,
     "users":[{"id":"ca88e9f3-e81e-419f-b678-3407c7fcf531","encryption":"none"}]}]},
   "streamSettings":{"network":"tcp","security":"reality",
     "realitySettings":{"serverName":"www.microsoft.com","fingerprint":"chrome",
       "publicKey":"5nsYvvqbbZYst338tC8tlwbY7NOyDq8X20xTjrYjOHU",
       "shortId":"5b830b742b92b05a","spiderX":""}}},
  {"tag":"direct","protocol":"freedom"},
  {"tag":"block","protocol":"blackhole"},
  {"tag":"dns-out","protocol":"dns"}],
 "routing":{"domainStrategy":"IPIfNonMatch","rules":[
   {"type":"field","port":53,"outboundTag":"dns-out"},
   {"type":"field","ip":["10.0.0.0/8","172.16.0.0/12","192.168.0.0/16","127.0.0.0/8",
     "169.254.0.0/16","224.0.0.0/4","::1/128","fc00::/7","fe80::/10"],
    "outboundTag":"direct"}]}
}`

func TestGeneratedConfigParses(t *testing.T) {
	if _, err := serial.LoadJSONConfig(strings.NewReader(generated)); err != nil {
		t.Fatalf("ядро отвергло конфигурацию: %v", err)
	}
}
