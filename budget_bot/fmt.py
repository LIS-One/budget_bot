"""Форматирование сумм, дат и прогресс-баров для сообщений."""

from __future__ import annotations

import html
import math
from datetime import date, datetime

NBSP = " "
MINUS = "−"

MONTHS_NOM = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь",
              "Октябрь", "Ноябрь", "Декабрь"]
MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября",
              "октября", "ноября", "декабря"]
MONTHS_PREP = ["январе", "феврале", "марте", "апреле", "мае", "июне", "июле", "августе", "сентябре",
               "октябре", "ноябре", "декабре"]

# Во сколько раз «типичные» суммы в других валютах отличаются от шекелей — для кнопок-подсказок.
CURRENCY_FACTORS = {"₪": 1.0, "$": 0.3, "€": 0.27, "£": 0.23, "₽": 25.0}
CURRENCIES = tuple(CURRENCY_FACTORS)


def esc(text: object) -> str:
    return html.escape(str(text), quote=False)


def fit(text: str, limit: int = 4000) -> str:
    """Telegram не принимает сообщения длиннее 4096 символов — обрезаем по строке."""
    if len(text) <= limit:
        return text
    cut = text.rfind("\n", 0, limit - 40)
    return text[: cut if cut > 0 else limit - 40] + "\n…и ещё — полный список в Excel (📁 Excel)"


def num(x: float) -> str:
    x = float(x or 0)
    neg = x < 0
    x = abs(x)
    if abs(x - round(x)) < 0.005:
        body = f"{round(x):,}".replace(",", NBSP)
    else:
        whole, frac = f"{x:,.2f}".split(".")
        body = whole.replace(",", NBSP) + "," + frac
    return (MINUS if neg else "") + body


def money(x: float, cur: str = "₪") -> str:
    return f"{num(x)}{NBSP}{cur}"


def signed(x: float, cur: str = "₪") -> str:
    return ("+" if x > 0 else "") + money(x, cur)


def pct(ratio: float) -> str:
    return f"{round(ratio * 100)}%"


def bar(ratio: float, width: int = 8) -> str:
    if ratio > 1:
        return "🟥" * width
    filled = max(0, min(width, round(ratio * width)))
    if ratio > 0 and filled == 0:
        filled = 1
    color = "🟩" if ratio < 0.75 else "🟨"
    return color * filled + "⬜" * (width - filled)


def plural(n: float, one: str, few: str, many: str) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def days(n: int) -> str:
    return f"{n} {plural(n, 'день', 'дня', 'дней')}"


def month_title(d: date) -> str:
    return f"{MONTHS_NOM[d.month - 1]} {d.year}"


def month_prep(d: date) -> str:
    return MONTHS_PREP[d.month - 1]


def day_month(d: date) -> str:
    return f"{d.day} {MONTHS_GEN[d.month - 1]}"


def dm(d: date) -> str:
    return f"{d.day:02d}.{d.month:02d}"


def dmy(d: date) -> str:
    return f"{d.day:02d}.{d.month:02d}.{d.year}"


def dmhm(dt: datetime) -> str:
    return f"{dt.day:02d}.{dt.month:02d} {dt.hour:02d}:{dt.minute:02d}"


def round_step(x: float) -> int:
    return 5 if x < 100 else 10 if x < 1000 else 50 if x < 5000 else 100


def nice_round(x: float, down: bool = False) -> float:
    """Округление до «человеческих» сумм: 5, 10, 50 или 100 в зависимости от величины."""
    if x <= 0:
        return 0.0
    step = round_step(x)
    q = x / step
    return float((math.floor(q + 1e-9) if down else round(q)) * step)


def scaled(amount_ils: float, cur: str) -> float:
    """Типичная сумма в шекелях → примерно такая же в валюте пользователя (для подсказок)."""
    return nice_round(amount_ils * CURRENCY_FACTORS.get(cur, 1.0))
