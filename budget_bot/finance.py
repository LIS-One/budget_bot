"""Расчёты по данным пользователя: статистика месяца, баланс счёта, карты, регулярные платежи."""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from .models import (
    ACCOUNT,
    CARD_CHARGE,
    CARD_CHARGE_CATEGORY,
    EXPENSE,
    FIXED_CATEGORY,
    INCOME,
    INCOME_CATEGORY,
    SOURCE_AUTO,
    Card,
    Transaction,
    UserData,
)

_EPS = 0.005


# --- календарь ---------------------------------------------------------------
def month_start(d: date) -> date:
    return date(d.year, d.month, 1)


def next_month(d: date) -> date:
    return date(d.year + (d.month == 12), d.month % 12 + 1, 1)


def prev_month(d: date) -> date:
    return date(d.year - (d.month == 1), (d.month - 2) % 12 + 1, 1)


def days_in_month(d: date) -> int:
    return calendar.monthrange(d.year, d.month)[1]


def effective_day(year: int, month: int, day: int) -> date:
    """«31-е число» в феврале — это 28/29 февраля."""
    last = calendar.monthrange(year, month)[1]
    return date(year, month, max(1, min(day, last)))


def day_start(d: date) -> datetime:
    return datetime.combine(d, time(0, 0))


def next_date_for_day(day: int, today: date) -> date:
    """Ближайшая дата (сегодня не считается) с таким числом месяца."""
    this = effective_day(today.year, today.month, day)
    if this > today:
        return this
    nm = next_month(today)
    return effective_day(nm.year, nm.month, day)


# --- статистика месяца ----------------------------------------------------------
@dataclass
class CategoryStat:
    name: str
    label: str
    limit: float
    spent: float
    in_budget: bool = True

    @property
    def left(self) -> float:
        return self.limit - self.spent

    @property
    def ratio(self) -> float:
        if self.limit > 0:
            return self.spent / self.limit
        return 2.0 if self.spent > _EPS else 0.0

    @property
    def over(self) -> bool:
        return self.spent > self.limit + _EPS


@dataclass
class MonthStats:
    month: date
    start: datetime
    end: datetime
    is_current: bool
    days_total: int
    days_left: int
    income_plan: float
    income_fact: float
    fixed_plan: float
    fixed_fact: float
    savings_goal: float
    budget: float
    spent: float
    categories: list[CategoryStat] = field(default_factory=list)

    @property
    def left(self) -> float:
        return self.budget - self.spent

    @property
    def over(self) -> list[CategoryStat]:
        return [c for c in self.categories if c.over]

    @property
    def daily_allowance(self) -> float | None:
        if not self.is_current or self.days_left <= 0:
            return None
        return max(self.left, 0.0) / self.days_left

    @property
    def planned_savings(self) -> float:
        return self.income_plan - self.fixed_plan - self.budget

    @property
    def expected_savings(self) -> float:
        """Сколько останется в конце месяца, если дальше тратить строго по лимитам."""
        return self.income_plan - self.fixed_plan - max(self.budget, self.spent)

    @property
    def actual_result(self) -> float:
        """Для прошедшего месяца: фактический доход минус все фактические расходы."""
        return self.income_fact - self.fixed_fact - self.spent


def _in_range(tx: Transaction, start: datetime, end: datetime) -> bool:
    return start <= tx.at < end


def variable_spending(data: UserData, start: datetime, end: datetime) -> dict[str, float]:
    """Переменные траты по категориям (ключ — имя категории в нижнем регистре)."""
    out: dict[str, float] = {}
    for tx in data.transactions:
        if tx.kind == EXPENSE and tx.category != FIXED_CATEGORY and _in_range(tx, start, end):
            key = tx.category.lower()
            out[key] = out.get(key, 0.0) + tx.amount
    return out


def month_stats(data: UserData, month: date, now: datetime) -> MonthStats:
    m0 = month_start(month)
    start, end = day_start(m0), day_start(next_month(m0))
    spent_by = variable_spending(data, start, end)

    income_fact = fixed_fact = 0.0
    for tx in data.transactions:
        if not _in_range(tx, start, end):
            continue
        if tx.kind == INCOME:
            income_fact += tx.amount
        elif tx.kind == EXPENSE and tx.category == FIXED_CATEGORY:
            fixed_fact += tx.amount

    stats: list[CategoryStat] = []
    known = set()
    for c in data.categories:
        key = c.name.lower()
        known.add(key)
        stats.append(CategoryStat(c.name, c.label, c.limit, round(spent_by.get(key, 0.0), 2)))
    # Траты в категориях, которых уже нет в бюджете (удалили/переименовали в Excel).
    extra_names = {}
    for tx in data.transactions:
        key = tx.category.lower()
        if key in spent_by and key not in known and key not in extra_names:
            extra_names[key] = tx.category
    for key, name in extra_names.items():
        stats.append(CategoryStat(name, data.category_label(name), 0.0, round(spent_by[key], 2), in_budget=False))

    is_current = start <= now < end
    total_days = days_in_month(m0)
    days_left = total_days - now.day + 1 if is_current else 0
    return MonthStats(
        month=m0,
        start=start,
        end=end,
        is_current=is_current,
        days_total=total_days,
        days_left=days_left,
        income_plan=data.profile.income,
        income_fact=round(income_fact, 2),
        fixed_plan=data.fixed_total,
        fixed_fact=round(fixed_fact, 2),
        savings_goal=data.profile.savings_goal,
        budget=data.limits_total,
        spent=round(sum(spent_by.values()), 2),
        categories=stats,
    )


def top_spending(data: UserData, start: datetime, end: datetime) -> list[tuple[str, float]]:
    spent = variable_spending(data, start, end)
    names = {c.name.lower(): c.name for c in data.categories}
    for tx in data.transactions:
        names.setdefault(tx.category.lower(), tx.category)
    return sorted(((names[k], v) for k, v in spent.items()), key=lambda kv: kv[1], reverse=True)


# --- деньги на счёте и карты -----------------------------------------------------
def account_balance(data: UserData, at: datetime) -> float:
    """Остаток на счёте: сверка + всё, что было после неё.

    Доход увеличивает счёт, траты «со счёта» и списания кредиток уменьшают.
    Траты картой копятся на карте и уходят со счёта в день списания.
    """
    p = data.profile
    total = p.balance
    for tx in data.transactions:
        if not (p.balance_at < tx.at <= at):
            continue
        if tx.kind == INCOME:
            total += tx.amount
        elif tx.kind == EXPENSE and tx.payment == ACCOUNT:
            total -= tx.amount
        elif tx.kind == CARD_CHARGE:
            total -= tx.amount
    return round(total, 2)


def card_pending(data: UserData, card: Card, at: datetime) -> float:
    total = card.baseline
    for tx in data.transactions:
        if tx.payment != card.name or not (card.baseline_at < tx.at <= at):
            continue
        if tx.kind == EXPENSE:
            total += tx.amount
        elif tx.kind == CARD_CHARGE:
            total -= tx.amount
    return round(total, 2)


def cards_pending_total(data: UserData, at: datetime) -> float:
    return round(sum(card_pending(data, c, at) for c in data.cards), 2)


# --- регулярные события: списания карт, постоянные платежи, зарплата --------------
@dataclass
class Event:
    kind: str  # "card" | "fixed" | "payday"
    day: date
    tx: Transaction


def _fixed_posted(data: UserData, name: str, d: date) -> bool:
    return any(
        tx.source == SOURCE_AUTO
        and tx.category == FIXED_CATEGORY
        and tx.comment.lower() == name.lower()
        and tx.at.year == d.year
        and tx.at.month == d.month
        for tx in data.transactions
    )


def _income_posted(data: UserData, d: date) -> bool:
    return any(
        tx.source == SOURCE_AUTO and tx.kind == INCOME and tx.at.year == d.year and tx.at.month == d.month
        for tx in data.transactions
    )


def _card_charged(data: UserData, card: Card, d: date) -> bool:
    return any(tx.kind == CARD_CHARGE and tx.payment == card.name and tx.at.date() == d for tx in data.transactions)


def _fixed_payment(data: UserData, payment: str) -> str:
    return payment if payment == ACCOUNT or data.card(payment) else ACCOUNT


def post_fixed(data: UserData, d: date, only_due: bool = True) -> list[Transaction]:
    """Записывает постоянные платежи, у которых день списания = d (или ≤ d при only_due=False)."""
    posted = []
    for f in data.fixed:
        if not f.active or f.amount <= 0:
            continue
        due = effective_day(d.year, d.month, f.day)
        if (due == d if only_due else due <= d) and not _fixed_posted(data, f.name, d):
            posted.append(data.add_transaction(
                at=day_start(due), kind=EXPENSE, amount=f.amount, category=FIXED_CATEGORY,
                comment=f.name, payment=_fixed_payment(data, f.payment), source=SOURCE_AUTO,
            ))
    return posted


def post_income(data: UserData, d: date, only_due: bool = True) -> Transaction | None:
    p = data.profile
    if not p.payday or p.income <= 0:
        return None
    due = effective_day(d.year, d.month, p.payday)
    if (due == d if only_due else due <= d) and not _income_posted(data, d):
        return data.add_transaction(
            at=day_start(due), kind=INCOME, amount=p.income, category=INCOME_CATEGORY,
            comment="Зарплата (авто)", payment=ACCOUNT, source=SOURCE_AUTO,
        )
    return None


def charge_card(data: UserData, card: Card, d: date) -> Transaction | None:
    if effective_day(d.year, d.month, card.charge_day) != d or _card_charged(data, card, d):
        return None
    amount = card_pending(data, card, day_start(d) - timedelta(microseconds=1))
    if amount <= _EPS:
        return None
    return data.add_transaction(
        at=day_start(d), kind=CARD_CHARGE, amount=amount, category=CARD_CHARGE_CATEGORY,
        comment=f"Списание по карте {card.name}", payment=card.name, source=SOURCE_AUTO,
    )


def process_recurring(data: UserData, last_done: date, today: date) -> list[Event]:
    """Проходит дни (last_done, today] и записывает всё регулярное. Идемпотентно."""
    events: list[Event] = []
    d = last_done + timedelta(days=1)
    while d <= today:
        for card in data.cards:
            tx = charge_card(data, card, d)
            if tx:
                events.append(Event("card", d, tx))
        for tx in post_fixed(data, d):
            events.append(Event("fixed", d, tx))
        tx = post_income(data, d)
        if tx:
            events.append(Event("payday", d, tx))
        d += timedelta(days=1)
    return events


def months_with_data(data: UserData, now: datetime) -> list[date]:
    first = month_start(data.profile.created_at.date())
    if data.transactions:
        first = min(first, month_start(min(t.at for t in data.transactions).date()))
    months, m, last = [], first, month_start(now.date())
    while m <= last:
        months.append(m)
        m = next_month(m)
    return months or [last]
