// Клиент TargetVPN для Windows.
//
// Привязывается к компьютеру по HWID, забирает ключ подключения с сервера,
// поднимает ядро Xray локально и переключает системный прокси на него.
package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"
	"time"
)

var appVersion = "1.0.0"

type config struct {
	Token      string `json:"token"`
	Config     string `json:"config"`
	DeviceName string `json:"device_name"`
	Location   string `json:"location"`
}

func configDir() string {
	base := os.Getenv("APPDATA")
	if base == "" {
		base, _ = os.UserHomeDir()
	}
	return filepath.Join(base, "TargetVPN")
}

func loadConfig() config {
	var stored config
	data, err := os.ReadFile(filepath.Join(configDir(), "config.json"))
	if err == nil {
		_ = json.Unmarshal(data, &stored)
	}
	return stored
}

func saveConfig(stored config) {
	_ = os.MkdirAll(configDir(), 0o755)
	data, _ := json.MarshalIndent(stored, "", "  ")
	_ = os.WriteFile(filepath.Join(configDir(), "config.json"), data, 0o600)
}

func main() {
	fmt.Println("TargetVPN " + appVersion)
	fmt.Println("Сервер:", apiBase)
	fmt.Println()

	stored := loadConfig()
	reader := bufio.NewReader(os.Stdin)

	if stored.Token == "" {
		if !bindDevice(reader, &stored) {
			pause(reader)
			return
		}
	}

	current, err := apiState(stored.Token)
	if err != nil {
		fmt.Println("Ошибка:", err)
		if strings.Contains(err.Error(), "привязк") || strings.Contains(err.Error(), "устройство") {
			// Привязку сняли — начинаем с ввода нового кода.
			stored = config{}
			saveConfig(stored)
			if !bindDevice(reader, &stored) {
				pause(reader)
				return
			}
			current, err = apiState(stored.Token)
		}
		if err != nil {
			pause(reader)
			return
		}
	}

	if !current.Active || current.SecondsLeft <= 0 {
		fmt.Println("Подписка неактивна.", current.Message)
		fmt.Println("Продлите её в Telegram-боте и запустите приложение снова.")
		pause(reader)
		return
	}

	fmt.Printf("Подписка активна: %s\n", current.PlanTitle)
	fmt.Printf("Осталось: %s\n", humanLeft(current.SecondsLeft))
	if current.Location != "" {
		fmt.Println("Локация:", current.Location)
	}
	fmt.Println()

	key := current.Config
	if fresh, err := apiConfig(stored.Token); err == nil && fresh != "" {
		key = fresh
	}
	stored.Config = key
	stored.DeviceName = current.DeviceName
	stored.Location = current.Location
	saveConfig(stored)

	if err := connect(key); err != nil {
		fmt.Println("Не удалось подключиться:", err)
		pause(reader)
	}
}

func bindDevice(reader *bufio.Reader, stored *config) bool {
	fmt.Println("Устройство не привязано.")
	fmt.Println("Откройте мини-приложение TargetVPN в Telegram, нажмите")
	fmt.Println("«Показать код привязки» и введите код ниже.")
	fmt.Print("\nКод: ")

	code, _ := reader.ReadString('\n')
	result, err := apiBind(code)
	if err != nil {
		fmt.Println("Ошибка:", err)
		return false
	}

	stored.Token = result.Token
	stored.Config = result.Config
	stored.DeviceName = result.DeviceName
	stored.Location = result.Location
	saveConfig(*stored)
	fmt.Println("Устройство привязано:", result.DeviceName)
	fmt.Println()
	return true
}

func connect(key string) error {
	dir := filepath.Join(configDir(), "core")
	binary, err := ensureXray(dir)
	if err != nil {
		return err
	}

	configJSON, err := buildConfig(key)
	if err != nil {
		return err
	}
	configPath := filepath.Join(dir, "config.json")
	if err := os.WriteFile(configPath, configJSON, 0o600); err != nil {
		return err
	}

	core := exec.Command(binary, "run", "-c", configPath)
	core.Stdout = os.Stdout
	core.Stderr = os.Stderr
	if err := core.Start(); err != nil {
		return err
	}
	// Ядру нужно мгновение, чтобы поднять локальные входы.
	time.Sleep(2 * time.Second)

	if err := setSystemProxy(true); err != nil {
		fmt.Println("Не удалось включить системный прокси:", err)
		fmt.Printf("Настройте вручную: HTTP-прокси 127.0.0.1:%d\n", httpPort)
	} else {
		fmt.Println("Подключено. Весь трафик идёт через TargetVPN.")
	}
	fmt.Printf("SOCKS5: 127.0.0.1:%d, HTTP: 127.0.0.1:%d\n", socksPort, httpPort)
	fmt.Println("\nЗакройте это окно или нажмите Ctrl+C, чтобы отключиться.")

	stop := make(chan os.Signal, 1)
	signal.Notify(stop, os.Interrupt, syscall.SIGTERM)
	<-stop

	fmt.Println("\nОтключаемся…")
	_ = setSystemProxy(false)
	_ = core.Process.Kill()
	return nil
}

func humanLeft(seconds int64) string {
	days := seconds / 86400
	hours := (seconds % 86400) / 3600
	minutes := (seconds % 3600) / 60
	switch {
	case days > 0:
		return fmt.Sprintf("%d дн. %d ч.", days, hours)
	case hours > 0:
		return fmt.Sprintf("%d ч. %d мин.", hours, minutes)
	default:
		return fmt.Sprintf("%d мин.", minutes)
	}
}

func pause(reader *bufio.Reader) {
	fmt.Print("\nНажмите Enter, чтобы закрыть…")
	_, _ = reader.ReadString('\n')
}
