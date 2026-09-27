"""Дополнительные сценарии: без дохода, повторная анкета, ввод кода пары, удаление данных, карты."""

from budget_bot import finance
from test_flow import A, AN, B, BN, answer, onboard_quick
from tg_sim import button, texts


async def onboard_no_income(sim, uid, name):
    s = await sim.text(uid, name, "/start")
    s = await answer(sim, uid, name, s, press="Начать")
    for label in ("Не плачу", "Входят в аренду", "Не плачу", "Нет подписок", "Больше ничего", "Пока нет дохода"):
        s = await answer(sim, uid, name, s, press=label)
    assert "Счёт" in texts(s)  # вопрос о дне зарплаты пропущен
    s = await answer(sim, uid, name, s, text="минус 300")
    s = await answer(sim, uid, name, s, press="Нет")
    s = await answer(sim, uid, name, s, text="1200 в месяц")
    for label in ("Не трачу", "Почти не трачу", "Почти не трачу", "Нет хобби", "Каждый день"):
        s = await answer(sim, uid, name, s, press=label)
    s = await answer(sim, uid, name, s, press="15")
    for label in ("Почти никогда", "Почти никогда"):
        s = await answer(sim, uid, name, s, press=label)
    s = await answer(sim, uid, name, s, press="100")  # сигареты в неделю
    for label in ("Не трачу", "Нет / не трачу", "Почти никогда", "Почти никогда"):
        s = await answer(sim, uid, name, s, press=label)
    s = await answer(sim, uid, name, s, press="200")
    s = await answer(sim, uid, name, s, press="Учёба")
    s = await answer(sim, uid, name, s, press="Готово")
    s = await answer(sim, uid, name, s, text="300")
    s = await answer(sim, uid, name, s, press="Нет, всё")
    s = await answer(sim, uid, name, s, press="Нет")
    return s


async def test_student_without_income(sim):
    s = await onboard_no_income(sim, A, AN)
    # без дохода шаг «цель» пропущен: сразу анализ и предложение
    assert "Мой анализ" in texts(s) and "Предлагаемый бюджет" in texts(s)
    assert "Сигареты" in texts(s) and "в минусе" in texts(s)
    s = await answer(sim, A, AN, s, press="Сохранить бюджет")
    data = sim.app.bot_data["svc"].store.load(A)
    assert data.profile.income == 0 and data.profile.payday is None and data.profile.balance == -300
    assert data.category("Учёба").limit == 300
    assert data.category("Продукты").limit == 1200


async def test_rerun_keeps_transactions(sim):
    s = await sim.text(A, AN, "/start")
    s = await onboard_quick(sim, A, AN, s)
    s = await sim.text(A, AN, "45 хлеб")
    assert "Продукты" in texts(s)
    store = sim.app.bot_data["svc"].store
    tx_count = len(store.load(A).transactions)
    s = await sim.text(A, AN, "⚙️ Настройки")
    mid, cb = button(s, "Анкета заново")
    s = await sim.press(A, AN, mid, cb)
    mid, cb = button(s, "Да, заново")
    s = await sim.press(A, AN, mid, cb)
    assert "Пройдём анкету заново" in texts(s)
    s = await onboard_quick(sim, A, AN, s)
    data = store.load(A)
    assert len(data.transactions) == tx_count  # постоянные этого месяца не задублировались
    assert any(t.comment == "хлеб" for t in data.transactions)
    backups = list((store.backups_dir / str(A)).glob("before-rerun-*.xlsx"))
    assert backups


async def test_rerun_can_be_cancelled(sim):
    s = await sim.text(A, AN, "/start")
    await onboard_quick(sim, A, AN, s)
    s = await sim.text(A, AN, "/setup")
    s = await answer(sim, A, AN, s, press="Отменить")
    assert "Анкета отменена" in texts(s)
    assert "ob" not in sim.app.user_data[A]


async def test_pair_by_code_and_delete(sim):
    s = await sim.text(A, AN, "/start")
    await onboard_quick(sim, A, AN, s)
    s = await sim.text(B, BN, "/start")
    await onboard_quick(sim, B, BN, s)
    s = await sim.text(A, AN, "👫 Пара")
    mid, cb = button(s, "Пригласить")
    s = await sim.press(A, AN, mid, cb)
    code = texts(s).split("<code>")[1][:6]
    s = await sim.text(B, BN, "👫 Пара")
    mid, cb = button(s, "Ввести код")
    s = await sim.press(B, BN, mid, cb)
    s = await sim.text(B, BN, "WRONG1")
    assert "недействителен" in texts(s)
    s = await sim.text(B, BN, code.lower())
    assert "Вы в паре с Аня" in texts(s)
    reg = sim.app.bot_data["svc"].registry
    assert reg.partner(A) == B
    # Боря удаляет данные → связь рвётся, Аня получает уведомление
    s = await sim.text(B, BN, "⚙️ Настройки")
    mid, cb = button(s, "Удалить мои данные")
    s = await sim.press(B, BN, mid, cb)
    mid, cb = button(s, "Да, удалить всё")
    s = await sim.press(B, BN, mid, cb)
    assert "Все данные удалены" in texts(s)
    assert any("удалил(а) свои данные" in x.text and int(x.params["chat_id"]) == A for x in s)
    assert reg.partner(A) is None and not sim.app.bot_data["svc"].store.exists(B)
    s = await sim.text(B, BN, "45 кофе")  # без данных — снова анкета
    assert "Привет, Боря" in texts(s)


async def test_menu_flows_add_income_cards(sim):
    s = await sim.text(A, AN, "/start")
    await onboard_quick(sim, A, AN, s)
    store = sim.app.bot_data["svc"].store
    # «➕ Трата»: сначала категория, потом сумма
    s = await sim.text(A, AN, "➕ Трата")
    mid, cb = button(s, "Продукты")
    s = await sim.press(A, AN, mid, cb)
    s = await sim.text(A, AN, "abc")
    assert "Не понял сумму" in texts(s)
    s = await sim.text(A, AN, "230 рынок")
    assert "Продукты" in texts(s) and "230" in texts(s)
    # «💵 Доход»
    s = await sim.text(A, AN, "💵 Доход")
    s = await sim.text(A, AN, "1500 подработка")
    assert "+1 500" in texts(s)
    # Новая карта через настройки (три шага)
    s = await sim.text(A, AN, "⚙️ Настройки")
    mid, cb = button(s, "Карты")
    s = await sim.press(A, AN, mid, cb)
    mid, cb = button(s, "Добавить карту")
    s = await sim.press(A, AN, mid, cb)
    s = await sim.text(A, AN, "Isracard")
    s = await sim.text(A, AN, "2")
    s = await sim.text(A, AN, "640")
    assert "добавлена" in texts(s)
    data = store.load(A)
    card = data.card("Isracard")
    assert card.charge_day == 2 and card.baseline == 640
    assert finance.card_pending(data, card, sim.now) == 640
    # новые траты идут на первую карту в списке
    s = await sim.text(A, AN, "20 хлеб")
    assert "Isracard" in texts(s)


async def test_history_paging(sim):
    s = await sim.text(A, AN, "/start")
    await onboard_quick(sim, A, AN, s)
    for i in range(12):
        await sim.text(A, AN, f"{10 + i} хлеб")
    s = await sim.text(A, AN, "📋 История")
    assert "стр. 1/" in texts(s)
    mid, cb = button(s, "Старее")
    s = await sim.press(A, AN, mid, cb)
    assert "стр. 2/" in texts(s)
    mid, cb = button(s, "Новее")
    s = await sim.press(A, AN, mid, cb)
    assert "стр. 1/" in texts(s)
