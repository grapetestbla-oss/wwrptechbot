"""Страница подключения.

Telegram WebView не открывает ссылки нестандартных схем вроде `vless://` —
кнопка внутри мини-аппа просто ничего не делает. Поэтому мини-апп открывает
эту страницу в обычном браузере, а уже она передаёт ключ в установленный
клиент по его схеме.
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_session
from ..models import Device, User
from ..services import subs

router = APIRouter(tags=["connect"])

# Схемы популярных клиентов. Happ и Streisand — основной путь для iPhone,
# v2rayNG и Hiddify — для Android и десктопа.
CLIENTS = [
    ("Happ", "🍏", "iPhone, iPad, Android", "happ://add/{sub}"),
    ("v2rayNG", "🤖", "Android", "v2rayng://install-config?url={sub}"),
    ("Hiddify", "💻", "Android, Windows, macOS", "hiddify://install-config?url={sub}"),
    ("Streisand", "🍏", "iPhone, iPad", "streisand://import/{sub}"),
    ("V2Box", "🍏", "iPhone, iPad", "v2box://install-sub?url={sub}"),
]

PAGE = """<!DOCTYPE html>
<html lang="ru"><head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Подключение TargetVPN</title>
<style>
  body {{ margin:0; background:#0b0e14; color:#eef2ff; font:15px/1.45 -apple-system,
    BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
  .wrap {{ max-width:520px; margin:0 auto; padding:28px 18px 48px; }}
  h1 {{ font-size:22px; margin:0 0 6px; }}
  p.sub {{ color:#8b93a9; margin:0 0 22px; font-size:14px; }}
  a.client, button.copy {{ display:flex; align-items:center; gap:12px; width:100%;
    box-sizing:border-box; text-decoration:none; color:#eef2ff; cursor:pointer;
    background:rgba(255,255,255,.05); border:1px solid rgba(255,255,255,.09);
    border-radius:16px; padding:14px 16px; margin-bottom:10px; font-size:15px; }}
  a.client:first-of-type {{ background:linear-gradient(135deg,#5b8cff,#8b5cff); border:0; }}
  a.client span.emoji {{ font-size:20px; }}
  a.client small {{ display:block; color:#b9c0d4; font-size:12px; }}
  .key {{ background:rgba(0,0,0,.35); border:1px solid rgba(255,255,255,.08);
    border-radius:12px; padding:12px; font-family:ui-monospace,Menlo,monospace;
    font-size:11px; word-break:break-all; color:#8b93a9; margin:18px 0 10px; }}
  .hint {{ color:#8b93a9; font-size:12.5px; margin-top:18px; }}
</style></head>
<body><div class="wrap">
  <h1>Подключение TargetVPN</h1>
  <p class="sub">{device}Выберите приложение — ключ импортируется автоматически.
    Если приложение не установлено, сначала поставьте его из магазина.</p>
  {buttons}
  <div class="key" id="key">{sub_url}</div>
  <button class="copy" onclick="copyKey()">📋 Скопировать ссылку-подписку</button>
  <p class="hint">Ссылка-подписка сама обновляет ключи: при смене локации или
    перевыпуске ничего заново настраивать не нужно.</p>
</div>
<script>
function copyKey() {{
  const text = document.getElementById('key').textContent.trim();
  navigator.clipboard.writeText(text).then(
    () => alert('Ссылка скопирована'),
    () => alert(text)
  );
}}
</script>
</body></html>"""


@router.get("/connect/{token}", response_class=HTMLResponse)
async def connect_page(token: str, device: int | None = None,
                       session: AsyncSession = Depends(get_session)):
    user = (await session.execute(select(User).where(User.sub_token == token))).scalar_one_or_none()
    if user is None or user.is_banned:
        raise HTTPException(404, "Страница не найдена")
    sub = await subs.active_subscription(session, user)
    if sub is None:
        raise HTTPException(403, "Подписка неактивна")

    sub_url = f"{settings.public_base_url.rstrip('/')}/sub/{user.sub_token}"
    encoded = quote(sub_url, safe="")

    device_note = ""
    if device:
        row = (await session.execute(select(Device).where(
            Device.id == device, Device.user_id == user.id))).scalar_one_or_none()
        if row is not None:
            device_note = f"Устройство «{row.name}». "

    buttons = "\n".join(
        f'<a class="client" href="{scheme.format(sub=encoded)}">'
        f'<span class="emoji">{emoji}</span><span>{name}<small>{platforms}</small></span></a>'
        for name, emoji, platforms, scheme in CLIENTS
    )
    return HTMLResponse(PAGE.format(device=device_note, buttons=buttons, sub_url=sub_url))
