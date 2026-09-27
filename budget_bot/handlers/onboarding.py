"""Первичная настройка: анкета → анализ → предложенный бюджет, который можно поправить «на месте».

Анкета — это один «экран» (сообщение), который редактируется на месте: вопрос, кнопки с частыми
ответами и «⬅️ Назад». Ответ можно нажать кнопкой или написать текстом — сообщение пользователя
бот удаляет, чтобы чат оставался чистым. Черновик ответов лежит в context.user_data["ob"].
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from telegram import Update
from telegram.ext import ContextTypes

from .. import charts, finance, fmt, planner
from ..categories import CUSTOM_EMOJI, DEFAULTS, EXTRA_CODES, make_category, short_label
from ..fmt import esc
from ..models import ACCOUNT, Card, Category, FixedExpense, Profile, SurveyItem, UserData
from ..parsing import (
    clean_text,
    detect_period,
    parse_amount,
    parse_day,
    parse_items,
    parse_name,
    to_monthly,
)
from .common import MAIN_MENU, answer, clear_await, kb, safe_delete, show, svc

log = logging.getLogger(__name__)
Ctx = ContextTypes.DEFAULT_TYPE

SECTIONS = {1: "🏠 Постоянные платежи", 2: "💼 Доход и деньги", 3: "🛒 Основные траты", 4: "🎯 Привычки",
            5: "💰 Цель накоплений"}
PERIOD_LABEL = {"week": "в неделю", "month": "в месяц", "day": "в день", "year": "в год"}
CARD_NAMES = ["Max", "Isracard", "Cal", "Amex", "Visa", "Кредитка"]


@dataclass
class Q:
    section: int
    kind: str  # money | choice | day | list | multi | text
    text: str | Callable[[dict, str | None, str], str]
    title: str = ""  # короткое название для листа «Анкета»
    presets: tuple = ()
    none: tuple | None = None  # (подпись, значение) для кнопки «нет»
    unit: str = "month"  # в чём ввод по умолчанию: month | week | each (разовая сумма)
    allow_negative: bool = False
    options: tuple = ()  # для choice: ((подпись, значение), …)
    free_text: bool = False  # choice, где можно написать своё (название карты)


def _card(d: dict, param: str | None) -> dict:
    i = int(param or 0)
    while len(d["cards"]) <= i:
        d["cards"].append({})
    return d["cards"][i]


QUESTIONS: dict[str, Q] = {
    "rent": Q(1, "money", "🏠 <b>Жильё</b>\nСколько платишь за квартиру в месяц? Аренда, ипотека или твоя доля, "
                          "если снимаете вместе.", "Жильё", none=("Не плачу", 0)),
    "bills": Q(1, "money", "💡 <b>Коммунальные счета и связь</b>\nСколько в среднем в месяц уходит на электричество, "
                           "воду, газ, интернет и мобильный?\n<i>Если счёт приходит раз в 2 месяца — раздели на 2.</i>",
               "Счета", none=("Входят в аренду", 0)),
    "arnona": Q(1, "money", "🏛 <b>Арнона</b>\nСколько арноны выходит в месяц?\n"
                            "<i>Платишь раз в 2 месяца — раздели на 2.</i>", "Арнона", none=("Не плачу", 0)),
    "subs": Q(1, "list", "📺 <b>Подписки</b>\nNetflix, Spotify, YouTube, iCloud, ChatGPT, спортзал… "
                         "Перечисли через запятую:\n<code>Netflix 50, Spotify 20, iCloud 13</code>\n"
                         "или напиши общую сумму: <code>120</code>", "Подписки", none=("Нет подписок", None)),
    "other_fixed": Q(1, "list", "📌 <b>Другие постоянные платежи</b>\nЧто ещё списывается каждый месяц? Ваад байт, "
                                "страховка, кредит, рассрочка (תשלומים), садик, учёба…\n"
                                "<code>Ваад байт 150, Страховка 120</code>", "Другие платежи",
                     none=("Больше ничего", None)),
    "income": Q(2, "money", "💼 <b>Доход</b>\nСколько ты примерно получаешь в месяц на руки? Зарплата, стипендия, "
                            "помощь родителей, подработки — всё вместе.", "Доход", none=("Пока нет дохода", 0)),
    "payday": Q(2, "day", "📅 <b>День зарплаты</b>\nКакого числа обычно приходит основной доход? В этот день я сам "
                          "запишу его — сумму всегда можно поправить.", "День зарплаты", presets=(1, 5, 9, 10, 15),
                none=("Нерегулярно", None)),
    "balance": Q(2, "money", "🏦 <b>Счёт</b>\nСколько сейчас денег на банковском счёте?\n"
                             "<i>Если в минусе — напиши с минусом: <code>-1500</code></i>", "Баланс счёта",
                 unit="each", allow_negative=True),
    "has_card": Q(2, "choice", "💳 <b>Кредитная карта</b>\nПлатишь кредиткой? Израильские карты списываются раз в месяц "
                               "одной суммой — я буду это учитывать, чтобы знать, сколько денег реально свободно.",
                  "Кредитка", options=(("Да", True), ("Нет", False))),
    "card_name": Q(2, "choice", lambda d, p, cur: f"💳 <b>Карта {int(p) + 1}</b>\nКак назовём карту? Выбери или "
                                                  "напиши своё название.",
                   options=tuple((n, n) for n in CARD_NAMES), free_text=True),
    "card_day": Q(2, "day", lambda d, p, cur: f"📅 Какого числа списывается «{esc(_card(d, p).get('name', ''))}»?",
                  presets=(2, 10, 15, 20)),
    "card_amount": Q(2, "money", lambda d, p, cur: f"💳 Какая сейчас сумма к списанию по «{esc(_card(d, p).get('name', ''))}»?"
                                                   "\n<i>Посмотри в приложении банка или карты.</i>",
                     unit="each", none=("0 — ничего", 0)),
    "card_more": Q(2, "choice", "Есть ещё кредитка с другой датой списания?",
                   options=(("➕ Да, добавить", True), ("Нет, это всё", False))),
    "groceries": Q(3, "money", "🛒 <b>Продукты</b>\nСколько в среднем уходит на продукты и супермаркет <b>в неделю</b>?\n"
                               "<i>Если привычнее за месяц — так и напиши: «1600 в месяц».</i>", "Продукты",
                   presets=(150, 250, 400, 600), unit="week"),
    "transport": Q(3, "money", "🚌 <b>Транспорт</b>\nРав-Кав, автобусы, такси, бензин, парковка — сколько в месяц?",
                   "Транспорт", presets=(100, 250, 500, 900), none=("Не трачу", 0)),
    "health": Q(3, "money", "💊 <b>Здоровье</b>\nАптека, лекарства, врачи, линзы — сколько в среднем в месяц?",
                "Здоровье", presets=(50, 100, 200), none=("Почти не трачу", 0)),
    "clothes": Q(3, "money", "👕 <b>Одежда и обувь</b>\nСколько в среднем в месяц? Если покупаешь раз в сезон — "
                             "прикинь среднее.", "Одежда", presets=(100, 200, 400), none=("Почти не трачу", 0)),
    "hobby": Q(4, "money", "🎨 <b>Хобби</b>\nЕсть хобби, на которое уходят деньги? Музыка, рисование, фото, "
                           "коллекции, настолки… Сколько в месяц?", "Хобби", presets=(100, 250, 500),
               none=("Нет хобби", 0)),
    "cafe_freq": Q(4, "choice", "☕ <b>Кафе и кофе с собой</b>\nКак часто берёшь кофе, перекус или заходишь в кафе?",
                   "Кафе — как часто", options=(("Почти никогда", 0), ("1–2 раза в неделю", 6.5),
                                               ("3–5 раз в неделю", 17), ("Каждый день", 30),
                                               ("Несколько раз в день", 60))),
    "cafe_check": Q(4, "money", "☕ Сколько обычно выходит за раз?", "Кафе — чек", presets=(15, 25, 40, 60),
                    unit="each"),
    "rest_freq": Q(4, "choice", "🍽 <b>Рестораны и бары</b>\nКак часто ходишь в рестораны или бары?",
                   "Рестораны — как часто", options=(("Почти никогда", 0), ("1–2 раза в месяц", 1.5),
                                                    ("Раз в неделю", 4.3), ("2+ раза в неделю", 9))),
    "rest_check": Q(4, "money", "🍽 Сколько обычно выходит на тебя, с чаевыми?", "Рестораны — чек",
                    presets=(80, 120, 180, 250), unit="each"),
    "delivery_freq": Q(4, "choice", "🛵 <b>Доставка еды</b>\nЗаказываешь еду домой (Wolt, 10bis и т.п.)?",
                       "Доставка — как часто", options=(("Почти никогда", 0), ("1–2 раза в месяц", 1.5),
                                                       ("Раз в неделю", 4.3), ("2–3 раза в неделю", 11),
                                                       ("Почти каждый день", 25))),
    "delivery_check": Q(4, "money", "🛵 Средний заказ?", "Доставка — чек", presets=(50, 70, 100, 150), unit="each"),
    "tobacco": Q(4, "money", "🚬 <b>Сигареты, табак, вейп</b>\nСколько уходит <b>в неделю</b>?", "Сигареты",
                 presets=(50, 100, 200, 300), none=("Не курю", 0), unit="week"),
    "games": Q(4, "money", "🎮 <b>Игры</b>\nПокупки в Steam/PlayStation, донаты, игровые подписки — сколько в месяц?",
               "Игры", presets=(50, 100, 200, 400), none=("Не трачу", 0)),
    "partner": Q(4, "money", "❤️ <b>Вторая половинка</b>\nСвидания, подарки, сюрпризы — сколько в месяц уходит "
                             "на отношения?", "Вторая половинка", presets=(200, 400, 700, 1000),
                 none=("Нет / не трачу", 0)),
    "impulse_freq": Q(4, "choice", "🛍 <b>Спонтанные покупки</b>\nСладости, мелочи, AliExpress, «увидел — захотел»… "
                                   "Как часто так бывает?", "Спонтанные — как часто",
                      options=(("Почти никогда", 0), ("Пару раз в месяц", 2.5), ("Раз в неделю", 4.3),
                               ("Несколько раз в неделю", 12))),
    "impulse_check": Q(4, "money", "🛍 Обычная сумма такой покупки?", "Спонтанные — сумма",
                       presets=(20, 50, 100, 200), unit="each"),
    "unexpected_freq": Q(4, "choice", "⚡ <b>Неожиданные расходы</b>\nРемонт, врач, штраф, разбитый телефон… "
                                      "Как часто случается что-то непредвиденное?", "Непредвиденное — как часто",
                         options=(("Почти никогда", 0.25), ("Раз в пару месяцев", 0.5),
                                  ("Каждый месяц что-то", 1), ("Постоянно 😅", 2))),
    "unexpected_amount": Q(4, "money", "⚡ И сколько обычно стоит такая «неожиданность»?", "Непредвиденное — сумма",
                           presets=(200, 500, 1000, 2000), unit="each"),
    "extras": Q(4, "multi", "📝 <b>На что ещё тратишься часто?</b>\nОтметь всё подходящее и нажми «Готово».",
                "Другие траты"),
    "extra_amt": Q(4, "money", lambda d, p, cur: f"{DEFAULTS[p].emoji} <b>{DEFAULTS[p].name}</b>\n"
                                                 "Сколько в месяц примерно?"),
    "custom": Q(4, "list", "✍️ <b>Свои категории</b>\nЕсть траты, которых не было в списке? Напиши название и сумму "
                           "в месяц:\n<code>Танцы 250, Косметика 100</code>", "Свои категории", none=("Нет, всё", None)),
    "notes": Q(4, "text", "💬 <b>Нюансы</b>\nЕсть что-то важное для бюджета? Копишь на отпуск, отдаёшь долг, скоро "
                          "крупная покупка… Напиши пару слов — сохраню в профиль.", "Нюансы", none=("Нет", "")),
}

LINEAR = ["intro", "rent", "bills", "arnona", "subs", "other_fixed", "income", "payday", "balance", "has_card",
          "groceries", "transport", "health", "clothes", "hobby", "cafe_freq", "cafe_check", "rest_freq",
          "rest_check", "delivery_freq", "delivery_check", "tobacco", "games", "partner", "impulse_freq",
          "impulse_check", "unexpected_freq", "unexpected_amount", "extras", "custom", "notes", "savings",
          "proposal"]
FREQ_TO_CHECK = {"cafe_freq": "cafe_check", "rest_freq": "rest_check", "delivery_freq": "delivery_check",
                 "impulse_freq": "impulse_check"}


# --- черновик ------------------------------------------------------------------------------------
def new_draft(rerun: bool, invite: str | None) -> dict:
    return {"pos": "intro", "hist": [], "a": {"disp": {}}, "cards": [], "msg_id": None, "rerun": rerun,
            "invite": invite, "prop": None, "edit": None}


def draft(context: Ctx) -> dict | None:
    return context.user_data.get("ob")


def _split(pos: str) -> tuple[str, str | None]:
    sid, _, param = pos.partition(".")
    return sid, (param or None)


def fixed_total(a: dict) -> float:
    return sum(v for _, v in planner.fixed_items(a))


def complete_cards(d: dict) -> list[dict]:
    if not d["a"].get("has_card"):
        return []
    cards = []
    for c in d["cards"]:
        if not c.get("name") or not c.get("day"):
            break
        cards.append(c)
        if not c.get("more"):
            break
    return cards


def next_pos(d: dict, pos: str) -> str:
    sid, param = _split(pos)
    a = d["a"]
    if sid == "income" and (a.get("income") or 0) <= 0:
        return "balance"
    if sid == "has_card":
        return "card_name.0" if a.get("has_card") else "groceries"
    if sid == "card_name":
        return f"card_day.{param}"
    if sid == "card_day":
        return f"card_amount.{param}"
    if sid == "card_amount":
        return f"card_more.{param}"
    if sid == "card_more":
        return f"card_name.{int(param) + 1}" if _card(d, param).get("more") else "groceries"
    if sid in FREQ_TO_CHECK and not a.get(sid):
        return LINEAR[LINEAR.index(FREQ_TO_CHECK[sid]) + 1]
    if sid == "extras":
        sel = a.get("extras") or []
        return f"extra_amt.{sel[0]}" if sel else "custom"
    if sid == "extra_amt":
        sel = a.get("extras") or []
        i = sel.index(param) if param in sel else len(sel)
        return f"extra_amt.{sel[i + 1]}" if i + 1 < len(sel) else "custom"
    if sid == "notes":
        return "savings" if (a.get("income") or 0) > 0 else "proposal"
    return LINEAR[LINEAR.index(sid) + 1]


def _answer_key(sid: str, param: str | None) -> str:
    return f"{sid}.{param}" if param else sid


# --- отрисовка вопроса ------------------------------------------------------------------------------
def _presets(q: Q, cur: str) -> list[float]:
    return [fmt.scaled(p, cur) for p in q.presets]


def _nav(d: dict, pos: str) -> list[tuple[str, str]]:
    row = []
    if d["hist"]:
        row.append(("⬅️ Назад", f"ob|{pos}|b"))
    if d.get("rerun"):
        row.append(("✖️ Отменить", f"ob|{pos}|x"))
    return row


def _context_line(d: dict, sid: str, cur: str) -> str:
    a = d["a"]
    if sid == "income":
        return f"📌 Постоянные платежи: {fmt.money(fixed_total(a), cur)} в месяц ✔️\n\n"
    if sid == "groceries":
        income = a.get("income") or 0
        if income > 0:
            free = income - fixed_total(a)
            return (f"После постоянных платежей остаётся ≈ {fmt.money(free, cur)} в месяц. "
                    "Разложим их по категориям 👇\n\n")
    return ""


def render(d: dict, cur: str, name: str = "") -> tuple[str, object]:
    pos = d["pos"]
    sid, param = _split(pos)
    if sid == "intro":
        return _render_intro(d, name)
    if sid == "savings":
        return _render_savings(d, cur)
    if sid == "proposal":
        return _render_proposal(d, cur)

    q = QUESTIONS[sid]
    text = q.text(d, param, cur) if callable(q.text) else q.text
    header = f"<b>[{q.section}/5] {SECTIONS[q.section]}</b>\n\n"
    body = header + _context_line(d, sid, cur) + text
    rows: list[list[tuple[str, str]]] = []
    if q.kind == "money":
        presets = _presets(q, cur) if sid != "extra_amt" else [fmt.scaled(p, cur) for p in DEFAULTS[param].presets]
        buttons = [(fmt.money(v, cur), f"ob|{pos}|v|{i}") for i, v in enumerate(presets)]
        rows += [buttons[i: i + 4] for i in range(0, len(buttons), 4)]
        body += "\n\n✍️ Напиши сумму" + (" или выбери вариант" if presets else "")
    elif q.kind == "day":
        rows.append([(f"{v}-го", f"ob|{pos}|v|{i}") for i, v in enumerate(q.presets)])
        body += "\n\n✍️ Выбери или напиши число месяца"
    elif q.kind == "choice":
        buttons = [(label, f"ob|{pos}|c|{i}") for i, (label, _) in enumerate(q.options)]
        per_row = 3 if sid == "card_name" else 2 if len(buttons) <= 4 else 1
        rows += [buttons[i: i + per_row] for i in range(0, len(buttons), per_row)]
    elif q.kind == "multi":
        selected = set(d["a"].get("extras") or [])
        buttons = [((("✅ " if code in selected else "") + f"{DEFAULTS[code].emoji} {DEFAULTS[code].name}"),
                    f"ob|{pos}|t|{code}") for code in EXTRA_CODES]
        rows += [buttons[i: i + 2] for i in range(0, len(buttons), 2)]
        rows.append([("✅ Готово" if selected else "Ничего из этого", f"ob|{pos}|d")])
    elif q.kind in ("list", "text"):
        body += "\n\n✍️ Напиши ответ сообщением"
    if q.none:
        rows.append([(q.none[0], f"ob|{pos}|n")])
    rows.append(_nav(d, pos))
    if d.get("error"):
        body += f"\n\n⚠️ {d['error']}"
    return body, kb(rows)


def _render_intro(d: dict, name: str) -> tuple[str, object]:
    if d.get("rerun"):
        text = ("🔄 <b>Пройдём анкету заново</b>\n\nОперации сохранятся, а лимиты, постоянные платежи и карты я "
                "пересоберу по новым ответам.")
        rows = [[("🚀 Начать", "ob|intro|go")], [("✖️ Отменить", "ob|intro|x")]]
    else:
        hello = f", {esc(name)}" if name else ""
        text = (f"👋 <b>Привет{hello}!</b>\n\n"
                "Я помогу вести бюджет: записывать траты за пару секунд, следить за лимитами и откладывать деньги. "
                "Подхожу и для одного, и для пары.\n\n"
                "Сначала за 3–5 минут составим твой бюджет:\n"
                "1️⃣ постоянные платежи\n2️⃣ доход, счёт и кредитка\n3️⃣ основные траты\n"
                "4️⃣ привычки — чтобы я предложил оптимальные лимиты\n5️⃣ цель накоплений\n\n"
                "Всё сохраняется в твою личную Excel-таблицу, и всё можно поменять потом.")
        rows = [[("🚀 Начать настройку", "ob|intro|go")]]
    return text, kb(rows)


def _items(d: dict) -> list[planner.PlanItem]:
    return planner.plan_items(d["a"], d["a"].get("income") or 0)


def _render_savings(d: dict, cur: str) -> tuple[str, object]:
    a = d["a"]
    income = a.get("income") or 0
    fixed = fixed_total(a)
    items = _items(d)
    est = sum(i.estimate for i in items)
    free = income - fixed - est
    rec = planner.recommend_savings(income, fixed, items)
    m = lambda x: fmt.money(x, cur)  # noqa: E731
    lines = [f"<b>[5/5] {SECTIONS[5]}</b>", "", "Посчитаем твой месяц:",
             f"💼 Доход: {m(income)}", f"📌 Постоянные: {fmt.MINUS} {m(fixed)}",
             f"🧾 Обычные траты (по ответам): {fmt.MINUS} {m(est)}", "━━━━━━━━━━━━",
             f"Остаётся: <b>≈ {m(free)}</b>", "",
             "💰 <b>Сколько в идеале хочешь откладывать в конце месяца?</b>"]
    if rec > 0:
        if free >= 0.2 * income:
            why = "урезать ничего не придётся, и ещё останется запас"
        elif free >= rec:
            why = "это всё, что остаётся после обычных трат"
        else:
            why = "для этого немного подрежем гибкие траты"
        lines.append(f"Рекомендую <b>{m(rec)}</b> ({fmt.pct(rec / income)} дохода): {why}.")
    lines.append("\n✍️ Выбери вариант или напиши свою сумму.")
    if d.get("error"):
        lines.append(f"\n⚠️ {d['error']}")
    options = []
    if rec > 0:
        options.append(rec)
    for share in (0.10, 0.15, 0.20):
        v = fmt.nice_round(income * share, down=True)
        if v > 0 and v not in options:
            options.append(v)
    d["savings_options"] = options
    rows = []
    if rec > 0:
        rows.append([(f"✅ {m(rec)} — рекомендую", "ob|savings|v|0")])
    pct_buttons = [(f"{fmt.pct(v / income)} · {m(v)}", f"ob|savings|v|{i}") for i, v in enumerate(options)
                   if not (rec > 0 and i == 0)]
    if pct_buttons:
        rows.append(pct_buttons)
    rows.append([("Пока не буду копить", "ob|savings|n")])
    rows.append(_nav(d, "savings"))
    return "\n".join(lines), kb(rows)


def _proposal_state(d: dict) -> dict:
    a = d["a"]
    items = _items(d)
    prop = d.get("prop") or {"locked": {}, "goal": a.get("savings_goal", 0)}
    result = planner.optimize(items, a.get("income") or 0, fixed_total(a), prop["goal"], prop["locked"])
    prop["budgets"] = result.budgets
    d["prop"] = prop
    return {"items": items, "result": result}


def analysis_text(d: dict, cur: str, today) -> str:
    a = d["a"]
    st = _proposal_state(d)
    items, result = st["items"], st["result"]
    income = a.get("income") or 0
    fixed = fixed_total(a)
    goal = d["prop"]["goal"]
    est_total = sum(i.estimate for i in items)
    cards = complete_cards(d)
    pending = sum(c.get("pending") or 0 for c in cards)
    pending_date = None
    if cards:
        pending_date = fmt.dm(min(finance.next_date_for_day(c["day"], today) for c in cards))
    m = lambda x: fmt.money(x, cur)  # noqa: E731
    lines = ["🔍 <b>Мой анализ</b>", ""]
    if income > 0:
        lines.append(f"💼 Доход: {m(income)}")
    lines.append(f"📌 Постоянные: {m(fixed)}" + (f" ({fmt.pct(fixed / income)} дохода)" if income > 0 else ""))
    lines.append(f"🧾 Переменные траты сейчас (оценка): {m(est_total)}")
    if goal > 0:
        lines.append(f"🎯 Хочешь откладывать: {m(goal)}")
        gap = est_total - (income - fixed - goal)
        if gap > 0:
            lines.append(f"➡️ Для этого траты нужно сократить на ≈ {m(gap)} — ниже мой вариант, как это сделать.")
        else:
            lines.append("➡️ Цель достижима без сокращений 👍")
    shares = planner.structure(items, result.budgets, income, fixed)
    if shares:
        needs, wants, savings = shares
        lines.append(f"📐 Структура: обязательное {fmt.pct(needs)} · желания {fmt.pct(wants)} · "
                     f"накопления {fmt.pct(max(savings, 0))} (ориентир 50/30/20)")
    tips = planner.insights(a, items, result, income, fixed, goal, cur, balance=a.get("balance"),
                            pending=pending or None, pending_date=pending_date)
    if tips:
        lines.append("")
        lines.append("💡 <b>Наблюдения</b>")
        lines += [f"• {t}" for t in tips]
    return "\n".join(lines)


def _render_proposal(d: dict, cur: str) -> tuple[str, object]:
    a = d["a"]
    st = _proposal_state(d)
    items, result = st["items"], st["result"]
    prop = d["prop"]
    income = a.get("income") or 0
    fixed = fixed_total(a)
    goal = prop["goal"]
    m = lambda x: fmt.money(x, cur)  # noqa: E731
    lines = ["📋 <b>Предлагаемый бюджет на месяц</b>", ""]
    if income > 0:
        lines.append(f"💼 Доход: {m(income)}")
    lines.append(f"📌 Постоянные: {m(fixed)}")
    if goal > 0:
        lines.append(f"🎯 Цель накоплений: {m(goal)}")
    lines.append("")
    for it in items:
        budget = result.budgets.get(it.code, 0)
        note = ""
        if it.code in prop["locked"]:
            note = " ✏️"
        elif it.code in result.cuts:
            note = f" <i>(сейчас ≈ {fmt.num(it.estimate)})</i>"
        lines.append(f"{it.emoji} {esc(it.name)} — <b>{m(budget)}</b>{note}")
    total = sum(result.budgets.values())
    lines.append("━━━━━━━━━━━━")
    lines.append(f"🧾 На переменные траты: <b>{m(total)}</b>")
    if income > 0:
        planned = result.planned_savings
        if goal > 0 and planned + 1 < goal:
            lines.append(f"💰 Отложится: <b>{m(planned)}</b> ⚠️ меньше цели на {m(goal - planned)}")
        elif planned >= 0:
            extra = f" (цель {m(goal)} ✅)" if goal > 0 else ""
            lines.append(f"💰 Отложится: <b>{m(planned)}</b>{extra}")
        else:
            lines.append(f"🔴 Расходы больше дохода на {m(-planned)}")
    lines.append("")
    edit = d.get("edit")
    if edit == "savings":
        lines.append("✏️ <b>Напиши, сколько хочешь откладывать в месяц</b> — лимиты подстрою под цель.")
    elif edit:
        it = next((i for i in items if i.code == edit), None)
        if it:
            lines.append(f"✏️ <b>Напиши новый лимит для «{esc(it.name)}»</b> (сейчас "
                         f"{m(result.budgets.get(it.code, 0))}).")
    else:
        lines.append("Нажми на категорию, чтобы поставить свою сумму, или сохрани бюджет.")
    if d.get("error"):
        lines.append(f"⚠️ {d['error']}")

    rows: list[list[tuple[str, str]]] = []
    if edit:
        rows.append([("↩️ Отмена", "ob|proposal|c")])
    else:
        buttons = [(f"{short_label(it.emoji, it.name, it.code if not it.code.startswith('custom') else '')} · "
                    f"{fmt.num(result.budgets.get(it.code, 0))}", f"ob|proposal|e|{i}") for i, it in enumerate(items)]
        rows += [buttons[i: i + 2] for i in range(0, len(buttons), 2)]
        if income > 0:
            rows.append([(f"💰 Цель: {m(goal)}", "ob|proposal|s")])
            if prop["locked"]:
                rows.append([("🔄 Сбросить мои правки", "ob|proposal|r")])
        rows.append([("⬅️ Назад", "ob|proposal|b"), ("✅ Сохранить бюджет", "ob|proposal|ok")])
    return "\n".join(lines), kb(rows)


# --- вход в анкету ---------------------------------------------------------------------------------
async def start_onboarding(update: Update, context: Ctx, rerun: bool = False, invite: str | None = None) -> None:
    chat_id = update.effective_chat.id
    clear_await(context)
    d = draft(context)
    if d and not rerun:
        if invite:
            d["invite"] = invite
        text, markup = render(d, _cur(context, d), update.effective_user.first_name or "")
        msg = await context.bot.send_message(chat_id, "Продолжим настройку с того места, где остановились 👇\n\n" + text,
                                             reply_markup=markup)
        d["msg_id"] = msg.message_id
        return
    d = new_draft(rerun, invite)
    existing = svc(context).store.load(update.effective_user.id)
    d["cur"] = existing.profile.currency if existing else svc(context).cfg.currency
    context.user_data["ob"] = d
    text, markup = render(d, _cur(context, d), update.effective_user.first_name or "")
    msg = await context.bot.send_message(chat_id, text, reply_markup=markup)
    d["msg_id"] = msg.message_id


def _cur(context: Ctx, d: dict) -> str:
    return d.get("cur") or svc(context).cfg.currency


async def _refresh(update: Update, context: Ctx, d: dict) -> None:
    if d["pos"] == "proposal" and d.get("prop") is None:  # без дохода шаг «цель» пропускается
        d["prop"] = {"locked": {}, "goal": 0.0}
        await _enter_proposal(update, context, d, _cur(context, d))
        return
    text, markup = render(d, _cur(context, d), update.effective_user.first_name or "")
    d["msg_id"] = await show(context, update.effective_chat.id, d.get("msg_id"), text, markup)


def _go_next(d: dict) -> None:
    d["hist"].append(d["pos"])
    d["pos"] = next_pos(d, d["pos"])
    d["error"] = None


def _store(d: dict, sid: str, param: str | None, value, disp: str) -> None:
    a = d["a"]
    if sid == "card_name":
        _card(d, param)["name"] = value
    elif sid == "card_day":
        _card(d, param)["day"] = value
    elif sid == "card_amount":
        _card(d, param)["pending"] = value
    elif sid == "card_more":
        _card(d, param)["more"] = value
    elif sid == "extra_amt":
        a.setdefault("extra_amt", {})[param] = value
    else:
        a[sid] = value
    a.setdefault("disp", {})[_answer_key(sid, param)] = disp


def _money_disp(value: float, period: str | None, q: Q, cur: str) -> str:
    if q.unit == "each" and not period:
        return fmt.money(value, cur)
    return f"{fmt.money(value, cur)} {PERIOD_LABEL[period or q.unit]}"


def _unique_card_name(d: dict, name: str, index: int) -> str:
    taken = {c.get("name", "").lower() for i, c in enumerate(d["cards"]) if i != index}
    base, n = name, 2
    while name.lower() in taken or name.lower() == ACCOUNT.lower():
        name = f"{base} {n}"
        n += 1
    return name


# --- обработка ответов ------------------------------------------------------------------------------
async def on_callback(update: Update, context: Ctx) -> None:
    query = update.callback_query
    d = draft(context)
    parts = (query.data or "").split("|")
    if not d or len(parts) < 3:
        await answer(update, "Анкета уже закрыта")
        return
    _, pos, action, *rest = parts
    arg = rest[0] if rest else None
    if pos != d["pos"] or (query.message and d.get("msg_id") and query.message.message_id != d["msg_id"]):
        await answer(update, "Эта кнопка уже неактуальна")
        return
    await answer(update)
    cur = _cur(context, d)
    sid, param = _split(pos)

    if action == "x":
        context.user_data.pop("ob", None)
        await show(context, update.effective_chat.id, d.get("msg_id"), "Анкета отменена — всё осталось как было.")
        return
    if action == "b":
        if sid == "proposal":
            d["edit"] = None
        if d["hist"]:
            d["pos"] = d["hist"].pop()
        d["error"] = None
        await _refresh(update, context, d)
        return
    if sid == "intro":
        if action == "go":
            _go_next(d)
        await _refresh(update, context, d)
        return
    if sid == "savings":
        await _savings_callback(update, context, d, action, arg, cur)
        return
    if sid == "proposal":
        await _proposal_callback(update, context, d, action, arg, cur)
        return

    q = QUESTIONS[sid]
    if action == "n" and q.none:
        value = q.none[1]
        if q.kind == "list":
            value = []
        _store(d, sid, param, value, q.none[0])
        _go_next(d)
    elif action == "v":
        idx = int(arg)
        if q.kind == "day":
            value = q.presets[idx]
            _store(d, sid, param, value, f"{value}-го")
        else:
            presets = _presets(q, cur) if sid != "extra_amt" else [fmt.scaled(p, cur) for p in DEFAULTS[param].presets]
            value = presets[idx]
            monthly = value if q.unit == "each" else to_monthly(value, None, q.unit)
            _store(d, sid, param, monthly, _money_disp(value, None, q, cur))
        _go_next(d)
    elif action == "c":
        label, value = q.options[int(arg)]
        if sid == "card_name":
            value = _unique_card_name(d, value, int(param))
        _store(d, sid, param, value, label)
        _go_next(d)
    elif action == "t":
        sel = d["a"].setdefault("extras", [])
        if arg in sel:
            sel.remove(arg)
        elif arg in EXTRA_CODES:
            sel.append(arg)
        d["a"]["extras"] = [c for c in EXTRA_CODES if c in sel]  # порядок как в списке
    elif action == "d":
        d["a"].setdefault("extras", [])
        names = ", ".join(DEFAULTS[c].name for c in d["a"]["extras"]) or "ничего"
        d["a"]["disp"]["extras"] = names
        _go_next(d)
    await _refresh(update, context, d)


async def on_text(update: Update, context: Ctx) -> None:
    d = draft(context)
    message = update.effective_message
    text = (message.text or "").strip()
    await safe_delete(message)
    cur = _cur(context, d)
    sid, param = _split(d["pos"])
    d["error"] = None

    if sid == "intro":
        _go_next(d)
    elif sid == "savings":
        value = parse_amount(text)
        if value is None:
            d["error"] = "Не понял сумму 🤔 Напиши число, например: 1500"
        else:
            await _set_goal_and_show_proposal(update, context, d, value, cur)
            return
    elif sid == "proposal":
        await _proposal_text(update, context, d, text, cur)
        return
    else:
        err = _apply_text(d, sid, param, text, cur)
        if err:
            d["error"] = err
        else:
            _go_next(d)
    await _refresh(update, context, d)


def _apply_text(d: dict, sid: str, param: str | None, text: str, cur: str) -> str | None:
    q = QUESTIONS[sid]
    if q.kind == "money":
        value = parse_amount(text, allow_negative=q.allow_negative)
        if value is None:
            return "Не понял сумму 🤔 Напиши число, например: 450"
        period = detect_period(text)
        monthly = value if (q.unit == "each" and not period) else to_monthly(value, period, q.unit)
        _store(d, sid, param, round(monthly, 2), _money_disp(value, period, q, cur))
        return None
    if q.kind == "day":
        day = parse_day(text)
        if day is None:
            return "Нужно число месяца от 1 до 31"
        _store(d, sid, param, day, f"{day}-го")
        return None
    if q.kind == "list":
        if parse_amount(text) == 0.0 and not any(ch.isdigit() for ch in text.replace("0", "")):
            _store(d, sid, param, [], "нет")
            return None
        default = {"subs": "Подписки", "other_fixed": "Прочие платежи", "custom": "Своя категория"}[sid]
        try:
            items = parse_items(text, default)
        except ValueError as exc:
            return f"Не получилось разобрать: {exc}. Пример: <code>Netflix 50, Spotify 20</code>"
        if not items:
            return "Напиши название и сумму, например: <code>Netflix 50</code>"
        if sid == "custom":
            items = [(name[:30], amount) for name, amount in items]
        _store(d, sid, param, [[n, a] for n, a in items],
               ", ".join(f"{n} {fmt.num(a)}" for n, a in items))
        return None
    if q.kind == "choice":
        if q.free_text:
            name = parse_name(text, 30)
            if not name:
                return "Напиши название карты"
            name = _unique_card_name(d, name, int(param or 0))
            _store(d, sid, param, name, name)
            return None
        low = text.lower()
        for label, value in q.options:
            if low in (label.lower(), str(value).lower()) or (low in ("да", "yes") and value is True) or (
                    low in ("нет", "no") and value is False):
                _store(d, sid, param, value, label)
                return None
        return "Выбери вариант кнопкой 👇"
    if q.kind == "multi":
        return "Отметь варианты кнопками и нажми «Готово» 👇"
    if q.kind == "text":
        note = "" if parse_amount(text) == 0.0 and not any(ch.isdigit() for ch in text) else clean_text(text, 300)
        _store(d, sid, param, note, note or "нет")
        return None
    return None


# --- цель накоплений и предложение бюджета ---------------------------------------------------------
async def _savings_callback(update, context, d, action, arg, cur) -> None:
    if action == "v":
        options = d.get("savings_options") or []
        idx = int(arg)
        if idx < len(options):
            await _set_goal_and_show_proposal(update, context, d, options[idx], cur)
            return
    elif action == "n":
        await _set_goal_and_show_proposal(update, context, d, 0.0, cur)
        return
    await _refresh(update, context, d)


async def _set_goal_and_show_proposal(update, context, d, goal: float, cur: str) -> None:
    d["a"]["savings_goal"] = goal
    d["a"]["disp"]["savings_goal"] = fmt.money(goal, cur)
    d["prop"] = {"locked": {}, "goal": goal}
    d["hist"].append(d["pos"])
    d["pos"] = "proposal"
    await _enter_proposal(update, context, d, cur)


async def _enter_proposal(update, context, d, cur: str) -> None:
    d["edit"] = None
    d["error"] = None
    # Экран анкеты превращается в «анализ», а предложение бюджета — отдельное окно под ним.
    await show(context, update.effective_chat.id, d.get("msg_id"), analysis_text(d, cur, svc(context).today()))
    text, markup = _render_proposal(d, cur)
    msg = await context.bot.send_message(update.effective_chat.id, text, reply_markup=markup)
    d["msg_id"] = msg.message_id


async def _proposal_callback(update, context, d, action, arg, cur) -> None:
    items = _items(d)
    prop = d["prop"]
    d["error"] = None
    if action == "e":
        idx = int(arg)
        if idx < len(items):
            d["edit"] = items[idx].code
    elif action == "s":
        d["edit"] = "savings"
    elif action == "c":
        d["edit"] = None
    elif action == "r":
        prop["locked"] = {}
    elif action == "ok":
        await finish(update, context, d)
        return
    await _refresh(update, context, d)


async def _proposal_text(update, context, d, text: str, cur: str) -> None:
    edit = d.get("edit")
    value = parse_amount(text)
    if not edit:
        d["error"] = "Сначала нажми на категорию или «Цель», которую хочешь поменять."
    elif value is None:
        d["error"] = "Не понял сумму 🤔 Напиши число, например: 400"
    elif edit == "savings":
        d["prop"]["goal"] = value
        d["a"]["savings_goal"] = value
        d["a"]["disp"]["savings_goal"] = fmt.money(value, cur)
        d["edit"] = None
    else:
        d["prop"]["locked"][edit] = value
        d["edit"] = None
    await _refresh(update, context, d)


# --- сохранение ----------------------------------------------------------------------------------------
def survey_items(d: dict, items: list[planner.PlanItem], cur: str) -> list[SurveyItem]:
    """Ответы анкеты для листа «Анкета»: вопрос, ответ как его дал пользователь, оценка в месяц."""
    a = d["a"]
    disp = a.get("disp", {})
    est = {i.code: i.estimate for i in items}
    monthly = {
        "rent": a.get("rent"), "bills": a.get("bills"), "arnona": a.get("arnona"),
        "subs": sum(v for _, v in a.get("subs") or []), "other_fixed": sum(v for _, v in a.get("other_fixed") or []),
        "income": a.get("income"), "custom": sum(v for _, v in a.get("custom") or []),
        "cafe_freq": est.get("cafe"), "rest_freq": est.get("restaurants"), "delivery_freq": est.get("delivery"),
        "impulse_freq": est.get("impulse"), "unexpected_freq": est.get("unexpected"),
    }
    for code in ("groceries", "transport", "health", "clothes", "hobby", "tobacco", "games", "partner"):
        monthly[code] = est.get(code)
    second = dict(FREQ_TO_CHECK, unexpected_freq="unexpected_amount")
    out: list[SurveyItem] = []
    for sid in LINEAR:
        if sid == "has_card":
            for i, c in enumerate(complete_cards(d)):
                out.append(SurveyItem(f"Кредитка {i + 1}", f"{c['name']}, списание {c['day']}-го, к списанию "
                                                             f"{fmt.money(c.get('pending') or 0, cur)}"))
            continue
        if sid not in QUESTIONS or sid not in disp or sid in second.values():
            continue
        answer_text = str(disp[sid])
        if sid in second and a.get(sid) and second[sid] in disp:
            answer_text += f" × {disp[second[sid]]}"
        title = QUESTIONS[sid].title.replace(" — как часто", "")
        value = monthly.get(sid)
        out.append(SurveyItem(title, clean_text(answer_text, 200), value if value else None))
        if sid == "extras":
            for code in a.get("extras") or []:
                out.append(SurveyItem(DEFAULTS[code].name, str(disp.get(f"extra_amt.{code}", "")), est.get(code)))
    if "savings_goal" in disp:
        out.append(SurveyItem("Цель накоплений", str(disp["savings_goal"]), a.get("savings_goal")))
    return out


def build_user_data(user_id: int, d: dict, existing: UserData | None, now: datetime, cur: str,
                    name: str) -> UserData:
    a = d["a"]
    income = a.get("income") or 0.0
    items = _items(d)
    st = _proposal_state(d)
    budgets = st["result"].budgets

    categories: list[Category] = []
    for it in items:
        limit = float(budgets.get(it.code, it.estimate))
        if it.code.startswith("custom"):
            categories.append(Category(name=parse_name(it.name) or "Своя категория", emoji=CUSTOM_EMOJI,
                                       limit=limit, keywords=it.keywords, code=""))
        else:
            categories.append(make_category(it.code, limit))

    cards = [Card(name=c["name"], charge_day=int(c["day"]), baseline=float(c.get("pending") or 0), baseline_at=now)
             for c in complete_cards(d)]
    sub_names = {n.lower() for n, _ in a.get("subs") or []}
    old_fixed = {f.name.lower(): f for f in (existing.fixed if existing else [])}
    fixed: list[FixedExpense] = []
    for fname, amount in planner.fixed_items(a):
        prev = old_fixed.get(fname.lower())
        payment = cards[0].name if (cards and fname.lower() in sub_names) else ACCOUNT
        if prev and (prev.payment == ACCOUNT or any(c.name == prev.payment for c in cards)):
            payment = prev.payment
        fixed.append(FixedExpense(name=parse_name(fname) or "Платёж", amount=amount,
                                  day=prev.day if prev else 1, payment=payment))

    profile = Profile(
        name=clean_text(name, 60),
        currency=cur,
        income=float(income),
        payday=a.get("payday") if income > 0 else None,
        savings_goal=float(d["prop"]["goal"]),
        balance=float(a.get("balance") or 0),
        balance_at=now,
        created_at=existing.profile.created_at if existing else now,
        notes=a.get("notes") or "",
    )
    data = UserData(user_id=user_id, profile=profile, categories=categories, fixed=fixed, cards=cards,
                    transactions=list(existing.transactions) if existing else [],
                    survey=survey_items(d, items, cur))
    # Платежи и зарплата этого месяца, которые уже прошли, — для статистики (баланс они не меняют:
    # они датированы раньше момента сверки).
    finance.post_fixed(data, now.date(), only_due=False)
    finance.post_income(data, now.date(), only_due=False)
    return data


async def finish(update: Update, context: Ctx, d: dict) -> None:
    services = svc(context)
    user = update.effective_user
    chat_id = update.effective_chat.id
    now = services.now()
    cur = _cur(context, d)
    existing = services.store.load(user.id)
    data = build_user_data(user.id, d, existing, now, cur, user.first_name or "")
    if existing:
        services.store.backup(user.id, f"before-rerun-{now:%Y%m%d-%H%M%S}")
    services.store.save(data, now)
    services.registry.upsert(user.id, chat_id=chat_id, name=clean_text(user.first_name or "", 60),
                             username=user.username or "", last_processed=now.date().isoformat())
    context.user_data.pop("ob", None)

    m = lambda x: fmt.money(x, cur)  # noqa: E731
    summary = [
        "✅ <b>Бюджет сохранён!</b>", "",
        f"📌 Постоянные: {m(data.fixed_total)}",
        f"🧾 Лимиты на жизнь: {m(data.limits_total)} ({len(data.categories)} категорий)",
    ]
    if data.profile.income > 0:
        summary.append(f"💰 Отложится по плану: {m(data.planned_savings)}")
    await show(context, chat_id, d.get("msg_id"), "\n".join(summary))

    await context.bot.send_message(
        chat_id,
        "🎉 Всё готово! Теперь просто пиши траты в чат — например, <code>45 кофе</code> или "
        "<code>wolt 89</code>. Категорию я подберу сам.\n\n"
        f"📬 {services.cfg.summary_days.capitalize()} в {services.cfg.summary_time:%H:%M} буду присылать сводку за неделю.\n"
        "📁 Excel-таблица — в меню. Кнопки внизу — навигация по боту.",
        reply_markup=MAIN_MENU,
    )
    png = charts.budget_pie(data, now.date())
    if png:
        await context.bot.send_photo(chat_id, png, caption="🥧 Так распределяется твой бюджет")

    invite = d.get("invite")
    if invite:
        from .partner import offer_invite

        await offer_invite(update, context, invite)
