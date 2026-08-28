from functools import lru_cache
from typing import List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Конфигурация сервиса. Читается из .env (см. .env.example)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Telegram ---
    bot_token: str = Field(default="", alias="BOT_TOKEN")
    bot_username: str = Field(default="", alias="BOT_USERNAME")
    webapp_url: str = Field(default="http://localhost:8000/app/", alias="WEBAPP_URL")
    support_url: str = Field(default="https://t.me/", alias="SUPPORT_URL")

    # --- Права ---
    # Владелец сервиса: полный доступ, снять права нельзя.
    owner_id: int = Field(default=7824168810, alias="OWNER_ID")

    # --- Инфраструктура ---
    database_url: str = Field(default="sqlite+aiosqlite:///./targetvpn.db", alias="DATABASE_URL")
    api_base_url: str = Field(default="http://127.0.0.1:8000", alias="API_BASE_URL")
    public_base_url: str = Field(default="http://localhost:8000", alias="PUBLIC_BASE_URL")
    internal_secret: str = Field(default="change-me-internal", alias="INTERNAL_SECRET")
    jwt_secret: str = Field(default="change-me-jwt", alias="JWT_SECRET")
    jwt_ttl_hours: int = Field(default=12, alias="JWT_TTL_HOURS")

    # --- Marzban (VPN-нода) ---
    marzban_url: str = Field(default="", alias="MARZBAN_URL")
    marzban_username: str = Field(default="", alias="MARZBAN_USERNAME")
    marzban_password: str = Field(default="", alias="MARZBAN_PASSWORD")
    marzban_verify_ssl: bool = Field(default=True, alias="MARZBAN_VERIFY_SSL")
    # Инбаунды Xray, в которые добавляются юзеры: {"vless": ["VLESS TCP REALITY"]}
    marzban_inbounds: dict = Field(default_factory=lambda: {"vless": ["VLESS TCP REALITY"]},
                                   alias="MARZBAN_INBOUNDS")
    # Префикс имён пользователей на ноде, чтобы не конфликтовать с ручными юзерами.
    marzban_prefix: str = Field(default="tv", alias="MARZBAN_PREFIX")
    # Демо-режим: не ходить на ноду, генерировать фейковые ключи (для локальной разработки).
    demo_mode: bool = Field(default=False, alias="DEMO_MODE")

    # --- Платежи ---
    cryptobot_token: str = Field(default="", alias="CRYPTOBOT_TOKEN")
    cryptobot_api: str = Field(default="https://pay.crypt.bot/api", alias="CRYPTOBOT_API")
    cryptobot_asset: str = Field(default="USDT", alias="CRYPTOBOT_ASSET")
    # Курс рубля к активу CryptoBot и к звёздам (сколько рублей в единице).
    rub_per_usdt: float = Field(default=100.0, alias="RUB_PER_USDT")
    rub_per_star: float = Field(default=1.6, alias="RUB_PER_STAR")

    # LZT Market (lolz.live): оплата переводом на аккаунт с уникальным комментарием.
    lzt_token: str = Field(default="", alias="LZT_TOKEN")
    lzt_api: str = Field(default="https://prod-api.lzt.market", alias="LZT_API")
    lzt_user_id: int = Field(default=0, alias="LZT_USER_ID")
    lzt_username: str = Field(default="", alias="LZT_USERNAME")
    # Шаблон ссылки на перевод: {username}, {user_id}, {amount}, {comment}.
    lzt_transfer_url: str = Field(
        default="https://lzt.market/balance/transfer/?username={username}&amount={amount}&comment={comment}",
        alias="LZT_TRANSFER_URL")
    # Как часто опрашивать входящие переводы, секунд.
    lzt_poll_interval: int = Field(default=60, alias="LZT_POLL_INTERVAL")

    # --- Прочее ---
    trial_enabled: bool = Field(default=True, alias="TRIAL_ENABLED")
    referral_bonus_days: int = Field(default=7, alias="REFERRAL_BONUS_DAYS")
    cors_origins: List[str] = Field(default_factory=lambda: ["*"], alias="CORS_ORIGINS")

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v):
        if isinstance(v, str):
            return [x.strip() for x in v.split(",") if x.strip()]
        return v

    @field_validator("marzban_inbounds", mode="before")
    @classmethod
    def _parse_inbounds(cls, v):
        if isinstance(v, str) and v.strip():
            import json
            return json.loads(v)
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
