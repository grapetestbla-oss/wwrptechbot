package main

import (
	"encoding/json"
	"testing"
)

func TestBuildConfigFromRealityLink(t *testing.T) {
	link := "vless://11111111-2222-3333-4444-555555555555@node.example.com:8443" +
		"?type=tcp&security=reality&sni=www.microsoft.com&fp=chrome&pbk=PUBKEY&sid=ab12#TargetVPN"

	raw, err := buildConfig(link)
	if err != nil {
		t.Fatalf("ссылка не разобралась: %v", err)
	}

	var parsed struct {
		Outbounds []struct {
			Settings struct {
				Vnext []struct {
					Address string `json:"address"`
					Port    int    `json:"port"`
					Users   []struct {
						ID string `json:"id"`
					} `json:"users"`
				} `json:"vnext"`
			} `json:"settings"`
			StreamSettings struct {
				Security        string `json:"security"`
				RealitySettings struct {
					ServerName string `json:"serverName"`
					PublicKey  string `json:"publicKey"`
					ShortID    string `json:"shortId"`
				} `json:"realitySettings"`
			} `json:"streamSettings"`
		} `json:"outbounds"`
	}
	if err := json.Unmarshal(raw, &parsed); err != nil {
		t.Fatalf("получился невалидный JSON: %v", err)
	}

	proxy := parsed.Outbounds[0]
	if got := proxy.Settings.Vnext[0].Address; got != "node.example.com" {
		t.Errorf("адрес узла: получили %q", got)
	}
	if got := proxy.Settings.Vnext[0].Port; got != 8443 {
		t.Errorf("порт: получили %d", got)
	}
	if got := proxy.Settings.Vnext[0].Users[0].ID; got != "11111111-2222-3333-4444-555555555555" {
		t.Errorf("uuid: получили %q", got)
	}
	if proxy.StreamSettings.Security != "reality" {
		t.Errorf("режим: получили %q", proxy.StreamSettings.Security)
	}
	if proxy.StreamSettings.RealitySettings.PublicKey != "PUBKEY" ||
		proxy.StreamSettings.RealitySettings.ShortID != "ab12" ||
		proxy.StreamSettings.RealitySettings.ServerName != "www.microsoft.com" {
		t.Errorf("параметры reality разобраны неверно: %+v", proxy.StreamSettings.RealitySettings)
	}
}

func TestBuildConfigRejectsGarbage(t *testing.T) {
	if _, err := buildConfig("https://example.com"); err == nil {
		t.Error("ожидали отказ на ссылке не того протокола")
	}
}
