"""Сквозной сценарий: анкета → траты → просмотр → настройки → Excel → пара → фоновые задачи."""

from datetime import datetime

import openpyxl

from budget_bot import finance
from budget_bot.excel_store import read_workbook
from budget_bot.jobs import run_daily, run_weekly
from budget_bot.models import CARD_CHARGE, EXPENSE, FIXED_CATEGORY, INCOME
from tg_sim import button, last_with_buttons, texts

A, AN = 111, "Аня"
B, BN = 222, "Боря"


async def answer(sim, uid, name, sent, *, text=None, press=None):
    """Ответ на текущий вопрос анкеты: текстом или кнопкой с подписью press."""
    if text is not None:
        return await sim.text(uid, name, text)
    mid, data = button(sent, press)
    return await sim.press(uid, name, mid, data)


async def onboard_a(sim):
    s = await sim.text(A, AN, "/start")
    assert "Привет, Аня" in texts(s)
    s = await answer(sim, A, AN, s, press="Начать")
    assert "Жильё" in texts(s)
    s = await answer(sim, A, AN, s, text="4500")
    assert "Коммунальные" in texts(s)
    s = await answer(sim, A, AN, s, text="450")
    s = await answer(sim, A, AN, s, press="Не плачу")  # арнона
    assert "Подписки" in texts(s)
    s = await answer(sim, A, AN, s, text="Netflix 50, Spotify 20")
    s = await answer(sim, A, AN, s, press="Больше ничего")
    assert "Доход" in texts(s) and "5 020" in texts(s)  # постоянные: 4500+450+50+20
    s = await answer(sim, A, AN, s, text="12000")
    s = await answer(sim, A, AN, s, press="10-го")
    s = await answer(sim, A, AN, s, text="8000")
    s = await answer(sim, A, AN, s, press="Да")  # есть кредитка
    s = await answer(sim, A, AN, s, press="Max")
    s = await answer(sim, A, AN, s, press="10-го")
    s = await answer(sim, A, AN, s, text="2300")
    s = await answer(sim, A, AN, s, press="Нет, это всё")
    assert "Продукты" in texts(s)
    s = await answer(sim, A, AN, s, text="300")  # в неделю
    s = await answer(sim, A, AN, s, press="250")  # транспорт
    s = await answer(sim, A, AN, s, press="Почти не трачу")  # здоровье
    s = await answer(sim, A, AN, s, press="200")  # одежда
    s = await answer(sim, A, AN, s, press="Нет хобби")
    s = await answer(sim, A, AN, s, press="3–5 раз в неделю")
    s = await answer(sim, A, AN, s, press="25")
    s = await answer(sim, A, AN, s, press="1–2 раза в месяц")
    s = await answer(sim, A, AN, s, text="150")
    s = await answer(sim, A, AN, s, press="Раз в неделю")
    s = await answer(sim, A, AN, s, press="70")
    s = await answer(sim, A, AN, s, press="Не курю")
    s = await answer(sim, A, AN, s, press="100")  # игры
    s = await answer(sim, A, AN, s, press="400")  # вторая половинка
    s = await answer(sim, A, AN, s, press="Раз в неделю")  # спонтанные
    s = await answer(sim, A, AN, s, press="50")
    s = await answer(sim, A, AN, s, press="Раз в пару месяцев")
    s = await answer(sim, A, AN, s, press="500")
    assert "На что ещё" in texts(s)
    s = await answer(sim, A, AN, s, press="Спорт")
    s = await answer(sim, A, AN, s, press="Подарки")
    s = await answer(sim, A, AN, s, press="Готово")
    assert "Спорт" in texts(s)
    s = await answer(sim, A, AN, s, press="200")
    assert "Подарки" in texts(s)
    s = await answer(sim, A, AN, s, press="200")
    s = await answer(sim, A, AN, s, text="Танцы 250")
    s = await answer(sim, A, AN, s, text="Коплю на отпуск")
    assert "Сколько в идеале хочешь откладывать" in texts(s)
    s = await answer(sim, A, AN, s, press="рекомендую")
    assert "Мой анализ" in texts(s) and "Предлагаемый бюджет" in texts(s)
    # в том же окне меняем лимит категории и цель
    s = await answer(sim, A, AN, s, press="Кафе")
    assert "Напиши новый лимит" in texts(s)
    s = await answer(sim, A, AN, s, text="500")
    assert "Кафе и перекусы — <b>500" in texts(s)
    s = await answer(sim, A, AN, s, press="Цель")
    s = await answer(sim, A, AN, s, text="2000")
    assert "Цель накоплений: 2" in texts(s)
    s = await answer(sim, A, AN, s, press="Сохранить бюджет")
    assert "Бюджет сохранён" in texts(s)
    assert any(x.method == "sendPhoto" for x in s)
    return s


async def test_full_flow(sim):
    await onboard_a(sim)
    store = sim.app.bot_data["svc"].store
    data = store.load(A)
    assert data.profile.income == 12000 and data.profile.payday == 10 and data.profile.savings_goal == 2000
    assert [c.name for c in data.cards] == ["Max"] and data.cards[0].baseline == 2300
    assert {f.name for f in data.fixed} == {"Жильё", "Счета", "Netflix", "Spotify"}
    assert next(f for f in data.fixed if f.name == "Netflix").payment == "Max"
    names = [c.name for c in data.categories]
    assert "Танцы" in names and "Кафе и перекусы" in names and "Спорт" in names
    assert data.category("Кафе и перекусы").limit == 500
    # Постоянные платежи и зарплата этого месяца уже прошли → записаны для статистики, но баланс не меняют
    assert sum(1 for t in data.transactions if t.category == FIXED_CATEGORY) == 4
    assert any(t.kind == INCOME for t in data.transactions)
    assert finance.account_balance(data, sim.now) == 8000
    assert round(data.planned_savings) >= 1990  # лимиты подогнаны под цель 2000

    # --- траты ---
    s = await sim.text(A, AN, "45 кофе")
    assert "Кафе и перекусы" in texts(s) and "Max" in texts(s)
    s = await sim.text(A, AN, "шаурма 38")
    assert "Кафе и перекусы" in texts(s)
    s = await sim.text(A, AN, "120 штука")
    assert "Куда записать" in texts(s)
    mid, data_cb = button(s, "Продукты")
    s = await sim.press(A, AN, mid, data_cb)
    assert "Записал" in texts(s) and "Продукты" in texts(s)
    s = await sim.text(A, AN, "+500 кешбэк")
    assert "Доход" in texts(s)
    s = await sim.text(A, AN, "зарплата 3000")
    assert "доход или трата" in texts(s)
    mid, data_cb = button(s, "Доход")
    s = await sim.press(A, AN, mid, data_cb)
    assert "+3 000" in texts(s)
    # следующая «штука» уже категоризируется по истории
    s = await sim.text(A, AN, "15 штука")
    assert "Продукты" in texts(s) and "Куда записать" not in texts(s)
    # поменять категорию и оплату у записи
    mid, data_cb = button(s, "Категория")
    s = await sim.press(A, AN, mid, data_cb)
    mid, data_cb = button(s, "Игры")
    s = await sim.press(A, AN, mid, data_cb)
    assert "Игры" in texts(s)
    mid, data_cb = button(s, "Оплата")
    s = await sim.press(A, AN, mid, data_cb)
    mid, data_cb = button(s, "Счёт")
    s = await sim.press(A, AN, mid, data_cb)
    assert "🏦 Счёт" in texts(s)
    data = store.load(A)
    last = max(data.transactions, key=lambda t: t.id)
    assert last.category == "Игры" and last.payment == "Счёт" and last.amount == 15
    # отмена последней записи
    s = await sim.text(A, AN, "/undo")
    assert "Отменил" in texts(s)
    assert store.load(A).transaction(last.id) is None

    # --- просмотр ---
    s = await sim.text(A, AN, "📊 Бюджет")
    assert "Бюджет · Сентябрь 2026" in texts(s) and "Кафе и перекусы" in texts(s)
    s = await sim.text(A, AN, "🥧 Диаграмма")
    assert s[-1].method == "sendPhoto"
    mid, data_cb = button(s, "Траты")
    s = await sim.press(A, AN, mid, data_cb)
    assert any(x.method == "editMessageMedia" for x in s)
    s = await sim.text(A, AN, "📈 Сводка")
    assert "За 7 дней потрачено: 203 ₪" in texts(s) and "На счету сейчас: ≈ 11 500 ₪" in texts(s)
    assert "Max — спишется 10.10: 2 503 ₪" in texts(s)  # 2300 + 45 + 38 + 120
    s = await sim.text(A, AN, "🏦 Счёт")
    mid, data_cb = button(s, "Сверить баланс")
    s = await sim.press(A, AN, mid, data_cb)
    s = await sim.text(A, AN, "7000")
    assert "Баланс сверен" in texts(s)
    assert any(x.method == "deleteMessage" for x in s)  # ответ пользователя убран — «то же окно»
    assert finance.account_balance(store.load(A), sim.now) == 7000

    # --- история: открыть и удалить операцию ---
    s = await sim.text(A, AN, "📋 История")
    assert "Операции" in texts(s)
    mid, data_cb = button(s, "1")
    s = await sim.press(A, AN, mid, data_cb)
    mid, data_cb = button(s, "Удалить")
    s = await sim.press(A, AN, mid, data_cb)
    mid, data_cb = button(s, "Да, удалить")
    count_before = len(store.load(A).transactions)
    s = await sim.press(A, AN, mid, data_cb)
    assert "Удалено" in texts(s)
    assert len(store.load(A).transactions) == count_before - 1

    # --- настройки: лимит, новая категория, постоянный платёж, цель ---
    s = await sim.text(A, AN, "⚙️ Настройки")
    mid, data_cb = button(s, "Категории и лимиты")
    s = await sim.press(A, AN, mid, data_cb)
    mid, data_cb = button(s, "Продукты")
    s = await sim.press(A, AN, mid, data_cb)
    mid, data_cb = button(s, "Лимит")
    s = await sim.press(A, AN, mid, data_cb)
    s = await sim.text(A, AN, "1500")
    assert "Лимит обновлён" in texts(s)
    assert store.load(A).category("Продукты").limit == 1500
    mid, data_cb = button(s, "К категориям")
    s = await sim.press(A, AN, mid, data_cb)
    mid, data_cb = button(s, "Новая категория")
    s = await sim.press(A, AN, mid, data_cb)
    s = await sim.text(A, AN, "🎸 Гитара 200")
    assert "добавлена" in texts(s)
    assert store.load(A).category("Гитара").emoji == "🎸"
    s = await sim.text(A, AN, "гитара струны 60")
    assert "Гитара" in texts(s)
    s = await sim.text(A, AN, "⚙️ Настройки")
    mid, data_cb = button(s, "Постоянные")
    s = await sim.press(A, AN, mid, data_cb)
    mid, data_cb = button(s, "Добавить платёж")
    s = await sim.press(A, AN, mid, data_cb)
    s = await sim.text(A, AN, "Ваад байт 150")
    assert "Ваад байт" in texts(s)
    s = await sim.text(A, AN, "⚙️ Настройки")
    mid, data_cb = button(s, "Накопления")
    s = await sim.press(A, AN, mid, data_cb)
    mid, data_cb = button(s, "Изменить цель")
    s = await sim.press(A, AN, mid, data_cb)
    s = await sim.text(A, AN, "3000")
    assert "Цель обновлена" in texts(s)
    mid, data_cb = button(s, "Подогнать")
    s = await sim.press(A, AN, mid, data_cb)
    data = store.load(A)
    assert data.profile.savings_goal == 3000 and data.planned_savings >= 2950

    # --- Excel: выгрузка и загрузка обратно ---
    s = await sim.text(A, AN, "📁 Excel")
    doc = next(x for x in s if x.method == "sendDocument")
    content = doc.files["content"]
    assert content[:2] == b"PK"
    path = sim.app.bot_data["svc"].store.tmp_dir / "check.xlsx"
    path.write_bytes(content)
    exported, warnings = read_workbook(path, A)
    assert not warnings and len(exported.transactions) == len(store.load(A).transactions)
    wb = openpyxl.load_workbook(path)
    ws = wb["Операции"]
    ws.append([None, datetime(2026, 9, 26, 12, 0), "Расход", 99, "Продукты", "рынок", "Счёт", None])
    wb["Бюджет"]["C2"] = 1700  # лимит «Продукты»
    wb.save(path)
    s = await sim.document(A, AN, path.read_bytes())
    assert "Нашёл в файле" in texts(s)
    mid, data_cb = button(s, "Загрузить")
    s = await sim.press(A, AN, mid, data_cb)
    assert "загружены" in texts(s)
    data = store.load(A)
    assert data.category("Продукты").limit == 1700
    assert any(t.comment == "рынок" and t.amount == 99 for t in data.transactions)

    # --- пара ---
    s = await sim.text(A, AN, "👫 Пара")
    mid, data_cb = button(s, "Пригласить")
    s = await sim.press(A, AN, mid, data_cb)
    link_text = texts(s)
    code = link_text.split("start=pair_")[1][:6]
    s = await sim.text(B, BN, f"/start pair_{code}")
    assert "Привет, Боря" in texts(s)
    s = await onboard_quick(sim, B, BN, s)
    assert "приглашает объединиться" in texts(s)
    mid, data_cb = button(s, "Принять")
    s = await sim.press(B, BN, mid, data_cb)
    assert "Вы в паре с Аня" in texts(s)
    assert any("принял(а) приглашение" in x.text for x in s)  # уведомление Ане
    s = await sim.text(A, AN, "👫 Пара")
    assert "Вы в паре с Боря" in texts(s)
    mid, data_cb = button(s, "Бюджет")
    s = await sim.press(A, AN, mid, data_cb)
    assert "Бюджет · Боря" in texts(s)
    mid, data_cb = button(s, "Операции")
    s = await sim.press(A, AN, mid, data_cb)
    assert "Операции · Боря" in texts(s)
    s = await sim.text(B, BN, "👫 Пара")
    mid, data_cb = button(s, "Сводка")
    s = await sim.press(B, BN, mid, data_cb)
    assert "Последние 7 дней · Аня" in texts(s)
    mid, data_cb = button(s, "Пара")
    s = await sim.press(B, BN, mid, data_cb)
    mid, data_cb = button(s, "Excel партнёра")
    s = await sim.press(B, BN, mid, data_cb)
    assert any(x.method == "sendDocument" for x in s)

    # --- фоновые задачи: сводка в воскресенье, списание карты и зарплата 10-го ---
    sim.now = datetime(2026, 10, 4, 9, 0)
    sim._new()
    await run_weekly(sim.app)
    weekly = sim._new()
    assert {int(x.params["chat_id"]) for x in weekly if "Сводка за неделю" in x.text} == {A, B}
    assert any("Итоги: Сентябрь 2026" in x.text for x in weekly)

    before = store.load(A)
    pending = finance.card_pending(before, before.cards[0], datetime(2026, 10, 9, 23, 59))
    sim.now = datetime(2026, 10, 10, 8, 0)
    await run_daily(sim.app)
    daily = sim._new()
    data = store.load(A)
    charges = [t for t in data.transactions if t.kind == CARD_CHARGE]
    assert len(charges) == 1 and charges[0].amount == pending
    assert any("списание по карте" in x.text for x in daily)
    assert any("день зарплаты" in x.text for x in daily)
    oct_fixed = [t for t in data.transactions if t.category == FIXED_CATEGORY and t.at.month == 10]
    assert {t.comment for t in oct_fixed} == {"Жильё", "Счета", "Netflix", "Spotify", "Ваад байт"}
    # повторный запуск в тот же день ничего не дублирует
    await run_daily(sim.app)
    assert len(store.load(A).transactions) == len(data.transactions)
    # баланс: сверка 7000 + всё после неё
    after_reconcile = [t for t in data.transactions if t.at > data.profile.balance_at]
    expected = 7000 + sum(t.amount for t in after_reconcile if t.kind == INCOME) \
        - sum(t.amount for t in after_reconcile if t.kind == EXPENSE and t.payment == "Счёт") \
        - sum(t.amount for t in after_reconcile if t.kind == CARD_CHARGE)
    assert finance.account_balance(data, sim.now) == round(expected, 2)


async def onboard_quick(sim, uid, name, s):
    """Самый короткий путь анкеты: минимальные ответы."""
    s = await answer(sim, uid, name, s, press="Начать")
    s = await answer(sim, uid, name, s, text="2000")  # жильё
    for label in ("Входят в аренду", "Не плачу", "Нет подписок", "Больше ничего"):
        s = await answer(sim, uid, name, s, press=label)
    s = await answer(sim, uid, name, s, text="7000")  # доход
    s = await answer(sim, uid, name, s, press="Нерегулярно")
    s = await answer(sim, uid, name, s, text="1200")  # счёт
    s = await answer(sim, uid, name, s, press="Нет")  # кредитки нет
    s = await answer(sim, uid, name, s, press="150")  # продукты в неделю
    for label in ("Не трачу", "Почти не трачу", "Почти не трачу", "Нет хобби", "Почти никогда", "Почти никогда",
                  "Почти никогда", "Не курю", "Не трачу", "Нет / не трачу", "Почти никогда"):
        s = await answer(sim, uid, name, s, press=label)
    s = await answer(sim, uid, name, s, press="Почти никогда")  # непредвиденное
    s = await answer(sim, uid, name, s, press="200")
    s = await answer(sim, uid, name, s, press="Ничего из этого")
    s = await answer(sim, uid, name, s, press="Нет, всё")
    s = await answer(sim, uid, name, s, press="Нет")  # нюансы
    s = await answer(sim, uid, name, s, press="рекомендую")
    s = await answer(sim, uid, name, s, press="Сохранить бюджет")
    return s


async def test_back_button_and_resume(sim):
    s = await sim.text(A, AN, "/start")
    s = await answer(sim, A, AN, s, press="Начать")
    s = await answer(sim, A, AN, s, text="4000")
    assert "Коммунальные" in texts(s)
    s = await answer(sim, A, AN, s, press="Назад")
    assert "Жильё" in texts(s)
    s = await answer(sim, A, AN, s, text="abc")
    assert "Не понял сумму" in texts(s)
    s = await answer(sim, A, AN, s, text="4200")
    s = await sim.text(A, AN, "/start")  # продолжение с того же места
    assert "Продолжим" in texts(s) and "Коммунальные" in texts(s)
    ob = sim.app.user_data[A]["ob"]
    assert ob["a"]["rent"] == 4200


async def test_access_whitelist(cfg):
    from datetime import time as dtime
    from zoneinfo import ZoneInfo

    from budget_bot.app import build_application
    from budget_bot.config import Config
    from tg_sim import FakeTelegram, Sim

    closed = Config(bot_token=cfg.bot_token, data_dir=cfg.data_dir, timezone=ZoneInfo("Asia/Jerusalem"),
                    currency="₪", summary_weekday=0, summary_time=dtime(9), daily_time=dtime(8),
                    allowed_user_ids=frozenset({A}))
    fake = FakeTelegram()
    state = Sim(None, fake, datetime(2026, 9, 27, 12, 0))
    app = build_application(closed, clock=state.clock, request=fake, persistence=False)
    state.app = app
    await app.initialize()
    try:
        s = await state.text(B, BN, "/start")
        assert "личный бот" in texts(s) and str(B) in texts(s)
        s = await state.text(A, AN, "/start")
        assert "Привет" in texts(s)
    finally:
        await app.shutdown()


async def test_last_with_buttons_helper(sim):
    s = await sim.text(A, AN, "/start")
    assert last_with_buttons(s).buttons
