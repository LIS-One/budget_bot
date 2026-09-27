"""Цвета диаграмм (картинки и Excel) и разбиение бюджета на сегменты круговой диаграммы.

Палитра — проверенная категориальная палитра (порядок слотов подобран так, чтобы соседние цвета
различались и при дальтонизме). Цвет закреплён за смыслом: «Постоянные» всегда фиолетовые,
«Накопления» — зелёные, четыре самые крупные категории — синий/оранжевый/бирюзовый/жёлтый,
всё мелкое сворачивается в серое «Остальное».
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import UserData

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
OTHER = "#c3c2b7"

SLOTS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
TOP = SLOTS[:4]
EXTRA = SLOTS[4]
SAVINGS = SLOTS[5]
FIXED = SLOTS[6]

TOP_N = 4


@dataclass
class Segment:
    label: str
    amount: float
    color: str
    category: str | None = None  # имя категории бюджета, если сегмент — одна категория


def top_categories(data: UserData, n: int = TOP_N) -> list[str]:
    """Самые крупные по лимиту категории — у них постоянные цвета на всех диаграммах."""
    cats = sorted((c for c in data.categories if c.limit > 0), key=lambda c: (-c.limit, c.name.lower()))
    return [c.name for c in cats[:n]]


def plan_segments(data: UserData) -> list[Segment]:
    segs: list[Segment] = []
    if data.fixed_total > 0:
        segs.append(Segment("Постоянные", data.fixed_total, FIXED))
    top = top_categories(data)
    for i, name in enumerate(top):
        cat = data.category(name)
        segs.append(Segment(name, cat.limit, TOP[i], category=name))
    rest = data.limits_total - sum(s.amount for s in segs if s.category)
    if rest > 0.005:
        segs.append(Segment("Остальное", rest, OTHER))
    if data.planned_savings > 0.005:
        segs.append(Segment("Накопления", data.planned_savings, SAVINGS))
    return segs


def fact_segments(data: UserData, spent: dict[str, float]) -> list[Segment]:
    """spent: имя категории → потрачено за месяц (только переменные траты)."""
    top = top_categories(data)
    spent = {k: v for k, v in spent.items() if v > 0.005}
    lower = {k.lower(): k for k in spent}
    segs: list[Segment] = []
    used = set()
    for i, name in enumerate(top):
        key = lower.get(name.lower())
        if key:
            segs.append(Segment(name, spent[key], TOP[i], category=name))
            used.add(key)
    others = sorted(((k, v) for k, v in spent.items() if k not in used), key=lambda kv: -kv[1])
    if others:
        name, amount = others[0]
        segs.append(Segment(name, amount, EXTRA, category=name))
        rest = sum(v for _, v in others[1:])
        if rest > 0.005:
            segs.append(Segment("Остальное", rest, OTHER))
    return segs
