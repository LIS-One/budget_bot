from datetime import datetime

import pytest

from budget_bot.categories import DEFAULTS, detect_category, looks_like_income, make_category, split_emoji
from budget_bot.models import EXPENSE, Category, Transaction

CATS = [make_category(code) for code in DEFAULTS]


@pytest.mark.parametrize("text, expected", [
    ("кофе", "Кафе и перекусы"),
    ("шаурма", "Кафе и перекусы"),
    ("wolt пицца", "Доставка еды"),  # канал покупки важнее блюда
    ("суперфарм", "Здоровье и аптека"),  # длинное совпадение важнее «супер»
    ("супер", "Продукты"),
    ("сигареты", "Сигареты и вейп"),
    ("виноград", "Продукты"),
    ("билет на автобус", "Транспорт"),
    ("подарок девушке", "Вторая половинка"),
    ("подарок маме", "Подарки"),
    ("h&m футболка", "Одежда и обувь"),
    ("Rami Levy", "Продукты"),
    ("steam", "Игры"),
])
def test_keywords(text, expected):
    assert detect_category(text, CATS).name == expected


def test_unknown_and_missing_category():
    assert detect_category("штука", CATS) is None
    only_food = [make_category("groceries")]
    assert detect_category("сигареты", only_food) is None  # у пользователя нет такой категории


def test_history_wins_over_keywords():
    tx = Transaction(1, datetime(2026, 9, 1), EXPENSE, 30, "Хобби", comment="кофе")
    assert detect_category("Кофе", CATS, [tx]).name == "Хобби"


def test_user_keywords():
    cats = CATS + [Category("Гитара", "🎸", 200, ["гитар"])]
    assert detect_category("гитарные струны", cats).name == "Гитара"


def test_helpers():
    assert split_emoji("🎸 Гитара") == ("🎸", "Гитара")
    assert split_emoji("❤️ Любовь") == ("❤️", "Любовь")
    assert split_emoji("Танцы") == (None, "Танцы")
    assert looks_like_income("зп за сентябрь")
    assert not looks_like_income("кофе")
