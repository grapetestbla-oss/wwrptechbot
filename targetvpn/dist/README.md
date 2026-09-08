# Готовые сборки

Файлы отсюда попадают на сервер вместе с кодом: установщик копирует их в
`/opt/targetvpn/data/downloads` и прописывает ссылки в настройках, после чего
в мини-аппе и в боте появляются кнопки скачивания.

| Файл | Платформа | Чем собрано |
|---|---|---|
| `targetvpn-1.1.0.apk` | Android 7+ | Gradle, `-PapiBase=https://rustplus-steamconnect.us` |
| `targetvpn-1.0.0.exe` | Windows 10/11 x64 | Go, `-X main.apiBase=https://rustplus-steamconnect.us` |

Адрес сервера зашит в сборку, поэтому при смене домена файлы нужно пересобрать:
GitHub Actions → «Build TargetVPN APK» и «Build TargetVPN EXE».

Сборки под iOS здесь нет: `.ipa` собирается только на macOS (Actions →
«Build TargetVPN IPA»), а для установки на телефон нужен сайдлоад или
TestFlight — см. `../ios/README.md`.

Опубликовать файл вручную:

```bash
cd /opt/targetvpn
.venv/bin/python scripts/publish_build.py dist/targetvpn-1.0.0.apk --version 1.0.0
```
