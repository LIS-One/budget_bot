"""Настройки бота из переменных окружения (.env)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# python-telegram-bot нумерует дни недели как cron: 0 = воскресенье ... 6 = суббота.
WEEKDAYS = {
    "sun": 0, "вс": 0, "воскресенье": 0,
    "mon": 1, "пн": 1, "понедельник": 1,
    "tue": 2, "вт": 2, "вторник": 2,
    "wed": 3, "ср": 3, "среда": 3,
    "thu": 4, "чт": 4, "четверг": 4,
    "fri": 5, "пт": 5, "пятница": 5,
    "sat": 6, "сб": 6, "суббота": 6,
}
WEEKDAY_NAMES_PO = ["воскресеньям", "понедельникам", "вторникам", "средам", "четвергам", "пятницам", "субботам"]


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    bot_token: str
    data_dir: Path
    timezone: ZoneInfo
    currency: str
    summary_weekday: int  # 0 = воскресенье (как в JobQueue.run_daily)
    summary_time: time
    daily_time: time
    allowed_user_ids: frozenset[int]
    log_level: str = "INFO"

    @property
    def summary_days(self) -> str:
        """«по воскресеньям» — когда приходит еженедельная сводка."""
        return f"по {WEEKDAY_NAMES_PO[self.summary_weekday]}"

    def is_allowed(self, user_id: int) -> bool:
        return not self.allowed_user_ids or user_id in self.allowed_user_ids


def _parse_time(value: str, name: str) -> time:
    try:
        hours, minutes = value.strip().split(":")
        return time(int(hours), int(minutes))
    except (ValueError, TypeError) as exc:
        raise ConfigError(f"{name} должно быть в формате ЧЧ:ММ, получено {value!r}") from exc


def _parse_ids(value: str) -> frozenset[int]:
    ids = set()
    for part in value.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if not part.lstrip("-").isdigit():
            raise ConfigError(f"ALLOWED_USER_IDS: {part!r} — не число")
        ids.add(int(part))
    return frozenset(ids)


def load_config(env_file: str | os.PathLike | None = ".env", require_token: bool = True) -> Config:
    if env_file:
        try:
            from dotenv import load_dotenv

            load_dotenv(env_file)
        except ImportError:  # python-dotenv не обязателен, если переменные заданы окружением
            pass

    token = os.getenv("BOT_TOKEN", "").strip()
    if require_token and not token:
        raise ConfigError("Не задан BOT_TOKEN. Скопируй .env.example в .env и впиши токен от @BotFather.")

    tz_name = os.getenv("TIMEZONE", "Asia/Jerusalem").strip()
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError as exc:
        raise ConfigError(f"Неизвестный часовой пояс TIMEZONE={tz_name!r}") from exc

    weekday_raw = os.getenv("SUMMARY_WEEKDAY", "sun").strip().lower()
    if weekday_raw not in WEEKDAYS:
        raise ConfigError(f"SUMMARY_WEEKDAY: ожидается sun/mon/.../sat, получено {weekday_raw!r}")

    return Config(
        bot_token=token,
        data_dir=Path(os.getenv("DATA_DIR", "data")).expanduser().resolve(),
        timezone=tz,
        currency=os.getenv("CURRENCY", "₪").strip() or "₪",
        summary_weekday=WEEKDAYS[weekday_raw],
        summary_time=_parse_time(os.getenv("SUMMARY_TIME", "09:00"), "SUMMARY_TIME"),
        daily_time=_parse_time(os.getenv("DAILY_JOB_TIME", "08:00"), "DAILY_JOB_TIME"),
        allowed_user_ids=_parse_ids(os.getenv("ALLOWED_USER_IDS", "")),
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
    )
