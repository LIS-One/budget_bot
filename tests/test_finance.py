from datetime import date, datetime

from budget_bot import finance
from budget_bot.categories import make_category
from budget_bot.models import (
    ACCOUNT,
    CARD_CHARGE,
    EXPENSE,
    FIXED_CATEGORY,
    INCOME,
    Card,
    FixedExpense,
    Profile,
    UserData,
)

T0 = datetime(2026, 1, 20, 12, 0)


def user() -> UserData:
    return UserData(
        user_id=1,
        profile=Profile(name="Тест", income=10000, payday=31, savings_goal=1000, balance=5000, balance_at=T0,
                        created_at=T0),
        categories=[make_category("groceries", 1000), make_category("cafe", 300)],
        fixed=[FixedExpense("Жильё", 3000, day=1), FixedExpense("Netflix", 50, day=15, payment="Max")],
        cards=[Card("Max", charge_day=31, baseline=1000, baseline_at=T0)],
    )


def test_effective_day_and_next_date():
    assert finance.effective_day(2026, 2, 31) == date(2026, 2, 28)
    assert finance.effective_day(2028, 2, 31) == date(2028, 2, 29)
    assert finance.next_date_for_day(10, date(2026, 9, 27)) == date(2026, 10, 10)
    assert finance.next_date_for_day(31, date(2026, 9, 27)) == date(2026, 9, 30)
    assert finance.next_date_for_day(27, date(2026, 9, 27)) == date(2026, 10, 27)


def test_balance_and_card_pending():
    d = user()
    d.add_transaction(at=datetime(2026, 1, 21, 9), kind=EXPENSE, amount=100, category="Продукты", payment="Max")
    d.add_transaction(at=datetime(2026, 1, 21, 10), kind=EXPENSE, amount=40, category="Кафе и перекусы",
                      payment=ACCOUNT)
    d.add_transaction(at=datetime(2026, 1, 22), kind=INCOME, amount=500, category="Доход")
    d.add_transaction(at=datetime(2026, 1, 19), kind=EXPENSE, amount=999, category="Продукты")  # до сверки
    now = datetime(2026, 1, 25)
    assert finance.account_balance(d, now) == 5000 - 40 + 500
    assert finance.card_pending(d, d.cards[0], now) == 1100


def test_recurring_month_boundaries_and_idempotency():
    d = user()
    events = finance.process_recurring(d, date(2026, 1, 20), date(2026, 3, 1))
    kinds = [(e.kind, e.day) for e in events]
    # 31-е в январе, 28 февраля вместо 31-го, 1 февраля и 1 марта — жильё, 15 февраля — Netflix
    assert ("card", date(2026, 1, 31)) in kinds and ("card", date(2026, 2, 28)) in kinds
    assert ("payday", date(2026, 1, 31)) in kinds and ("payday", date(2026, 2, 28)) in kinds
    assert ("fixed", date(2026, 2, 1)) in kinds and ("fixed", date(2026, 3, 1)) in kinds
    assert ("fixed", date(2026, 2, 15)) in kinds
    # списание 31 января = сумма на карте до этого дня (сверка 1000)
    jan_charge = next(e.tx for e in events if e.kind == "card" and e.day == date(2026, 1, 31))
    assert jan_charge.amount == 1000
    # в феврале на карту ушёл только Netflix
    feb_charge = next(e.tx for e in events if e.kind == "card" and e.day == date(2026, 2, 28))
    assert feb_charge.amount == 50
    before = len(d.transactions)
    assert finance.process_recurring(d, date(2026, 1, 20), date(2026, 3, 1)) == []
    assert len(d.transactions) == before
    # баланс: 5000 + 2×10000 − 2×3000 (жильё) − 1000 − 50 (списания карты)
    assert finance.account_balance(d, datetime(2026, 3, 1, 12)) == 5000 + 20000 - 6000 - 1050


def test_same_day_purchase_goes_to_next_cycle():
    d = user()
    d.add_transaction(at=datetime(2026, 1, 31, 10), kind=EXPENSE, amount=70, category="Продукты", payment="Max")
    finance.process_recurring(d, date(2026, 1, 20), date(2026, 1, 31))
    charge = next(t for t in d.transactions if t.kind == CARD_CHARGE)
    assert charge.amount == 1000
    assert finance.card_pending(d, d.cards[0], datetime(2026, 2, 1)) == 70


def test_month_stats():
    d = user()
    d.add_transaction(at=datetime(2026, 1, 21), kind=EXPENSE, amount=400, category="Кафе и перекусы")
    d.add_transaction(at=datetime(2026, 1, 22), kind=EXPENSE, amount=80, category="Удалённая")
    d.add_transaction(at=datetime(2026, 1, 1), kind=EXPENSE, amount=3000, category=FIXED_CATEGORY, comment="Жильё")
    s = finance.month_stats(d, date(2026, 1, 1), datetime(2026, 1, 25, 10))
    assert s.spent == 480 and s.fixed_fact == 3000
    assert s.days_left == 7
    cafe = next(c for c in s.categories if c.name == "Кафе и перекусы")
    assert cafe.over and cafe.left == -100
    extra = next(c for c in s.categories if c.name == "Удалённая")
    assert not extra.in_budget and extra.limit == 0
    assert s.daily_allowance == (1300 - 480) / 7
    assert finance.months_with_data(d, datetime(2026, 3, 5)) == [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)]
