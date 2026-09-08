package main

import (
	"archive/zip"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

const (
	xrayRelease = "https://github.com/XTLS/Xray-core/releases/latest/download/Xray-windows-64.zip"
	socksPort   = 10808
	httpPort    = 10809
)

// ensureXray скачивает ядро Xray при первом запуске: класть 30 МБ бинарника
// в установщик незачем, обновлять его так тоже проще.
func ensureXray(dir string) (string, error) {
	binary := filepath.Join(dir, "xray.exe")
	if _, err := os.Stat(binary); err == nil {
		return binary, nil
	}

	fmt.Println("Скачиваем ядро Xray (около 30 МБ, только при первом запуске)…")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return "", err
	}

	client := &http.Client{Timeout: 10 * time.Minute}
	resp, err := client.Get(xrayRelease)
	if err != nil {
		return "", fmt.Errorf("не удалось скачать ядро: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("не удалось скачать ядро: код %d", resp.StatusCode)
	}

	archivePath := filepath.Join(dir, "xray.zip")
	file, err := os.Create(archivePath)
	if err != nil {
		return "", err
	}
	if _, err := io.Copy(file, resp.Body); err != nil {
		file.Close()
		return "", err
	}
	file.Close()

	if err := unzip(archivePath, dir); err != nil {
		return "", err
	}
	os.Remove(archivePath)

	if _, err := os.Stat(binary); err != nil {
		return "", fmt.Errorf("в архиве нет xray.exe")
	}
	return binary, nil
}

func unzip(archivePath, dir string) error {
	reader, err := zip.OpenReader(archivePath)
	if err != nil {
		return err
	}
	defer reader.Close()

	for _, entry := range reader.File {
		if entry.FileInfo().IsDir() || strings.Contains(entry.Name, "..") {
			continue
		}
		source, err := entry.Open()
		if err != nil {
			return err
		}
		target, err := os.Create(filepath.Join(dir, filepath.Base(entry.Name)))
		if err != nil {
			source.Close()
			return err
		}
		_, err = io.Copy(target, source)
		source.Close()
		target.Close()
		if err != nil {
			return err
		}
	}
	return nil
}

// buildConfig превращает ссылку vless:// в конфигурацию Xray с локальными
// входами SOCKS и HTTP — их и слушает системный прокси.
func buildConfig(link string) ([]byte, error) {
	parsed, err := url.Parse(strings.TrimSpace(link))
	if err != nil || parsed.Scheme != "vless" {
		return nil, fmt.Errorf("ключ подключения не распознан")
	}
	query := parsed.Query()
	port := parsed.Port()
	if port == "" {
		port = "443"
	}

	stream := map[string]any{
		"network":  valueOr(query.Get("type"), "tcp"),
		"security": valueOr(query.Get("security"), "reality"),
	}
	if stream["security"] == "reality" {
		stream["realitySettings"] = map[string]any{
			"serverName":  query.Get("sni"),
			"fingerprint": valueOr(query.Get("fp"), "chrome"),
			"publicKey":   query.Get("pbk"),
			"shortId":     query.Get("sid"),
			"spiderX":     query.Get("spx"),
		}
	} else if stream["security"] == "tls" {
		stream["tlsSettings"] = map[string]any{
			"serverName":  query.Get("sni"),
			"fingerprint": valueOr(query.Get("fp"), "chrome"),
		}
	}

	config := map[string]any{
		"log": map[string]any{"loglevel": "warning"},
		"inbounds": []any{
			map[string]any{
				"tag": "socks", "listen": "127.0.0.1", "port": socksPort,
				"protocol": "socks",
				"settings": map[string]any{"udp": true},
			},
			map[string]any{
				"tag": "http", "listen": "127.0.0.1", "port": httpPort,
				"protocol": "http",
			},
		},
		"outbounds": []any{
			map[string]any{
				"tag":      "proxy",
				"protocol": "vless",
				"settings": map[string]any{
					"vnext": []any{map[string]any{
						"address": parsed.Hostname(),
						"port":    atoi(port, 443),
						"users": []any{map[string]any{
							"id":         parsed.User.Username(),
							"encryption": "none",
							"flow":       query.Get("flow"),
						}},
					}},
				},
				"streamSettings": stream,
			},
			map[string]any{"tag": "direct", "protocol": "freedom"},
		},
	}
	return json.MarshalIndent(config, "", "  ")
}

func valueOr(value, fallback string) string {
	if strings.TrimSpace(value) == "" {
		return fallback
	}
	return value
}

func atoi(value string, fallback int) int {
	result := 0
	for _, symbol := range value {
		if symbol < '0' || symbol > '9' {
			return fallback
		}
		result = result*10 + int(symbol-'0')
	}
	if result == 0 {
		return fallback
	}
	return result
}

// setSystemProxy включает системный прокси Windows на локальный вход Xray.
func setSystemProxy(enabled bool) error {
	const key = `HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings`
	value := "0"
	if enabled {
		value = "1"
		if err := exec.Command("reg", "add", key, "/v", "ProxyServer", "/t", "REG_SZ",
			"/d", fmt.Sprintf("127.0.0.1:%d", httpPort), "/f").Run(); err != nil {
			return err
		}
		if err := exec.Command("reg", "add", key, "/v", "ProxyOverride", "/t", "REG_SZ",
			"/d", "localhost;127.*;10.*;172.16.*;192.168.*;<local>", "/f").Run(); err != nil {
			return err
		}
	}
	return exec.Command("reg", "add", key, "/v", "ProxyEnable", "/t", "REG_DWORD",
		"/d", value, "/f").Run()
}
