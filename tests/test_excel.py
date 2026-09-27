import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import openpyxl
import pytest

from budget_bot import demo, finance
from budget_bot.excel_store import ExcelStore, WorkbookError, read_workbook, write_workbook
from budget_bot.models import ACCOUNT, EXPENSE

NOW = datetime(2026, 9, 27, 14, 30)


def as_tuple(tx):
    return tx.id, tx.at, tx.kind, tx.amount, tx.category, tx.comment, tx.payment, tx.source


def test_roundtrip(tmp_path):
    data = demo.build_demo(NOW)
    path = tmp_path / "u.xlsx"
    write_workbook(path, data, NOW)
    back, warnings = read_workbook(path, data.user_id)
    assert warnings == []
    assert sorted(map(as_tuple, back.transactions)) == sorted(map(as_tuple, data.transactions))
    assert [(c.name, c.emoji, c.limit, c.keywords, c.code) for c in back.categories] == \
           [(c.name, c.emoji, c.limit, c.keywords, c.code) for c in data.categories]
    assert back.fixed == data.fixed and back.cards == data.cards and back.profile == data.profile
    wb = openpyxl.load_workbook(path)
    assert wb.sheetnames[:5] == ["Сводка", "Операции", "Бюджет", "Постоянные", "Карты"]
    assert wb["Списки"].sheet_state == "hidden"


def test_formula_injection_is_stored_as_text(tmp_path):
    data = demo.build_demo(NOW)
    data.add_transaction(at=NOW, kind=EXPENSE, amount=10, category="Продукты", comment='=HYPERLINK("http://x","y")')
    path = tmp_path / "u.xlsx"
    write_workbook(path, data, NOW)
    ws = openpyxl.load_workbook(path)["Операции"]
    comments = [row[5] for row in ws.iter_rows(min_row=2, values_only=True)]
    assert '=HYPERLINK("http://x","y")' in comments
    cell = next(c for c in ws["F"] if c.value == '=HYPERLINK("http://x","y")')
    assert cell.data_type == "s"


def test_cached_values_match_python(tmp_path):
    """Кешированные значения формул = то, что считает бот (превью на телефоне покажет верные числа)."""
    data = demo.build_demo(NOW)
    path = tmp_path / "u.xlsx"
    write_workbook(path, data, NOW)
    wb = openpyxl.load_workbook(path, data_only=True)
    dash = wb["Сводка"]
    assert dash["B14"].value == finance.account_balance(data, NOW)
    assert dash["B15"].value == pytest.approx(finance.cards_pending_total(data, NOW))
    stats = finance.month_stats(data, NOW.date(), NOW)
    assert dash["B11"].value == pytest.approx(stats.spent)


RECALC_ALWAYS = """<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry" xmlns:xs="http://www.w3.org/2001/XMLSchema"
 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<item oor:path="/org.openoffice.Office.Calc/Formula/Load"><prop oor:name="OOXMLRecalcMode" oor:op="fuse">
<value>0</value></prop></item>
</oor:items>
"""


@pytest.mark.skipif(shutil.which("soffice") is None, reason="нужен LibreOffice для пересчёта формул")
def test_formulas_recalculate_to_same_values(tmp_path):
    """LibreOffice заново считает все формулы — результаты совпадают с тем, что посчитал бот."""
    import xlsxwriter

    data = demo.build_demo(NOW)
    src = tmp_path / "u.xlsx"
    write_workbook(src, data, NOW)
    # «канарейка»: формула с заведомо неверным кешем — после пересчёта должна стать 2
    canary = tmp_path / "canary.xlsx"
    wb = xlsxwriter.Workbook(str(canary))
    wb.add_worksheet("A").write_formula(0, 0, "=1+1", None, 5)
    wb.close()
    profile = tmp_path / "lo-profile"
    (profile / "user").mkdir(parents=True)
    (profile / "user" / "registrymodifications.xcu").write_text(RECALC_ALWAYS)
    out = tmp_path / "out"
    result = subprocess.run(
        ["soffice", f"-env:UserInstallation={profile.as_uri()}", "--headless", "--norestore",
         "--convert-to", "xlsx:Calc MS Excel 2007 XML", "--outdir", str(out), str(src), str(canary)],
        capture_output=True, timeout=240,
        env={"SAL_USE_VCLPLUGIN": "svp", "HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )
    if result.returncode != 0 or not (out / "u.xlsx").exists():
        pytest.skip(f"LibreOffice не смог сконвертировать файл: {result.stderr[-200:]!r}")
    assert openpyxl.load_workbook(out / "canary.xlsx", data_only=True)["A"]["A1"].value == 2
    cached = openpyxl.load_workbook(src, data_only=True)
    formulas = openpyxl.load_workbook(src)
    recalced = openpyxl.load_workbook(out / "u.xlsx", data_only=True)
    checked = 0
    for ws in formulas.worksheets:
        for row in ws.iter_rows():
            for c in row:
                if isinstance(c.value, str) and c.value.startswith("="):
                    want = cached[ws.title][c.coordinate].value
                    got = recalced[ws.title][c.coordinate].value
                    assert got == pytest.approx(want, abs=0.01), (ws.title, c.coordinate, c.value)
                    checked += 1
    assert checked > 50


def test_import_messy_file(tmp_path):
    data = demo.build_demo(NOW)
    path = tmp_path / "u.xlsx"
    write_workbook(path, data, NOW)
    wb = openpyxl.load_workbook(path)
    ws = wb["Операции"]
    ws.append([None, "26.09.2026 12:00", "расход", "45,5", "продукты", "рынок", "счет", None])  # текстом
    ws.append([None, None, "Расход", 10, "Продукты", "без даты", "Счёт", None])
    ws.append([None, datetime(2026, 9, 25), "Перевод", 10, "Продукты", "странный тип", "Счёт", None])
    ws.append([None, datetime(2026, 9, 25), "Расход", 30, "Новая", "неизвестная карта", "Visa", None])
    ws.append([7, datetime(2026, 9, 24), "Расход", 12, "Продукты", "дубль id", "max", None])
    wb["Карты"]["A2"] = "MAX"
    wb.save(path)
    back, warnings = read_workbook(path, 1)
    assert any("без даты" in w or "нет даты" in w for w in warnings)
    assert any("Перевод" in w for w in warnings)
    assert any("Visa" in w for w in warnings)
    market = next(t for t in back.transactions if t.comment == "рынок")
    assert market.amount == 45.5 and market.category == "Продукты" and market.payment == ACCOUNT
    assert next(t for t in back.transactions if t.comment == "неизвестная карта").payment == ACCOUNT
    assert next(t for t in back.transactions if t.comment == "дубль id").payment == "MAX"
    ids = [t.id for t in back.transactions]
    assert len(ids) == len(set(ids))


def test_import_rejects_foreign_files(tmp_path):
    wrong = tmp_path / "x.xlsx"
    wb = openpyxl.Workbook()
    wb.active.title = "Лист1"
    wb.save(wrong)
    with pytest.raises(WorkbookError):
        read_workbook(wrong, 1)
    not_excel = tmp_path / "y.xlsx"
    not_excel.write_text("hello")
    with pytest.raises(WorkbookError):
        read_workbook(not_excel, 1)


def test_store_recovers_from_backup(tmp_path):
    store = ExcelStore(tmp_path)
    data = demo.build_demo(NOW, user_id=5)
    store.save(data, NOW)
    store.backup(5, "daily-1")
    Path(store.path(5)).write_text("broken")
    store._cache.clear()
    restored = store.load(5)
    assert restored is not None and len(restored.transactions) == len(data.transactions)
    assert (tmp_path / "users" / "5.broken.xlsx").exists()


def test_store_cache_returns_copies(tmp_path):
    store = ExcelStore(tmp_path)
    store.save(demo.build_demo(NOW, user_id=6), NOW)
    a = store.load(6)
    a.categories.clear()
    assert store.load(6).categories
