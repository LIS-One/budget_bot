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
