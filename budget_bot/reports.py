"""Тексты отчётов: бюджет месяца, еженедельная сводка, счёт и карты, история операций."""

from __future__ import annotations

from datetime import datetime, timedelta

from . import finance, fmt
from .fmt import esc
from .models import ACCOUNT, CARD_CHARGE, EXPENSE, INCOME, Transaction, UserData


def _m(data: UserData):
    cur = data.profile.currency
    return lambda x: fmt.money(x, cur)


def payment_label(payment: str) -> str:
    return "🏦 Счёт" if payment == ACCOUNT else f"💳 {esc(payment)}"


def budget_text(data: UserData, stats: finance.MonthStats, now: datetime, owner: str | None = None) -> str:
    m = _m(data)
    who = f" · {esc(owner)}" if owner else ""
    lines = [f"📊 <b>Бюджет{who} · {fmt.month_title(stats.month)}</b>"]
    if stats.is_current:
        lines.append(f"<i>День {now.day} из {stats.days_total} · осталось {fmt.days(stats.days_left)}</i>")
    lines.append("")
    if data.profile.income > 0:
        lines.append(f"💼 Доход (план): {m(data.profile.income)}")
    lines.append(f"📌 Постоянные платежи: {m(stats.fixed_plan)}")
    if stats.savings_goal > 0:
        lines.append(f"🎯 Цель накоплений: {m(stats.savings_goal)}")
    lines.append(f"🧾 Лимиты на жизнь: {m(stats.budget)}")
    lines.append("")

    ratio = stats.spent / stats.budget if stats.budget > 0 else (2.0 if stats.spent > 0 else 0.0)
    lines.append(f"<b>Потрачено {m(stats.spent)} из {m(stats.budget)}</b>"
                 + (f" ({fmt.pct(ratio)})" if stats.budget > 0 else ""))
    lines.append(fmt.bar(ratio, 10))
    if stats.left >= 0:
        line = f"Осталось {m(stats.left)}"
        if stats.daily_allowance is not None:
            line += f" · ≈ {m(round(stats.daily_allowance))} в день"
        lines.append(line)
    else:
        lines.append(f"🔴 Перерасход {m(-stats.left)}")

    rows = [c for c in stats.categories if c.limit > 0 or c.spent > 0]
    if rows:
        lines.append("")
    for c in rows:
        head = c.label if c.in_budget else f"{c.label} <i>(нет в бюджете)</i>"
        if c.over:
            tail = f"🔴 +{fmt.num(c.spent - c.limit)}"
        elif c.limit > 0:
            tail = f"ост. {fmt.num(c.left)}"
        else:
            tail = ""
        numbers = f"{fmt.num(c.spent)} / {fmt.num(c.limit)}"
        lines.append(f"{head}\n{fmt.bar(c.ratio)} {numbers}" + (f" · {tail}" if tail else ""))
    return fmt.fit("\n".join(lines))


def money_lines(data: UserData, now: datetime) -> list[str]:
    m = _m(data)
    balance = finance.account_balance(data, now)
    lines = [f"🏦 <b>На счету сейчас: ≈ {m(balance)}</b>"]
    total_pending = 0.0
    for card in data.cards:
        pending = finance.card_pending(data, card, now)
        total_pending += pending
        when = finance.next_date_for_day(card.charge_day, now.date())
        lines.append(f"💳 {esc(card.name)} — спишется {fmt.dm(when)}: {m(pending)}")
    if data.cards:
        free = balance - total_pending
        if free >= 0:
            lines.append(f"💰 Свободно после списаний: ≈ {m(free)}")
        else:
            lines.append(f"⚠️ До списания по картам не хватает ≈ {m(-free)}")
    return lines


def balance_text(data: UserData, now: datetime) -> str:
    m = _m(data)
    p = data.profile
    lines = ["🏦 <b>Счёт и карты</b>", ""]
    lines += money_lines(data, now)
    lines.append(f"<i>Сверено с банком {fmt.dmy(p.balance_at)} {p.balance_at:%H:%M}, дальше — по записанным "
                 f"операциям.</i>")
    if p.payday and p.income > 0:
        lines.append("")
        lines.append(f"💼 Зарплата ≈ {m(p.income)} — {fmt.dm(finance.next_date_for_day(p.payday, now.date()))}")
    upcoming = sorted(
        ((finance.next_date_for_day(f.day, now.date()), f) for f in data.fixed if f.active and f.amount > 0),
        key=lambda x: x[0],
    )[:3]
    if upcoming:
        lines.append("📌 Ближайшие платежи: " + ", ".join(
            f"{esc(f.name)} {fmt.num(f.amount)} ({fmt.dm(d)})" for d, f in upcoming))
    return "\n".join(lines)


def goal_line(data: UserData, stats: finance.MonthStats) -> str | None:
    goal = data.profile.savings_goal
    if goal <= 0 or data.profile.income <= 0:
        return None
    m = _m(data)
    expected = stats.expected_savings
    if expected >= goal - 1:
        return f"🎯 Цель накоплений {m(goal)}: идёшь по плану ✅"
    return f"🎯 Цель накоплений {m(goal)}: при текущих тратах выйдет ≈ {m(max(expected, 0))} ⚠️"


def summary_text(data: UserData, now: datetime, owner: str | None = None, scheduled: bool = False) -> str:
    """Сводка: плановая (в начале недели — за прошлые 7 дней) или по кнопке (последние 7 дней с сегодня)."""
    m = _m(data)
    today = now.date()
    end = finance.day_start(today) + (timedelta(0) if scheduled else timedelta(days=1))
    start = end - timedelta(days=7)
    who = f" · {esc(owner)}" if owner else ""
    title = "Сводка за неделю" if scheduled else "Последние 7 дней"
    lines = [f"📬 <b>{title}{who}</b> · {fmt.dm(start)}–{fmt.dm((end - timedelta(days=1)).date())}", ""]

    top = finance.top_spending(data, start, end)
    week_total = sum(v for _, v in top)
    lines.append(f"💸 <b>{'За неделю' if scheduled else 'За 7 дней'} потрачено: {m(week_total)}</b>")
    for name, value in top[:4]:
        lines.append(f"   {data.category_label(name)} — {m(value)}")
    if len(top) > 4:
        rest = sum(v for _, v in top[4:])
        lines.append(f"   …и ещё {len(top) - 4} — {m(rest)}")

    stats = finance.month_stats(data, today, now)
    lines.append("")
    ratio = stats.spent / stats.budget if stats.budget > 0 else (2.0 if stats.spent > 0 else 0.0)
    lines.append(f"📅 <b>{fmt.MONTHS_NOM[today.month - 1]}: потрачено {m(stats.spent)} из {m(stats.budget)}</b>"
                 + (f" ({fmt.pct(ratio)})" if stats.budget > 0 else ""))
    lines.append(fmt.bar(ratio, 10))
    if stats.left >= 0:
        line = f"✅ Осталось {m(stats.left)}"
        if stats.daily_allowance is not None:
            line += f" — ≈ {m(round(stats.daily_allowance))} в день ({fmt.days(stats.days_left)})"
        lines.append(line)
    else:
        lines.append(f"🔴 Перерасход {m(-stats.left)}")
    over = stats.over
    if over:
        lines.append("🔴 Превышены: " + ", ".join(f"{c.label} +{fmt.num(c.spent - c.limit)}" for c in over[:5]))

    lines.append("")
    lines += money_lines(data, now)
    goal = goal_line(data, stats)
    if goal:
        lines.append("")
        lines.append(goal)

    if today.day <= 7:
        prev = finance.month_stats(data, finance.prev_month(today), now)
        if prev.spent > 0 or prev.income_fact > 0:
            lines.append("")
            lines.append(f"📆 <b>Итоги: {fmt.month_title(prev.month)}</b> — траты {m(prev.spent)} из "
                         f"{m(prev.budget)}, постоянные {m(prev.fixed_fact)}.")
            if prev.income_fact > 0:
                lines.append(f"Доход {m(prev.income_fact)} → итог месяца {fmt.signed(prev.actual_result, data.profile.currency)}")
    return "\n".join(lines)


def tx_line(data: UserData, tx: Transaction, with_date: bool = True) -> str:
    cur = data.profile.currency
    if tx.kind == INCOME:
        amount = f"+{fmt.money(tx.amount, cur)}"
    else:
        amount = f"{fmt.MINUS}{fmt.money(tx.amount, cur)}"
    when = f"{fmt.dmhm(tx.at)} · " if with_date else ""
    label = data.category_label(tx.category) if tx.kind != CARD_CHARGE else f"💳 Списание {esc(tx.payment)}"
    comment = f" · {esc(tx.comment)}" if tx.comment and tx.kind != CARD_CHARGE else ""
    pay = ""
    if tx.kind == EXPENSE:
        pay = " · " + ("🏦" if tx.payment == ACCOUNT else "💳")
    return f"{when}<b>{amount}</b> · {label}{comment}{pay}"


def tx_card_text(data: UserData, tx: Transaction) -> str:
    cur = data.profile.currency
    sign = "+" if tx.kind == INCOME else fmt.MINUS
    lines = [
        f"{data.category_label(tx.category)}",
        f"<b>{sign}{fmt.money(tx.amount, cur)}</b> · {fmt.dmy(tx.at)} {tx.at:%H:%M}",
        f"Тип: {tx.kind} · Оплата: {payment_label(tx.payment)}",
    ]
    if tx.comment:
        lines.append(f"«{esc(tx.comment)}»")
    if tx.source != "бот":
        lines.append(f"<i>Источник: {esc(tx.source)}</i>")
    return "\n".join(lines)


def expense_saved_text(data: UserData, tx: Transaction, now: datetime) -> str:
    cur = data.profile.currency
    m = _m(data)
    lines = [f"✅ Записал: <b>{fmt.MINUS}{fmt.money(tx.amount, cur)}</b> · {data.category_label(tx.category)}"]
    details = []
    if tx.comment:
        details.append(f"«{esc(tx.comment)}»")
    details.append(payment_label(tx.payment))
    lines.append(" · ".join(details))
    cat = data.category(tx.category)
    if cat:
        stats = finance.month_stats(data, tx.at.date(), now)
        cs = next((c for c in stats.categories if c.name == cat.name), None)
        if cs and cat.limit > 0:
            if cs.over:
                lines.append(f"🔴 Лимит «{esc(cat.name)}» превышен на {m(cs.spent - cs.limit)}")
            else:
                lines.append(f"Осталось в категории: {m(cs.left)} из {m(cs.limit)}")
            lines.append(fmt.bar(cs.ratio))
        elif cs:
            lines.append(f"В этом месяце в категории: {m(cs.spent)} (лимит не задан)")
    return "\n".join(lines)


def income_saved_text(data: UserData, tx: Transaction, now: datetime) -> str:
    cur = data.profile.currency
    comment = f" «{esc(tx.comment)}»" if tx.comment else ""
    return (f"✅ Доход: <b>+{fmt.money(tx.amount, cur)}</b>{comment}\n"
            f"🏦 На счету теперь ≈ {fmt.money(finance.account_balance(data, now), cur)}")
