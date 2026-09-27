"""Хранение данных пользователя в личной Excel-таблице.

* Запись — XlsxWriter: книга собирается заново при каждом сохранении, со всеми формулами,
  оформлением, выпадающими списками и диаграммами. У каждой формулы сохраняется посчитанное
  ботом значение, поэтому таблица правильно выглядит даже в превью на телефоне, а Excel/Google
  Таблицы при открытии всё равно пересчитают формулы сами.
* Чтение — openpyxl: бот берёт данные из листов «Операции», «Бюджет», «Постоянные», «Карты»,
  «Профиль», «Анкета». Эти же листы пользователь может отредактировать и прислать файл обратно.
"""

from __future__ import annotations

import copy
import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import openpyxl
import xlsxwriter
from openpyxl.utils.datetime import from_excel

from . import finance, fmt, palette
from .categories import CUSTOM_EMOJI, DEFAULTS, normalize, split_emoji
from .models import (
    ACCOUNT,
    CARD_CHARGE,
    CARD_CHARGE_CATEGORY,
    EXPENSE,
    FIXED_CATEGORY,
    INCOME,
    INCOME_CATEGORY,
    KINDS,
    MAX_COMMENT_LEN,
    SOURCE_EXCEL,
    Card,
    Category,
    FixedExpense,
    Profile,
    SurveyItem,
    Transaction,
    UserData,
)
from .parsing import clean_text, parse_amount, parse_day, parse_name

log = logging.getLogger(__name__)

SHEET_DASH = "Сводка"
SHEET_TX = "Операции"
SHEET_BUDGET = "Бюджет"
SHEET_FIXED = "Постоянные"
SHEET_CARDS = "Карты"
SHEET_MONTHS = "По месяцам"
SHEET_SURVEY = "Анкета"
SHEET_PROFILE = "Профиль"
SHEET_HELP = "Справка"
SHEET_LISTS = "Списки"

TX_HEADERS = ["ID", "Дата", "Тип", "Сумма", "Категория", "Комментарий", "Оплата", "Источник"]
BUDGET_HEADERS = ["Эмодзи", "Категория", "Лимит в месяц", "Потрачено за месяц", "Осталось", "Ключевые слова", "Код"]
FIXED_HEADERS = ["Название", "Сумма в месяц", "День списания", "Оплата", "Активно"]
CARD_HEADERS = ["Название", "День списания", "К списанию на дату сверки", "Дата сверки", "К списанию сейчас"]
SURVEY_HEADERS = ["Вопрос", "Ответ", "Оценка в месяц"]
MONTH_HEADERS = ["Месяц", "Начало", "Конец", "Доход", "Постоянные", "Переменные траты", "Всего расходов",
                 "Итог месяца"]

# Строки листа «Профиль» (порядок важен: на ячейки B4, B6, B7, B8, B9 ссылаются формулы).
PROFILE_ROWS = [
    ("name", "Имя"),
    ("currency", "Валюта"),
    ("income", "Доход в месяц"),
    ("payday", "День зарплаты"),
    ("savings_goal", "Цель накоплений в месяц"),
    ("balance", "Баланс счёта на дату сверки"),
    ("balance_at", "Дата сверки баланса"),
    ("balance_now", "Баланс счёта сейчас (оценка)"),
    ("created_at", "Дата регистрации"),
    ("notes", "Заметки"),
]

MAX_ROWS = 50_000
MAX_CATEGORIES = 60
EXTRA_VALIDATION_ROWS = 1000

BLUE = "#0000FF"  # вводимые данные
GREEN = "#008000"  # ссылки на другие листы
HEADER_BG = "#1c5cab"
KEY_FILL = "#FFFF00"  # ключевые допущения, которые стоит проверить


class WorkbookError(ValueError):
    """Файл нельзя загрузить (нет нужных листов и т.п.)."""


def _q(sheet: str) -> str:
    return "'" + sheet.replace("'", "''") + "'"


TXQ = _q(SHEET_TX)


def _sumifs(criteria: str) -> str:
    return f"SUMIFS({TXQ}!$D:$D,{criteria})"


def _date_range(start_cell: str, end_cell: str) -> str:
    return f'{TXQ}!$B:$B,">="&{start_cell},{TXQ}!$B:$B,"<"&{end_cell}+1'


# ---------------------------------------------------------------------------------------------
# Запись
# ---------------------------------------------------------------------------------------------
class _Styles:
    def __init__(self, wb: xlsxwriter.Workbook, cur: str):
        base = {"font_name": "Arial", "font_size": 10}
        sym = cur.replace('"', "")
        money = f'#,##0 "{sym}";(#,##0 "{sym}");"-"'
        money2 = f'#,##0.00 "{sym}";(#,##0.00 "{sym}");"-"'

        def f(**kw):
            return wb.add_format({**base, **kw})

        self.title = f(bold=True, font_size=16)
        self.subtitle = f(bold=True, font_size=12)
        self.note = f(italic=True, font_color=palette.MUTED)
        self.header = f(bold=True, font_color="#FFFFFF", bg_color=HEADER_BG, text_wrap=True, valign="vcenter")
        self.text = f()
        self.bold = f(bold=True)
        self.text_in = f(font_color=BLUE)
        self.wrap_in = f(font_color=BLUE, text_wrap=True, valign="top")
        self.int_in = f(font_color=BLUE, num_format="0")
        self.money_in = f(font_color=BLUE, num_format=money)
        self.money_key = f(font_color=BLUE, num_format=money, bg_color=KEY_FILL)
        self.money2_in = f(font_color=BLUE, num_format=money2)
        self.money = f(num_format=money)
        self.money_bold = f(num_format=money, bold=True, top=1)
        self.money_link = f(font_color=GREEN, num_format=money)
        self.pct = f(num_format='0.0%;(0.0%);"-"')
        self.pct_bold = f(num_format='0.0%;(0.0%);"-"', bold=True, top=1)
        self.total_label = f(bold=True, top=1)
        self.date_in = f(font_color=BLUE, num_format="dd.mm.yyyy")
        self.datetime_in = f(font_color=BLUE, num_format="dd.mm.yyyy hh:mm")
        self.date = f(num_format="dd.mm.yyyy")
        self.muted = f(font_color=palette.MUTED)
        self.help = f(text_wrap=True, valign="top")
        self.help_title = f(bold=True, font_size=12)
        self.over = wb.add_format({"bg_color": "#f8d7d7", "font_color": "#9c1c1c"})
        self.warn = wb.add_format({"bg_color": "#fdf0c8"})


def write_workbook(path: Path, data: UserData, now: datetime) -> None:
    cur = data.profile.currency
    wb = xlsxwriter.Workbook(
        str(path),
        {"strings_to_formulas": False, "strings_to_urls": False, "strings_to_numbers": False},
    )
    try:
        wb.formats[0].set_font_name("Arial")
    except (AttributeError, IndexError):  # pragma: no cover - на случай изменений внутри XlsxWriter
        pass
    wb.set_properties({"title": f"Бюджет — {data.profile.name}", "author": "Бот «Бюджет»"})
    st = _Styles(wb, cur)

    stats = finance.month_stats(data, now.date(), now)
    balance_now = finance.account_balance(data, now)
    pending = {c.name: finance.card_pending(data, c, now) for c in data.cards}

    ws_dash = wb.add_worksheet(SHEET_DASH)
    ws_tx = wb.add_worksheet(SHEET_TX)
    ws_budget = wb.add_worksheet(SHEET_BUDGET)
    ws_fixed = wb.add_worksheet(SHEET_FIXED)
    ws_cards = wb.add_worksheet(SHEET_CARDS)
    ws_months = wb.add_worksheet(SHEET_MONTHS)
    ws_survey = wb.add_worksheet(SHEET_SURVEY)
    ws_profile = wb.add_worksheet(SHEET_PROFILE)
    ws_help = wb.add_worksheet(SHEET_HELP)
    ws_lists = wb.add_worksheet(SHEET_LISTS)

    lists = _write_lists(ws_lists, data, st)
    _write_transactions(ws_tx, data, st, lists)
    budget_rows = _write_budget(ws_budget, data, st, stats)
    _write_fixed(ws_fixed, data, st, lists)
    _write_cards(ws_cards, data, st, pending)
    _write_profile(ws_profile, data, st, balance_now)
    _write_months(wb, ws_months, data, st, now)
    _write_survey(ws_survey, data, st)
    _write_help(ws_help, data, st)
    _write_dashboard(wb, ws_dash, data, st, stats, budget_rows, balance_now, sum(pending.values()), now)

    ws_dash.activate()
    ws_lists.hide()
    wb.close()


def _table(ws, headers: list[str], nrows: int, name: str, widths: list[float]) -> None:
    ws.add_table(0, 0, max(nrows, 1), len(headers) - 1, {
        "name": name,
        "style": "Table Style Light 9",
        "columns": [{"header": h} for h in headers],
    })
    for col, width in enumerate(widths):
        ws.set_column(col, col, width)
    ws.freeze_panes(1, 0)
    ws.set_row(0, 30)


def _write_lists(ws, data: UserData, st: _Styles) -> dict[str, str]:
    categories = [c.name for c in data.categories] + [FIXED_CATEGORY, INCOME_CATEGORY, CARD_CHARGE_CATEGORY]
    payments = [ACCOUNT] + [c.name for c in data.cards]
    columns = {"Категории": categories, "Оплата": payments, "Тип": list(KINDS), "Да/Нет": ["Да", "Нет"]}
    refs = {}
    for col, (title, values) in enumerate(columns.items()):
        ws.write_string(0, col, title, st.bold)
        for row, value in enumerate(values, start=1):
            ws.write_string(row, col, value)
        letter = chr(ord("A") + col)
        refs[title] = f"={_q(SHEET_LISTS)}!${letter}$2:${letter}${len(values) + 1}"
    return refs


def _write_transactions(ws, data: UserData, st: _Styles, lists: dict[str, str]) -> None:
    txs = sorted(data.transactions, key=lambda t: (t.at, t.id), reverse=True)
    _table(ws, TX_HEADERS, len(txs), "Operations", [6, 16, 15, 13, 22, 36, 14, 10])
    for r, tx in enumerate(txs, start=1):
        ws.write_number(r, 0, tx.id, st.int_in)
        ws.write_datetime(r, 1, tx.at, st.datetime_in)
        ws.write_string(r, 2, tx.kind, st.text_in)
        ws.write_number(r, 3, tx.amount, st.money2_in)
        ws.write_string(r, 4, tx.category, st.text_in)
        ws.write_string(r, 5, tx.comment, st.text_in)
        ws.write_string(r, 6, tx.payment, st.text_in)
        ws.write_string(r, 7, tx.source, st.text_in)
    last = len(txs) + EXTRA_VALIDATION_ROWS
    ws.data_validation(1, 2, last, 2, {"validate": "list", "source": lists["Тип"]})
    ws.data_validation(1, 4, last, 4, {
        "validate": "list", "source": lists["Категории"], "error_type": "warning",
        "error_message": "Такой категории нет в бюджете. Она появится в сводке с нулевым лимитом.",
    })
    ws.data_validation(1, 6, last, 6, {
        "validate": "list", "source": lists["Оплата"], "error_type": "warning",
        "error_message": "«Счёт» — оплата со счёта/наличными, иначе название кредитки с листа «Карты».",
    })
    ws.data_validation(1, 3, last, 3, {"validate": "decimal", "criteria": ">", "value": 0,
                                       "error_message": "Сумма должна быть положительной."})


def _write_budget(ws, data: UserData, st: _Styles, stats: finance.MonthStats) -> dict[str, int]:
    """Возвращает номер строки Excel (1-based) каждой категории на листе «Бюджет»."""
    _table(ws, BUDGET_HEADERS, len(data.categories), "Budget", [8, 26, 15, 15, 14, 60, 10])
    ws.set_column(6, 6, 10, st.muted, {"hidden": True})
    spent = {c.name.lower(): c.spent for c in stats.categories}
    rows = {}
    for i, cat in enumerate(data.categories, start=1):
        n = i + 1
        rows[cat.name] = n
        sp = spent.get(cat.name.lower(), 0.0)
        ws.write_string(i, 0, cat.emoji, st.text_in)
        ws.write_string(i, 1, cat.name, st.text_in)
        ws.write_number(i, 2, cat.limit, st.money_key)
        ws.write_formula(
            i, 3,
            "=" + _sumifs(f'{TXQ}!$E:$E,$B{n},{TXQ}!$C:$C,"{EXPENSE}",'
                          + _date_range(f"{_q(SHEET_DASH)}!$B$4", f"{_q(SHEET_DASH)}!$B$5")),
            st.money, sp,
        )
        ws.write_formula(i, 4, f"=C{n}-D{n}", st.money, cat.limit - sp)
        ws.write_string(i, 5, ", ".join(cat.keywords), st.wrap_in)
        ws.write_string(i, 6, cat.code, st.muted)
    ws.write_comment("C1", "Месячный лимит категории. Меняй здесь или в боте: ⚙️ Настройки → Категории.")
    ws.write_comment("F1", "Слова через запятую: если описание траты начинается с такого слова, "
                           "бот сам положит её в эту категорию.")
    return rows


def _write_fixed(ws, data: UserData, st: _Styles, lists: dict[str, str]) -> None:
    _table(ws, FIXED_HEADERS, len(data.fixed), "Fixed", [28, 15, 14, 16, 10])
    for i, f in enumerate(data.fixed, start=1):
        ws.write_string(i, 0, f.name, st.text_in)
        ws.write_number(i, 1, f.amount, st.money_key)
        ws.write_number(i, 2, f.day, st.int_in)
        ws.write_string(i, 3, f.payment, st.text_in)
        ws.write_string(i, 4, "Да" if f.active else "Нет", st.text_in)
    last = len(data.fixed) + 200
    ws.data_validation(1, 2, last, 2, {"validate": "integer", "criteria": "between", "minimum": 1, "maximum": 31,
                                       "error_message": "День месяца: от 1 до 31."})
    ws.data_validation(1, 3, last, 3, {"validate": "list", "source": lists["Оплата"], "error_type": "warning"})
    ws.data_validation(1, 4, last, 4, {"validate": "list", "source": lists["Да/Нет"]})
    ws.write_comment("C1", "В этот день бот сам запишет платёж в «Операции» (для 31-го в коротких месяцах — "
                           "последний день).")


def _write_cards(ws, data: UserData, st: _Styles, pending: dict[str, float]) -> None:
    _table(ws, CARD_HEADERS, len(data.cards), "Cards", [20, 14, 18, 18, 18])
    for i, card in enumerate(data.cards, start=1):
        n = i + 1
        ws.write_string(i, 0, card.name, st.text_in)
        ws.write_number(i, 1, card.charge_day, st.int_in)
        ws.write_number(i, 2, card.baseline, st.money_in)
        ws.write_datetime(i, 3, card.baseline_at, st.datetime_in)
        spent = _sumifs(f'{TXQ}!$C:$C,"{EXPENSE}",{TXQ}!$G:$G,$A{n},{TXQ}!$B:$B,">"&$D{n}')
        charged = _sumifs(f'{TXQ}!$C:$C,"{CARD_CHARGE}",{TXQ}!$G:$G,$A{n},{TXQ}!$B:$B,">"&$D{n}')
        ws.write_formula(i, 4, f"=C{n}+{spent}-{charged}", st.money, pending.get(card.name, 0.0))
    ws.write_comment("E1", "Сумма на дату сверки + покупки этой картой после неё − уже прошедшие списания.")


def _write_profile(ws, data: UserData, st: _Styles, balance_now: float) -> None:
    p = data.profile
    ws.set_column(0, 0, 32)
    ws.set_column(1, 1, 24)
    ws.write_string(0, 0, "Параметр", st.header)
    ws.write_string(0, 1, "Значение", st.header)
    for r, (key, label) in enumerate(PROFILE_ROWS, start=1):
        ws.write_string(r, 0, label, st.text)
        if key == "name":
            ws.write_string(r, 1, p.name, st.text_in)
        elif key == "currency":
            ws.write_string(r, 1, p.currency, st.text_in)
        elif key == "income":
            ws.write_number(r, 1, p.income, st.money_key)
        elif key == "payday":
            if p.payday:
                ws.write_number(r, 1, p.payday, st.int_in)
            else:
                ws.write_blank(r, 1, None, st.int_in)
        elif key == "savings_goal":
            ws.write_number(r, 1, p.savings_goal, st.money_key)
        elif key == "balance":
            ws.write_number(r, 1, p.balance, st.money_in)
        elif key == "balance_at":
            ws.write_datetime(r, 1, p.balance_at, st.datetime_in)
        elif key == "balance_now":
            income = _sumifs(f'{TXQ}!$C:$C,"{INCOME}",{TXQ}!$B:$B,">"&B8')
            spent = _sumifs(f'{TXQ}!$C:$C,"{EXPENSE}",{TXQ}!$G:$G,"{ACCOUNT}",{TXQ}!$B:$B,">"&B8')
            charged = _sumifs(f'{TXQ}!$C:$C,"{CARD_CHARGE}",{TXQ}!$B:$B,">"&B8')
            ws.write_formula(r, 1, f"=B7+{income}-{spent}-{charged}", st.money, balance_now)
        elif key == "created_at":
            ws.write_datetime(r, 1, p.created_at, st.date)
        elif key == "notes":
            ws.write_string(r, 1, p.notes, st.wrap_in)
    ws.write_comment("B5", "Какого числа приходит зарплата. В этот день бот сам запишет доход. Пусто — не записывать.")
    ws.write_comment("B8", "Момент, когда баланс сверяли с банком. Операции после этой даты меняют оценку баланса.")


def _write_months(wb, ws, data: UserData, st: _Styles, now: datetime) -> None:
    months = finance.months_with_data(data, now)
    _table(ws, MONTH_HEADERS, len(months), "Months", [18, 12, 12, 14, 14, 16, 16, 14])
    for i, m in enumerate(months, start=1):
        n = i + 1
        s = finance.month_stats(data, m, now)
        end = finance.next_month(m) - timedelta(days=1)
        rng = _date_range(f"$B{n}", f"$C{n}")
        ws.write_string(i, 0, fmt.month_title(m), st.text)
        ws.write_datetime(i, 1, datetime.combine(m, datetime.min.time()), st.date_in)
        ws.write_datetime(i, 2, datetime.combine(end, datetime.min.time()), st.date_in)
        ws.write_formula(i, 3, "=" + _sumifs(f'{TXQ}!$C:$C,"{INCOME}",{rng}'), st.money, s.income_fact)
        ws.write_formula(i, 4, "=" + _sumifs(f'{TXQ}!$C:$C,"{EXPENSE}",{TXQ}!$E:$E,"{FIXED_CATEGORY}",{rng}'),
                         st.money, s.fixed_fact)
        ws.write_formula(i, 5, "=" + _sumifs(f'{TXQ}!$C:$C,"{EXPENSE}",{TXQ}!$E:$E,"<>{FIXED_CATEGORY}",{rng}'),
                         st.money, s.spent)
        ws.write_formula(i, 6, f"=E{n}+F{n}", st.money, s.fixed_fact + s.spent)
        ws.write_formula(i, 7, f"=D{n}-G{n}", st.money, s.actual_result)
    if months:
        chart = wb.add_chart({"type": "column"})
        last = len(months) + 1
        for col, color, name in ((3, palette.SLOTS[0], "Доход"), (6, palette.SLOTS[1], "Всего расходов")):
            chart.add_series({
                "name": name,
                "categories": [SHEET_MONTHS, 1, 0, last - 1, 0],
                "values": [SHEET_MONTHS, 1, col, last - 1, col],
                "fill": {"color": color},
                "border": {"none": True},
                "gap": 80,
            })
        chart.set_title({"name": "Доход и расходы по месяцам", "name_font": {"size": 12, "bold": True}})
        chart.set_legend({"position": "bottom"})
        chart.set_y_axis({"major_gridlines": {"visible": True, "line": {"color": palette.GRID}},
                          "num_format": "#,##0"})
        chart.set_size({"width": 620, "height": 320})
        ws.insert_chart(last + 1, 0, chart, {"x_offset": 5, "y_offset": 10})


def _write_survey(ws, data: UserData, st: _Styles) -> None:
    _table(ws, SURVEY_HEADERS, len(data.survey), "Survey", [40, 36, 16])
    for i, item in enumerate(data.survey, start=1):
        ws.write_string(i, 0, item.question, st.text)
        ws.write_string(i, 1, item.answer, st.text_in)
        if item.monthly is not None:
            ws.write_number(i, 2, item.monthly, st.money_in)


HELP_TEXT = [
    ("h", "Как устроена таблица"),
    ("p", "Эту таблицу ведёт бот. Всё, что ты записываешь в Telegram, попадает сюда, а бот берёт данные "
          "отсюда — для сводок, диаграмм и напоминаний."),
    ("h", "Листы"),
    ("p", "• Сводка — месяц целиком: лимиты, траты, остатки, деньги на счету и диаграммы. Пересобирается ботом.\n"
          "• Операции — все траты, доходы и списания кредиток. Можно добавлять и исправлять строки.\n"
          "• Бюджет — категории, месячные лимиты и ключевые слова для автоматического выбора категории.\n"
          "• Постоянные — ежемесячные платежи: жильё, счета, арнона, подписки. В день списания бот записывает их сам.\n"
          "• Карты — кредитки, день списания и сумма к списанию.\n"
          "• По месяцам — доходы и расходы помесячно.\n"
          "• Анкета — ответы из первой настройки и оценка трат.\n"
          "• Профиль — доход, день зарплаты, цель накоплений и сверка баланса счёта."),
    ("h", "Цвета"),
    ("p", "Синий шрифт — данные, которые можно менять. Жёлтая заливка — ключевые допущения бюджета "
          "(доход, цель накоплений, лимиты). Чёрный — формулы. Зелёный — ссылки на другие листы."),
    ("h", "Как отредактировать и загрузить обратно"),
    ("p", "1. Скачай файл в боте (кнопка «📁 Excel»).\n"
          "2. Поменяй данные на листах. Не переименовывай листы и заголовки колонок.\n"
          "3. Пришли файл боту обратно — он покажет, что нашёл, и попросит подтвердить замену.\n"
          "Перед заменой бот сохраняет резервную копию прежней таблицы."),
    ("h", "Правила заполнения"),
    ("p", "• Тип операции: Расход, Доход или Списание карты.\n"
          "• Оплата: «Счёт» (дебетовая карта, Bit, перевод, наличные) или название кредитки с листа «Карты».\n"
          "• Сумма всегда положительная — знак задаёт тип операции.\n"
          "• Дата — ДД.ММ.ГГГГ ЧЧ:ММ. ID можно не заполнять, бот проставит сам."),
]


def _write_help(ws, data: UserData, st: _Styles) -> None:
    ws.set_column(0, 0, 110)
    row = 0
    for kind, text in HELP_TEXT:
        if kind == "h":
            if row:
                row += 1
            ws.write_string(row, 0, text, st.help_title)
        else:
            ws.write_string(row, 0, text, st.help)
            ws.set_row(row, 14 * (text.count("\n") + 1))
        row += 1


def _write_dashboard(wb, ws, data: UserData, st: _Styles, stats: finance.MonthStats,
                     budget_rows: dict[str, int], balance_now: float, pending_total: float,
                     now: datetime) -> None:
    cur = data.profile.currency
    ws.set_column(0, 0, 34)
    ws.set_column(1, 4, 14)
    ws.set_column(5, 5, 3)
    ws.write_string(0, 0, f"Бюджет · {fmt.month_title(stats.month)}", st.title)
    ws.write_string(1, 0, f"Обновлено ботом {fmt.dmy(now)} {now:%H:%M}. Суммы в {cur}. "
                          "Синий — вводимые данные, чёрный — формулы, зелёный — ссылки на другие листы.", st.note)

    month_end = finance.next_month(stats.month) - timedelta(days=1)
    prof = _q(SHEET_PROFILE)
    budget_q = _q(SHEET_BUDGET)
    fixed_q = _q(SHEET_FIXED)
    cards_q = _q(SHEET_CARDS)
    rows = [  # (подпись, формула или None, значение, стиль)
        ("Начало месяца", None, datetime.combine(stats.month, datetime.min.time()), st.date_in),
        ("Конец месяца", None, datetime.combine(month_end, datetime.min.time()), st.date_in),
        ("Доход в месяц (план)", f"={prof}!B4", data.profile.income, st.money_link),
        ("Постоянные расходы", f'=SUMIFS({fixed_q}!B:B,{fixed_q}!E:E,"Да")', data.fixed_total, st.money),
        ("Цель накоплений", f"={prof}!B6", data.profile.savings_goal, st.money_link),
        ("Лимиты на переменные траты", f"=SUM({budget_q}!C:C)", data.limits_total, st.money),
        ("Отложится по плану", "=B6-B7-B9", data.planned_savings, st.money),
        ("Потрачено за месяц (переменные)",
         "=" + _sumifs(f'{TXQ}!$C:$C,"{EXPENSE}",{TXQ}!$E:$E,"<>{FIXED_CATEGORY}",' + _date_range("$B$4", "$B$5")),
         stats.spent, st.money),
        ("Осталось лимитов", "=B9-B11", stats.left, st.money),
        ("Доход за месяц (факт)", "=" + _sumifs(f'{TXQ}!$C:$C,"{INCOME}",' + _date_range("$B$4", "$B$5")),
         stats.income_fact, st.money),
        ("На счету сейчас (оценка)", f"={prof}!B9", balance_now, st.money_link),
        ("К списанию по картам", f"=SUM({cards_q}!E:E)", pending_total, st.money),
        ("Свободно после списаний", "=B14-B15", balance_now - pending_total, st.money),
    ]
    for i, (label, formula, value, style) in enumerate(rows):
        r = 3 + i
        ws.write_string(r, 0, label, st.text)
        if formula is None:
            ws.write_datetime(r, 1, value, style)
        else:
            ws.write_formula(r, 1, formula, style, value)
    ws.write_comment("B4", "Период сводки. Поменяй даты — и сводка пересчитается за другой месяц.")
    ws.set_landscape()
    ws.set_paper(9)  # A4
    ws.fit_to_pages(1, 0)

    head = 17
    for c, h in enumerate(["Категория", "Лимит", "Потрачено", "Осталось", "Использовано"]):
        ws.write_string(head, c, h, st.header)
    first = head + 1
    r = first
    cat_cells: dict[str, str] = {}
    for cs in stats.categories:
        n = r + 1
        ws.write_string(r, 0, cs.name, st.text)
        if cs.in_budget and cs.name in budget_rows:
            ws.write_formula(r, 1, f"={budget_q}!C{budget_rows[cs.name]}", st.money_link, cs.limit)
        else:
            ws.write_number(r, 1, 0, st.money_in)
            ws.write_comment(r, 0, "Этой категории нет в бюджете — траты есть, лимита нет.")
        ws.write_formula(
            r, 2,
            "=" + _sumifs(f'{TXQ}!$E:$E,$A{n},{TXQ}!$C:$C,"{EXPENSE}",' + _date_range("$B$4", "$B$5")),
            st.money, cs.spent,
        )
        ws.write_formula(r, 3, f"=B{n}-C{n}", st.money, cs.limit - cs.spent)
        ratio = cs.spent / cs.limit if cs.limit > 0 else (1.0 if cs.spent > 0 else 0.0)
        ws.write_formula(r, 4, f"=IF(B{n}>0,C{n}/B{n},IF(C{n}>0,1,0))", st.pct, ratio)
        cat_cells[cs.name] = f"B{n}"
        r += 1
    last = r - 1
    total = r
    tn = total + 1
    ws.write_string(total, 0, "Итого", st.total_label)
    if stats.categories:
        ws.write_formula(total, 1, f"=SUM(B{first + 1}:B{last + 1})", st.money_bold,
                         sum(c.limit for c in stats.categories))
        ws.write_formula(total, 2, f"=SUM(C{first + 1}:C{last + 1})", st.money_bold,
                         sum(c.spent for c in stats.categories))
        ws.write_formula(total, 3, f"=SUM(D{first + 1}:D{last + 1})", st.money_bold,
                         sum(c.limit - c.spent for c in stats.categories))
        tl = sum(c.limit for c in stats.categories)
        ts = sum(c.spent for c in stats.categories)
        ws.write_formula(total, 4, f"=IF(B{tn}>0,C{tn}/B{tn},0)", st.pct_bold, ts / tl if tl > 0 else 0.0)
        ws.conditional_format(first, 4, last, 4, {"type": "cell", "criteria": ">", "value": 1, "format": st.over})
        ws.conditional_format(first, 4, last, 4, {"type": "cell", "criteria": "between", "minimum": 0.8,
                                                  "maximum": 1, "format": st.warn})

    # Таблица для круговой диаграммы: те же сегменты, что и на картинке в боте.
    seg_head = total + 2
    ws.write_string(seg_head, 0, "Структура бюджета (для диаграммы)", st.header)
    ws.write_string(seg_head, 1, "Сумма", st.header)
    segments = palette.plan_segments(data)
    top_cells = [cat_cells[s.category] for s in segments if s.category and s.category in cat_cells]
    seg_rows = []
    for i, seg in enumerate(segments):
        r = seg_head + 1 + i
        ws.write_string(r, 0, seg.label, st.text)
        if seg.label == "Постоянные" and not seg.category:
            formula = "=B7"
        elif seg.label == "Накопления" and not seg.category:
            formula = "=MAX(0,B10)"
        elif seg.label == "Остальное" and not seg.category:
            formula = "=B9-" + ("-".join(top_cells) if top_cells else "0")
        else:
            formula = f"={cat_cells.get(seg.category, '0')}"
        ws.write_formula(r, 1, formula, st.money, seg.amount)
        seg_rows.append(r)

    if seg_rows:
        pie = wb.add_chart({"type": "doughnut"})
        pie.add_series({
            "name": "Структура бюджета",
            "categories": [SHEET_DASH, seg_rows[0], 0, seg_rows[-1], 0],
            "values": [SHEET_DASH, seg_rows[0], 1, seg_rows[-1], 1],
            "points": [{"fill": {"color": s.color}, "border": {"color": palette.SURFACE, "width": 1.5}}
                       for s in segments],
            "data_labels": {"percentage": True, "font": {"size": 9, "color": palette.INK}},
        })
        pie.set_hole_size(55)
        pie.set_title({"name": f"Структура бюджета · {fmt.month_title(stats.month)}",
                       "name_font": {"size": 12, "bold": True}})
        pie.set_legend({"position": "right", "font": {"size": 9}})
        pie.set_size({"width": 620, "height": 330})
        ws.insert_chart(2, 6, pie, {"x_offset": 5, "y_offset": 0})

    if stats.categories:
        bar = wb.add_chart({"type": "bar"})
        for col, color, name in ((1, palette.OTHER, "Лимит"), (2, palette.SLOTS[0], "Потрачено")):
            bar.add_series({
                "name": name,
                "categories": [SHEET_DASH, first, 0, last, 0],
                "values": [SHEET_DASH, first, col, last, col],
                "fill": {"color": color},
                "border": {"none": True},
                "gap": 60,
            })
        bar.set_title({"name": "Лимит и траты по категориям", "name_font": {"size": 12, "bold": True}})
        bar.set_legend({"position": "bottom"})
        bar.set_y_axis({"reverse": True, "crossing": "max"})
        bar.set_x_axis({"major_gridlines": {"visible": True, "line": {"color": palette.GRID}},
                        "num_format": "#,##0"})
        bar.set_size({"width": 620, "height": max(260, 70 + 26 * len(stats.categories))})
        ws.insert_chart(19, 6, bar, {"x_offset": 5, "y_offset": 0})
    ws.freeze_panes(3, 0)


# ---------------------------------------------------------------------------------------------
# Чтение
# ---------------------------------------------------------------------------------------------
@dataclass
class ReadReport:
    warnings: list[str] = field(default_factory=list)

    def warn(self, text: str) -> None:
        if len(self.warnings) < 200:
            self.warnings.append(text)


def _norm_header(value) -> str:
    return normalize(str(value or ""))


def _header_map(header_row, aliases: list[tuple[str, str]]) -> dict[str, int]:
    """aliases: (начало заголовка, ключ). Длинные варианты проверяются первыми."""
    ordered = sorted(aliases, key=lambda a: len(a[0]), reverse=True)
    result: dict[str, int] = {}
    for idx, value in enumerate(header_row):
        h = _norm_header(value)
        if not h:
            continue
        for alias, key in ordered:
            if h.startswith(normalize(alias)):
                if key not in result:
                    result[key] = idx
                break
    return result


def _rows(ws):
    for row in ws.iter_rows(values_only=True):
        yield list(row)


def _is_empty(row) -> bool:
    return all(v is None or (isinstance(v, str) and not v.strip()) for v in row)


def _cell(row, idx):
    return row[idx] if idx is not None and idx < len(row) else None


def _to_str(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def _to_float(value) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return parse_amount(str(value), allow_negative=True)


def _round_dt(dt: datetime) -> datetime:
    if dt.microsecond >= 500_000:
        dt = dt + timedelta(seconds=1)
    return dt.replace(microsecond=0)


def _to_datetime(value) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return _round_dt(value.replace(tzinfo=None))
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return _round_dt(from_excel(value))
        except (ValueError, OverflowError):
            return None
    text = str(value).strip()
    for pattern in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y", "%d.%m.%y %H:%M", "%d.%m.%y",
                    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    return None


def _to_bool(value, default=True) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    text = normalize(str(value))
    if text in ("да", "yes", "true", "1", "вкл", "y", "д"):
        return True
    if text in ("нет", "no", "false", "0", "выкл", "n", "н"):
        return False
    return default


def _sheet(wb, name: str):
    if name in wb.sheetnames:
        return wb[name]
    low = normalize(name)
    for sn in wb.sheetnames:
        if normalize(sn) == low:
            return wb[sn]
    return None


def _read_profile(wb, rep: ReadReport, fallback_currency: str) -> Profile:
    ws = _sheet(wb, SHEET_PROFILE)
    p = Profile(currency=fallback_currency)
    if ws is None:
        rep.warn(f"Нет листа «{SHEET_PROFILE}» — доход, цель и баланс не заданы.")
        return p
    labels = {normalize(label): key for key, label in PROFILE_ROWS}
    labels.update({"доход": "income", "цель накоплений": "savings_goal", "баланс": "balance",
                   "баланс на дату сверки": "balance", "дата сверки": "balance_at"})
    for row in _rows(ws):
        if len(row) < 2:
            continue
        key = labels.get(_norm_header(row[0]))
        value = row[1]
        if not key:
            continue
        if key == "name":
            p.name = clean_text(_to_str(value), 60)
        elif key == "currency":
            p.currency = clean_text(_to_str(value), 5) or fallback_currency
        elif key == "income":
            p.income = max(0.0, _to_float(value) or 0.0)
        elif key == "payday":
            p.payday = parse_day(_to_str(value)) if value not in (None, "") else None
        elif key == "savings_goal":
            p.savings_goal = max(0.0, _to_float(value) or 0.0)
        elif key == "balance":
            p.balance = _to_float(value) or 0.0
        elif key == "balance_at":
            p.balance_at = _to_datetime(value) or p.balance_at
        elif key == "created_at":
            p.created_at = _to_datetime(value) or p.created_at
        elif key == "notes":
            p.notes = clean_text(_to_str(value), 500)
    return p


def _read_categories(wb, rep: ReadReport) -> list[Category]:
    ws = _sheet(wb, SHEET_BUDGET)
    if ws is None:
        raise WorkbookError(f"В файле нет листа «{SHEET_BUDGET}».")
    rows = _rows(ws)
    header = next(rows, [])
    cols = _header_map(header, [("эмодзи", "emoji"), ("иконка", "emoji"), ("категория", "name"),
                                ("название", "name"), ("лимит", "limit"), ("ключевые", "keywords"),
                                ("слова", "keywords"), ("код", "code")])
    if "name" not in cols:
        raise WorkbookError(f"На листе «{SHEET_BUDGET}» нет колонки «Категория».")
    cats: list[Category] = []
    seen = set()
    for i, row in enumerate(rows, start=2):
        if _is_empty(row):
            continue
        raw_name = _to_str(_cell(row, cols["name"]))
        emoji_in_name, raw_name = split_emoji(raw_name)
        name = parse_name(raw_name)
        if not name:
            continue
        if name.lower() in seen or name.lower() in (FIXED_CATEGORY.lower(), INCOME_CATEGORY.lower(),
                                                    CARD_CHARGE_CATEGORY.lower()):
            rep.warn(f"«{SHEET_BUDGET}», строка {i}: категория «{name}» повторяется или зарезервирована — пропущена.")
            continue
        if len(cats) >= MAX_CATEGORIES:
            rep.warn(f"Категорий больше {MAX_CATEGORIES} — лишние пропущены.")
            break
        limit = _to_float(_cell(row, cols.get("limit")))
        if limit is None or limit < 0:
            if _cell(row, cols.get("limit")) not in (None, ""):
                rep.warn(f"«{SHEET_BUDGET}», строка {i}: лимит не число — поставлен 0.")
            limit = 0.0
        code = _to_str(_cell(row, cols.get("code")))
        code = code if code in DEFAULTS else ""
        emoji = _to_str(_cell(row, cols.get("emoji"))) or emoji_in_name or (DEFAULTS[code].emoji if code else CUSTOM_EMOJI)
        kw_raw = _to_str(_cell(row, cols.get("keywords")))
        keywords = [clean_text(k, 40).lower() for k in re.split(r"[,;\n]", kw_raw) if clean_text(k, 40)]
        cats.append(Category(name=name, emoji=clean_text(emoji, 8), limit=round(limit, 2),
                             keywords=keywords[:200], code=code))
        seen.add(name.lower())
    return cats


def _read_fixed(wb, rep: ReadReport) -> list[FixedExpense]:
    ws = _sheet(wb, SHEET_FIXED)
    if ws is None:
        rep.warn(f"Нет листа «{SHEET_FIXED}» — постоянных платежей нет.")
        return []
    rows = _rows(ws)
    cols = _header_map(next(rows, []), [("название", "name"), ("сумма", "amount"), ("день", "day"),
                                        ("оплата", "payment"), ("активн", "active")])
    items = []
    for i, row in enumerate(rows, start=2):
        if _is_empty(row):
            continue
        name = parse_name(_to_str(_cell(row, cols.get("name"))))
        amount = _to_float(_cell(row, cols.get("amount")))
        if not name or amount is None:
            rep.warn(f"«{SHEET_FIXED}», строка {i}: нужны название и сумма — строка пропущена.")
            continue
        day = parse_day(_to_str(_cell(row, cols.get("day")))) or 1
        items.append(FixedExpense(name=name, amount=round(abs(amount), 2), day=day,
                                  payment=_to_str(_cell(row, cols.get("payment"))) or ACCOUNT,
                                  active=_to_bool(_cell(row, cols.get("active")))))
    return items


def _read_cards(wb, rep: ReadReport, fallback_at: datetime) -> list[Card]:
    ws = _sheet(wb, SHEET_CARDS)
    if ws is None:
        return []
    rows = _rows(ws)
    cols = _header_map(next(rows, []), [("название", "name"), ("день", "day"),
                                        ("к списанию на дату", "baseline"), ("сумма к списанию", "baseline"),
                                        ("к списанию сейчас", "_now"), ("дата сверки", "baseline_at")])
    cards: list[Card] = []
    for i, row in enumerate(rows, start=2):
        if _is_empty(row):
            continue
        name = parse_name(_to_str(_cell(row, cols.get("name"))), 30)
        day = parse_day(_to_str(_cell(row, cols.get("day"))))
        if not name or not day:
            rep.warn(f"«{SHEET_CARDS}», строка {i}: нужны название и день списания — строка пропущена.")
            continue
        if name.lower() == ACCOUNT.lower() or any(c.name.lower() == name.lower() for c in cards):
            rep.warn(f"«{SHEET_CARDS}», строка {i}: карта «{name}» повторяется — пропущена.")
            continue
        cards.append(Card(name=name, charge_day=day,
                          baseline=round(_to_float(_cell(row, cols.get("baseline"))) or 0.0, 2),
                          baseline_at=_to_datetime(_cell(row, cols.get("baseline_at"))) or fallback_at))
    return cards


def _canon_payment(value: str, cards: list[Card]) -> str | None:
    v = normalize(value)
    if v in ("", "счет", "счёт", "наличные", "нал", "дебет", "bit", "бит", "paybox"):
        return ACCOUNT
    for c in cards:
        if normalize(c.name) == v:
            return c.name
    return None


_KIND_MAP = {"расход": EXPENSE, "трата": EXPENSE, "доход": INCOME, "списание карты": CARD_CHARGE,
             "списание": CARD_CHARGE, "списание кредитки": CARD_CHARGE}


def _read_transactions(wb, rep: ReadReport, cats: list[Category], cards: list[Card]) -> list[Transaction]:
    ws = _sheet(wb, SHEET_TX)
    if ws is None:
        raise WorkbookError(f"В файле нет листа «{SHEET_TX}».")
    rows = _rows(ws)
    cols = _header_map(next(rows, []), [("id", "id"), ("номер", "id"), ("дата", "at"), ("тип", "kind"),
                                        ("сумма", "amount"), ("категория", "category"),
                                        ("комментарий", "comment"), ("описание", "comment"),
                                        ("оплата", "payment"), ("источник", "source")])
    for key in ("at", "amount"):
        if key not in cols:
            raise WorkbookError(f"На листе «{SHEET_TX}» нет колонки «{'Дата' if key == 'at' else 'Сумма'}».")
    specials = {c.lower(): c for c in (FIXED_CATEGORY, INCOME_CATEGORY, CARD_CHARGE_CATEGORY)}
    cat_names = {c.name.lower(): c.name for c in cats}
    txs: list[Transaction] = []
    used_ids: set[int] = set()
    pending_ids: list[Transaction] = []
    for i, row in enumerate(rows, start=2):
        if _is_empty(row):
            continue
        if len(txs) >= MAX_ROWS:
            rep.warn(f"Операций больше {MAX_ROWS} — остальные пропущены.")
            break
        at = _to_datetime(_cell(row, cols["at"]))
        amount = _to_float(_cell(row, cols["amount"]))
        if at is None or amount is None or abs(amount) < 0.005:
            rep.warn(f"«{SHEET_TX}», строка {i}: нет даты или суммы — пропущена.")
            continue
        kind_raw = normalize(_to_str(_cell(row, cols.get("kind"))))
        kind = _KIND_MAP.get(kind_raw, EXPENSE if not kind_raw else None)
        if kind is None:
            rep.warn(f"«{SHEET_TX}», строка {i}: непонятный тип «{_to_str(_cell(row, cols.get('kind')))}» — пропущена.")
            continue
        cat_raw = parse_name(_to_str(_cell(row, cols.get("category"))))
        if kind == INCOME:
            category = cat_names.get(cat_raw.lower()) or specials.get(cat_raw.lower()) or cat_raw or INCOME_CATEGORY
        elif kind == CARD_CHARGE:
            category = CARD_CHARGE_CATEGORY
        else:
            category = specials.get(cat_raw.lower()) or cat_names.get(cat_raw.lower()) or cat_raw or "Прочее"
        pay_raw = _to_str(_cell(row, cols.get("payment")))
        payment = _canon_payment(pay_raw, cards)
        if payment is None:
            if kind == CARD_CHARGE:
                payment = parse_name(pay_raw, 30)
            else:
                rep.warn(f"«{SHEET_TX}», строка {i}: оплата «{pay_raw}» не найдена среди карт — считаю «{ACCOUNT}».")
                payment = ACCOUNT
        tx = Transaction(
            id=0, at=at, kind=kind, amount=round(abs(amount), 2), category=category,
            comment=clean_text(_to_str(_cell(row, cols.get("comment"))), MAX_COMMENT_LEN),
            payment=payment, source=clean_text(_to_str(_cell(row, cols.get("source"))), 20) or SOURCE_EXCEL,
        )
        raw_id = _to_float(_cell(row, cols.get("id")))
        if raw_id is not None and raw_id.is_integer() and raw_id > 0 and int(raw_id) not in used_ids:
            tx.id = int(raw_id)
            used_ids.add(tx.id)
        else:
            pending_ids.append(tx)
        txs.append(tx)
    next_id = max(used_ids, default=0) + 1
    for tx in sorted(pending_ids, key=lambda t: t.at):
        tx.id = next_id
        next_id += 1
    txs.sort(key=lambda t: (t.at, t.id))
    return txs


def _read_survey(wb) -> list[SurveyItem]:
    ws = _sheet(wb, SHEET_SURVEY)
    if ws is None:
        return []
    rows = _rows(ws)
    cols = _header_map(next(rows, []), [("вопрос", "q"), ("ответ", "a"), ("оценка", "m")])
    items = []
    for row in rows:
        if _is_empty(row):
            continue
        q = clean_text(_to_str(_cell(row, cols.get("q"))), 120)
        if q:
            items.append(SurveyItem(q, clean_text(_to_str(_cell(row, cols.get("a"))), 200),
                                    _to_float(_cell(row, cols.get("m")))))
    return items[:100]


def read_workbook(path: Path, user_id: int, fallback_currency: str = "₪") -> tuple[UserData, list[str]]:
    rep = ReadReport()
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:  # битый файл, не xlsx и т.п.
        raise WorkbookError("Не получилось открыть файл как Excel (.xlsx).") from exc
    try:
        profile = _read_profile(wb, rep, fallback_currency)
        categories = _read_categories(wb, rep)
        cards = _read_cards(wb, rep, profile.balance_at)
        fixed = _read_fixed(wb, rep)
        for f in fixed:
            f.payment = _canon_payment(f.payment, cards) or ACCOUNT
        transactions = _read_transactions(wb, rep, categories, cards)
        survey = _read_survey(wb)
    finally:
        wb.close()
    data = UserData(user_id=user_id, profile=profile, categories=categories, fixed=fixed, cards=cards,
                    transactions=transactions, survey=survey)
    return data, rep.warnings


# ---------------------------------------------------------------------------------------------
# Хранилище
# ---------------------------------------------------------------------------------------------
class ExcelStore:
    def __init__(self, data_dir: Path, default_currency: str = "₪"):
        self.users_dir = data_dir / "users"
        self.backups_dir = data_dir / "backups"
        self.tmp_dir = data_dir / "tmp"
        for d in (self.users_dir, self.backups_dir, self.tmp_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.default_currency = default_currency
        self._cache: dict[int, tuple[tuple[int, int], UserData]] = {}

    def path(self, user_id: int) -> Path:
        return self.users_dir / f"{user_id}.xlsx"

    def exists(self, user_id: int) -> bool:
        return self.path(user_id).exists()

    def user_ids(self) -> list[int]:
        ids = []
        for p in self.users_dir.glob("*.xlsx"):
            if p.stem.lstrip("-").isdigit():
                ids.append(int(p.stem))
        return sorted(ids)

    def load(self, user_id: int) -> UserData | None:
        path = self.path(user_id)
        try:
            st = path.stat()
        except FileNotFoundError:
            return None
        key = (st.st_mtime_ns, st.st_size)
        cached = self._cache.get(user_id)
        if cached and cached[0] == key:
            return copy.deepcopy(cached[1])
        try:
            data, warnings = read_workbook(path, user_id, self.default_currency)
        except WorkbookError:
            log.exception("user %s: таблица повреждена, пробую резервную копию", user_id)
            data, warnings = self._recover(user_id)
            st = path.stat()
            key = (st.st_mtime_ns, st.st_size)
        if warnings:
            log.info("user %s: при чтении таблицы %d предупреждений", user_id, len(warnings))
        self._cache[user_id] = (key, data)
        return copy.deepcopy(data)

    def save(self, data: UserData, now: datetime) -> None:
        path = self.path(data.user_id)
        tmp = path.with_name(f".{path.stem}.tmp.xlsx")
        write_workbook(tmp, data, now)
        os.replace(tmp, path)
        st = path.stat()
        self._cache[data.user_id] = ((st.st_mtime_ns, st.st_size), copy.deepcopy(data))

    def _recover(self, user_id: int) -> tuple[UserData, list[str]]:
        folder = self.backups_dir / str(user_id)
        for backup in sorted(folder.glob("*.xlsx"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                result = read_workbook(backup, user_id, self.default_currency)
            except WorkbookError:
                continue
            broken = self.path(user_id).with_suffix(".broken.xlsx")
            shutil.copy2(self.path(user_id), broken)
            shutil.copy2(backup, self.path(user_id))
            log.warning("user %s: восстановлено из %s (битый файл сохранён как %s)", user_id, backup.name, broken.name)
            return result
        raise WorkbookError("Таблица повреждена, и резервных копий нет.")

    def delete(self, user_id: int) -> None:
        self._cache.pop(user_id, None)
        self.path(user_id).unlink(missing_ok=True)
        shutil.rmtree(self.backups_dir / str(user_id), ignore_errors=True)

    def backup(self, user_id: int, tag: str, keep: int = 30) -> Path | None:
        src = self.path(user_id)
        if not src.exists():
            return None
        folder = self.backups_dir / str(user_id)
        folder.mkdir(parents=True, exist_ok=True)
        dst = folder / f"{tag}.xlsx"
        shutil.copy2(src, dst)
        backups = sorted(folder.glob("*.xlsx"), key=lambda p: p.stat().st_mtime)
        for old in backups[:-keep]:
            old.unlink(missing_ok=True)
        return dst

    def tmp_path(self, user_id: int) -> Path:
        return self.tmp_dir / f"{user_id}-import.xlsx"
