"""Общие помощники для обработчиков: доступ к сервисам, клавиатуры, безопасная отправка/редактирование."""

from __future__ import annotations

import functools
import logging
from typing import Awaitable, Callable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, ReplyKeyboardMarkup, Update
from telegram.error import BadRequest, Forbidden
from telegram.ext import ContextTypes

from ..models import UserData
from ..services import Services

log = logging.getLogger(__name__)

Ctx = ContextTypes.DEFAULT_TYPE

# --- главное меню ----------------------------------------------------------------------------
BTN_ADD = "➕ Трата"
BTN_INCOME = "💵 Доход"
BTN_BUDGET = "📊 Бюджет"
BTN_CHART = "🥧 Диаграмма"
BTN_SUMMARY = "📈 Сводка"
BTN_BALANCE = "🏦 Счёт"
BTN_HISTORY = "📋 История"
BTN_EXCEL = "📁 Excel"
BTN_PARTNER = "👫 Пара"
BTN_SETTINGS = "⚙️ Настройки"
BTN_HELP = "❓ Помощь"

MAIN_MENU = ReplyKeyboardMarkup(
    [
        [BTN_ADD, BTN_INCOME, BTN_BUDGET],
        [BTN_CHART, BTN_SUMMARY, BTN_BALANCE],
        [BTN_HISTORY, BTN_EXCEL, BTN_PARTNER],
        [BTN_SETTINGS, BTN_HELP],
    ],
    resize_keyboard=True,
    is_persistent=True,
    input_field_placeholder="Например: 45 кофе",
)

HELP_TEXT = (
    "❓ <b>Как пользоваться</b>\n\n"
    "✍️ <b>Записать трату</b> — просто напиши сумму и что купил(а):\n"
    "<code>45 кофе</code> · <code>wolt 89</code> · <code>1 200 шуферсаль</code>\n"
    "Категорию я подберу сам (и запомню твой выбор), способ оплаты — твоя кредитка по умолчанию. "
    "Под записью есть кнопки, чтобы поменять категорию, оплату или отменить.\n\n"
    "💵 <b>Доход</b> — с плюсом: <code>+12000 зарплата</code>\n\n"
    "📊 <b>Бюджет</b> — лимиты и остатки по категориям за месяц\n"
    "🥧 <b>Диаграмма</b> — круговая диаграмма бюджета и трат\n"
    "📈 <b>Сводка</b> — итоги недели (приходит сама раз в неделю)\n"
    "🏦 <b>Счёт</b> — сколько на счету, сколько спишется по карте, сверка с банком\n"
    "📋 <b>История</b> — операции, исправление и удаление\n"
    "📁 <b>Excel</b> — твоя таблица; её можно поправить и прислать обратно\n"
    "👫 <b>Пара</b> — связать бюджеты с партнёром, чтобы видеть бюджет друг друга\n"
    "⚙️ <b>Настройки</b> — лимиты, постоянные платежи, доход, цель, карты, анкета заново\n\n"
    "Команды: /undo — отменить последнюю запись, /menu — показать меню, /cancel — отменить ввод."
)


def svc(context: Ctx) -> Services:
    return context.application.bot_data["svc"]


def kb(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    """[[(текст, callback_data или https://-ссылка), …], …] → инлайн-клавиатура."""
    keyboard = []
    for row in rows:
        buttons = []
        for text, data in row:
            if data.startswith(("https://", "http://", "tg://")):
                buttons.append(InlineKeyboardButton(text, url=data))
            else:
                buttons.append(InlineKeyboardButton(text, callback_data=data))
        if buttons:
            keyboard.append(buttons)
    return InlineKeyboardMarkup(keyboard)


def chunk(items: list, size: int) -> list[list]:
    return [items[i: i + size] for i in range(0, len(items), size)]


# --- ожидание ввода текста ---------------------------------------------------------------------
def set_await(context: Ctx, kind: str, **params) -> None:
    context.user_data["await"] = {"kind": kind, **params}


def get_await(context: Ctx) -> dict | None:
    return context.user_data.get("await")


def clear_await(context: Ctx) -> None:
    context.user_data.pop("await", None)


# --- безопасная работа с сообщениями ---------------------------------------------------------------
async def safe_delete(message: Message | None) -> None:
    if message is None:
        return
    try:
        await message.delete()
    except (BadRequest, Forbidden):
        pass


async def show(context: Ctx, chat_id: int, message_id: int | None, text: str, markup=None) -> int:
    """Отредактировать сообщение «на месте», а если не выйдет — отправить новое. Возвращает id сообщения."""
    if message_id:
        try:
            await context.bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, reply_markup=markup)
            return message_id
        except BadRequest as exc:
            if "not modified" in str(exc).lower():
                return message_id
            log.debug("edit failed (%s), sending new message", exc)
    msg = await context.bot.send_message(chat_id, text, reply_markup=markup)
    return msg.message_id


async def answer(update: Update, text: str | None = None, alert: bool = False) -> None:
    query = update.callback_query
    if query is None:
        return
    try:
        await query.answer(text, show_alert=alert)
    except BadRequest:
        pass


# --- доступ и данные пользователя ------------------------------------------------------------------
async def allowed(update: Update, context: Ctx) -> bool:
    user = update.effective_user
    chat = update.effective_chat
    if user is None or chat is None:
        return False
    if chat.type != "private":
        return False
    if not svc(context).cfg.is_allowed(user.id):
        text = (f"⛔ Это личный бот. Твой Telegram ID: <code>{user.id}</code> — попроси владельца "
                "добавить его в ALLOWED_USER_IDS.")
        if update.callback_query:
            await answer(update, "Нет доступа", alert=True)
        elif update.effective_message:
            await update.effective_message.reply_text(text)
        return False
    return True


def guarded(func: Callable[[Update, Ctx], Awaitable[None]]):
    @functools.wraps(func)
    async def wrapper(update: Update, context: Ctx, *args, **kwargs):
        if not await allowed(update, context):
            return
        return await func(update, context, *args, **kwargs)

    return wrapper


def load(context: Ctx, user_id: int) -> UserData | None:
    return svc(context).store.load(user_id)


def save(context: Ctx, data: UserData) -> None:
    svc(context).store.save(data, svc(context).now())


async def need_setup(update: Update, context: Ctx) -> None:
    from .onboarding import start_onboarding

    await start_onboarding(update, context)
