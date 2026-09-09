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
import subprocess
import sys
from pathlib import Path

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
SUFFIXES = {".dart", ".yaml", ".yml", ".gradle", ".kts", ".xml", ".json", ".cpp", ".h",
            ".cmake", ".txt", ".xcconfig", ".pbxproj", ".plist", ".rc", ".desktop", ".arb",
            ".properties", ".iss", ".sh", ".ps1"}
SKIP_DIRS = {".git", "build", ".dart_tool", "core"}
SKIP_FILES = {"LICENSE", "LICENSE.md", "NOTICE"}


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
        if not path.is_file() or path.suffix not in SUFFIXES:
            continue
        if path.name in SKIP_FILES or any(part in SKIP_DIRS for part in path.parts):
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
        if any(part in SKIP_DIRS for part in path.parts):
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
    icons = replace_icons(work)
    write_notice(work, args.ref)

    print(f"Готово: {work}")
    print(f"  файлов изменено: {files}")
    print(f"  иконок заменено: {icons}")
    print("Сборка: flutter build apk --release (или windows/linux/macos)")


if __name__ == "__main__":
    main()
