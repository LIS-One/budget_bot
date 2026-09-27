"""Собирает пример таблицы (examples/budget_demo.xlsx) и картинки диаграмм для документации.

Запуск из корня репозитория:  python scripts/make_demo.py
"""

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from budget_bot import charts, demo  # noqa: E402
from budget_bot.excel_store import write_workbook  # noqa: E402


def main() -> None:
    now = datetime(2026, 9, 27, 14, 30)
    data = demo.build_demo(now)
    (ROOT / "examples").mkdir(exist_ok=True)
    (ROOT / "docs" / "images").mkdir(parents=True, exist_ok=True)
    write_workbook(ROOT / "examples" / "budget_demo.xlsx", data, now)
    (ROOT / "docs" / "images" / "chart_plan.png").write_bytes(charts.budget_pie(data, now.date()))
    (ROOT / "docs" / "images" / "chart_fact.png").write_bytes(charts.spending_pie(data, now.date(), now))
    print("Готово: examples/budget_demo.xlsx, docs/images/chart_plan.png, docs/images/chart_fact.png")


if __name__ == "__main__":
    main()
