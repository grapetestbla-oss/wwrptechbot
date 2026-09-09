"""Ребрендинг FlClashX в TargetVPN.

Скрипт клонирует форк, заменяет имена, идентификаторы и иконки, после чего
проект собирается обычными средствами Flutter.

    python client/rebrand.py --work build/targetvpn-client

FlClashX и его предок FlClash распространяются под GPL-3.0. Мы обязаны:
сохранить лицензию и авторские заголовки, опубликовать исходники изменённой
версии и обозначить, что это модификация. Скрипт делает первое и третье,
публикация — на вашей стороне (репозиторий уже открытый).
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Windows-раннер печатает в cp1252 и падает на кириллице — переводим вывод в utf-8.
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

UPSTREAM = "https://github.com/pluralplay/FlClashX.git"
ROOT = Path(__file__).resolve().parents[1]

APP_NAME = "TargetVPN"
PACKAGE_ID = "us.targetvpn.app"
DART_NAME = "targetvpn"

# Что и на что меняем. Порядок важен: сначала длинные строки.
REPLACEMENTS = [
    ("com.follow.clashx", PACKAGE_ID),
    ("com.follow.clash", PACKAGE_ID),
    ("FlClashX", APP_NAME),
    ("FlClash", APP_NAME),
    ("flclashx", DART_NAME),
    ("flclash", DART_NAME),
    ("fl_clash", DART_NAME),
]

# Файлы, где замена осмысленна. Бинарники и лицензии не трогаем.
# Kotlin (.kt) и AIDL обязательны: без них имя пакета в gradle разъезжается
# с объявлениями package в исходниках и сборка Android падает.
SUFFIXES = {".dart", ".yaml", ".yml", ".gradle", ".kts", ".kt", ".java", ".aidl", ".xml",
            ".json", ".cpp", ".cc", ".h", ".cmake", ".txt", ".xcconfig", ".pbxproj",
            ".xcscheme", ".xib", ".swift", ".plist", ".rc", ".rs", ".pro", ".desktop",
            ".arb", ".properties", ".iss", ".sh", ".ps1"}
# Файлы без расширения, которые тоже надо переименовать.
EXTRA_NAMES = {"Makefile"}
# Каталоги верхнего уровня, которые не трогаем. Именно верхнего: "core" в корне
# — это исходники ядра на Go, а вот android/core уже наш модуль и его надо
# переименовать вместе со всеми.
SKIP_TOP_DIRS = {".git", "build", ".dart_tool", "core"}
SKIP_FILES = {"LICENSE", "LICENSE.md", "NOTICE"}

# Flutter из stable проверяет три минимума сразу — Gradle, AGP и Kotlin (это
# ровно то, что снимает флаг --android-skip-build-dependency-validation).
# Форк везёт Gradle 8.11.1, AGP 8.9.2 и Kotlin 2.2.0 — ниже порога все три,
# и сборка спотыкалась о следующий, едва поднимешь предыдущий. Версии
# согласованы между собой: AGP 8.11 требует Gradle не ниже 8.13.
GRADLE_VERSION = "8.14.3"
AGP_VERSION = "8.11.1"
KOTLIN_VERSION = "2.2.20"

# id плагина в блоке plugins -> версия, до которой поднимаем.
PLUGIN_VERSIONS = {
    "com.android.application": AGP_VERSION,
    "com.android.library": AGP_VERSION,
    "org.jetbrains.kotlin.android": KOTLIN_VERSION,
    "org.jetbrains.kotlin.plugin.parcelize": KOTLIN_VERSION,
    "org.jetbrains.kotlin.plugin.compose": KOTLIN_VERSION,
    "org.jetbrains.kotlin.plugin.serialization": KOTLIN_VERSION,
}


def skipped(work: Path, path: Path) -> bool:
    """True, если файл лежит в каталоге верхнего уровня, который мы не трогаем."""
    parts = path.relative_to(work).parts
    return bool(parts) and parts[0] in SKIP_TOP_DIRS


def run(command: list[str], cwd: Path | None = None) -> None:
    result = subprocess.run(command, cwd=cwd)
    if result.returncode != 0:
        raise SystemExit(f"Команда не выполнилась: {' '.join(command)}")


def clone(work: Path, ref: str) -> None:
    if work.exists():
        print(f"Каталог {work} уже есть — обновляем")
        run(["git", "fetch", "--depth", "1", "origin", ref], cwd=work)
        run(["git", "reset", "--hard", "FETCH_HEAD"], cwd=work)
        return
    work.parent.mkdir(parents=True, exist_ok=True)
    run(["git", "clone", "--depth", "1", "--branch", ref, UPSTREAM, str(work)])


def patch_text(work: Path) -> int:
    changed = 0
    for path in work.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in SUFFIXES and path.name not in EXTRA_NAMES:
            continue
        if path.name in SKIP_FILES or skipped(work, path):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        original = text
        for old, new in REPLACEMENTS:
            text = text.replace(old, new)
        if text != original:
            path.write_text(text, encoding="utf-8")
            changed += 1
    return changed


def bump_gradle(work: Path) -> list[str]:
    """Поднимает Gradle и AGP до минимумов, которых требует текущий Flutter."""
    done = []

    props = work / "android" / "gradle" / "wrapper" / "gradle-wrapper.properties"
    if props.is_file():
        text = props.read_text(encoding="utf-8")
        patched = re.sub(r"gradle-\d+(?:\.\d+)*-(all|bin)\.zip",
                         f"gradle-{GRADLE_VERSION}-\\1.zip", text)
        if patched != text:
            props.write_text(patched, encoding="utf-8")
            done.append(f"Gradle {GRADLE_VERSION}")

    for name in ("settings.gradle.kts", "settings.gradle", "build.gradle.kts", "build.gradle"):
        path = work / "android" / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        patched = text
        for plugin, version in PLUGIN_VERSIONS.items():
            # id("plugin") version "X" и id 'plugin' version 'X'
            pattern = (r"""(id\s*\(?\s*['"]""" + re.escape(plugin)
                       + r"""['"]\s*\)?\s+version\s+['"])[\d.]+(['"])""")
            patched = re.sub(pattern, r"\g<1>" + version + r"\g<2>", patched)
        patched = re.sub(r"(com\.android\.tools\.build:gradle:)[\d.]+",
                         r"\g<1>" + AGP_VERSION, patched)
        patched = re.sub(r"""(ext\.kotlin_version\s*=\s*['"])[\d.]+(['"])""",
                         r"\g<1>" + KOTLIN_VERSION + r"\g<2>", patched)
        if patched != text:
            path.write_text(patched, encoding="utf-8")
            done.append(f"AGP {AGP_VERSION} / Kotlin {KOTLIN_VERSION} ({name})")
    return done


def move_packages(work: Path) -> int:
    """Переносит исходники в каталог нового пакета.

    Kotlin разрешает любое расположение файлов, а компилятор AIDL — нет: он
    требует, чтобы путь совпадал с объявленным package, иначе падает с
    «should be declared in a file called ...». Поэтому com/follow/clashx
    переезжает в us/targetvpn/app во всех наборах исходников.
    """
    parts = PACKAGE_ID.split(".")
    moved = 0
    for old in ("clashx", "clash"):
        for src in sorted(work.rglob(f"com/follow/{old}")):
            if not src.is_dir() or skipped(work, src):
                continue
            root = src.parent.parent.parent  # каталог, в котором лежит com/
            dest = root.joinpath(*parts)
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, dest, dirs_exist_ok=True)
            shutil.rmtree(src)
            moved += 1
    # Убираем опустевшие com/follow и com.
    for name in ("com/follow", "com"):
        for leftover in sorted(work.rglob(name), key=lambda p: -len(p.parts)):
            if leftover.is_dir() and not skipped(work, leftover) and not any(leftover.iterdir()):
                leftover.rmdir()
    return moved


def replace_icons(work: Path) -> int:
    """Кладёт наш логотип вместо иконок форка."""
    source = ROOT / "miniapp" / "logo.png"
    if not source.is_file():
        print("Логотип не найден, иконки оставлены прежними")
        return 0

    try:
        from PIL import Image
    except ImportError:
        print("Pillow не установлен — иконки оставлены прежними (pip install pillow)")
        return 0

    logo = Image.open(source).convert("RGBA")
    replaced = 0
    for path in list(work.rglob("*.png")):
        if skipped(work, path):
            continue
        name = path.name.lower()
        if not any(marker in name for marker in ("ic_launcher", "app_icon", "logo", "icon")):
            continue
        try:
            with Image.open(path) as current:
                size = current.size
            logo.resize(size, Image.LANCZOS).save(path)
            replaced += 1
        except OSError:
            continue
    return replaced


MARKERS = ("com.follow.clash", "FlClash", "flclash", "fl_clash")


def find_leftovers(work: Path) -> list[str]:
    """Ищет недоделанный ребрендинг: молча собранный FlClashX нам не нужен."""
    found = []
    for path in work.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in SUFFIXES and path.name not in EXTRA_NAMES:
            continue
        if path.name in SKIP_FILES or skipped(work, path):
            continue
        if path.name == "TARGETVPN_CHANGES.md":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        rel = path.relative_to(work)
        if any(marker in text for marker in MARKERS):
            found.append(str(rel))
        elif "com/follow" in rel.as_posix():
            found.append(f"{rel} (старый путь пакета)")
    return found


def write_notice(work: Path, ref: str) -> None:
    """GPL требует обозначить, что это изменённая версия."""
    (work / "TARGETVPN_CHANGES.md").write_text(
        f"""# TargetVPN — сборка на основе FlClashX

Это изменённая версия проекта [FlClashX]({UPSTREAM}) (ветка/тег `{ref}`),
который сам является форком [FlClash](https://github.com/chen08209/FlClash).
Оба распространяются под лицензией GPL-3.0, она сохраняется и здесь.

## Что изменено

- название приложения и идентификатор пакета заменены на {APP_NAME}
  (`{PACKAGE_ID}`);
- иконки заменены на логотип TargetVPN;
- прочий код не менялся: ядро, логика и интерфейс остались авторскими.

Исходный код изменённой версии публикуется вместе с этим файлом, как того
требует GPL-3.0. Авторские права на исходный проект принадлежат его авторам.
""", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ребрендинг FlClashX в TargetVPN")
    parser.add_argument("--work", default="build/targetvpn-client", help="куда клонировать")
    parser.add_argument("--ref", default="main", help="ветка или тег форка")
    args = parser.parse_args()

    work = Path(args.work).resolve()
    clone(work, args.ref)
    files = patch_text(work)
    moved = move_packages(work)
    gradle = bump_gradle(work)
    icons = replace_icons(work)
    write_notice(work, args.ref)

    leftovers = find_leftovers(work)
    print(f"Готово: {work}")
    print(f"  файлов изменено: {files}")
    print(f"  иконок заменено: {icons}")
    print(f"  каталогов пакета перенесено: {moved}")
    for item in gradle:
        print(f"  поднято: {item}")
    if leftovers:
        print("  ВНИМАНИЕ, старые имена остались в файлах:")
        for item in leftovers[:20]:
            print(f"    {item}")
        raise SystemExit("Ребрендинг неполный — сборка получилась бы с чужим именем")
    print("Сборка: flutter build apk --release (или windows/linux/macos)")


if __name__ == "__main__":
    main()
