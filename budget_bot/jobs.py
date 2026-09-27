"""Фоновые задачи: ежедневная обработка регулярных платежей и еженедельная сводка."""

from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta

from telegram.error import Forbidden
from telegram.ext import Application, ContextTypes

from . import finance, fmt, reports
from .fmt import esc
from .handlers.common import kb
from .handlers.views import summary_markup
from .models import UserData
from .services import Services

log = logging.getLogger(__name__)


def _svc(app: Application) -> Services:
    return app.bot_data["svc"]


def _chat(s: Services, user_id: int) -> int:
    return int((s.registry.user(user_id) or {}).get("chat_id") or user_id)


async def _notify_events(app: Application, user_id: int, data: UserData, events: list[finance.Event],
                         today: date) -> None:
    s = _svc(app)
    cur = data.profile.currency
    now = s.now()
    recent = [e for e in events if e.day >= today - timedelta(days=1)]
    chat = _chat(s, user_id)
    fixed = [e for e in recent if e.kind == "fixed"]
    for e in recent:
        if e.kind == "card":
            text = (f"💳 Сегодня списание по карте «{esc(e.tx.payment)}»: <b>{fmt.money(e.tx.amount, cur)}</b>\n"
                    f"🏦 На счету сейчас ≈ {fmt.money(finance.account_balance(data, now), cur)}\n"
                    "<i>Если в банке другая сумма — поправь её.</i>")
            await app.bot.send_message(chat, text, reply_markup=kb([[("✏️ Поправить сумму", f"tx|amt|{e.tx.id}")]]))
        elif e.kind == "payday":
            text = (f"💼 Сегодня день зарплаты — записал доход <b>{fmt.money(e.tx.amount, cur)}</b>.\n"
                    "Сумма другая или деньги не пришли? Поправь 👇")
            await app.bot.send_message(chat, text, reply_markup=kb([[("✏️ Сумма", f"tx|amt|{e.tx.id}"),
                                                                      ("🗑 Не пришла", f"tx|del|{e.tx.id}")]]))
    if fixed:
        items = ", ".join(f"{esc(e.tx.comment)} {fmt.num(e.tx.amount)}" for e in fixed)
        total = sum(e.tx.amount for e in fixed)
        await app.bot.send_message(chat, f"📌 Записал постоянные платежи: {items} — всего {fmt.money(total, cur)}.")


async def process_user(app: Application, user_id: int, today: date, notify: bool = True) -> list[finance.Event]:
    """Записывает всё регулярное (списания карт, постоянные платежи, зарплату) с прошлого запуска."""
    s = _svc(app)
    data = s.store.load(user_id)
    if data is None:
        return []
    last = s.registry.last_processed(user_id) or data.profile.created_at.date()
    if last >= today:
        return []
    last = max(last, today - timedelta(days=62))  # не догоняем больше двух месяцев простоя
    events = finance.process_recurring(data, last, today)
    if events:
        s.store.save(data, s.now())
    s.registry.set_last_processed(user_id, today)
    if events and notify:
        try:
            await _notify_events(app, user_id, data, events, today)
        except Forbidden:
            log.info("user %s заблокировал бота", user_id)
    return events


async def run_daily(app: Application) -> None:
    s = _svc(app)
    today = s.today()
    for user_id in s.registry.ids():
        try:
            await process_user(app, user_id, today)
            backup = s.store.backups_dir / str(user_id) / f"daily-{today:%Y%m%d}.xlsx"
            if not backup.exists():
                s.store.backup(user_id, f"daily-{today:%Y%m%d}", keep=30)
        except Exception:  # noqa: BLE001 - один пользователь не должен ломать остальных
            log.exception("daily job: ошибка у пользователя %s", user_id)
        await asyncio.sleep(0.05)


async def run_weekly(app: Application) -> None:
    s = _svc(app)
    today = s.today()
    for user_id in s.registry.ids():
        rec = s.registry.user(user_id) or {}
        if not rec.get("notify", True):
            continue
        try:
            await process_user(app, user_id, today)
            data = s.store.load(user_id)
            if data is None:
                continue
            await app.bot.send_message(_chat(s, user_id), reports.summary_text(data, s.now(), scheduled=True),
                                       reply_markup=summary_markup())
        except Forbidden:
            log.info("user %s заблокировал бота — выключаю сводки", user_id)
            s.registry.upsert(user_id, notify=False)
        except Exception:  # noqa: BLE001
            log.exception("weekly job: ошибка у пользователя %s", user_id)
        await asyncio.sleep(0.05)


async def _daily_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    await run_daily(context.application)


async def _weekly_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    await run_weekly(context.application)


async def _startup_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """После перезапуска догоняем пропущенные дни (уведомления — только о вчерашнем и сегодняшнем)."""
    await run_daily(context.application)


def schedule(app: Application) -> None:
    s = _svc(app)
    tz = s.cfg.timezone
    jq = app.job_queue
    jq.run_daily(_daily_job, time=s.cfg.daily_time.replace(tzinfo=tz), name="daily")
    jq.run_daily(_weekly_job, time=s.cfg.summary_time.replace(tzinfo=tz), days=(s.cfg.summary_weekday,),
                 name="weekly")
    jq.run_once(_startup_job, when=10, name="startup")
