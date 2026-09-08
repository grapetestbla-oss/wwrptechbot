package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

// apiBase подставляется при сборке: -ldflags "-X main.apiBase=https://домен"
var apiBase = "https://example.com"

var httpClient = &http.Client{Timeout: 20 * time.Second}

type state struct {
	Active      bool   `json:"active"`
	DeviceName  string `json:"device_name"`
	Location    string `json:"location"`
	PlanTitle   string `json:"plan_title"`
	SecondsLeft int64  `json:"seconds_left"`
	Config      string `json:"config"`
	Message     string `json:"message"`
}

type bindResponse struct {
	Token       string `json:"token"`
	DeviceName  string `json:"device_name"`
	Config      string `json:"config"`
	Location    string `json:"location"`
	SecondsLeft int64  `json:"seconds_left"`
}

type apiError struct {
	Detail string `json:"detail"`
}

func request(method, path, token string, body any) ([]byte, error) {
	var payload io.Reader
	if body != nil {
		encoded, err := json.Marshal(body)
		if err != nil {
			return nil, err
		}
		payload = bytes.NewReader(encoded)
	}

	req, err := http.NewRequest(method, strings.TrimRight(apiBase, "/")+path, payload)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-HWID", hwid())
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}

	resp, err := httpClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("нет связи с сервером: %w", err)
	}
	defer resp.Body.Close()

	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}
	if resp.StatusCode >= 300 {
		var failure apiError
		_ = json.Unmarshal(data, &failure)
		if failure.Detail == "" {
			failure.Detail = fmt.Sprintf("ошибка сервера (%d)", resp.StatusCode)
		}
		return nil, fmt.Errorf("%s", failure.Detail)
	}
	return data, nil
}

func apiBind(code string) (*bindResponse, error) {
	data, err := request(http.MethodPost, "/api/client/bind", "", map[string]string{
		"code":        strings.ToUpper(strings.TrimSpace(code)),
		"hwid":        hwid(),
		"name":        hostName(),
		"model":       "Windows",
		"app_version": appVersion,
	})
	if err != nil {
		return nil, err
	}
	var result bindResponse
	if err := json.Unmarshal(data, &result); err != nil {
		return nil, err
	}
	return &result, nil
}

func apiState(token string) (*state, error) {
	data, err := request(http.MethodGet, "/api/client/state", token, nil)
	if err != nil {
		return nil, err
	}
	var result state
	if err := json.Unmarshal(data, &result); err != nil {
		return nil, err
	}
	return &result, nil
}

func apiConfig(token string) (string, error) {
	data, err := request(http.MethodGet, "/api/client/config", token, nil)
	if err != nil {
		return "", err
	}
	var result struct {
		Config string `json:"config"`
	}
	if err := json.Unmarshal(data, &result); err != nil {
		return "", err
	}
	return result.Config, nil
}
