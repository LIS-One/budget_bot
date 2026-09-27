"""📁 Excel: выгрузить свою таблицу и загрузить отредактированную обратно."""

from __future__ import annotations

import re

from telegram import Update

from .. import fmt
from ..excel_store import WorkbookError, read_workbook
from ..fmt import esc
from .common import Ctx, answer, kb, load, need_setup, show, svc

MAX_UPLOAD = 5 * 1024 * 1024


def _filename(name: str, now) -> str:
    safe = re.sub(r"[^\w-]+", "_", name, flags=re.UNICODE).strip("_") or "budget"
    return f"budget_{safe}_{now:%Y-%m-%d}.xlsx"


async def export_excel(update: Update, context: Ctx) -> None:
    s = svc(context)
    data = load(context, update.effective_user.id)
    if data is None:
        await need_setup(update, context)
        return
    now = s.now()
    s.store.save(data, now)  # пересобрать «Сводку» на сегодня
    with s.store.path(data.user_id).open("rb") as fh:
        await context.bot.send_document(
            update.effective_chat.id, fh, filename=_filename(data.profile.name, now),
            caption=("📁 Твоя таблица бюджета — открывается в Excel, Google Таблицах и Numbers.\n"
                     "✏️ Можно поправить данные и прислать файл обратно — я покажу, что изменилось, "
                     "и загружу после подтверждения."),
        )


async def on_document(update: Update, context: Ctx) -> None:
    s = svc(context)
    message = update.effective_message
    doc = message.document
    data = load(context, update.effective_user.id)
    if data is None:
        await need_setup(update, context)
        return
    if not (doc.file_name or "").lower().endswith(".xlsx"):
        await message.reply_text("Я принимаю только Excel-файлы .xlsx — например, таблицу, которую выгрузил через «📁 Excel».")
        return
    if (doc.file_size or 0) > MAX_UPLOAD:
        await message.reply_text("Файл слишком большой (больше 5 МБ).")
        return
    tmp = s.store.tmp_path(data.user_id)
    tg_file = await doc.get_file()
    await tg_file.download_to_drive(tmp)
    try:
        new, warnings = read_workbook(tmp, data.user_id, data.profile.currency)
    except WorkbookError as exc:
        tmp.unlink(missing_ok=True)
        await message.reply_text(f"⚠️ Не получилось загрузить: {esc(str(exc))}")
        return

    cur = new.profile.currency
    m = lambda x: fmt.money(x, cur)  # noqa: E731
    lines = [
        "📥 <b>Нашёл в файле:</b>",
        f"• Операций: {len(new.transactions)} (сейчас в боте {len(data.transactions)})",
        f"• Категорий: {len(new.categories)}, лимиты {m(new.limits_total)} (сейчас {fmt.money(data.limits_total, data.profile.currency)})",
        f"• Постоянных платежей: {len(new.fixed)} на {m(new.fixed_total)}",
        f"• Карт: {len(new.cards)}",
        f"• Доход {m(new.profile.income)}, цель накоплений {m(new.profile.savings_goal)}",
    ]
    if new.profile.name:
        lines.append(f"• Имя в профиле: {esc(new.profile.name)}")
    if warnings:
        lines.append("")
        lines.append(f"⚠️ Замечания ({len(warnings)}):")
        lines += [f"• {esc(w)}" for w in warnings[:6]]
        if len(warnings) > 6:
            lines.append(f"…и ещё {len(warnings) - 6}")
    lines += ["", "Заменить текущие данные данными из файла? Перед заменой я сохраню резервную копию."]
    context.user_data["import"] = str(tmp)
    await message.reply_text("\n".join(lines),
                             reply_markup=kb([[("✅ Загрузить", "xl|ok"), ("✖️ Отмена", "xl|no")]]))


async def on_import(update: Update, context: Ctx) -> None:
    s = svc(context)
    query = update.callback_query
    action = query.data.split("|")[1]
    path_str = context.user_data.pop("import", None)
    msg_id = query.message.message_id
    chat_id = update.effective_chat.id
    await answer(update)
    if not path_str or not s.store.tmp_path(update.effective_user.id).exists():
        await show(context, chat_id, msg_id, "Файл для загрузки не найден — пришли его ещё раз.")
        return
    tmp = s.store.tmp_path(update.effective_user.id)
    if action != "ok":
        tmp.unlink(missing_ok=True)
        await show(context, chat_id, msg_id, "Отменено — данные не менялись.")
        return
    old = load(context, update.effective_user.id)
    try:
        new, _ = read_workbook(tmp, update.effective_user.id, old.profile.currency if old else s.cfg.currency)
    except WorkbookError as exc:
        await show(context, chat_id, msg_id, f"⚠️ Не получилось загрузить: {esc(str(exc))}")
        return
    finally:
        tmp.unlink(missing_ok=True)
    now = s.now()
    s.store.backup(update.effective_user.id, f"before-import-{now:%Y%m%d-%H%M%S}")
    s.store.save(new, now)
    await show(context, chat_id, msg_id,
               f"✅ Данные из файла загружены: {len(new.transactions)} операций, {len(new.categories)} категорий. "
               "Прежняя версия сохранена в резервной копии.")
