"""Демо-данные: пример пользователя с месяцем трат. Используется для примера таблицы и в тестах."""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from . import finance, planner
from .categories import make_category
from .models import ACCOUNT, EXPENSE, SOURCE_BOT, Card, Category, FixedExpense, Profile, SurveyItem, UserData

DEMO_ANSWERS = dict(
    rent=4200, bills=450, arnona=320, subs=[["Netflix", 50], ["Spotify", 20], ["iCloud", 13]],
    other_fixed=[["Ваад байт", 150], ["Страховка", 120]],
    income=12500, payday=10, balance=6400,
    groceries=300 * 52 / 12, transport=250, health=100, clothes=200, hobby=150,
    cafe_freq=17, cafe_check=22, rest_freq=1.5, rest_check=150, delivery_freq=4.3, delivery_check=85,
    tobacco=0, games=60, partner=450, impulse_freq=4.3, impulse_check=45,
    unexpected_freq=0.5, unexpected_amount=600, extras=["sport", "gifts"], extra_amt={"sport": 180, "gifts": 150},
    custom=[], notes="Коплю на отпуск летом",
)

_SAMPLES = {
    "groceries": [("Шуферсаль", 120, 420), ("Рами Леви", 90, 380), ("Овощи на шуке", 40, 110)],
    "cafe": [("кофе", 14, 22), ("круассан и кофе", 25, 38), ("шаурма", 38, 52)],
    "restaurants": [("ужин с друзьями", 120, 220)],
    "delivery": [("wolt суши", 70, 130), ("wolt пицца", 60, 95)],
    "transport": [("рав-кав", 30, 60), ("такси gett", 35, 70)],
    "health": [("суперфарм", 25, 90)],
    "clothes": [("кроссовки", 180, 320)],
    "hobby": [("струны для гитары", 45, 90)],
    "games": [("steam", 20, 60)],
    "partner": [("цветы", 60, 120), ("свидание", 120, 240)],
    "impulse": [("aliexpress", 20, 70), ("шоколадки", 10, 25)],
    "sport": [("бассейн", 40, 60)],
    "gifts": [("подарок маме", 100, 180)],
}


def build_demo(now: datetime, user_id: int = 1, seed: int = 3) -> UserData:
    rnd = random.Random(seed)
    a = DEMO_ANSWERS
    items = planner.plan_items(a, a["income"])
    fixed_list = planner.fixed_items(a)
    fixed_total = sum(v for _, v in fixed_list)
    goal = planner.recommend_savings(a["income"], fixed_total, items)
    result = planner.optimize(items, a["income"], fixed_total, goal)

    categories: list[Category] = []
    for it in items:
        cat = make_category(it.code, result.budgets[it.code])
        categories.append(cat)

    start = finance.month_start(finance.prev_month(now.date()))
    created = datetime.combine(start, datetime.min.time()) + timedelta(days=2, hours=20)
    profile = Profile(name="Аня", currency="₪", income=a["income"], payday=a["payday"], savings_goal=goal,
                      balance=a["balance"], balance_at=created, created_at=created, notes=a["notes"])
    card = Card(name="Max", charge_day=10, baseline=2350, baseline_at=created)
    fixed = [FixedExpense(name, amount, day=1, payment=card.name if name in ("Netflix", "Spotify", "iCloud") else ACCOUNT)
             for name, amount in fixed_list]
    data = UserData(user_id=user_id, profile=profile, categories=categories, fixed=fixed, cards=[card],
                    survey=[SurveyItem("Жильё", "4 200 ₪ в месяц", 4200), SurveyItem("Продукты", "300 ₪ в неделю", 1300)])

    finance.post_fixed(data, created.date(), only_due=False)
    day = created.date()
    last = now.date()
    while day <= last:
        if day > created.date():
            finance.process_recurring(data, day - timedelta(days=1), day)
        for code, samples in _SAMPLES.items():
            cat = next((c for c in categories if c.code == code), None)
            if not cat or rnd.random() > _daily_chance(code):
                continue
            name, lo, hi = rnd.choice(samples)
            hour = rnd.randint(8, 21)
            at = datetime.combine(day, datetime.min.time()) + timedelta(hours=hour, minutes=rnd.randint(0, 59))
            if at > now:
                continue
            data.add_transaction(at=at, kind=EXPENSE, amount=float(rnd.randint(lo, hi)), category=cat.name,
                                 comment=name, payment=card.name if rnd.random() < 0.75 else ACCOUNT,
                                 source=SOURCE_BOT)
        day += timedelta(days=1)
    return data


def _daily_chance(code: str) -> float:
    """Вероятность траты в категории за день — примерно 80–90% лимита к концу месяца."""
    return {"groceries": 0.16, "cafe": 0.35, "restaurants": 0.04, "delivery": 0.12, "transport": 0.15,
            "health": 0.05, "clothes": 0.02, "hobby": 0.06, "games": 0.04, "partner": 0.09,
            "impulse": 0.15, "sport": 0.1, "gifts": 0.03}.get(code, 0.05)
