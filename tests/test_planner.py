import pytest

from budget_bot import planner

ANSWERS = dict(
    rent=4500, bills=500, arnona=350, subs=[["Netflix", 50], ["Spotify", 20], ["Спортзал", 200]],
    other_fixed=[["Ваад байт", 150]], income=12000, groceries=300 * 52 / 12, transport=250, health=100,
    clothes=200, cafe_freq=17, cafe_check=25, rest_freq=1.5, rest_check=150, delivery_freq=11,
    delivery_check=80, tobacco=100 * 52 / 12, games=100, partner=500, impulse_freq=4.3, impulse_check=50,
    unexpected_freq=0.5, unexpected_amount=500, extras=["beauty", "gifts"], extra_amt={"beauty": 150, "gifts": 200},
    custom=[["Танцы", 250]],
)
FIXED = sum(v for _, v in planner.fixed_items(ANSWERS))
ITEMS = planner.plan_items(ANSWERS, 12000)


def test_fixed_items_and_estimates():
    assert FIXED == 5770
    est = {i.code: i.estimate for i in ITEMS}
    assert est["groceries"] == 1300  # 300 в неделю
    assert est["cafe"] == 420  # 17 раз × 25
    assert est["delivery"] == 880
    assert est["unexpected"] == 250  # не меньше 2% дохода
    assert "custom0" in est and "other" in est
    assert [i.code for i in ITEMS][0] == "groceries"


def test_duplicate_fixed_names_are_numbered():
    items = planner.fixed_items({"subs": [["Netflix", 50], ["netflix", 30]]})
    assert [n for n, _ in items] == ["Netflix", "netflix 2"]


def test_recommend_savings():
    assert planner.recommend_savings(12000, FIXED, ITEMS) == 1200  # остаётся мало → 10% с урезаниями
    assert planner.recommend_savings(0, FIXED, ITEMS) == 0


@pytest.mark.parametrize("goal", [0, 1200, 1500, 2000])
def test_optimize_meets_goal_exactly_when_possible(goal):
    r = planner.optimize(ITEMS, 12000, FIXED, goal)
    assert r.shortfall == 0
    assert r.planned_savings >= goal
    if goal >= 1200:  # пришлось урезать → итог в цель, лишнее — меньше шага округления
        assert goal <= r.planned_savings < goal + 10
    # «Непредвиденное» и «Здоровье» не режем никогда
    est = {i.code: i.estimate for i in ITEMS}
    assert r.budgets["unexpected"] == est["unexpected"]
    assert r.budgets["health"] == est["health"]
    # доставку режем сильнее продуктов (в долях)
    if goal >= 1500:
        assert r.budgets["delivery"] / est["delivery"] < r.budgets["groceries"] / est["groceries"]


def test_optimize_beyond_capacity_reports_shortfall():
    r = planner.optimize(ITEMS, 12000, FIXED, 4000)
    assert r.shortfall > 1000
    assert r.planned_savings < 4000


def test_locked_categories_stay():
    r = planner.optimize(ITEMS, 12000, FIXED, 1500, locked={"cafe": 500})
    assert r.budgets["cafe"] == 500
    assert 1500 <= r.planned_savings < 1510


def test_surplus_goes_to_savings():
    r = planner.optimize(ITEMS, 20000, FIXED, 1000)
    assert r.cuts == {}
    assert r.planned_savings > 1000


def test_insights_and_structure():
    r = planner.optimize(ITEMS, 12000, FIXED, 1500)
    tips = planner.insights(ANSWERS, ITEMS, r, 12000, FIXED, 1500, "₪", balance=3000, pending=3500,
                            pending_date="10.10")
    joined = "\n".join(tips)
    assert 1 <= len(tips) <= 5
    assert "Сигареты" in joined and "к списанию" in joined
    needs, wants, savings = planner.structure(ITEMS, r.budgets, 12000, FIXED)
    assert needs + wants + savings == pytest.approx(1.0)
