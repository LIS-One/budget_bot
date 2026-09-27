"""Круговые диаграммы (кольцо) бюджета и трат — PNG для отправки в Telegram.

Форма: кольцо «часть от целого» не больше чем из 7 сегментов; мелкие категории сворачиваются
в «Остальное». Под кольцом — легенда-таблица с суммами и процентами, так что цвет никогда не
единственный носитель смысла.
"""

from __future__ import annotations

import io
from datetime import date

from . import finance, fmt, palette
from .models import UserData
from .palette import INK, INK_2, MUTED, SURFACE, Segment

FIG_W, FIG_H, DPI = 7.2, 8.4, 150


def _render(title: str, subtitle: str, segments: list[Segment], center_value: str, center_caption: str,
            cur: str) -> bytes:
    import matplotlib

    matplotlib.use("Agg", force=False)
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib.patches import FancyBboxPatch

    fig = Figure(figsize=(FIG_W, FIG_H), dpi=DPI, facecolor=SURFACE)
    FigureCanvasAgg(fig)
    fig.text(0.07, 0.955, title, fontsize=19, fontweight="bold", color=INK, ha="left", va="top")
    fig.text(0.07, 0.912, subtitle, fontsize=11.5, color=INK_2, ha="left", va="top")

    ax = fig.add_axes([0.14, 0.395, 0.72, 0.47])
    ax.set_facecolor(SURFACE)
    values = [max(s.amount, 0.0) for s in segments]
    ax.pie(values, colors=[s.color for s in segments], startangle=90, counterclock=False,
           wedgeprops={"width": 0.34, "edgecolor": SURFACE, "linewidth": 2.0})
    ax.set_aspect("equal")
    ax.text(0, 0.07, center_value, ha="center", va="center", fontsize=21, fontweight="bold", color=INK)
    ax.text(0, -0.15, center_caption, ha="center", va="center", fontsize=10.5, color=INK_2)

    total = sum(values) or 1.0
    y = 0.345
    step = 0.043
    sw = 0.026  # сторона квадратика легенды (по ширине фигуры)
    for seg, value in zip(segments, values, strict=True):
        fig.patches.append(FancyBboxPatch(
            (0.075, y - sw * FIG_W / FIG_H / 2), sw, sw * FIG_W / FIG_H,
            boxstyle="round,pad=0,rounding_size=0.005", transform=fig.transFigure,
            facecolor=seg.color, edgecolor="none",
        ))
        label = seg.label if len(seg.label) <= 26 else seg.label[:25] + "…"
        fig.text(0.12, y, label, fontsize=12.5, color=INK, ha="left", va="center")
        fig.text(0.79, y, fmt.money(value, cur), fontsize=12.5, color=INK, ha="right", va="center")
        fig.text(0.93, y, fmt.pct(value / total), fontsize=12, color=MUTED, ha="right", va="center")
        y -= step

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=SURFACE)
    return buf.getvalue()


def budget_pie(data: UserData, month: date, owner: str | None = None) -> bytes | None:
    """Как распределён доход: постоянные, крупнейшие категории, остальное, накопления."""
    segments = palette.plan_segments(data)
    if not segments:
        return None
    cur = data.profile.currency
    whose = f" · {owner}" if owner else ""
    total = sum(s.amount for s in segments)
    if data.profile.income > 0:
        subtitle = f"Доход {fmt.money(data.profile.income, cur)} — куда он распределён"
    else:
        subtitle = "Постоянные платежи и лимиты категорий"
    return _render(f"Бюджет на {fmt.MONTHS_NOM[month.month - 1].lower()}{whose}", subtitle, segments,
                   fmt.money(total, cur), "в месяц", cur)


def spending_pie(data: UserData, month: date, now, owner: str | None = None) -> bytes | None:
    """На что реально ушли деньги в этом месяце (переменные траты)."""
    stats = finance.month_stats(data, month, now)
    spent = {c.name: c.spent for c in stats.categories}
    segments = palette.fact_segments(data, spent)
    if not segments:
        return None
    cur = data.profile.currency
    whose = f" · {owner}" if owner else ""
    subtitle = f"Потрачено {fmt.money(stats.spent, cur)} из {fmt.money(stats.budget, cur)} лимитов"
    return _render(f"Траты за {fmt.MONTHS_NOM[month.month - 1].lower()}{whose}", subtitle, segments,
                   fmt.money(stats.spent, cur), "потрачено", cur)
