"""Публикация сборки приложения: файл кладётся в раздачу, ссылка идёт в настройки.

    python scripts/publish_build.py dist/targetvpn-1.0.0.apk --version 1.0.0

Готовый релиз с GitHub можно подключить ссылкой, не скачивая файл:

    python scripts/publish_build.py TargetVPN.dmg --version 1.0.0 \\
        --url https://github.com/<owner>/<repo>/releases/download/client-1/TargetVPN.dmg

Платформа определяется по расширению: .apk — Android, .ipa — iOS,
.exe/.msi/.zip — Windows, .dmg — macOS, .AppImage/.deb — Linux.
"""
from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.services import settings_store  # noqa: E402

PLATFORMS = {
    ".apk": ("android", "apk_url", "apk_version"),
    ".ipa": ("ios", "ios_url", "ios_version"),
    ".exe": ("windows", "windows_url", "windows_version"),
    ".msi": ("windows", "windows_url", "windows_version"),
    ".zip": ("windows", "windows_url", "windows_version"),
    ".dmg": ("macos", "macos_url", "macos_version"),
    ".appimage": ("linux", "linux_url", "linux_version"),
    ".deb": ("linux", "linux_url", "linux_version"),
}


async def main() -> None:
    parser = argparse.ArgumentParser(description="Опубликовать сборку приложения")
    parser.add_argument("file", help="путь к файлу сборки (или его имя вместе с --url)")
    parser.add_argument("--version", default="", help="версия для подписи кнопки")
    parser.add_argument("--downloads-dir", default=str(ROOT / "data" / "downloads"))
    parser.add_argument("--base-url", default="", help="по умолчанию PUBLIC_BASE_URL из .env")
    parser.add_argument("--url", default="",
                        help="готовая ссылка (например, ассет релиза GitHub); "
                             "файл тогда никуда не копируется")
    args = parser.parse_args()

    source = Path(args.file).expanduser()
    if not args.url:
        source = source.resolve()
        if not source.is_file():
            raise SystemExit(f"Файл не найден: {source}")

    platform = PLATFORMS.get(source.suffix.lower())
    if platform is None:
        raise SystemExit(f"Неизвестный тип файла: {source.suffix}. "
                         f"Поддерживаются: {', '.join(PLATFORMS)}")
    _, url_key, version_key = platform

    if args.url:
        await init_db()
        async with SessionLocal() as session:
            await settings_store.set_value(session, url_key, args.url)
            if args.version:
                await settings_store.set_value(session, version_key, args.version)
            await session.commit()
        print(f"Ссылка в мини-аппе и боте: {args.url}")
        return

    target_dir = Path(args.downloads_dir).expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / source.name
    if source != target:
        shutil.copy2(source, target)
        target.chmod(0o644)

    base = (args.base_url or settings.public_base_url).rstrip("/")
    url = f"{base}/downloads/{source.name}"

    await init_db()
    async with SessionLocal() as session:
        await settings_store.set_value(session, url_key, url)
        if args.version:
            await settings_store.set_value(session, version_key, args.version)
        await session.commit()

    size_mb = target.stat().st_size / 1024 ** 2
    print(f"Опубликовано: {target.name} ({size_mb:.1f} МБ)")
    print(f"Ссылка в мини-аппе: {url}")


if __name__ == "__main__":
    asyncio.run(main())
