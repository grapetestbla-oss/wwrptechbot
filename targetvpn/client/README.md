# Клиент TargetVPN (на базе FlClashX)

Полноценный клиент на ядре **mihomo (Clash.Meta)** — ребрендированная сборка
[pluralplay/FlClashX](https://github.com/pluralplay/FlClashX).

## Что делает `rebrand.py`

Скрипт клонирует FlClashX во временную папку и переименовывает проект:

| Было | Стало |
|---|---|
| `com.follow.clashx`, `com.follow.clash` | `us.targetvpn.app` |
| `FlClashX`, `FlClash` | `TargetVPN` |
| `flclashx`, `flclash`, `fl_clash` | `targetvpn` |
| каталоги `com/follow/clashx/` | `us/targetvpn/app/` |
| иконки приложения | `miniapp/logo.png` |

Запуск локально:

```bash
pip install pillow
python targetvpn/client/rebrand.py --work build/client --ref main
cd build/client
flutter pub get
dart setup.dart android --arch arm64     # или linux/windows/macos
```

## Сборка релизов

GitHub Actions: **Actions → «Build TargetVPN client (FlClashX)» → Run workflow**.
Вход `release: true` — сразу опубликовать релиз с артефактами.

Матрица: `android` (.apk), `windows` (.exe), `linux` (.AppImage/.deb).

Помимо переименования скрипт поднимает сборочную цепочку Android до минимумов,
которых требует Flutter из stable, и в конце проверяет, что старых имён в файлах
сборки не осталось.

| Что | В форке | Ставим |
|---|---|---|
| Gradle | 8.11.1 | 8.14.3 |
| Android Gradle Plugin | 8.9.2 | 8.11.1 |
| Kotlin | 2.2.0 | 2.2.20 |

Flutter проверяет все три порога подряд, поэтому поднимать их надо вместе:
подняв один, упрёшься в следующий. Версии согласованы между собой — AGP 8.11
требует Gradle не ниже 8.13.

Каталоги исходников переносятся не для красоты: Kotlin разрешает файлам лежать
где угодно, а компилятор AIDL требует, чтобы путь совпадал с объявленным
`package`, иначе падает с «should be declared in a file called …».

## macOS

Сборщик форка упаковывает приложение в `.dmg` через `create-dmg`, а тот требует
подписи **Apple Developer ID**. Без неё сборка падает на шаге подписи, а
неподписанное приложение macOS всё равно не запустит. Поэтому macOS в матрице
нет: там подключаемся ссылкой-подпиской через Hiddify или Happ.

## iOS

**У FlClashX нет iOS-сборки** — в репозитории отсутствует каталог `ios/`,
ядро mihomo подключается через FFI, которое Apple не пропускает в том виде,
в каком его использует проект. Поэтому релиза под iPhone из этого исходника
сделать нельзя. Для iOS остаётся **Happ** — кнопка «Подключиться» на странице
`/connect/<token>` открывает подписку прямо в нём.

## Подписка

Клиент импортирует профиль по ссылке:

```
https://rustplus-steamconnect.us/sub/<sub_token>?format=clash
```

Тот же `/sub/<token>` отдаёт base64-список `vless://` всем остальным клиентам —
формат выбирается по параметру `?format=` или по User-Agent
(`clash`/`mihomo`/`flclash` → YAML).

## Лицензия (важно)

FlClashX распространяется под **GPL-3.0**. Из этого следуют обязательства:

1. Исходники нашей модифицированной версии должны быть доступны получателям
   сборок. Здесь это выполняется тем, что `rebrand.py` — воспроизводимый
   рецепт поверх публичного апстрима, и он лежит в репозитории.
2. Файл `LICENSE` в сборке не изменяется.
3. Скрипт создаёт `TARGETVPN_CHANGES.md` со списком изменений — этого требует
   GPL-3.0 §5(a) (пометка о модификации и дате).
4. Нельзя добавлять ограничения поверх GPL: сборки клиента раздаются
   свободно, платной является услуга VPN, а не программа.
