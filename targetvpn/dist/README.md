# Готовые сборки

Файлы отсюда попадают на сервер вместе с кодом: установщик копирует их в
`/opt/targetvpn/data/downloads` и прописывает ссылки в настройках, после чего
в мини-аппе и в боте появляются кнопки скачивания.

| Файл | Платформа | Чем собрано |
|---|---|---|
| `targetvpn-2.2.0.apk` | Android 7+, arm64 | Gradle, `-PapiBase=https://rustplus-steamconnect.us` |
| `targetvpn-1.0.0.exe` | Windows 10/11 x64 | Go, `-X main.apiBase=https://rustplus-steamconnect.us` |

В APK встроено ядро (Xray + tun2socks), поэтому файл весит около 42 МБ. Здесь
лежит сборка под arm64 — это все современные телефоны. Вариант под старые
armeabi-v7a собирается тем же workflow и лежит в его артефактах.

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
