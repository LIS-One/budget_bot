"""Сборка приложения python-telegram-bot."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable

from telegram import LinkPreviewOptions, Update
from telegram.constants import ParseMode
from telegram.ext import Application, ApplicationBuilder, Defaults, PersistenceInput, PicklePersistence

from . import handlers, jobs
from .config import Config, ConfigError, load_config
from .services import Services

log = logging.getLogger(__name__)


def build_application(
    cfg: Config,
    clock: Callable[[], datetime] | None = None,
    request=None,
    persistence: bool = True,
) -> Application:
    services = Services.create(cfg, clock)
    builder = (
        ApplicationBuilder()
        .token(cfg.bot_token)
        .defaults(Defaults(parse_mode=ParseMode.HTML, tzinfo=cfg.timezone,
                           link_preview_options=LinkPreviewOptions(is_disabled=True)))
        .post_init(handlers.set_commands)
    )
    if persistence:
        builder = builder.persistence(PicklePersistence(
            filepath=cfg.data_dir / "conversations.pickle",
            store_data=PersistenceInput(bot_data=False, chat_data=False, user_data=True, callback_data=False),
            update_interval=30,
        ))
    if request is not None:
        builder = builder.request(request).get_updates_request(request)
    app = builder.build()
    app.bot_data["svc"] = services
    handlers.register(app)
    jobs.schedule(app)
    return app


def main() -> None:
    try:
        cfg = load_config()
    except ConfigError as exc:
        raise SystemExit(f"Ошибка настройки: {exc}") from None
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=getattr(logging, cfg.log_level, logging.INFO),
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    log.info("Запускаю бота. Данные: %s, часовой пояс: %s", cfg.data_dir, cfg.timezone.key)
    app = build_application(cfg)
    app.run_polling(allowed_updates=Update.ALL_TYPES)
