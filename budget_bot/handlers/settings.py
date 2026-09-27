"""⚙️ Настройки: всё, что задано в анкете, можно поменять по ходу дела.

Каждый экран — одно сообщение, которое редактируется на месте. Когда нужно ввести значение,
экран превращается в подсказку, пользователь пишет ответ, бот удаляет его сообщение и
возвращает обновлённый экран.
"""

from __future__ import annotations

import re

from telegram import Update

from .. import finance, fmt, planner
from ..categories import CUSTOM_EMOJI, max_cut, normalize, short_label, split_emoji
from ..fmt import esc
from ..models import ACCOUNT, SPECIAL_CATEGORIES, Card, Category, FixedExpense, UserData
from ..parsing import find_numbers, parse_amount, parse_clock, parse_day, parse_items, parse_name
from .common import Ctx, answer, chunk, clear_await, kb, load, need_setup, safe_delete, save, set_await, show, svc

BACK_MAIN = ("⬅️ Назад", "st|main")


# --- экраны --------------------------------------------------------------------------------------------
def main_screen(data: UserData, context: Ctx):
    s = svc(context)
    p = data.profile
    cur = p.currency
    m = lambda x: fmt.money(x, cur)  # noqa: E731
    prefs = s.registry.prefs(data.user_id)
    cards = ", ".join(f"{esc(c.name)} ({c.charge_day}-го)" for c in data.cards) or "нет"
    lines = [
        "⚙️ <b>Настройки</b>", "",
        f"💼 Доход: {m(p.income)}" + (f" · зарплата {p.payday}-го" if p.payday else ""),
        f"🎯 Цель накоплений: {m(p.savings_goal)}",
        f"📌 Постоянные платежи: {m(data.fixed_total)} ({len(data.fixed)})",
        f"🏷 Категорий: {len(data.categories)} · лимиты {m(data.limits_total)}",
    ]
    if p.income > 0:
        lines.append(f"💰 Отложится по плану: {m(data.planned_savings)}")
    lines += [
        f"💳 Карты: {cards}",
        "🔔 Уведомления: сводка " + ("✅" if prefs["summary"] else "❌") + " · лимиты "
        + ("✅" if prefs["limits"] else "❌") + " · напоминание " + (prefs["reminder"] or "❌"),
        f"💱 Валюта: {cur}",
    ]
    rows = [
        [("🏷 Категории и лимиты", "st|cats"), ("📌 Постоянные", "st|fixed")],
        [("💼 Доход", "st|inc"), ("🎯 Накопления", "st|goal")],
        [("💳 Карты", "st|cards"), ("🔔 Уведомления", "st|notify")],
        [("💱 Валюта", "st|cur"), ("🔄 Анкета заново", "st|rerun")],
        [("🗑 Удалить мои данные", "st|del")],
    ]
    return "\n".join(lines), kb(rows)


REMINDER_TIMES = ("20:00", "21:00", "22:00", "23:00")


def _onoff(flag: bool) -> str:
    return "✅ вкл" if flag else "❌ выкл"


def notify_screen(data: UserData, context: Ctx, note: str = ""):
    s = svc(context)
    prefs = s.registry.prefs(data.user_id)
    has_partner = s.registry.partner(data.user_id) is not None
    lines = ([note, ""] if note else []) + [
        "🔔 <b>Уведомления</b>", "",
        f"📬 Сводка за неделю ({s.cfg.summary_days} в {s.cfg.summary_time:%H:%M}): {_onoff(prefs['summary'])}",
        f"⚠️ Предупреждения, когда категория дошла до 80% и 100% лимита: {_onoff(prefs['limits'])}",
        f"👀 Сообщать партнёру о моих лимитах: {_onoff(prefs['partner'])}"
        + ("" if has_partner else " <i>(пары пока нет)</i>"),
        "🌙 Вечернее напоминание, если за день ничего не записано: "
        + (f"в {prefs['reminder']}" if prefs["reminder"] else "❌ выкл"),
    ]
    rows = [
        [("📬 Сводка", "st|ntsum"), ("⚠️ Лимиты", "st|ntlim")],
        [("👀 Партнёру о лимитах", "st|ntpar")],
        [("🌙 Напоминание: " + (prefs["reminder"] or "выкл"), "st|ntrem")],
        [BACK_MAIN],
    ]
    return "\n".join(lines), kb(rows)


def reminder_screen(data: UserData, context: Ctx):
    current = svc(context).registry.prefs(data.user_id)["reminder"]
    text = ("🌙 <b>Вечернее напоминание</b>\n\nЕсли за день не записано ни одной траты, я спрошу, всё ли верно. "
            f"Сейчас: {'в ' + current if current else 'выключено'}.\n\nВыбери время:")
    rows = [
        [((f"✓ {t}" if t == current else t), f"st|ntremset|{i}") for i, t in enumerate(REMINDER_TIMES)],
        [("✍️ Своё время", "st|ntremtxt"), ("🔕 Выключить", "st|ntremoff")],
        [("⬅️ Назад", "st|notify")],
    ]
    return text, kb(rows)


def set_reminder(context: Ctx, user_id: int, value: str) -> None:
    """Сохраняет время напоминания; если сегодня оно уже прошло — начинаем с завтра."""
    s = svc(context)
    fields = {"reminder": value}
    if value and value <= f"{s.now():%H:%M}":
        fields["reminded_on"] = s.today().isoformat()
    s.registry.upsert(user_id, **fields)


def cats_screen(data: UserData, note: str = ""):
    p = data.profile
    m = lambda x: fmt.money(x, p.currency)  # noqa: E731
    lines = ([note, ""] if note else []) + ["🏷 <b>Категории и лимиты</b>", "", f"Лимиты всего: <b>{m(data.limits_total)}</b>"]
    if p.income > 0:
        available = p.income - data.fixed_total - p.savings_goal
        lines.append(f"Доступно на жизнь (доход − постоянные − цель): {m(available)}")
        diff = available - data.limits_total
        if diff > 0.5:
            lines.append(f"✅ Не распределено {m(diff)} — это пойдёт в накопления сверх цели")
        elif diff < -0.5:
            lines.append(f"⚠️ Лимиты больше доступного на {m(-diff)} — накопления выйдут меньше цели")
        else:
            lines.append("✅ Распределено ровно под цель")
    lines += ["", "Нажми на категорию, чтобы поменять лимит, название или ключевые слова."]
    buttons = [(f"{short_label(c.emoji, c.name, c.code)} · {fmt.num(c.limit)}", f"st|cat|{i}")
               for i, c in enumerate(data.categories)]
    rows = chunk(buttons, 2) + [[("➕ Новая категория", "st|catnew")], [BACK_MAIN]]
    return "\n".join(lines), kb(rows)


def cat_screen(data: UserData, idx: int, now, note: str = ""):
    c = data.categories[idx]
    cur = data.profile.currency
    stats = finance.month_stats(data, now.date(), now)
    spent = next((s.spent for s in stats.categories if s.name == c.name), 0.0)
    words = ", ".join(c.keywords[:20]) + ("…" if len(c.keywords) > 20 else "")
    lines = ([note, ""] if note else []) + [
        f"<b>{esc(c.label)}</b>",
        f"Лимит: <b>{fmt.money(c.limit, cur)}</b> в месяц",
        f"Потрачено в {fmt.month_prep(now.date())}: {fmt.money(spent, cur)}",
        f"🔤 Ключевые слова: {esc(words) if words else 'нет'}",
    ]
    rows = [
        [("✏️ Лимит", f"st|catlim|{idx}"), ("✏️ Название", f"st|catname|{idx}")],
        [("🔤 Ключевые слова", f"st|catkw|{idx}"), ("🗑 Удалить", f"st|catdel|{idx}")],
        [("⬅️ К категориям", "st|cats")],
    ]
    return "\n".join(lines), kb(rows)


def _pay_label(payment: str) -> str:
    return "со счёта" if payment == ACCOUNT else f"картой {payment}"


def fixed_screen(data: UserData, note: str = ""):
    cur = data.profile.currency
    lines = ([note, ""] if note else []) + ["📌 <b>Постоянные платежи</b>", ""]
    for f in data.fixed:
        mark = "✅" if f.active else "⏸"
        lines.append(f"{mark} {esc(f.name)} — {fmt.money(f.amount, cur)} · {f.day}-го · {esc(_pay_label(f.payment))}")
    if not data.fixed:
        lines.append("Пока нет ни одного.")
    lines += ["", f"Итого: <b>{fmt.money(data.fixed_total, cur)}</b> в месяц",
              "<i>В день списания я сам записываю платёж в операции.</i>"]
    buttons = [(f"{f.name[:16]} · {fmt.num(f.amount)}", f"st|fx|{i}") for i, f in enumerate(data.fixed)]
    rows = chunk(buttons, 2) + [[("➕ Добавить платёж", "st|fxnew")], [BACK_MAIN]]
    return "\n".join(lines), kb(rows)


def fx_screen(data: UserData, idx: int, note: str = ""):
    f = data.fixed[idx]
    cur = data.profile.currency
    lines = ([note, ""] if note else []) + [
        f"📌 <b>{esc(f.name)}</b>",
        f"Сумма: {fmt.money(f.amount, cur)} в месяц",
        f"День списания: {f.day}-го",
        f"Оплата: {esc(_pay_label(f.payment))}",
        "Статус: " + ("активен ✅" if f.active else "на паузе ⏸"),
    ]
    rows = [
        [("✏️ Сумма", f"st|fxamt|{idx}"), ("📅 День", f"st|fxday|{idx}")],
        [("💳 Оплата", f"st|fxpay|{idx}"), ("⏸ Пауза" if f.active else "▶️ Включить", f"st|fxtog|{idx}")],
        [("✏️ Название", f"st|fxname|{idx}"), ("🗑 Удалить", f"st|fxdel|{idx}")],
        [("⬅️ К платежам", "st|fixed")],
    ]
    return "\n".join(lines), kb(rows)


def income_screen(data: UserData, note: str = ""):
    p = data.profile
    lines = ([note, ""] if note else []) + [
        "💼 <b>Доход</b>", "",
        f"Доход в месяц: <b>{fmt.money(p.income, p.currency)}</b>",
        f"День зарплаты: {p.payday}-го — в этот день я сам записываю доход" if p.payday
        else "День зарплаты не задан — доход записывай сам: <code>+12000 зарплата</code>",
    ]
    rows = [[("✏️ Доход в месяц", "st|incamt"), ("📅 День зарплаты", "st|incday")]]
    if p.payday:
        rows.append([("🚫 Не записывать зарплату сам", "st|incnoday")])
    rows.append([BACK_MAIN])
    return "\n".join(lines), kb(rows)


def goal_screen(data: UserData, note: str = ""):
    p = data.profile
    m = lambda x: fmt.money(x, p.currency)  # noqa: E731
    available = p.income - data.fixed_total - p.savings_goal
    lines = ([note, ""] if note else []) + [
        "🎯 <b>Цель накоплений</b>", "",
        f"Цель: <b>{m(p.savings_goal)}</b> в месяц",
        f"Лимиты категорий: {m(data.limits_total)} · доступно под лимиты: {m(available)}",
        f"Отложится по плану: {m(data.planned_savings)}",
    ]
    rows = [[("✏️ Изменить цель", "st|goalset")]]
    if data.limits_total > available + 0.5 and p.income > 0:
        lines.append("⚠️ Лимиты не дают выйти на цель.")
        rows.append([("🔄 Подогнать лимиты под цель", "st|goalfit")])
    rows.append([BACK_MAIN])
    return "\n".join(lines), kb(rows)


def cards_screen(data: UserData, now, note: str = ""):
    cur = data.profile.currency
    lines = ([note, ""] if note else []) + ["💳 <b>Кредитные карты</b>", ""]
    for c in data.cards:
        when = finance.next_date_for_day(c.charge_day, now.date())
        lines.append(f"💳 {esc(c.name)} — списание {c.charge_day}-го (следующее {fmt.dm(when)}): "
                     f"{fmt.money(finance.card_pending(data, c, now), cur)}")
    if not data.cards:
        lines.append("Карт нет — все траты считаются оплаченными со счёта.")
    rows = chunk([(f"💳 {c.name}", f"st|cd|{i}") for i, c in enumerate(data.cards)], 2)
    rows += [[("➕ Добавить карту", "st|cdnew")], [BACK_MAIN]]
    return "\n".join(lines), kb(rows)


def card_screen(data: UserData, idx: int, now, note: str = ""):
    c = data.cards[idx]
    cur = data.profile.currency
    lines = ([note, ""] if note else []) + [
        f"💳 <b>{esc(c.name)}</b>",
        f"День списания: {c.charge_day}-го",
        f"К списанию сейчас: {fmt.money(finance.card_pending(data, c, now), cur)}",
        f"<i>Сверено {fmt.dmy(c.baseline_at)}: {fmt.money(c.baseline, cur)}</i>",
    ]
    rows = [
        [("✏️ Сумма к списанию", f"st|cdamt|{idx}"), ("📅 День", f"st|cdday|{idx}")],
        [("✏️ Название", f"st|cdname|{idx}"), ("🗑 Удалить", f"st|cddel|{idx}")],
        [("⬅️ К картам", "st|cards")],
    ]
    return "\n".join(lines), kb(rows)


async def show_settings(update: Update, context: Ctx) -> None:
    data = load(context, update.effective_user.id)
    if data is None:
        await need_setup(update, context)
        return
    text, markup = main_screen(data, context)
    await update.effective_message.reply_text(text, reply_markup=markup)


# --- колбэки -------------------------------------------------------------------------------------------
def _prompt(context: Ctx, what: str, msg_id: int, back: str, **params) -> None:
    set_await(context, "settings", what=what, msg_id=msg_id, back=back, **params)


async def on_settings(update: Update, context: Ctx) -> None:
    query = update.callback_query
    parts = query.data.split("|")
    action = parts[1]
    idx = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
    await answer(update)
    data = load(context, update.effective_user.id)
    if data is None:
        await need_setup(update, context)
        return
    s = svc(context)
    now = s.now()
    chat_id = update.effective_chat.id
    msg_id = query.message.message_id
    cur = data.profile.currency

    async def render(text_markup):
        await show(context, chat_id, msg_id, *text_markup)

    async def prompt(text: str, what: str, back: str, **params):
        _prompt(context, what, msg_id, back, **params)
        await show(context, chat_id, msg_id, text, kb([[("↩️ Отмена", back)]]))

    def valid(items: list, i: int | None) -> bool:
        return i is not None and 0 <= i < len(items)

    if action == "main":
        await render(main_screen(data, context))
    # категории
    elif action == "cats":
        await render(cats_screen(data))
    elif action == "cat" and valid(data.categories, idx):
        await render(cat_screen(data, idx, now))
    elif action == "catlim" and valid(data.categories, idx):
        c = data.categories[idx]
        await prompt(f"✏️ Новый месячный лимит для «{esc(c.label)}» (сейчас {fmt.money(c.limit, cur)}):",
                     "catlim", f"st|cat|{idx}", idx=idx, name=c.name)
    elif action == "catname" and valid(data.categories, idx):
        c = data.categories[idx]
        await prompt(f"✏️ Новое название для «{esc(c.label)}». Можно с эмодзи в начале: <code>🎸 Гитара</code>",
                     "catname", f"st|cat|{idx}", idx=idx, name=c.name)
    elif action == "catkw" and valid(data.categories, idx):
        c = data.categories[idx]
        await prompt("🔤 Напиши ключевые слова через запятую — они заменят текущие. Если описание траты "
                     "начинается с такого слова, я сам выберу эту категорию.\n"
                     f"Сейчас: <i>{esc(', '.join(c.keywords[:30])) or 'нет'}</i>",
                     "catkw", f"st|cat|{idx}", idx=idx, name=c.name)
    elif action == "catdel" and valid(data.categories, idx):
        c = data.categories[idx]
        await show(context, chat_id, msg_id,
                   f"Удалить категорию «{esc(c.label)}»? Операции останутся в таблице и будут видны как "
                   "«нет в бюджете».", kb([[("🗑 Да, удалить", f"st|catdelok|{idx}"), ("↩️ Нет", f"st|cat|{idx}")]]))
    elif action == "catdelok" and valid(data.categories, idx):
        removed = data.categories.pop(idx)
        save(context, data)
        await render(cats_screen(data, f"🗑 Категория «{esc(removed.label)}» удалена."))
    elif action == "catnew":
        await prompt("➕ Напиши название и месячный лимит новой категории: <code>🎸 Гитара 200</code>",
                     "catnew", "st|cats")
    # постоянные платежи
    elif action == "fixed":
        await render(fixed_screen(data))
    elif action == "fx" and valid(data.fixed, idx):
        await render(fx_screen(data, idx))
    elif action == "fxamt" and valid(data.fixed, idx):
        f = data.fixed[idx]
        await prompt(f"✏️ Новая сумма для «{esc(f.name)}» в месяц (сейчас {fmt.money(f.amount, cur)}):",
                     "fxamt", f"st|fx|{idx}", idx=idx, name=f.name)
    elif action == "fxday" and valid(data.fixed, idx):
        f = data.fixed[idx]
        await prompt(f"📅 Какого числа списывается «{esc(f.name)}»? (сейчас {f.day}-го)", "fxday", f"st|fx|{idx}",
                     idx=idx, name=f.name)
    elif action == "fxname" and valid(data.fixed, idx):
        f = data.fixed[idx]
        await prompt(f"✏️ Новое название для «{esc(f.name)}»:", "fxname", f"st|fx|{idx}", idx=idx, name=f.name)
    elif action == "fxpay" and valid(data.fixed, idx):
        f = data.fixed[idx]
        methods = data.payment_methods
        f.payment = methods[(methods.index(f.payment) + 1) % len(methods)] if f.payment in methods else ACCOUNT
        save(context, data)
        await render(fx_screen(data, idx))
    elif action == "fxtog" and valid(data.fixed, idx):
        data.fixed[idx].active = not data.fixed[idx].active
        save(context, data)
        await render(fx_screen(data, idx))
    elif action == "fxdel" and valid(data.fixed, idx):
        f = data.fixed[idx]
        await show(context, chat_id, msg_id, f"Удалить платёж «{esc(f.name)}»?",
                   kb([[("🗑 Да, удалить", f"st|fxdelok|{idx}"), ("↩️ Нет", f"st|fx|{idx}")]]))
    elif action == "fxdelok" and valid(data.fixed, idx):
        removed = data.fixed.pop(idx)
        save(context, data)
        await render(fixed_screen(data, f"🗑 Платёж «{esc(removed.name)}» удалён."))
    elif action == "fxnew":
        await prompt("➕ Напиши название и сумму в месяц: <code>Ваад байт 150</code>", "fxnew", "st|fixed")
    # доход и цель
    elif action == "inc":
        await render(income_screen(data))
    elif action == "incamt":
        await prompt(f"✏️ Сколько получаешь в месяц на руки? (сейчас {fmt.money(data.profile.income, cur)})",
                     "incamt", "st|inc")
    elif action == "incday":
        await prompt("📅 Какого числа приходит зарплата? Напиши число от 1 до 31.", "incday", "st|inc")
    elif action == "incnoday":
        data.profile.payday = None
        save(context, data)
        await render(income_screen(data, "✅ Больше не записываю зарплату сам."))
    elif action == "goal":
        await render(goal_screen(data))
    elif action == "goalset":
        await prompt(f"🎯 Сколько хочешь откладывать в месяц? (сейчас {fmt.money(data.profile.savings_goal, cur)})",
                     "goal", "st|goal")
    elif action == "goalfit":
        fit_limits_to_goal(data)
        save(context, data)
        await render(cats_screen(data, "🔄 Лимиты подстроены под цель — гибкие категории урезаны сильнее."))
    # карты
    elif action == "cards":
        await render(cards_screen(data, now))
    elif action == "cd" and valid(data.cards, idx):
        await render(card_screen(data, idx, now))
    elif action == "cdamt" and valid(data.cards, idx):
        c = data.cards[idx]
        await prompt(f"💳 Сколько сейчас к списанию по «{esc(c.name)}»?", "cdamt", f"st|cd|{idx}", idx=idx, name=c.name)
    elif action == "cdday" and valid(data.cards, idx):
        c = data.cards[idx]
        await prompt(f"📅 Какого числа списывается «{esc(c.name)}»?", "cdday", f"st|cd|{idx}", idx=idx, name=c.name)
    elif action == "cdname" and valid(data.cards, idx):
        c = data.cards[idx]
        await prompt(f"✏️ Новое название для «{esc(c.name)}»:", "cdname", f"st|cd|{idx}", idx=idx, name=c.name)
    elif action == "cddel" and valid(data.cards, idx):
        c = data.cards[idx]
        await show(context, chat_id, msg_id,
                   f"Удалить карту «{esc(c.name)}»? Платежи с неё переключу на оплату со счёта.",
                   kb([[("🗑 Да, удалить", f"st|cddelok|{idx}"), ("↩️ Нет", f"st|cd|{idx}")]]))
    elif action == "cddelok" and valid(data.cards, idx):
        removed = data.cards.pop(idx)
        for f in data.fixed:
            if f.payment == removed.name:
                f.payment = ACCOUNT
        save(context, data)
        await render(cards_screen(data, now, f"🗑 Карта «{esc(removed.name)}» удалена."))
    elif action == "cdnew":
        await prompt("➕ Как называется карта? Например: <code>Max</code>", "cdnew", "st|cards", stage="name")
    # прочее
    elif action == "notify":
        await render(notify_screen(data, context))
    elif action in ("ntsum", "ntlim", "ntpar"):
        field = {"ntsum": ("notify", "summary"), "ntlim": ("limit_alerts", "limits"),
                 "ntpar": ("partner_alerts", "partner")}[action]
        s.registry.upsert(data.user_id, **{field[0]: not s.registry.prefs(data.user_id)[field[1]]})
        await render(notify_screen(data, context))
    elif action == "ntrem":
        await render(reminder_screen(data, context))
    elif action == "ntremset" and valid(list(REMINDER_TIMES), idx):
        set_reminder(context, data.user_id, REMINDER_TIMES[idx])
        await render(notify_screen(data, context, f"✅ Напоминание — в {REMINDER_TIMES[idx]}."))
    elif action == "ntremoff":
        set_reminder(context, data.user_id, "")
        await render(notify_screen(data, context, "🔕 Напоминание выключено."))
    elif action == "ntremtxt":
        await prompt("✍️ Во сколько напоминать? Напиши время, например: <code>21:30</code>", "remtime", "st|ntrem")
    elif action == "cur":
        rows = [[(c, f"st|setcur|{i}") for i, c in enumerate(fmt.CURRENCIES)], [BACK_MAIN]]
        await show(context, chat_id, msg_id, "💱 Выбери валюту — суммы в боте и в таблице будут с этим знаком.",
                   kb(rows))
    elif action == "setcur" and valid(list(fmt.CURRENCIES), idx):
        data.profile.currency = fmt.CURRENCIES[idx]
        save(context, data)
        await render(main_screen(data, context))
    elif action == "rerun":
        await show(context, chat_id, msg_id,
                   "🔄 Пройти анкету заново? Операции сохранятся, а лимиты, постоянные платежи и карты я "
                   "пересоберу по новым ответам. Перед этим сохраню копию таблицы.",
                   kb([[("🔄 Да, заново", "st|rerunok"), ("↩️ Нет", "st|main")]]))
    elif action == "rerunok":
        from .onboarding import start_onboarding

        await show(context, chat_id, msg_id, "Начинаем анкету заново 👇")
        await start_onboarding(update, context, rerun=True)
    elif action == "del":
        await show(context, chat_id, msg_id,
                   "⚠️ Удалить все твои данные? Таблица, операции, настройки и связь с партнёром будут удалены "
                   "без возможности восстановления. Сначала можно скачать Excel.",
                   kb([[("🗑 Да, удалить всё", "st|delok")], [("↩️ Нет", "st|main")]]))
    elif action == "delok":
        await delete_everything(update, context, data.user_id)
        await show(context, chat_id, msg_id, "🗑 Все данные удалены. Чтобы начать заново — /start")


def fit_limits_to_goal(data: UserData) -> None:
    items = [planner.PlanItem(code=str(i), emoji=c.emoji, name=c.name, estimate=c.limit, group="",
                              max_cut=max_cut(c)) for i, c in enumerate(data.categories)]
    result = planner.optimize(items, data.profile.income, data.fixed_total, data.profile.savings_goal)
    for i, c in enumerate(data.categories):
        c.limit = result.budgets.get(str(i), c.limit)


async def delete_everything(update: Update, context: Ctx, user_id: int) -> None:
    s = svc(context)
    partner = s.registry.partner(user_id)
    s.store.delete(user_id)
    s.registry.remove(user_id)
    context.user_data.clear()
    if partner:
        try:
            await context.bot.send_message(partner, f"💔 {esc(update.effective_user.first_name or 'Партнёр')} удалил(а) "
                                                    "свои данные — связь пары разорвана.")
        except Exception:  # noqa: BLE001 - партнёр мог заблокировать бота
            pass


# --- ввод значений ---------------------------------------------------------------------------------
def _find(items: list, idx: int | None, name: str | None, key=lambda x: x.name):
    if idx is not None and 0 <= idx < len(items) and key(items[idx]) == name:
        return idx
    for i, item in enumerate(items):
        if key(item) == name:
            return i
    return None


def _category_name_ok(data: UserData, name: str, exclude: Category | None = None) -> str | None:
    if not name:
        return "Название не может быть пустым."
    if name.lower() in (n.lower() for n in SPECIAL_CATEGORIES):
        return "Это служебное название, выбери другое."
    if any(c.name.lower() == name.lower() and c is not exclude for c in data.categories):
        return "Такая категория уже есть."
    return None


async def await_settings(update: Update, context: Ctx, state: dict) -> None:
    text = (update.effective_message.text or "").strip()
    await safe_delete(update.effective_message)
    data = load(context, update.effective_user.id)
    if data is None:
        clear_await(context)
        return
    s = svc(context)
    now = s.now()
    what = state["what"]
    msg_id = state["msg_id"]
    chat_id = update.effective_chat.id
    idx, name = state.get("idx"), state.get("name")

    async def error(msg: str):
        await show(context, chat_id, msg_id, f"⚠️ {msg}", kb([[("↩️ Отмена", state["back"])]]))

    async def done(screen):
        clear_await(context)
        save(context, data)
        await show(context, chat_id, msg_id, *screen)

    if what in ("catlim", "catname", "catkw"):
        i = _find(data.categories, idx, name)
        if i is None:
            clear_await(context)
            await show(context, chat_id, msg_id, *cats_screen(data, "Категория не найдена."))
            return
        cat = data.categories[i]
        if what == "catlim":
            value = parse_amount(text)
            if value is None:
                return await error("Не понял сумму. Напиши число, например: 400")
            cat.limit = value
            return await done(cat_screen(data, i, now, "✅ Лимит обновлён."))
        if what == "catname":
            emoji, raw = split_emoji(text)
            new_name = parse_name(raw)
            problem = _category_name_ok(data, new_name, exclude=cat)
            if problem:
                return await error(problem)
            old = cat.name
            cat.name = new_name
            if emoji:
                cat.emoji = emoji
            for tx in data.transactions:
                if tx.category == old:
                    tx.category = new_name
            return await done(cat_screen(data, i, now, "✅ Категория переименована."))
        words = [normalize(w) for w in re.split(r"[,;\n]", text) if normalize(w)]
        cat.keywords = words[:200]
        return await done(cat_screen(data, i, now, "✅ Ключевые слова сохранены."))

    if what == "remtime":
        value = parse_clock(text)
        if value is None:
            return await error("Не понял время. Напиши, например: 21:30")
        set_reminder(context, data.user_id, value)
        clear_await(context)
        await show(context, chat_id, msg_id, *notify_screen(data, context, f"✅ Напоминание — в {value}."))
        return

    if what == "catnew":
        nums = find_numbers(text)
        limit = abs(nums[0].value) if nums else 0.0
        raw = text[: nums[0].start] + text[nums[0].end:] if nums else text
        emoji, raw = split_emoji(raw.strip())
        new_name = parse_name(raw)
        problem = _category_name_ok(data, new_name)
        if problem:
            return await error(problem)
        data.categories.append(Category(name=new_name, emoji=emoji or CUSTOM_EMOJI, limit=limit,
                                        keywords=[normalize(new_name)]))
        return await done(cats_screen(data, f"✅ Категория «{esc(new_name)}» добавлена."))

    if what in ("fxamt", "fxday", "fxname"):
        i = _find(data.fixed, idx, name)
        if i is None:
            clear_await(context)
            await show(context, chat_id, msg_id, *fixed_screen(data, "Платёж не найден."))
            return
        f = data.fixed[i]
        if what == "fxamt":
            value = parse_amount(text)
            if value is None:
                return await error("Не понял сумму. Напиши число, например: 150")
            f.amount = value
        elif what == "fxday":
            day = parse_day(text)
            if day is None:
                return await error("Нужно число от 1 до 31.")
            f.day = day
        else:
            new_name = parse_name(text)
            if not new_name:
                return await error("Название не может быть пустым.")
            f.name = new_name
        return await done(fx_screen(data, i, "✅ Сохранено."))

    if what == "fxnew":
        try:
            items = parse_items(text, "Платёж")
        except ValueError as exc:
            return await error(f"Не получилось разобрать: {esc(str(exc))}. Пример: <code>Ваад байт 150</code>")
        for fname, amount in items:
            data.fixed.append(FixedExpense(name=fname, amount=amount))
        return await done(fixed_screen(data, "✅ Добавлено. День списания — 1-е, поменять можно в платеже."))

    if what == "incamt":
        value = parse_amount(text)
        if value is None:
            return await error("Не понял сумму. Напиши число, например: 12000")
        data.profile.income = value
        return await done(income_screen(data, "✅ Доход обновлён."))
    if what == "incday":
        day = parse_day(text)
        if day is None:
            return await error("Нужно число от 1 до 31.")
        data.profile.payday = day
        return await done(income_screen(data, "✅ День зарплаты сохранён."))
    if what == "goal":
        value = parse_amount(text)
        if value is None:
            return await error("Не понял сумму. Напиши число, например: 1500")
        data.profile.savings_goal = value
        return await done(goal_screen(data, "✅ Цель обновлена."))

    if what in ("cdamt", "cdday", "cdname"):
        i = _find(data.cards, idx, name)
        if i is None:
            clear_await(context)
            await show(context, chat_id, msg_id, *cards_screen(data, now, "Карта не найдена."))
            return
        card = data.cards[i]
        if what == "cdamt":
            value = parse_amount(text)
            if value is None:
                return await error("Не понял сумму. Напиши число, например: 2300")
            card.baseline, card.baseline_at = value, now
        elif what == "cdday":
            day = parse_day(text)
            if day is None:
                return await error("Нужно число от 1 до 31.")
            card.charge_day = day
        else:
            new_name = parse_name(text, 30)
            if not new_name or new_name.lower() == ACCOUNT.lower() or any(
                    c.name.lower() == new_name.lower() for c in data.cards if c is not card):
                return await error("Такое название не подходит — выбери другое.")
            old = card.name
            card.name = new_name
            for tx in data.transactions:
                if tx.payment == old:
                    tx.payment = new_name
            for f in data.fixed:
                if f.payment == old:
                    f.payment = new_name
        return await done(card_screen(data, i, now, "✅ Сохранено."))

    if what == "cdnew":
        stage = state.get("stage")
        if stage == "name":
            new_name = parse_name(text, 30)
            if not new_name or new_name.lower() == ACCOUNT.lower() or data.card(new_name):
                return await error("Такое название не подходит — выбери другое.")
            _prompt(context, "cdnew", msg_id, "st|cards", stage="day", card_name=new_name)
            await show(context, chat_id, msg_id, f"📅 Какого числа списывается «{esc(new_name)}»?",
                       kb([[("↩️ Отмена", "st|cards")]]))
            return
        if stage == "day":
            day = parse_day(text)
            if day is None:
                return await error("Нужно число от 1 до 31.")
            _prompt(context, "cdnew", msg_id, "st|cards", stage="amount", card_name=state["card_name"], day=day)
            await show(context, chat_id, msg_id, f"💳 Какая сейчас сумма к списанию по «{esc(state['card_name'])}»?",
                       kb([[("↩️ Отмена", "st|cards")]]))
            return
        value = parse_amount(text)
        if value is None:
            return await error("Не понял сумму. Напиши число, например: 0 или 1500")
        data.cards.append(Card(name=state["card_name"], charge_day=state["day"], baseline=value, baseline_at=now))
        return await done(cards_screen(data, now, f"✅ Карта «{esc(state['card_name'])}» добавлена. Новые траты по "
                                                  "умолчанию идут на первую карту в списке."))
    clear_await(context)


# --- кнопки под вечерним напоминанием «rm|…» --------------------------------------------------------------
async def on_reminder(update: Update, context: Ctx) -> None:
    query = update.callback_query
    action = query.data.split("|")[1]
    await answer(update)
    chat_id = update.effective_chat.id
    msg_id = query.message.message_id
    if action == "ok":
        await show(context, chat_id, msg_id, "👍 Отлично — день без трат! До завтра.")
    elif action == "off":
        set_reminder(context, update.effective_user.id, "")
        await show(context, chat_id, msg_id, "🔕 Напоминания выключены. Включить снова: ⚙️ Настройки → 🔔 Уведомления.")
