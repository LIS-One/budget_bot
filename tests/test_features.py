"""Запись задним числом, предупреждения о лимитах (себе и партнёру), вечернее напоминание."""

from datetime import date, datetime

from budget_bot import alerts
from budget_bot.categories import make_category
from budget_bot.jobs import run_reminders
from budget_bot.models import EXPENSE, INCOME, Profile, UserData
from test_flow import A, AN, B, BN, onboard_quick
from tg_sim import button, texts


def store(sim):
    return sim.app.bot_data["svc"].store


def registry(sim):
    return sim.app.bot_data["svc"].registry


async def ready(sim, uid=A, name=AN):
    s = await sim.text(uid, name, "/start")
    await onboard_quick(sim, uid, name, s)  # категории: Продукты 650, Непредвиденное 140, Прочее 40


# --- задним числом ------------------------------------------------------------------------------------------
async def test_backdated_entries(sim):
    await ready(sim)
    s = await sim.text(A, AN, "вчера 45 хлеб")
    assert "Записал за 26.09.2026" in texts(s) and "Продукты" in texts(s)
    s = await sim.text(A, AN, "25 сентября 120 хлеб")
    assert "за 25.09.2026" in texts(s)
    s = await sim.text(A, AN, "15.08 100 хлеб")
    assert "Август в категории" in texts(s)
    s = await sim.text(A, AN, "30.09 45 хлеб")
    assert "ещё не наступила" in texts(s)
    s = await sim.text(A, AN, "25.09 хлеб")  # без второй суммы — это сумма 25,09 за сегодня
    assert "25,09" in texts(s) and "Записал:" in texts(s)
    s = await sim.text(A, AN, "вчера +500 кешбэк")
    assert "Доход за 26.09.2026" in texts(s)
    s = await sim.text(A, AN, "вчера 70 штука")  # категория неизвестна → выбор с сохранением даты
    assert "за 26.09.2026" in texts(s)
    mid, cb = button(s, "Продукты")
    await sim.press(A, AN, mid, cb)
    data = store(sim).load(A)
    by_comment = {t.comment: t for t in data.transactions if t.source == "бот"}
    assert by_comment["кешбэк"].at == datetime(2026, 9, 26, 12, 0) and by_comment["кешбэк"].kind == INCOME
    assert by_comment["штука"].at == datetime(2026, 9, 26, 12, 0)
    dates = sorted(t.at.date() for t in data.transactions if t.comment == "хлеб")
    assert dates == [date(2026, 8, 15), date(2026, 9, 25), date(2026, 9, 26), date(2026, 9, 27)]


async def test_change_date_of_transaction(sim):
    await ready(sim)
    s = await sim.text(A, AN, "45 хлеб")
    mid, cb = button(s, "Дата")
    s = await sim.press(A, AN, mid, cb)
    assert "Когда это было" in texts(s)
    s = await sim.text(A, AN, "01.10")
    assert "ещё не наступила" in texts(s)
    s = await sim.text(A, AN, "позавчера")
    assert "за 25.09.2026" in texts(s)
    tx = max(store(sim).load(A).transactions, key=lambda t: t.id)
    assert tx.at.date() == date(2026, 9, 25) and tx.at.time() != datetime.min.time()


# --- предупреждения о лимитах ---------------------------------------------------------------------------------
async def test_limit_alerts_at_80_and_100(sim):
    await ready(sim)
    s = await sim.text(A, AN, "500 хлеб")  # 500 из 650 = 77%
    assert "лимита" not in texts(s).split("✅ Записал")[0]
    assert not any("⚠️" in x.text or "🔴 <b>Лимит" in x.text for x in s)
    s = await sim.text(A, AN, "30 хлеб")  # 530 → 82%
    alert = [x for x in s if x.text.startswith("⚠️")]
    assert len(alert) == 1 and "82% лимита" in alert[0].text and "осталось 120 ₪" in alert[0].text
    s = await sim.text(A, AN, "10 хлеб")  # порог уже пройден — без повторов
    assert not any(x.text.startswith(("⚠️", "🔴")) for x in s)
    s = await sim.text(A, AN, "150 хлеб")  # 690 → больше лимита
    alert = [x for x in s if x.text.startswith("🔴")]
    assert len(alert) == 1 and "исчерпан" in alert[0].text and "перерасход 40 ₪" in alert[0].text
    s = await sim.text(A, AN, "5 хлеб")
    assert not any(x.text.startswith(("⚠️", "🔴")) for x in s)
    s = await sim.text(A, AN, "15.08 900 хлеб")  # прошлый месяц — не предупреждаем
    assert not any(x.text.startswith(("⚠️", "🔴")) for x in s)


async def test_limit_alert_after_category_change_and_toggle(sim):
    await ready(sim)
    s = await sim.text(A, AN, "600 штука")
    mid, cb = button(s, "Прочее")  # лимит «Прочее» = 40
    s = await sim.press(A, AN, mid, cb)
    assert any(x.text.startswith("🔴") and "Прочее" in x.text for x in s)
    mid, cb = button(s, "Категория")
    s = await sim.press(A, AN, mid, cb)
    mid, cb = button(s, "Продукты")
    s = await sim.press(A, AN, mid, cb)
    assert any(x.text.startswith("⚠️") and "Продукты" in x.text for x in s)  # 600 из 650 = 92%
    # выключаем предупреждения в настройках
    s = await sim.text(A, AN, "⚙️ Настройки")
    mid, cb = button(s, "Уведомления")
    s = await sim.press(A, AN, mid, cb)
    mid, cb = button(s, "Лимиты")
    s = await sim.press(A, AN, mid, cb)
    assert registry(sim).prefs(A)["limits"] is False
    s = await sim.text(A, AN, "100 хлеб")  # 700 из 650 — но предупреждения выключены
    assert not any(x.text.startswith(("⚠️", "🔴")) for x in s)


async def test_partner_gets_alerts_only_when_enabled(sim):
    await ready(sim, A, AN)
    await ready(sim, B, BN)
    s = await sim.text(A, AN, "👫 Пара")
    mid, cb = button(s, "Пригласить")
    s = await sim.press(A, AN, mid, cb)
    code = texts(s).split("<code>")[1][:6]
    s = await sim.text(B, BN, "👫 Пара")
    mid, cb = button(s, "Ввести код")
    await sim.press(B, BN, mid, cb)
    await sim.text(B, BN, code)
    s = await sim.text(A, AN, "530 хлеб")
    assert not any(int(x.params.get("chat_id", 0)) == B for x in s if x.method == "sendMessage")
    s = await sim.text(A, AN, "⚙️ Настройки")
    mid, cb = button(s, "Уведомления")
    s = await sim.press(A, AN, mid, cb)
    mid, cb = button(s, "Партнёру о лимитах")
    s = await sim.press(A, AN, mid, cb)
    assert "Сообщать партнёру о моих лимитах: ✅ вкл" in texts(s)
    s = await sim.text(A, AN, "150 хлеб")
    to_b = [x.text for x in s if x.method == "sendMessage" and int(x.params["chat_id"]) == B]
    assert to_b and "👀 Аня: лимит «🛒 Продукты» исчерпан" in to_b[0]


def test_crossings_unit():
    now = datetime(2026, 9, 20, 12)
    data = UserData(1, Profile(created_at=now, balance_at=now), categories=[make_category("cafe", 100)])
    before = alerts.snapshot(data, now)
    data.add_transaction(at=now, kind=EXPENSE, amount=80, category="Кафе и перекусы")
    found = alerts.crossings(data, before, now)
    assert [(c.level, c.spent) for c in found] == [(0.8, 80)]  # ровно 80% — уже предупреждаем
    before = alerts.snapshot(data, now)
    data.add_transaction(at=now, kind=EXPENSE, amount=20, category="Кафе и перекусы")
    found = alerts.crossings(data, before, now)
    assert [(c.level, c.spent) for c in found] == [(1.0, 100)]
    assert "исчерпан" in alerts.own_text(data, found[0], now) and "перерасход" not in alerts.own_text(data, found[0], now)


# --- вечернее напоминание ---------------------------------------------------------------------------------------
async def test_evening_reminder(sim):
    await ready(sim)
    sim.now = datetime(2026, 9, 27, 20, 58)
    sim._new()
    await run_reminders(sim.app)
    assert not sim._new()  # ещё рано
    sim.now = datetime(2026, 9, 27, 21, 3)
    await run_reminders(sim.app)
    sent = sim._new()
    assert len(sent) == 1 and "ещё ничего не записано" in sent[0].text and int(sent[0].params["chat_id"]) == A
    await run_reminders(sim.app)
    assert not sim._new()  # не чаще раза в день
    mid, cb = button(sent, "трат не было")
    s = await sim.press(A, AN, mid, cb)
    assert "день без трат" in texts(s)
    # на следующий день трата записана — напоминать не нужно
    sim.now = datetime(2026, 9, 28, 13, 0)
    await sim.text(A, AN, "45 хлеб")
    sim.now = datetime(2026, 9, 28, 21, 1)
    sim._new()
    await run_reminders(sim.app)
    assert not sim._new()
    # ещё через день ничего не записано, но бот «проснулся» только глубокой ночью — окно прошло
    sim.now = datetime(2026, 9, 29, 23, 59)
    await run_reminders(sim.app)
    assert len(sim._new()) == 1
    sim.now = datetime(2026, 9, 30, 0, 30)
    await run_reminders(sim.app)
    assert not sim._new()


async def test_reminder_settings_and_off_button(sim):
    await ready(sim)
    s = await sim.text(A, AN, "⚙️ Настройки")
    assert "напоминание 21:00" in texts(s)
    mid, cb = button(s, "Уведомления")
    s = await sim.press(A, AN, mid, cb)
    mid, cb = button(s, "Напоминание: 21:00")
    s = await sim.press(A, AN, mid, cb)
    mid, cb = button(s, "Своё время")
    s = await sim.press(A, AN, mid, cb)
    s = await sim.text(A, AN, "вечером")
    assert "Не понял время" in texts(s)
    s = await sim.text(A, AN, "22:30")
    assert "Напоминание — в 22:30" in texts(s)
    assert registry(sim).prefs(A)["reminder"] == "22:30"
    sim.now = datetime(2026, 9, 27, 22, 31)
    sim._new()
    await run_reminders(sim.app)
    sent = sim._new()
    assert len(sent) == 1
    mid, cb = button(sent, "Не напоминать")
    s = await sim.press(A, AN, mid, cb)
    assert "Напоминания выключены" in texts(s)
    assert registry(sim).prefs(A)["reminder"] == ""
    sim.now = datetime(2026, 9, 28, 22, 31)
    await run_reminders(sim.app)
    assert not sim._new()


async def test_setting_passed_time_starts_tomorrow(sim):
    await ready(sim)
    sim.now = datetime(2026, 9, 27, 21, 40)
    s = await sim.text(A, AN, "⚙️ Настройки")
    mid, cb = button(s, "Уведомления")
    s = await sim.press(A, AN, mid, cb)
    mid, cb = button(s, "Напоминание")
    s = await sim.press(A, AN, mid, cb)
    mid, cb = button(s, "20:00")
    s = await sim.press(A, AN, mid, cb)
    sim._new()
    await run_reminders(sim.app)
    assert not sim._new()  # 20:00 сегодня уже прошло — начнём завтра


async def test_broken_reminder_value_is_skipped(sim):
    await ready(sim)
    await ready(sim, B, BN)
    registry(sim).upsert(A, reminder="потом")
    sim.now = datetime(2026, 9, 27, 21, 5)
    sim._new()
    await run_reminders(sim.app)
    sent = sim._new()
    assert [int(x.params["chat_id"]) for x in sent] == [B]  # у Бори напоминание пришло, у Ани — пропущено
