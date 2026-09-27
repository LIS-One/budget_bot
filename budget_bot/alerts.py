"""Предупреждения о лимитах: отдельное сообщение, когда категория дошла до 80% и до 100%.

Предупреждение срабатывает в момент пересечения порога (было меньше — стало больше или равно),
только для текущего месяца. По желанию пользователя то же самое получает его партнёр.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from . import finance, fmt
from .fmt import esc
from .models import Category, UserData

LEVELS = (1.0, 0.8)  # проверяем сначала 100%, чтобы за одну трату не прислать два сообщения
_EPS = 0.005


@dataclass
class Crossing:
    category: Category
    level: float
    spent: float


def snapshot(data: UserData, now: datetime) -> dict[str, float]:
    """Траты по категориям за текущий месяц — снимок «до» изменения."""
    start = finance.day_start(finance.month_start(now.date()))
    end = finance.day_start(finance.next_month(now.date()))
    return finance.variable_spending(data, start, end)


def crossings(data: UserData, before: dict[str, float], now: datetime) -> list[Crossing]:
    after = snapshot(data, now)
    found = []
    for cat in data.categories:
        if cat.limit <= 0:
            continue
        key = cat.name.lower()
        was, now_spent = before.get(key, 0.0), after.get(key, 0.0)
        for level in LEVELS:
            threshold = cat.limit * level
            if was < threshold - _EPS and now_spent >= threshold - _EPS:
                found.append(Crossing(cat, level, now_spent))
                break
    return found


def own_text(data: UserData, c: Crossing, now: datetime) -> str:
    cur = data.profile.currency
    cat, limit = c.category, c.category.limit
    if c.level >= 1.0:
        over = c.spent - limit
        head = f"🔴 <b>Лимит «{esc(cat.label)}» исчерпан</b>"
        body = f"Потрачено {fmt.money(c.spent, cur)} из {fmt.money(limit, cur)}"
        body += f" — перерасход {fmt.money(over, cur)}." if over > _EPS else "."
        return f"{head}\n{body}\nПри необходимости лимит можно поднять: ⚙️ Настройки → Категории."
    stats = finance.month_stats(data, now.date(), now)
    left = limit - c.spent
    line = f"Потрачено {fmt.money(c.spent, cur)} из {fmt.money(limit, cur)}, осталось {fmt.money(left, cur)}"
    if stats.days_left > 0:
        line += f" на {fmt.days(stats.days_left)} (≈ {fmt.money(round(left / stats.days_left), cur)} в день)"
    return f"⚠️ <b>«{esc(cat.label)}» — {fmt.pct(c.spent / limit)} лимита</b>\n{line}."


def partner_text(owner: str, data: UserData, c: Crossing) -> str:
    cur = data.profile.currency
    limit = c.category.limit
    amounts = f"{fmt.money(c.spent, cur)} из {fmt.money(limit, cur)}"
    if c.level >= 1.0:
        return f"👀 {esc(owner)}: лимит «{esc(c.category.label)}» исчерпан — {amounts}."
    return f"👀 {esc(owner)}: «{esc(c.category.label)}» — {fmt.pct(c.spent / limit)} лимита ({amounts})."
