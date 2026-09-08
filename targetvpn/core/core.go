// Пакет targetcore собирается в AAR и даёт приложению один туннель:
// Xray поднимается внутри процесса, а трафик TUN-интерфейса Android
// заворачивается в него через tun2socks. Стороннего клиента не нужно.
package targetcore

import (
	"errors"
	"fmt"
	"strings"
	"sync"
	"time"

	"github.com/xjasonlyu/tun2socks/v2/engine"
	t2slog "github.com/xjasonlyu/tun2socks/v2/log"
	xcore "github.com/xtls/xray-core/core"
	"github.com/xtls/xray-core/infra/conf/serial"
	_ "github.com/xtls/xray-core/main/distro/all"
	"go.uber.org/zap"
	"go.uber.org/zap/zapcore"
)

const (
	// Локальный вход Xray, в который tun2socks отдаёт перехваченный трафик.
	socksAddress = "127.0.0.1"
	socksPort    = 10808
)

var (
	mu       sync.Mutex
	instance *xcore.Instance
	running  bool
)

func init() {
	// tun2socks на ошибке старта зовёт Fatal, а zap по умолчанию завершает
	// процесс — для приложения это выглядело бы как вылет. Ставим логгер,
	// который вместо этого гасит только свою горутину.
	if logger, err := t2slog.NewLeveled(t2slog.WarnLevel,
		zap.WithFatalHook(zapcore.WriteThenGoexit)); err == nil {
		t2slog.SetLogger(logger)
	}
}

// startEngine запускает туннель и отличает успешный старт от падения.
func startEngine() error {
	outcome := make(chan bool, 1)
	go func() {
		ok := false
		// При Fatal внутри tun2socks горутина завершается, но defer сработает.
		defer func() { outcome <- ok }()
		engine.Start()
		ok = true
	}()

	select {
	case ok := <-outcome:
		if !ok {
			return errors.New("туннель не поднялся, подробности в логах ядра")
		}
	case <-time.After(10 * time.Second):
		// Start уже развернул обработчики и просто не вернул управление.
	}
	return nil
}

// Start поднимает ядро и туннель. tunFd — дескриптор от VpnService,
// configJSON — конфигурация Xray с локальным входом socks.
func Start(tunFd int, configJSON string, mtu int) error {
	mu.Lock()
	defer mu.Unlock()

	if running {
		return errors.New("туннель уже запущен")
	}
	if tunFd <= 0 {
		return errors.New("некорректный дескриптор TUN")
	}
	if mtu <= 0 {
		mtu = 1500
	}

	config, err := serial.LoadJSONConfig(strings.NewReader(configJSON))
	if err != nil {
		return fmt.Errorf("конфигурация Xray не разобрана: %w", err)
	}

	inst, err := xcore.New(config)
	if err != nil {
		return fmt.Errorf("ядро не создано: %w", err)
	}
	if err := inst.Start(); err != nil {
		return fmt.Errorf("ядро не запустилось: %w", err)
	}

	key := &engine.Key{
		Device:   fmt.Sprintf("fd://%d", tunFd),
		Proxy:    fmt.Sprintf("socks5://%s:%d", socksAddress, socksPort),
		LogLevel: "warning",
		MTU:      mtu,
	}
	engine.Insert(key)
	if err := startEngine(); err != nil {
		_ = inst.Close()
		return err
	}

	instance = inst
	running = true
	return nil
}

// Stop гасит туннель и ядро. Вызывать при остановке VpnService.
func Stop() {
	mu.Lock()
	defer mu.Unlock()

	if !running {
		return
	}
	engine.Stop()
	if instance != nil {
		_ = instance.Close()
		instance = nil
	}
	running = false
}

// IsRunning нужен приложению, чтобы восстановить состояние кнопки.
func IsRunning() bool {
	mu.Lock()
	defer mu.Unlock()
	return running
}

// Version отдаёт версию ядра — показываем в интерфейсе.
func Version() string {
	return xcore.Version()
}
