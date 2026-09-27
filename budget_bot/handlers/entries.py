"""Запись трат и доходов и действия с отдельной операцией (категория, оплата, сумма, дата, удаление)."""

from __future__ import annotations

from datetime import date, datetime, time

from telegram import Update
from telegram.error import TelegramError

from .. import alerts, fmt, reports
from ..categories import detect_category, looks_like_income, short_label
from ..fmt import esc
from ..models import ACCOUNT, CARD_CHARGE, EXPENSE, INCOME, INCOME_CATEGORY, SOURCE_BOT, Category, UserData
from ..parsing import DateError, clean_text, parse_amount, parse_date, parse_entry
from .common import (
    MAIN_MENU,
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

NOT_UNDERSTOOD = (
    "🤔 Не понял. Чтобы записать трату, напиши сумму и описание: <code>45 кофе</code>\n"
    "Задним числом: <code>вчера 45 кофе</code> или <code>25.09 120 продукты</code>\n"
    "Доход — с плюсом: <code>+12000 зарплата</code>\nОстальное — кнопками меню 👇"
)
BACKDATED_TIME = time(12, 0)  # время для записей задним числом


def category_buttons(data: UserData, prefix: str, per_row: int = 2) -> list[list[tuple[str, str]]]:
    buttons = [(short_label(c.emoji, c.name, c.code, 14), f"{prefix}{i}") for i, c in enumerate(data.categories)]
    return chunk(buttons, per_row)


def entry_time(day: date | None, now: datetime) -> datetime:
    """Когда записать операцию: сейчас или (задним числом) в полдень выбранного дня."""
    if day is None or day >= now.date():
        return now
    return datetime.combine(day, BACKDATED_TIME)


def tx_keyboard(data: UserData, tx_id: int, origin: str = "") -> object:
    tx = data.transaction(tx_id)
    suffix = f"|{origin}" if origin else ""
    rows: list[list[tuple[str, str]]] = []
    if tx and tx.kind == EXPENSE:
        row = [("🏷 Категория", f"tx|cat|{tx_id}{suffix}")]
        if data.cards:
            row.append(("💳 Оплата", f"tx|pay|{tx_id}{suffix}"))
        rows.append(row)
    if tx and tx.kind == CARD_CHARGE:
        rows.append([("✏️ Сумма", f"tx|amt|{tx_id}{suffix}")])
    else:
        rows.append([("✏️ Сумма", f"tx|amt|{tx_id}{suffix}"), ("💬 Описание", f"tx|cmt|{tx_id}{suffix}"),
                     ("📅 Дата", f"tx|date|{tx_id}{suffix}")])
    if origin.startswith("h"):
        rows.append([("🗑 Удалить", f"tx|delq|{tx_id}{suffix}"), ("⬅️ К списку", f"v|h|{origin[1:]}")])
    else:
        rows.append([("🗑 Отменить запись", f"tx|del|{tx_id}")])
    return kb(rows)


def tx_view(data: UserData, tx_id: int, now, origin: str = "") -> tuple[str, object]:
    tx = data.transaction(tx_id)
    if tx is None:
        return "Эта операция уже удалена.", None
    if origin.startswith("h") or tx.kind == CARD_CHARGE:
        text = reports.tx_card_text(data, tx)
    elif tx.kind == INCOME:
        text = reports.income_saved_text(data, tx, now)
    else:
        text = reports.expense_saved_text(data, tx, now)
    return text, tx_keyboard(data, tx_id, origin)


async def _send(update: Update, context: Ctx, text: str, markup, edit_message_id: int | None = None) -> None:
    await show(context, update.effective_chat.id, edit_message_id, text, markup)


async def send_limit_alerts(context: Ctx, data: UserData, before: dict[str, float], now: datetime) -> None:
    """Отдельные сообщения, если траты категории перешли 80% или 100% лимита (себе и, по желанию, партнёру)."""
    found = alerts.crossings(data, before, now)
    if not found:
        return
    s = svc(context)
    rec = s.registry.user(data.user_id) or {}
    if rec.get("limit_alerts", True):
        chat = rec.get("chat_id") or data.user_id
        for c in found:
            await context.bot.send_message(chat, alerts.own_text(data, c, now))
    partner = s.registry.partner(data.user_id)
    if partner and rec.get("partner_alerts", False):
        partner_chat = (s.registry.user(partner) or {}).get("chat_id") or partner
        owner = s.registry.name(data.user_id)
        for c in found:
            try:
                await context.bot.send_message(partner_chat, alerts.partner_text(owner, data, c))
            except TelegramError:  # партнёр мог заблокировать бота
                pass


async def record_expense(update: Update, context: Ctx, data: UserData, amount: float, comment: str,
                         category: Category | None = None, edit_message_id: int | None = None,
                         day: date | None = None) -> None:
    if not data.categories:
        await _send(update, context, "В бюджете нет ни одной категории — добавь её в ⚙️ Настройки → Категории.",
                    None, edit_message_id)
        return
    if category is None:
        category = detect_category(comment, data.categories, data.transactions)
    if category is None:
        context.user_data["pending"] = {"amount": amount, "comment": comment,
                                        "day": day.isoformat() if day else None}
        what = f" «{esc(comment)}»" if comment else ""
        when = f" за {fmt.dmy(day)}" if day else ""
        text = f"Куда записать <b>{fmt.MINUS}{fmt.money(amount, data.profile.currency)}</b>{what}{when}?"
        rows = category_buttons(data, "nx|c|") + [[("✖️ Не записывать", "nx|x")]]
        await _send(update, context, text, kb(rows), edit_message_id)
        return
    now = svc(context).now()
    before = alerts.snapshot(data, now)
    tx = data.add_transaction(at=entry_time(day, now), kind=EXPENSE, amount=amount, category=category.name,
                              comment=comment, payment=data.default_payment, source=SOURCE_BOT)
    save(context, data)
    text, markup = tx_view(data, tx.id, now)
    await _send(update, context, text, markup, edit_message_id)
    await send_limit_alerts(context, data, before, now)


async def record_income(update: Update, context: Ctx, data: UserData, amount: float, comment: str,
                        edit_message_id: int | None = None, day: date | None = None) -> None:
    now = svc(context).now()
    tx = data.add_transaction(at=entry_time(day, now), kind=INCOME, amount=amount, category=INCOME_CATEGORY,
                              comment=comment or "Доход", payment=ACCOUNT, source=SOURCE_BOT)
    save(context, data)
    text, markup = tx_view(data, tx.id, now)
    await _send(update, context, text, markup, edit_message_id)


def _parse(context: Ctx, text: str):
    """(запись или None, текст ошибки даты или None)."""
    try:
        return parse_entry(text, svc(context).today()), None
    except DateError as exc:
        return None, str(exc)


async def quick_entry(update: Update, context: Ctx, text: str) -> None:
    """Свободный текст в чате: «45 кофе», «+12000 зарплата», «вчера 45 кофе»."""
    data = load(context, update.effective_user.id)
    if data is None:
        await need_setup(update, context)
        return
    entry, date_error = _parse(context, text)
    if date_error:
        await update.effective_message.reply_text(f"⚠️ {esc(date_error)}")
        return
    if entry is None:
        await update.effective_message.reply_text(NOT_UNDERSTOOD, reply_markup=MAIN_MENU)
        return
    if entry.income:
        await record_income(update, context, data, entry.amount, entry.comment, day=entry.day)
        return
    if entry.comment and looks_like_income(entry.comment):
        context.user_data["pending"] = {"amount": entry.amount, "comment": entry.comment,
                                        "day": entry.day.isoformat() if entry.day else None}
        money = fmt.money(entry.amount, data.profile.currency)
        await update.effective_message.reply_text(
            f"{money} «{esc(entry.comment)}» — это доход или трата?",
            reply_markup=kb([[("💼 Доход", "nx|inc"), ("💸 Трата", "nx|exp")]]),
        )
        return
    await record_expense(update, context, data, entry.amount, entry.comment, day=entry.day)


# --- кнопки меню «➕ Трата» и «💵 Доход» -------------------------------------------------------------
async def add_menu(update: Update, context: Ctx) -> None:
    data = load(context, update.effective_user.id)
    if data is None:
        await need_setup(update, context)
        return
    text = ("✍️ Напиши сумму и что купил(а): <code>45 кофе</code> — категорию я подберу сам.\n"
            "Забыл(а) записать вовремя? <code>вчера 45 кофе</code> или <code>25.09 120 продукты</code>\n\n"
            "Или сначала выбери категорию 👇")
    await update.effective_message.reply_text(text, reply_markup=kb(category_buttons(data, "add|")))


async def income_menu(update: Update, context: Ctx) -> None:
    if load(context, update.effective_user.id) is None:
        await need_setup(update, context)
        return
    msg = await update.effective_message.reply_text(
        "💵 Напиши сумму дохода и откуда он: <code>12000 зарплата</code>",
        reply_markup=kb([[("↩️ Отмена", "nx|x")]]),
    )
    set_await(context, "income_amount", msg_id=msg.message_id)


async def on_add_category(update: Update, context: Ctx) -> None:
    query = update.callback_query
    await answer(update)
    data = load(context, update.effective_user.id)
    idx = int(query.data.split("|")[1])
    if data is None or idx >= len(data.categories):
        return
    cat = data.categories[idx]
    await query.edit_message_text(
        f"{cat.label}: напиши сумму (можно с описанием и датой): <code>45 латте</code>, <code>вчера 45</code>",
        reply_markup=kb([[("↩️ Отмена", "nx|x")]]),
    )
    set_await(context, "add_amount", category=cat.name, msg_id=query.message.message_id)


async def await_add_amount(update: Update, context: Ctx, state: dict) -> None:
    entry, date_error = _parse(context, update.effective_message.text or "")
    await safe_delete(update.effective_message)
    data = load(context, update.effective_user.id)
    if data is None:
        return
    if entry is None:
        problem = esc(date_error) if date_error else "Не понял сумму. Напиши число, например: <code>45 латте</code>"
        await show(context, update.effective_chat.id, state.get("msg_id"), f"⚠️ {problem}",
                   kb([[("↩️ Отмена", "nx|x")]]))
        return
    clear_await(context)
    cat = data.category(state.get("category", ""))
    await record_expense(update, context, data, entry.amount, entry.comment, category=cat,
                         edit_message_id=state.get("msg_id"), day=entry.day)


async def await_income_amount(update: Update, context: Ctx, state: dict) -> None:
    entry, date_error = _parse(context, update.effective_message.text or "")
    await safe_delete(update.effective_message)
    data = load(context, update.effective_user.id)
    if data is None:
        return
    if entry is None:
        problem = esc(date_error) if date_error else "Не понял сумму. Пример: <code>12000 зарплата</code>"
        await show(context, update.effective_chat.id, state.get("msg_id"), f"⚠️ {problem}",
                   kb([[("↩️ Отмена", "nx|x")]]))
        return
    clear_await(context)
    await record_income(update, context, data, entry.amount, entry.comment, edit_message_id=state.get("msg_id"),
                        day=entry.day)


# --- незавершённая запись: выбор категории / доход или трата ------------------------------------------
async def on_pending(update: Update, context: Ctx) -> None:
    query = update.callback_query
    parts = query.data.split("|")
    action = parts[1]
    pending = context.user_data.get("pending")
    msg_id = query.message.message_id if query.message else None
    if action == "x":
        await answer(update)
        context.user_data.pop("pending", None)
        clear_await(context)
        await show(context, update.effective_chat.id, msg_id, "Окей, ничего не записал.")
        return
    if not pending:
        await answer(update, "Эта запись уже неактуальна")
        return
    await answer(update)
    data = load(context, update.effective_user.id)
    if data is None:
        return
    context.user_data.pop("pending", None)
    day = date.fromisoformat(pending["day"]) if pending.get("day") else None
    if action == "c":
        idx = int(parts[2])
        cat = data.categories[idx] if idx < len(data.categories) else None
        await record_expense(update, context, data, pending["amount"], pending["comment"], category=cat,
                             edit_message_id=msg_id, day=day)
    elif action == "inc":
        await record_income(update, context, data, pending["amount"], pending["comment"], edit_message_id=msg_id,
                            day=day)
    elif action == "exp":
        await record_expense(update, context, data, pending["amount"], pending["comment"], edit_message_id=msg_id,
                             day=day)


# --- действия с операцией ----------------------------------------------------------------------------
async def on_tx(update: Update, context: Ctx) -> None:
    query = update.callback_query
    parts = query.data.split("|")
    action, tx_id = parts[1], int(parts[2])
    origin = parts[3] if len(parts) > 3 else ""
    if action in ("setcat", "setpay"):
        origin = parts[4] if len(parts) > 4 else ""
    await answer(update)
    data = load(context, update.effective_user.id)
    tx = data.transaction(tx_id) if data else None
    msg_id = query.message.message_id
    chat_id = update.effective_chat.id
    if tx is None:
        await show(context, chat_id, msg_id, "Эта операция уже удалена.")
        return
    suffix = f"|{origin}" if origin else ""
    now = svc(context).now()
    cancel = kb([[("↩️ Отмена", f"tx|view|{tx_id}{suffix}")]])

    if action == "view":
        text, markup = tx_view(data, tx_id, now, origin)
        await show(context, chat_id, msg_id, text, markup)
    elif action == "cat":
        rows = category_buttons(data, f"tx|setcat|{tx_id}|")
        rows = [[(label, f"{cb}{suffix}") for label, cb in row] for row in rows]
        rows.append([("⬅️ Назад", f"tx|view|{tx_id}{suffix}")])
        await show(context, chat_id, msg_id, f"Выбери категорию для {reports.tx_line(data, tx)}", kb(rows))
    elif action == "setcat":
        idx = int(parts[3])
        before = alerts.snapshot(data, now)
        if idx < len(data.categories):
            tx.category = data.categories[idx].name
            save(context, data)
        text, markup = tx_view(data, tx_id, now, origin)
        await show(context, chat_id, msg_id, text, markup)
        await send_limit_alerts(context, data, before, now)
    elif action == "pay":
        methods = data.payment_methods
        rows = chunk([("🏦 Счёт / наличные" if m == ACCOUNT else f"💳 {m}", f"tx|setpay|{tx_id}|{i}{suffix}")
                      for i, m in enumerate(methods)], 2)
        rows.append([("⬅️ Назад", f"tx|view|{tx_id}{suffix}")])
        await show(context, chat_id, msg_id, "Чем оплачено?", kb(rows))
    elif action == "setpay":
        idx = int(parts[3])
        methods = data.payment_methods
        if idx < len(methods):
            tx.payment = methods[idx]
            save(context, data)
        text, markup = tx_view(data, tx_id, now, origin)
        await show(context, chat_id, msg_id, text, markup)
    elif action == "amt":
        set_await(context, "tx_amount", tx_id=tx_id, msg_id=msg_id, origin=origin)
        await show(context, chat_id, msg_id, f"{reports.tx_line(data, tx)}\n\n✏️ Напиши новую сумму:", cancel)
    elif action == "cmt":
        set_await(context, "tx_comment", tx_id=tx_id, msg_id=msg_id, origin=origin)
        await show(context, chat_id, msg_id, f"{reports.tx_line(data, tx)}\n\n💬 Напиши новое описание:", cancel)
    elif action == "date":
        set_await(context, "tx_date", tx_id=tx_id, msg_id=msg_id, origin=origin)
        await show(context, chat_id, msg_id,
                   f"{reports.tx_line(data, tx)}\n\n📅 Когда это было? Напиши: <code>вчера</code>, "
                   "<code>25.09</code> или <code>25 сентября</code>", cancel)
    elif action == "delq":
        await show(context, chat_id, msg_id, f"Удалить операцию?\n{reports.tx_line(data, tx)}",
                   kb([[("🗑 Да, удалить", f"tx|del|{tx_id}{suffix}"), ("↩️ Нет", f"tx|view|{tx_id}{suffix}")]]))
    elif action == "del":
        line = reports.tx_line(data, tx)
        data.remove_transaction(tx_id)
        save(context, data)
        markup = kb([[("⬅️ К списку", f"v|h|{origin[1:]}")]]) if origin.startswith("h") else None
        await show(context, chat_id, msg_id, f"🗑 Удалено: {line}", markup)


async def await_tx_amount(update: Update, context: Ctx, state: dict) -> None:
    value = parse_amount(update.effective_message.text or "")
    await safe_delete(update.effective_message)
    data = load(context, update.effective_user.id)
    tx = data.transaction(state["tx_id"]) if data else None
    if tx is None:
        clear_await(context)
        return
    origin = state.get("origin", "")
    suffix = f"|{origin}" if origin else ""
    if not value:
        await show(context, update.effective_chat.id, state["msg_id"],
                   f"{reports.tx_line(data, tx)}\n\n⚠️ Не понял сумму. Напиши число, например: 45",
                   kb([[("↩️ Отмена", f"tx|view|{tx.id}{suffix}")]]))
        return
    clear_await(context)
    now = svc(context).now()
    before = alerts.snapshot(data, now)
    tx.amount = abs(value)
    save(context, data)
    text, markup = tx_view(data, tx.id, now, origin)
    await show(context, update.effective_chat.id, state["msg_id"], text, markup)
    await send_limit_alerts(context, data, before, now)


async def await_tx_date(update: Update, context: Ctx, state: dict) -> None:
    text = update.effective_message.text or ""
    await safe_delete(update.effective_message)
    data = load(context, update.effective_user.id)
    tx = data.transaction(state["tx_id"]) if data else None
    if tx is None:
        clear_await(context)
        return
    origin = state.get("origin", "")
    suffix = f"|{origin}" if origin else ""
    now = svc(context).now()
    try:
        day = parse_date(text, now.date())
        problem = None if day else "Не понял дату. Напиши: <code>вчера</code>, <code>25.09</code> или " \
                                   "<code>25 сентября</code>"
    except DateError as exc:
        day, problem = None, esc(str(exc))
    if problem:
        await show(context, update.effective_chat.id, state["msg_id"], f"{reports.tx_line(data, tx)}\n\n⚠️ {problem}",
                   kb([[("↩️ Отмена", f"tx|view|{tx.id}{suffix}")]]))
        return
    clear_await(context)
    before = alerts.snapshot(data, now)
    tx.at = min(datetime.combine(day, tx.at.time()), now)
    save(context, data)
    text, markup = tx_view(data, tx.id, now, origin)
    await show(context, update.effective_chat.id, state["msg_id"], text, markup)
    await send_limit_alerts(context, data, before, now)


async def await_tx_comment(update: Update, context: Ctx, state: dict) -> None:
    comment = clean_text(update.effective_message.text or "")
    await safe_delete(update.effective_message)
    clear_await(context)
    data = load(context, update.effective_user.id)
    tx = data.transaction(state["tx_id"]) if data else None
    if tx is None:
        return
    tx.comment = comment
    save(context, data)
    text, markup = tx_view(data, tx.id, svc(context).now(), state.get("origin", ""))
    await show(context, update.effective_chat.id, state["msg_id"], text, markup)


async def undo(update: Update, context: Ctx) -> None:
    data = load(context, update.effective_user.id)
    if data is None:
        await need_setup(update, context)
        return
    mine = [t for t in data.transactions if t.source == SOURCE_BOT]
    if not mine:
        await update.effective_message.reply_text("Нечего отменять — записей из бота пока нет.")
        return
    last = max(mine, key=lambda t: (t.id, t.at))
    line = reports.tx_line(data, last)
    data.remove_transaction(last.id)
    save(context, data)
    await update.effective_message.reply_text(f"↩️ Отменил последнюю запись: {line}")
