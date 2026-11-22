from __future__ import annotations

from decimal import Decimal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    bot_token: str = ""

    channel_id: int = 0
    channel_public_link: str = ""
    channel_title: str = "канал"

    admin_ids: str = ""
    invite_ttl_hours: int = 24
    grace_hours: int = 0

    reminder_days_1: int = 3
    reminder_days_2: int = 1

    plan_1m_days: int = 30
    plan_1m_stars: int = 300
    plan_3m_days: int = 90
    plan_3m_stars: int = 750

    timezone: str = "Europe/Moscow"
    log_level: str = "INFO"
    database_url: str = ""

    @field_validator("channel_id", mode="before")
    @classmethod
    def _empty_channel_id(cls, value: object) -> object:
        """Пустое значение в .env не должно ломать запуск."""
        if isinstance(value, str) and not value.strip():
            return 0
        return value

    @field_validator("plan_1m_days", "plan_3m_days", "plan_1m_stars", "plan_3m_stars",
                     "invite_ttl_hours", "grace_hours", "reminder_days_1",
                     "reminder_days_2", mode="before")
    @classmethod
    def _empty_int(cls, value: object, info) -> object:
        if isinstance(value, str) and not value.strip():
            return 0
        return value

    @property
    def admin_id_list(self) -> list[int]:
        ids: list[int] = []
        for chunk in self.admin_ids.replace(";", ",").split(","):
            chunk = chunk.strip()
            if chunk:
                try:
                    ids.append(int(chunk))
                except ValueError:
                    continue
        return ids

    @property
    def db_url(self) -> str:
        return self.database_url or "sqlite+aiosqlite:///data/bot.db"

    def validate_startup(self) -> list[str]:
        problems: list[str] = []
        if not self.bot_token:
            problems.append("BOT_TOKEN не задан")
        if not self.channel_id:
            problems.append("CHANNEL_ID не задан")
        if not self.admin_id_list:
            problems.append("ADMIN_IDS пуст — админ-команды будут недоступны")
        if self.plan_1m_days <= 0 or self.plan_3m_days <= 0:
            problems.append("Срок тарифа должен быть больше 0 дней")
        if self.plan_1m_stars <= 0 or self.plan_3m_stars <= 0:
            problems.append("Цена в Stars должна быть больше 0")
        return problems


def format_days(days: int) -> str:
    """Склонение слова «день»."""
    if days % 10 == 1 and days % 100 != 11:
        return f"{days} день"
    if 2 <= days % 10 <= 4 and not 12 <= days % 100 <= 14:
        return f"{days} дня"
    return f"{days} дней"


def stars_to_decimal(amount: int) -> Decimal:
    return Decimal(amount)


settings = Settings()
