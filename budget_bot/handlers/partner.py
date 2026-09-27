"""👫 Пара: связать свой бюджет с бюджетом партнёра и смотреть его (только просмотр).

Связь взаимная и появляется только по приглашению: один отправляет ссылку/код, второй принимает.
Разорвать связь может любой из двоих в любой момент.
"""

from __future__ import annotations

from urllib.parse import quote

from telegram import Update

from .. import reports
from ..excel_store import write_workbook
from ..fmt import esc
from .common import Ctx, answer, clear_await, kb, load, need_setup, safe_delete, set_await, show, svc
from .views import _month_from, budget_screen, history_screen, send_chart

INVITE_PREFIX = "pair_"


def partner_screen(user_id: int, context: Ctx):
    s = svc(context)
    pid = s.registry.partner(user_id)
    if pid:
        name = esc(s.registry.name(pid))
        text = (f"💞 <b>Вы в паре с {name}</b>\n\nВы видите бюджеты друг друга: лимиты, траты, сводки и диаграммы. "
                "У каждого остаётся свой бюджет и своя таблица.")
        rows = [
            [("📊 Бюджет", "pv|b|now"), ("📈 Сводка", "pv|sum")],
            [("🥧 Диаграмма", "pv|chs"), ("📋 Операции", "pv|h|0")],
            [("📁 Excel партнёра", "pv|xl")],
            [("💔 Разорвать связь", "pr|unlink")],
        ]
    else:
        text = ("👫 <b>Пара</b>\n\nСвяжись с партнёром — вы будете видеть бюджеты друг друга: лимиты, траты, сводки "
                "и диаграммы. У каждого остаётся свой бюджет и своя таблица.\n\n"
                "1️⃣ Нажми «Пригласить» и отправь ссылку партнёру\n"
                "2️⃣ Или введи код, который прислал тебе партнёр")
        rows = [[("🔗 Пригласить", "pr|inv"), ("🔑 Ввести код", "pr|code")]]
    return text, kb(rows)


async def show_partner(update: Update, context: Ctx) -> None:
    if load(context, update.effective_user.id) is None:
        await need_setup(update, context)
        return
    text, markup = partner_screen(update.effective_user.id, context)
    await update.effective_message.reply_text(text, reply_markup=markup)


def _check_invite(context: Ctx, user_id: int, code: str) -> tuple[int | None, str | None]:
    """Возвращает (id пригласившего, None) или (None, текст ошибки)."""
    s = svc(context)
    owner = s.registry.invite_owner(code, s.now())
    if owner is None:
        return None, "Код недействителен или устарел. Попроси партнёра прислать новое приглашение."
    if owner == user_id:
        return None, "Это твоё собственное приглашение 🙂 Отправь его партнёру."
    if s.registry.partner(user_id):
        return None, "У тебя уже есть пара. Сначала разорви связь в «👫 Пара»."
    if s.registry.partner(owner):
        return None, "У пригласившего уже есть пара."
    if not s.store.exists(owner):
        return None, "Пригласивший удалил свои данные — приглашение больше не действует."
    return owner, None


async def offer_invite(update: Update, context: Ctx, code: str) -> None:
    owner, error = _check_invite(context, update.effective_user.id, code)
    chat_id = update.effective_chat.id
    if error:
        await context.bot.send_message(chat_id, f"⚠️ {error}")
        return
    name = esc(svc(context).registry.name(owner))
    await context.bot.send_message(
        chat_id,
        f"💌 <b>{name}</b> приглашает объединиться в пару: вы будете видеть бюджеты друг друга "
        "(у каждого остаётся свой). Принять?",
        reply_markup=kb([[("✅ Принять", f"pr|acc|{code}"), ("❌ Отклонить", f"pr|dec|{code}")]]),
    )


async def _link(update: Update, context: Ctx, owner: int) -> str:
    s = svc(context)
    me = update.effective_user
    s.registry.link(owner, me.id)
    my_name = esc(s.registry.name(me.id))
    owner_chat = (s.registry.user(owner) or {}).get("chat_id", owner)
    try:
        await context.bot.send_message(owner_chat, f"💞 {my_name} принял(а) приглашение — теперь вы пара! "
                                                   "Бюджет партнёра — в меню «👫 Пара».")
    except Exception:  # noqa: BLE001 - партнёр мог заблокировать бота
        pass
    return f"💞 Готово! Вы в паре с {esc(s.registry.name(owner))}. Бюджет партнёра — в меню «👫 Пара»."


async def on_partner(update: Update, context: Ctx) -> None:
    query = update.callback_query
    parts = query.data.split("|")
    action = parts[1]
    s = svc(context)
    uid = update.effective_user.id
    chat_id = update.effective_chat.id
    msg_id = query.message.message_id
    await answer(update)
    if load(context, uid) is None:
        await need_setup(update, context)
        return
    if action == "menu":
        await show(context, chat_id, msg_id, *partner_screen(uid, context))
    elif action == "inv":
        code = s.registry.create_invite(uid, s.now())
        link = f"https://t.me/{context.bot.username}?start={INVITE_PREFIX}{code}"
        share = f"https://t.me/share/url?url={quote(link)}&text={quote('Давай вести бюджет вместе 💞')}"
        text = (f"🔗 Отправь партнёру эту ссылку — по ней бот предложит объединиться:\n{link}\n\n"
                f"Или продиктуй код: <code>{code}</code>\n<i>Действует 7 дней.</i>")
        await show(context, chat_id, msg_id, text, kb([[("📤 Поделиться ссылкой", share)], [("⬅️ Назад", "pr|menu")]]))
    elif action == "code":
        set_await(context, "partner_code", msg_id=msg_id)
        await show(context, chat_id, msg_id, "🔑 Напиши код приглашения от партнёра (6 символов):",
                   kb([[("↩️ Отмена", "pr|menu")]]))
    elif action == "acc":
        owner, error = _check_invite(context, uid, parts[2])
        text = f"⚠️ {error}" if error else await _link(update, context, owner)
        await show(context, chat_id, msg_id, text)
    elif action == "dec":
        await show(context, chat_id, msg_id, "Приглашение отклонено.")
    elif action == "unlink":
        pid = s.registry.partner(uid)
        if not pid:
            await show(context, chat_id, msg_id, *partner_screen(uid, context))
            return
        await show(context, chat_id, msg_id,
                   f"Разорвать связь с {esc(s.registry.name(pid))}? Вы перестанете видеть бюджеты друг друга.",
                   kb([[("💔 Да, разорвать", "pr|unlinkok"), ("↩️ Нет", "pr|menu")]]))
    elif action == "unlinkok":
        other = s.registry.unlink(uid)
        if other:
            try:
                other_chat = (s.registry.user(other) or {}).get("chat_id", other)
                await context.bot.send_message(other_chat, f"💔 {esc(s.registry.name(uid))} разорвал(а) связь пары.")
            except Exception:  # noqa: BLE001
                pass
        await show(context, chat_id, msg_id, *partner_screen(uid, context))


async def await_partner_code(update: Update, context: Ctx, state: dict) -> None:
    code = (update.effective_message.text or "").strip().upper().removeprefix(INVITE_PREFIX.upper())
    await safe_delete(update.effective_message)
    owner, error = _check_invite(context, update.effective_user.id, code)
    if error:
        await show(context, update.effective_chat.id, state["msg_id"], f"⚠️ {error}\n\nПопробуй ещё раз:",
                   kb([[("↩️ Отмена", "pr|menu")]]))
        return
    clear_await(context)
    text = await _link(update, context, owner)
    await show(context, update.effective_chat.id, state["msg_id"], text)


# --- просмотр бюджета партнёра «pv|…» -------------------------------------------------------------------
async def on_partner_view(update: Update, context: Ctx) -> None:
    query = update.callback_query
    parts = query.data.split("|")
    action = parts[1]
    s = svc(context)
    uid = update.effective_user.id
    pid = s.registry.partner(uid)
    if not pid:
        await answer(update, "Связи пары больше нет", alert=True)
        return
    pdata = s.store.load(pid)
    if pdata is None:
        await answer(update, "У партнёра пока нет бюджета", alert=True)
        return
    name = s.registry.name(pid)
    now = s.now()
    chat_id = update.effective_chat.id
    msg_id = query.message.message_id
    if action == "ch":
        await send_chart(update, context, pdata, parts[2], prefix="pv", owner=name, edit=True)
        return
    await answer(update)
    if action == "b":
        month = now.date() if parts[2] == "now" else _month_from(parts[2], now.date())
        await show(context, chat_id, msg_id, *budget_screen(pdata, month, now, prefix="pv", owner=name))
    elif action == "chs":
        await send_chart(update, context, pdata, "plan", prefix="pv", owner=name)
    elif action == "h":
        await show(context, chat_id, msg_id, *history_screen(pdata, int(parts[2]), prefix="pv", owner=name))
    elif action == "sum":
        markup = kb([[("📊 Бюджет", "pv|b|now"), ("⬅️ Пара", "pr|menu")]])
        await show(context, chat_id, msg_id, reports.summary_text(pdata, now, owner=name), markup)
    elif action == "xl":
        path = s.store.tmp_dir / f"partner-{uid}-{pid}.xlsx"
        write_workbook(path, pdata, now)
        try:
            with path.open("rb") as fh:
                await context.bot.send_document(chat_id, fh, filename=f"budget_{pid}_{now:%Y-%m-%d}.xlsx",
                                                caption=f"📁 Таблица бюджета: {esc(name)} (копия только для просмотра)")
        finally:
            path.unlink(missing_ok=True)
