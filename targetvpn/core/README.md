# targetcore — ядро туннеля

Модуль на Go, который собирается в `targetcore.aar` для Android. Внутри:

- **Xray-core** — протокол VLESS + Reality, тот же, что на сервере;
- **tun2socks** — заворачивает трафик TUN-интерфейса в локальный вход ядра.

Наружу торчат четыре функции: `Start(tunFd, configJSON, mtu)`, `Stop()`,
`IsRunning()`, `Version()`.

## Сборка AAR

```bash
export ANDROID_NDK_HOME=$ANDROID_HOME/ndk/26.1.10909125
go install golang.org/x/mobile/cmd/gomobile@latest
gomobile init
gomobile bind -target=android/arm64,android/arm -androidapi 24 \
  -ldflags "-s -w" -o ../android/app/libs/targetcore.aar .
```

Собранный AAR лежит в `android/app/libs/` и коммитится вместе с кодом:
пересобирать его нужно только при обновлении ядра.

## Тесты

`go test ./...` прогоняет конфигурацию через настоящий парсер Xray. Тест ловит
ошибки, которые иначе видно только на телефоне: например, правило
`geoip:private` требует файла `geoip.dat`, которого в APK нет, и ядро
отказывалось стартовать.

## Про Fatal в tun2socks

`engine.Start()` при ошибке вызывает `log.Fatal`, а zap по умолчанию завершает
процесс — приложение выглядело бы как вылетевшее. Поэтому в `init()`
подменяется логгер на вариант с `zap.WithFatalHook(zapcore.WriteThenGoexit)`,
а сам старт выполняется в отдельной горутине, чтобы отличить успех от падения.
