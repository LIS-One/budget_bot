"""Команды /start, /menu, /help, /myid, /cancel, /setup."""

from __future__ import annotations

from telegram import Update

from ..fmt import esc
from . import onboarding
from .common import HELP_TEXT, MAIN_MENU, Ctx, clear_await, load, svc
from .partner import INVITE_PREFIX, offer_invite


async def start(update: Update, context: Ctx) -> None:
    user = update.effective_user
    payload = context.args[0] if context.args else ""
    invite = payload[len(INVITE_PREFIX):].upper() if payload.startswith(INVITE_PREFIX) else None
    data = load(context, user.id)
    clear_await(context)
    if data is None or (onboarding.draft(context) and not invite):
        # новый пользователь — анкета; незаконченная повторная анкета — продолжаем её
        await onboarding.start_onboarding(update, context, invite=invite)
        return
    svc(context).registry.upsert(user.id, chat_id=update.effective_chat.id, name=user.first_name or "",
                                 username=user.username or "")
    if invite:
        await offer_invite(update, context, invite)
        return
    await update.effective_message.reply_text(
        f"👋 С возвращением, {esc(user.first_name or '')}! Пиши траты прямо в чат — например, <code>45 кофе</code>. "
        "Меню — кнопки внизу.",
        reply_markup=MAIN_MENU,
    )


async def menu(update: Update, context: Ctx) -> None:
    clear_await(context)
    if load(context, update.effective_user.id) is None:
        await onboarding.start_onboarding(update, context)
        return
    await update.effective_message.reply_text("Меню 👇", reply_markup=MAIN_MENU)


async def show_help(update: Update, context: Ctx) -> None:
    await update.effective_message.reply_text(HELP_TEXT, reply_markup=MAIN_MENU)


async def myid(update: Update, context: Ctx) -> None:
    await update.effective_message.reply_text(f"Твой Telegram ID: <code>{update.effective_user.id}</code>")


async def cancel(update: Update, context: Ctx) -> None:
    clear_await(context)
    context.user_data.pop("pending", None)
    registered = load(context, update.effective_user.id) is not None
    if registered:
        context.user_data.pop("ob", None)  # повторная анкета отменяется, бюджет остаётся прежним
    await update.effective_message.reply_text("Ок, отменил.", reply_markup=MAIN_MENU if registered else None)


async def setup(update: Update, context: Ctx) -> None:
    rerun = load(context, update.effective_user.id) is not None
    await onboarding.start_onboarding(update, context, rerun=rerun)
