"""Запуск приложения целиком: команды меню, планировщик задач, сохранение состояния диалогов."""

import asyncio
from datetime import datetime

from budget_bot.app import build_application
from tg_sim import FakeTelegram, Sim, texts


async def test_start_stop_with_jobs_and_persistence(cfg):
    fake = FakeTelegram()
    sim = Sim(None, fake, datetime(2026, 9, 27, 12, 0))
    app = build_application(cfg, clock=sim.clock, request=fake, persistence=True)
    sim.app = app
    await app.initialize()
    await app.post_init(app)
    await app.start()
    try:
        names = sorted(job.name for job in app.job_queue.jobs())
        assert names == ["daily", "reminders", "startup", "weekly"]
        weekly = next(j for j in app.job_queue.jobs() if j.name == "weekly")
        nxt = weekly.next_t.astimezone(cfg.timezone)
        assert (nxt.hour, nxt.minute, nxt.isoweekday()) == (9, 0, 7)  # воскресенье 09:00 по Израилю
        commands = next(x for x in fake.sent if x.method == "setMyCommands")
        assert "budget" in str(commands.params["commands"])
        s = await sim.text(111, "Аня", "/start")
        assert "Привет" in texts(s)
        await asyncio.sleep(0)
    finally:
        await app.stop()
        await app.shutdown()
    assert (cfg.data_dir / "conversations.pickle").exists()

    # после «перезапуска» анкета продолжается с того же места
    fake2 = FakeTelegram()
    sim2 = Sim(None, fake2, datetime(2026, 9, 27, 12, 5))
    app2 = build_application(cfg, clock=sim2.clock, request=fake2, persistence=True)
    sim2.app = app2
    await app2.initialize()
    try:
        s = await sim2.text(111, "Аня", "/start")
        assert "Продолжим" in texts(s)
    finally:
        await app2.shutdown()
