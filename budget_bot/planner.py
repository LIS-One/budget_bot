"""Аналитика анкеты: оценка трат по привычкам, подбор оптимальных лимитов и наблюдения.

Логика подбора:
1. Из ответов считаем, сколько человек тратит сейчас в каждой категории (оценка в месяц).
2. На переменные траты доступно: доход − постоянные − цель накоплений − суммы, которые пользователь
   задал сам (они «заморожены»).
3. Если оценок не больше доступного — лимиты = оценки, остаток сверху — дополнительные накопления.
4. Если больше — дефицит снимаем с категорий пропорционально их «гибкости»: доставку и спонтанные
   покупки режем смелее, продукты и транспорт — едва-едва, здоровье, учёба и резерв не трогаем.
   Если даже максимальных сокращений не хватает — честно говорим, какая сумма накоплений реальна.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import fmt
from .categories import BUFFER, DEFAULTS, NEED, WANT, CUSTOM_EMOJI

WEEKS_IN_MONTH = 52 / 12


@dataclass
class PlanItem:
    code: str
    emoji: str
    name: str
    estimate: float
    group: str
    max_cut: float
    keywords: list[str] = field(default_factory=list)


@dataclass
class PlanResult:
    budgets: dict[str, float]
    planned_savings: float
    shortfall: float  # сколько не хватает до цели даже после максимальных сокращений
    cuts: dict[str, float]  # насколько лимит ниже текущих трат


def _item(code: str, estimate: float) -> PlanItem:
    d = DEFAULTS[code]
    return PlanItem(code, d.emoji, d.name, max(0.0, estimate), d.group, d.max_cut, list(d.keywords))


def freq_estimate(a: dict, freq_key: str, check_key: str) -> float:
    return float(a.get(freq_key) or 0) * float(a.get(check_key) or 0)


def fixed_items(a: dict) -> list[tuple[str, float]]:
    items: list[tuple[str, float]] = []
    for key, name in (("rent", "Жильё"), ("bills", "Счета"), ("arnona", "Арнона")):
        if (a.get(key) or 0) > 0:
            items.append((name, float(a[key])))
    for key in ("subs", "other_fixed"):
        for name, amount in a.get(key) or []:
            if amount > 0:
                items.append((name, float(amount)))
    # одинаковые названия различаем номером
    seen: dict[str, int] = {}
    unique = []
    for name, amount in items:
        low = name.lower()
        seen[low] = seen.get(low, 0) + 1
        unique.append((name if seen[low] == 1 else f"{name} {seen[low]}", amount))
    return unique


def plan_items(a: dict, income: float) -> list[PlanItem]:
    """Категории бюджета с оценкой текущих трат в месяц — в порядке, в котором их показываем."""
    items = [_item("groceries", a.get("groceries") or 0)]
    simple = [("transport", a.get("transport")), ("health", a.get("health")), ("clothes", a.get("clothes")),
              ("cafe", freq_estimate(a, "cafe_freq", "cafe_check")),
              ("restaurants", freq_estimate(a, "rest_freq", "rest_check")),
              ("delivery", freq_estimate(a, "delivery_freq", "delivery_check")),
              ("hobby", a.get("hobby")), ("tobacco", a.get("tobacco")), ("games", a.get("games")),
              ("partner", a.get("partner")),
              ("impulse", freq_estimate(a, "impulse_freq", "impulse_check"))]
    for code, est in simple:
        if (est or 0) > 0:
            items.append(_item(code, float(est)))
    for code in a.get("extras") or []:
        est = (a.get("extra_amt") or {}).get(code) or 0
        if code in DEFAULTS and est > 0:
            items.append(_item(code, float(est)))
    for i, (name, amount) in enumerate(a.get("custom") or []):
        if amount > 0:
            items.append(PlanItem(f"custom{i}", CUSTOM_EMOJI, name, float(amount), WANT, 0.3, [name.lower()]))
    # Резерв на непредвиденное: по ответам, но не меньше 2% дохода.
    unexpected = freq_estimate(a, "unexpected_freq", "unexpected_amount")
    items.append(_item("unexpected", max(unexpected, 0.02 * max(income, 0))))
    # «Прочее» — мелочи, которые не попали в категории: ~5% от остальных трат.
    base = sum(i.estimate for i in items)
    items.append(_item("other", 0.05 * base))
    for it in items:
        it.estimate = fmt.nice_round(it.estimate)
    return items


def recommend_savings(income: float, fixed_total: float, items: list[PlanItem]) -> float:
    """Рекомендуемая цель накоплений: 20% дохода, если реально; иначе столько, сколько реально."""
    if income <= 0:
        return 0.0
    est = sum(i.estimate for i in items)
    free = income - fixed_total - est
    cut_cap = sum(i.estimate * i.max_cut for i in items)
    if free >= 0.20 * income:
        rec = 0.20 * income
    elif free >= 0.10 * income:
        rec = free
    else:
        rec = min(0.10 * income, max(0.0, free + cut_cap))
    return fmt.nice_round(rec, down=True)


def _round_keeping_total(exact: dict[str, float]) -> dict[str, float]:
    """Округляет суммы «красиво», не превышая их общий итог (метод наибольших остатков)."""
    floors = {k: fmt.nice_round(v, down=True) for k, v in exact.items()}
    leftover = sum(exact.values()) - sum(floors.values())
    by_remainder = sorted(exact, key=lambda k: (exact[k] - floors[k]) / fmt.round_step(exact[k]), reverse=True)
    for k in by_remainder:
        step = fmt.round_step(exact[k])
        if exact[k] > floors[k] and leftover >= step - 1e-6:
            floors[k] += step
            leftover -= step
    return floors


def optimize(
    items: list[PlanItem],
    income: float,
    fixed_total: float,
    goal: float,
    locked: dict[str, float] | None = None,
) -> PlanResult:
    locked = dict(locked or {})
    free_items = [it for it in items if it.code not in locked]
    available = income - fixed_total - goal - sum(locked.values())
    est_total = sum(it.estimate for it in free_items)
    budgets = dict(locked)
    cuts: dict[str, float] = {}

    if income <= 0 or est_total <= available:
        for it in free_items:
            budgets[it.code] = fmt.nice_round(it.estimate)
    else:
        deficit = est_total - available
        caps = {it.code: it.estimate * it.max_cut for it in free_items}
        cap_total = sum(caps.values())
        ratio = min(1.0, deficit / cap_total) if cap_total > 0 else 0.0
        exact = {it.code: it.estimate - caps[it.code] * ratio for it in free_items}
        rounded = _round_keeping_total(exact)
        for it in free_items:
            budgets[it.code] = rounded[it.code]
            if it.estimate - rounded[it.code] >= 1:
                cuts[it.code] = it.estimate - rounded[it.code]

    total = sum(budgets.values())
    planned = income - fixed_total - total
    shortfall = max(0.0, goal - planned) if income > 0 else 0.0
    # мелкое расхождение из-за округления не считаем проблемой
    if shortfall < 20:
        shortfall = 0.0
    # сохраняем исходный порядок категорий
    ordered = {it.code: budgets[it.code] for it in items if it.code in budgets}
    return PlanResult(ordered, round(planned, 2), round(shortfall, 2), cuts)


def structure(items: list[PlanItem], budgets: dict[str, float], income: float, fixed_total: float):
    """Доли «обязательное / желания / накопления» от дохода (ориентир 50/30/20)."""
    if income <= 0:
        return None
    needs = fixed_total + sum(budgets.get(i.code, 0) for i in items if i.group in (NEED, BUFFER))
    wants = sum(budgets.get(i.code, 0) for i in items if i.group == WANT)
    savings = income - fixed_total - sum(budgets.values())
    return needs / income, wants / income, savings / income


def insights(
    a: dict,
    items: list[PlanItem],
    result: PlanResult,
    income: float,
    fixed_total: float,
    goal: float,
    cur: str,
    balance: float | None = None,
    pending: float | None = None,
    pending_date: str | None = None,
) -> list[str]:
    """3–5 самых полезных наблюдений по ответам анкеты."""
    m = lambda x: fmt.money(x, cur)  # noqa: E731
    est = {it.code: it.estimate for it in items}
    tips: list[tuple[int, str]] = []
    s = lambda x: fmt.scaled(x, cur)  # noqa: E731

    if income > 0 and fixed_total > income:
        tips.append((100, f"🚨 Постоянные расходы ({m(fixed_total)}) больше дохода ({m(income)}). "
                          "Без пересмотра крупных платежей бюджет не сойдётся."))
    if result.shortfall > 0:
        tips.append((95, f"🎯 Чтобы откладывать {m(goal)}, пришлось бы урезать траты сильнее, чем реально. "
                         f"Реалистично сейчас — около {m(max(0, result.planned_savings))} в месяц."))
    if balance is not None and balance < 0:
        tips.append((88, f"🔻 Счёт в минусе ({m(balance)}). Банк берёт проценты за овердрафт — "
                         "первые свободные деньги лучше направить на выход из минуса."))
    if balance is not None and pending and pending > max(balance, 0):
        when = f" до {pending_date}" if pending_date else ""
        tips.append((90, f"⚠️ По карте к списанию {m(pending)}, а на счету {m(balance)}. "
                         f"Тратить{when} лучше аккуратно."))
    rent = float(a.get("rent") or 0)
    if income > 0 and rent / income > 0.35:
        tips.append((60, f"🏠 Жильё забирает {fmt.pct(rent / income)} дохода (ориентир — до 30–35%). "
                         "Это самая большая статья: любые изменения здесь дают максимальный эффект."))
    tobacco = est.get("tobacco", 0)
    if tobacco > 0:
        tips.append((58, f"🚬 Сигареты: {m(tobacco)} в месяц = {m(tobacco * 12)} в год. "
                         f"Даже минус четверть — это {m(tobacco * 3)} в год."))
    out = est.get("cafe", 0) + est.get("restaurants", 0) + est.get("delivery", 0)
    groceries = est.get("groceries", 0)
    delivery_n = float(a.get("delivery_freq") or 0)
    if delivery_n >= 8 and est.get("delivery", 0) > 0:
        save = fmt.nice_round(est["delivery"] * 0.5 * 0.6)
        tips.append((55, f"🛵 Доставка ≈ {round(delivery_n)} раз в месяц — {m(est['delivery'])}. "
                         f"Каждый второй заказ, заменённый готовкой дома, сэкономит ≈ {m(save)}."))
    elif out > groceries and out > 0:
        tips.append((52, f"🍔 Еда вне дома (кафе, рестораны, доставка) — {m(out)} в месяц, больше, "
                         f"чем продукты ({m(groceries)}). Готовить чаще — самый простой резерв."))
    cafe_n = float(a.get("cafe_freq") or 0)
    if cafe_n >= 17 and est.get("cafe", 0) >= s(300):
        save = fmt.nice_round(est["cafe"] * 0.35)
        tips.append((45, f"☕ Кафе и кофе ≈ {round(cafe_n)} раз в месяц — {m(est['cafe'])}. "
                         f"Кофе из дома 2–3 дня в неделю сэкономит ≈ {m(save)}."))
    impulse = est.get("impulse", 0)
    if impulse > 0 and (income > 0 and impulse / income > 0.04 or impulse >= s(300)):
        tips.append((42, f"🛍 Спонтанные покупки ≈ {m(impulse)} в месяц. Правило 24 часов "
                         "(подождать сутки перед покупкой) обычно срезает треть таких трат."))
    subs = sum(v for _, v in a.get("subs") or [])
    if subs >= s(100):
        tips.append((35, f"📺 Подписки: {m(subs)} в месяц = {m(subs * 12)} в год. "
                         "Проверь, всеми ли пользуешься."))
    games = est.get("games", 0)
    if games > 0 and income > 0 and games / income > 0.05:
        tips.append((30, f"🎮 Игры — {fmt.pct(games / income)} дохода. Можно задать себе лимит "
                         "и покупать игры на распродажах."))
    surplus = result.planned_savings - goal
    if income > 0 and goal >= 0 and surplus > 0.05 * income:
        tips.append((70, f"✨ Сверх цели остаётся ≈ {m(surplus)} — можно откладывать больше "
                         "или оставить себе запас."))
    if income > 0 and goal >= 0.2 * income:
        tips.append((20, f"💪 Цель — {fmt.pct(goal / income)} дохода. Отличный темп!"))
    elif income > 0 and goal <= 0:
        tips.append((25, "💡 Даже 5–10% дохода в месяц за год превращаются в подушку безопасности."))

    tips.sort(key=lambda t: t[0], reverse=True)
    return [text for _, text in tips[:5]]
