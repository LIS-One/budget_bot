"""Экраны просмотра: бюджет месяца, диаграммы, сводка, счёт и карты, история операций.

Те же экраны (с префиксом «pv» и без кнопок редактирования) показываются партнёру.
"""

from __future__ import annotations

import asyncio
import math
from datetime import date

from telegram import InputMediaPhoto, Update

from .. import charts, finance, fmt, reports
from ..fmt import esc
from ..models import UserData
from ..parsing import parse_amount
from .common import (
    Ctx,
    answer,
    chunk,
    clear_await,
    kb,
    load,
    need_setup,
    safe_delete,
    save,
    set_await,
    show,
    svc,
)

HISTORY_PAGE = 8


def _month_from(code: str, fallback: date) -> date:
    try:
        return date(int(code[:4]), int(code[4:6]), 1)
    except (ValueError, TypeError, IndexError):
        return finance.month_start(fallback)


# --- бюджет ------------------------------------------------------------------------------------------
def budget_screen(data: UserData, month: date, now, prefix: str = "v", owner: str | None = None):
    month = finance.month_start(month)
    stats = finance.month_stats(data, month, now)
    text = reports.budget_text(data, stats, now, owner)
    first = finance.months_with_data(data, now)[0]
    nav = []
    if month > first:
        prev = finance.prev_month(month)
        nav.append((f"◀️ {fmt.MONTHS_NOM[prev.month - 1]}", f"{prefix}|b|{prev:%Y%m}"))
    if month < finance.month_start(now.date()):
        nxt = finance.next_month(month)
        nav.append((f"{fmt.MONTHS_NOM[nxt.month - 1]} ▶️", f"{prefix}|b|{nxt:%Y%m}"))
    rows = [nav, [("🥧 Диаграмма", f"{prefix}|chs"), ("📋 Операции", f"{prefix}|h|0")]]
    if prefix == "v":
        rows.append([("✏️ Изменить лимиты", "st|cats")])
    else:
        rows.append([("⬅️ Пара", "pr|menu")])
    return text, kb(rows)


async def show_budget(update: Update, context: Ctx) -> None:
    data = load(context, update.effective_user.id)
    if data is None:
        await need_setup(update, context)
        return
    now = svc(context).now()
    text, markup = budget_screen(data, now.date(), now)
    await update.effective_message.reply_text(text, reply_markup=markup)


# --- диаграммы -------------------------------------------------------------------------------------------
async def chart_png(data: UserData, mode: str, now, owner: str | None = None) -> bytes | None:
    if mode == "fact":
        return await asyncio.to_thread(charts.spending_pie, data, now.date(), now, owner)
    return await asyncio.to_thread(charts.budget_pie, data, now.date(), owner)


def chart_markup(mode: str, prefix: str):
    plan = "🥧 План ✓" if mode == "plan" else "🥧 План"
    fact = "💸 Траты ✓" if mode == "fact" else "💸 Траты"
    return kb([[(plan, f"{prefix}|ch|plan"), (fact, f"{prefix}|ch|fact")]])


def chart_caption(data: UserData, mode: str, now) -> str:
    cur = data.profile.currency
    if mode == "fact":
        stats = finance.month_stats(data, now.date(), now)
        return f"💸 Траты за {fmt.MONTHS_NOM[now.month - 1].lower()}: {fmt.money(stats.spent, cur)} из {fmt.money(stats.budget, cur)}"
    return f"🥧 Бюджет на {fmt.MONTHS_NOM[now.month - 1].lower()}: куда распределяется доход"


async def send_chart(update: Update, context: Ctx, data: UserData, mode: str = "plan", prefix: str = "v",
                     owner: str | None = None, edit: bool = False) -> None:
    now = svc(context).now()
    png = await chart_png(data, mode, now, owner)
    if png is None:
        note = "В этом месяце трат ещё нет — диаграмма появится после первой записи." if mode == "fact" else \
            "Бюджет пока пустой — задай доход и лимиты в ⚙️ Настройках."
        if edit:
            await answer(update, note, alert=True)
        else:
            await update.effective_message.reply_text(note)
        return
    caption = chart_caption(data, mode, now)
    markup = chart_markup(mode, prefix)
    if edit and update.callback_query:
        await answer(update)
        await update.callback_query.edit_message_media(InputMediaPhoto(png, caption=caption), reply_markup=markup)
    else:
        await context.bot.send_photo(update.effective_chat.id, png, caption=caption, reply_markup=markup)


async def show_chart(update: Update, context: Ctx) -> None:
    data = load(context, update.effective_user.id)
    if data is None:
        await need_setup(update, context)
        return
    await send_chart(update, context, data)


# --- сводка, счёт ----------------------------------------------------------------------------------------
def summary_markup(prefix: str = "v"):
    rows = [[("📊 Бюджет", f"{prefix}|b|now")]]
    if prefix == "v":
        rows[0].insert(0, ("✏️ Сверить баланс", "v|setbal"))
    return kb(rows)


async def show_summary(update: Update, context: Ctx) -> None:
    data = load(context, update.effective_user.id)
    if data is None:
        await need_setup(update, context)
        return
    now = svc(context).now()
    await update.effective_message.reply_text(reports.summary_text(data, now), reply_markup=summary_markup())


def balance_screen(data: UserData, now):
    rows = [[("✏️ Сверить баланс с банком", "v|setbal")]]
    for i, card in enumerate(data.cards):
        rows.append([(f"💳 {card.name}: сумма к списанию", f"v|setcard|{i}")])
    rows.append([("💵 Записать доход", "v|inc")])
    return reports.balance_text(data, now), kb(rows)


async def show_balance(update: Update, context: Ctx) -> None:
    data = load(context, update.effective_user.id)
    if data is None:
        await need_setup(update, context)
        return
    text, markup = balance_screen(data, svc(context).now())
    await update.effective_message.reply_text(text, reply_markup=markup)


# --- история ----------------------------------------------------------------------------------------------
def history_screen(data: UserData, page: int, prefix: str = "v", owner: str | None = None):
    txs = sorted(data.transactions, key=lambda t: (t.at, t.id), reverse=True)
    pages = max(1, math.ceil(len(txs) / HISTORY_PAGE))
    page = max(0, min(page, pages - 1))
    items = txs[page * HISTORY_PAGE:(page + 1) * HISTORY_PAGE]
    who = f" · {esc(owner)}" if owner else ""
    lines = [f"📋 <b>Операции{who}</b> · стр. {page + 1}/{pages}", ""]
    if not items:
        lines.append("Пока нет ни одной операции.")
    for i, tx in enumerate(items, start=1):
        lines.append(f"{i}. {reports.tx_line(data, tx)}")
    rows = []
    if prefix == "v" and items:
        lines.append("")
        lines.append("Нажми номер, чтобы исправить или удалить операцию.")
        rows += chunk([(str(i), f"tx|view|{tx.id}|h{page}") for i, tx in enumerate(items, start=1)], 4)
    nav = []
    if page > 0:
        nav.append(("◀️ Новее", f"{prefix}|h|{page - 1}"))
    if page < pages - 1:
        nav.append(("Старее ▶️", f"{prefix}|h|{page + 1}"))
    rows.append(nav)
    if prefix != "v":
        rows.append([("⬅️ Пара", "pr|menu")])
    return "\n".join(lines), kb(rows)


async def show_history(update: Update, context: Ctx) -> None:
    data = load(context, update.effective_user.id)
    if data is None:
        await need_setup(update, context)
        return
    text, markup = history_screen(data, 0)
    await update.effective_message.reply_text(text, reply_markup=markup)


# --- колбэки «v|…» -------------------------------------------------------------------------------------
async def on_view(update: Update, context: Ctx) -> None:
    query = update.callback_query
    parts = query.data.split("|")
    action = parts[1]
    data = load(context, update.effective_user.id)
    if data is None:
        await answer(update)
        await need_setup(update, context)
        return
    now = svc(context).now()
    chat_id = update.effective_chat.id
    msg_id = query.message.message_id
    if action == "ch":
        await send_chart(update, context, data, parts[2], edit=True)
        return
    await answer(update)
    if action == "b":
        month = now.date() if parts[2] == "now" else _month_from(parts[2], now.date())
        text, markup = budget_screen(data, month, now)
        await show(context, chat_id, msg_id, text, markup)
    elif action == "chs":
        await send_chart(update, context, data)
    elif action == "h":
        text, markup = history_screen(data, int(parts[2]))
        await show(context, chat_id, msg_id, text, markup)
    elif action == "bal":
        text, markup = balance_screen(data, now)
        await show(context, chat_id, msg_id, text, markup)
    elif action == "setbal":
        set_await(context, "set_balance", msg_id=msg_id)
        await show(context, chat_id, msg_id,
                   f"✏️ Сколько сейчас на счету? Посмотри в приложении банка и напиши сумму.\n"
                   f"<i>Сейчас по моим данным ≈ {fmt.money(finance.account_balance(data, now), data.profile.currency)}. "
                   "В минусе — пиши с минусом.</i>",
                   kb([[("↩️ Отмена", "v|bal")]]))
    elif action == "setcard":
        idx = int(parts[2])
        if idx < len(data.cards):
            card = data.cards[idx]
            set_await(context, "set_card_pending", msg_id=msg_id, card=card.name)
            pending = finance.card_pending(data, card, now)
            await show(context, chat_id, msg_id,
                       f"💳 Сколько сейчас к списанию по «{esc(card.name)}»? Посмотри в приложении карты.\n"
                       f"<i>По моим данным ≈ {fmt.money(pending, data.profile.currency)}.</i>",
                       kb([[("↩️ Отмена", "v|bal")]]))
    elif action == "inc":
        set_await(context, "income_amount", msg_id=msg_id)
        await show(context, chat_id, msg_id, "💵 Напиши сумму дохода и откуда он: <code>12000 зарплата</code>",
                   kb([[("↩️ Отмена", "v|bal")]]))


async def await_set_balance(update: Update, context: Ctx, state: dict) -> None:
    value = parse_amount(update.effective_message.text or "", allow_negative=True)
    await safe_delete(update.effective_message)
    data = load(context, update.effective_user.id)
    if data is None:
        return
    if value is None:
        await show(context, update.effective_chat.id, state["msg_id"],
                   "⚠️ Не понял сумму. Напиши число, например: <code>8450</code> или <code>-1200</code>",
                   kb([[("↩️ Отмена", "v|bal")]]))
        return
    clear_await(context)
    now = svc(context).now()
    data.profile.balance = value
    data.profile.balance_at = now
    save(context, data)
    text, markup = balance_screen(data, now)
    await show(context, update.effective_chat.id, state["msg_id"], "✅ Баланс сверен.\n\n" + text, markup)


async def await_set_card_pending(update: Update, context: Ctx, state: dict) -> None:
    value = parse_amount(update.effective_message.text or "")
    await safe_delete(update.effective_message)
    data = load(context, update.effective_user.id)
    card = data.card(state.get("card", "")) if data else None
    if card is None:
        clear_await(context)
        return
    if value is None:
        await show(context, update.effective_chat.id, state["msg_id"],
                   "⚠️ Не понял сумму. Напиши число, например: <code>2130</code>", kb([[("↩️ Отмена", "v|bal")]]))
        return
    clear_await(context)
    now = svc(context).now()
    card.baseline = value
    card.baseline_at = now
    save(context, data)
    text, markup = balance_screen(data, now)
    await show(context, update.effective_chat.id, state["msg_id"], f"✅ Сумма по «{esc(card.name)}» обновлена.\n\n" + text,
               markup)
