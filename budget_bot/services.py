"""Общие сервисы бота: настройки, хранилище, реестр и «часы» (подменяются в тестах)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable

from .config import Config
from .excel_store import ExcelStore
from .registry import Registry


@dataclass
class Services:
    cfg: Config
    store: ExcelStore
    registry: Registry
    clock: Callable[[], datetime] | None = None

    def now(self) -> datetime:
        """Текущее локальное время (без tzinfo, с точностью до секунды) — так оно и хранится в Excel."""
        if self.clock:
            return self.clock().replace(microsecond=0)
        return datetime.now(self.cfg.timezone).replace(tzinfo=None, microsecond=0)

    def today(self) -> date:
        return self.now().date()

    @classmethod
    def create(cls, cfg: Config, clock: Callable[[], datetime] | None = None) -> "Services":
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        return cls(
            cfg=cfg,
            store=ExcelStore(cfg.data_dir, cfg.currency),
            registry=Registry(cfg.data_dir / "registry.json"),
            clock=clock,
        )
