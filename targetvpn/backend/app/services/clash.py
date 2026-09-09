"""Подписка в формате Clash (mihomo) — для клиентов вроде FlClash.

Ключи мы храним ссылками vless://, а mihomo принимает YAML. Здесь ссылки
разбираются и превращаются в конфигурацию с одной группой выбора.
"""
from __future__ import annotations

from urllib.parse import parse_qs, unquote, urlparse

# Локальные адреса мимо туннеля: без правил клиент уводит в прокси даже роутер.
DIRECT_RULES = [
    "GEOIP,PRIVATE,DIRECT,no-resolve",
    "IP-CIDR,10.0.0.0/8,DIRECT,no-resolve",
    "IP-CIDR,172.16.0.0/12,DIRECT,no-resolve",
    "IP-CIDR,192.168.0.0/16,DIRECT,no-resolve",
    "IP-CIDR,127.0.0.0/8,DIRECT,no-resolve",
]


def _quote(value: str) -> str:
    """YAML-строка в двойных кавычках."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def parse_vless(link: str, fallback_name: str) -> dict | None:
    parsed = urlparse(link.strip())
    if parsed.scheme != "vless" or not parsed.hostname or not parsed.username:
        return None

    query = {k: v[0] for k, v in parse_qs(parsed.query).items() if v}
    name = unquote(parsed.fragment) if parsed.fragment else ""
    return {
        "name": name or fallback_name,
        "server": parsed.hostname,
        "port": parsed.port or 443,
        "uuid": parsed.username,
        "network": query.get("type", "tcp"),
        "security": query.get("security", "reality"),
        "sni": query.get("sni", ""),
        "fingerprint": query.get("fp", "chrome"),
        "public_key": query.get("pbk", ""),
        "short_id": query.get("sid", ""),
        "flow": query.get("flow", ""),
    }


def render(links: list[str], group_name: str = "TargetVPN") -> str:
    """Собирает YAML-конфигурацию из ссылок. Пустой список — пустая подписка."""
    proxies = []
    for index, link in enumerate(links, start=1):
        node = parse_vless(link, f"{group_name} {index}")
        if node:
            proxies.append(node)

    lines: list[str] = [
        "# Подписка TargetVPN",
        "mixed-port: 7890",
        "allow-lan: false",
        "mode: rule",
        "log-level: warning",
        "ipv6: false",
        "dns:",
        "  enable: true",
        "  ipv6: false",
        "  enhanced-mode: fake-ip",
        "  nameserver:",
        "    - 1.1.1.1",
        "    - 8.8.8.8",
        "",
        "proxies:",
    ]

    names: list[str] = []
    for node in proxies:
        names.append(node["name"])
        lines += [
            f"  - name: {_quote(node['name'])}",
            "    type: vless",
            f"    server: {node['server']}",
            f"    port: {node['port']}",
            f"    uuid: {node['uuid']}",
            f"    network: {node['network']}",
            "    udp: true",
            "    tls: true",
            f"    servername: {_quote(node['sni'])}",
            f"    client-fingerprint: {node['fingerprint']}",
        ]
        if node["flow"]:
            lines.append(f"    flow: {node['flow']}")
        if node["security"] == "reality":
            lines += [
                "    reality-opts:",
                f"      public-key: {_quote(node['public_key'])}",
                f"      short-id: {_quote(node['short_id'])}",
            ]

    if not proxies:
        lines.append("  []")

    lines += ["", "proxy-groups:", f"  - name: {_quote(group_name)}", "    type: select",
              "    proxies:"]
    for name in names or ["DIRECT"]:
        lines.append(f"      - {_quote(name)}")

    lines += ["", "rules:"]
    lines += [f"  - {rule}" for rule in DIRECT_RULES]
    lines.append(f"  - MATCH,{group_name}")
    return "\n".join(lines) + "\n"
