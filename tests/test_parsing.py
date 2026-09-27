import pytest

from budget_bot.parsing import detect_period, parse_amount, parse_day, parse_entry, parse_items, parse_name, to_monthly


@pytest.mark.parametrize("text, amount, comment, income", [
    ("45 кофе", 45, "кофе", False),
    ("кофе 45₪", 45, "кофе", False),
    ("кофе 45 шек", 45, "кофе", False),
    ("+12000 зарплата", 12000, "зарплата", True),
    ("1 200 шуферсаль", 1200, "шуферсаль", False),
    ("1,200.50 ikea", 1200.5, "ikea", False),
    ("12,50 кофе", 12.5, "кофе", False),
    ("5к ремонт", 5000, "ремонт", False),
    ("1.5k телефон", 1500, "телефон", False),
    ("10bis 45", 45, "10bis", False),
    ("-45 кофе", 45, "кофе", False),
    ("45 к ужину", 45, "к ужину", False),  # «к» с пробелом — предлог, а не тысячи
    ("wolt пицца 89,90", 89.9, "wolt пицца", False),
])
def test_parse_entry(text, amount, comment, income):
    e = parse_entry(text)
    assert (e.amount, e.comment, e.income) == (amount, comment, income)


@pytest.mark.parametrize("text", ["кофе", "", "0 кофе", "привет!"])
def test_parse_entry_without_amount(text):
    assert parse_entry(text) is None


def test_parse_items():
    assert parse_items("Netflix 50, Spotify 20, iCloud 13") == [("Netflix", 50), ("Spotify", 20), ("iCloud", 13)]
    assert parse_items("120", "Подписки") == [("Подписки", 120)]
    assert parse_items("ваад байт 150 страховка 120") == [("ваад байт", 150), ("страховка", 120)]
    assert parse_items("150 ваад байт, 1,200 кредит") == [("ваад байт", 150), ("кредит", 1200)]
    assert parse_items("Netflix 49.90, Spotify 19,90") == [("Netflix", 49.9), ("Spotify", 19.9)]
    with pytest.raises(ValueError):
        parse_items("Netflix, Spotify")


def test_parse_amount_and_period():
    assert parse_amount("-1500", allow_negative=True) == -1500
    assert parse_amount("минус 2000", allow_negative=True) == -2000
    assert parse_amount("-1500") == 1500
    assert parse_amount("нет") == 0
    assert parse_amount("abc") is None
    assert detect_period("300 в неделю") == "week"
    assert detect_period("1600 в месяц") == "month"
    assert detect_period("1600") is None
    assert to_monthly(300, "week") == pytest.approx(1300)
    assert to_monthly(300, None, "week") == pytest.approx(1300)
    assert to_monthly(1600, "month", "week") == 1600


def test_parse_day_and_name():
    assert parse_day("10-го") == 10
    assert parse_day("каждое 15 число") == 15
    assert parse_day("45") is None
    assert parse_name("=SUM(A1) Кафе*") == "SUM(A1) Кафе"
    assert parse_name("Кафе <b>&") == "Кафе b"


# --- даты задним числом ---------------------------------------------------------------------------------
from datetime import date  # noqa: E402

from budget_bot.parsing import DateError, parse_clock, parse_date, split_date  # noqa: E402

TODAY = date(2026, 9, 27)


@pytest.mark.parametrize("text, day, rest", [
    ("вчера 45 кофе", date(2026, 9, 26), "45 кофе"),
    ("кофе позавчера 45", date(2026, 9, 25), "кофе   45"),
    ("25.09 120 продукты", date(2026, 9, 25), "120 продукты"),
    ("25 сентября 120 продукты", date(2026, 9, 25), "120 продукты"),
    ("3 янв 50 такси", date(2026, 1, 3), "50 такси"),
    ("25.12 100 подарок", date(2025, 12, 25), "100 подарок"),  # в будущем > месяца → прошлый год
    ("25.09.2026 45 кофе", date(2026, 9, 25), "45 кофе"),
    ("25.09.26 45 кофе", date(2026, 9, 25), "45 кофе"),
    ("2 марта 2026 кофе 45", date(2026, 3, 2), "кофе 45"),
    ("25.09 кофе", None, "25.09 кофе"),  # без второй суммы это сумма 25,09
    ("12.50 кофе", None, "12.50 кофе"),
    ("1.5k телефон", None, "1.5k телефон"),
    ("кофе 25.09 45", None, "кофе 25.09 45"),  # дата — только в начале
])
def test_split_date(text, day, rest):
    assert split_date(text, TODAY) == (day, rest)


@pytest.mark.parametrize("text", ["30.09 45 кофе", "01.10.2026 45", "10 окт 45", "31.02 45", "29.02 50 кофе"])
def test_split_date_errors(text):
    with pytest.raises(DateError):
        split_date(text, TODAY)


def test_parse_entry_with_date():
    e = parse_entry("вчера +500 кешбэк", TODAY)
    assert (e.amount, e.comment, e.income, e.day) == (500, "кешбэк", True, date(2026, 9, 26))
    assert parse_entry("сегодня 45 кофе", TODAY).day is None
    assert parse_entry("45 кофе", TODAY).day is None
    assert parse_entry("25.09 кофе", TODAY).amount == 25.09


def test_parse_date_and_clock():
    assert parse_date("вчера", TODAY) == date(2026, 9, 26)
    assert parse_date("25.09", TODAY) == date(2026, 9, 25)
    assert parse_date("25 сентября", TODAY) == date(2026, 9, 25)
    assert parse_date("abc", TODAY) is None
    assert parse_date("25.09 кофе", TODAY) is None
    with pytest.raises(DateError):
        parse_date("30.09", TODAY)
    assert parse_clock("21") == "21:00"
    assert parse_clock("21:30") == "21:30"
    assert parse_clock("9.05") == "09:05"
    assert parse_clock("25:00") is None and parse_clock("вечером") is None
