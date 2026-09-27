"""Регистрация обработчиков и маршрутизация текста/кнопок."""

from __future__ import annotations

import html
import logging

from telegram import BotCommand, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from . import entries, excel_io, onboarding, partner, settings, start, views
from .common import (
    BTN_ADD,
    BTN_BALANCE,
    BTN_BUDGET,
    BTN_CHART,
    BTN_EXCEL,
    BTN_HELP,
    BTN_HISTORY,
    BTN_INCOME,
    BTN_PARTNER,
    BTN_SETTINGS,
    BTN_SUMMARY,
    Ctx,
    clear_await,
    get_await,
    guarded,
    svc,
)

log = logging.getLogger(__name__)

MENU_ACTIONS = {
    BTN_ADD: entries.add_menu,
    BTN_INCOME: entries.income_menu,
    BTN_BUDGET: views.show_budget,
    BTN_CHART: views.show_chart,
    BTN_SUMMARY: views.show_summary,
    BTN_BALANCE: views.show_balance,
    BTN_HISTORY: views.show_history,
    BTN_EXCEL: excel_io.export_excel,
    BTN_PARTNER: partner.show_partner,
    BTN_SETTINGS: settings.show_settings,
    BTN_HELP: start.show_help,
}

AWAIT_HANDLERS = {
    "add_amount": entries.await_add_amount,
    "income_amount": entries.await_income_amount,
    "tx_amount": entries.await_tx_amount,
    "tx_comment": entries.await_tx_comment,
    "set_balance": views.await_set_balance,
    "set_card_pending": views.await_set_card_pending,
    "settings": settings.await_settings,
    "partner_code": partner.await_partner_code,
}

COMMANDS = [
    ("start", "Начать / главное меню", start.start),
    ("add", "Записать трату", entries.add_menu),
    ("income", "Записать доход", entries.income_menu),
    ("budget", "Бюджет на месяц", views.show_budget),
    ("chart", "Круговая диаграмма", views.show_chart),
    ("summary", "Сводка за неделю", views.show_summary),
    ("balance", "Счёт и карты", views.show_balance),
    ("history", "История операций", views.show_history),
    ("excel", "Скачать Excel-таблицу", excel_io.export_excel),
    ("partner", "Пара: бюджет партнёра", partner.show_partner),
    ("settings", "Настройки", settings.show_settings),
    ("undo", "Отменить последнюю запись", entries.undo),
    ("setup", "Пройти анкету заново", start.setup),
    ("menu", "Показать меню", start.menu),
    ("help", "Как пользоваться", start.show_help),
    ("myid", "Мой Telegram ID", start.myid),
    ("cancel", "Отменить ввод", start.cancel),
]


@guarded
async def on_text(update: Update, context: Ctx) -> None:
    text = (update.effective_message.text or "").strip()
    registered = svc(context).store.exists(update.effective_user.id)
    if registered and text in MENU_ACTIONS:
        clear_await(context)
        context.user_data.pop("pending", None)
        await MENU_ACTIONS[text](update, context)
        return
    if onboarding.draft(context) is not None:
        await onboarding.on_text(update, context)
        return
    state = get_await(context)
    if state and state.get("kind") in AWAIT_HANDLERS:
        await AWAIT_HANDLERS[state["kind"]](update, context, state)
        return
    if not registered:
        await onboarding.start_onboarding(update, context)
        return
    await entries.quick_entry(update, context, text)


@guarded
async def on_other(update: Update, context: Ctx) -> None:
    await update.effective_message.reply_text(
        "Я понимаю текст (например, <code>45 кофе</code>) и Excel-файлы .xlsx 🙂")


def _callback(handler, keep_await: bool = False):
    @guarded
    async def wrapper(update: Update, context: Ctx) -> None:
        if not keep_await:
            clear_await(context)
        await handler(update, context)

    return wrapper


async def on_error(update: object, context: Ctx) -> None:
    log.exception("Ошибка при обработке апдейта", exc_info=context.error)
    if isinstance(update, Update) and update.effective_chat:
        try:
            await context.bot.send_message(
                update.effective_chat.id,
                "⚠️ Что-то пошло не так. Попробуй ещё раз или нажми /menu.\n"
                f"<i>{html.escape(type(context.error).__name__)}</i>",
            )
        except Exception:  # noqa: BLE001
            pass


async def set_commands(app: Application) -> None:
    await app.bot.set_my_commands([BotCommand(name, desc) for name, desc, _ in COMMANDS])


def register(app: Application) -> None:
    private = filters.ChatType.PRIVATE
    for name, _, handler in COMMANDS:
        app.add_handler(CommandHandler(name, guarded(handler), filters=private))
    app.add_handler(CallbackQueryHandler(_callback(onboarding.on_callback, keep_await=True), pattern=r"^ob\|"))
    app.add_handler(CallbackQueryHandler(_callback(entries.on_add_category), pattern=r"^add\|"))
    app.add_handler(CallbackQueryHandler(_callback(entries.on_pending), pattern=r"^nx\|"))
    app.add_handler(CallbackQueryHandler(_callback(entries.on_tx), pattern=r"^tx\|"))
    app.add_handler(CallbackQueryHandler(_callback(views.on_view), pattern=r"^v\|"))
    app.add_handler(CallbackQueryHandler(_callback(settings.on_settings), pattern=r"^st\|"))
    app.add_handler(CallbackQueryHandler(_callback(partner.on_partner), pattern=r"^pr\|"))
    app.add_handler(CallbackQueryHandler(_callback(partner.on_partner_view), pattern=r"^pv\|"))
    app.add_handler(CallbackQueryHandler(_callback(excel_io.on_import), pattern=r"^xl\|"))
    app.add_handler(MessageHandler(private & filters.Document.ALL, guarded(excel_io.on_document)))
    app.add_handler(MessageHandler(private & filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(MessageHandler(private & ~filters.TEXT & ~filters.COMMAND & ~filters.StatusUpdate.ALL, on_other))
    app.add_error_handler(on_error)
