"""Служебный реестр бота (JSON): кому слать сводки, кто с кем в паре, коды приглашений.

Бюджетные данные сюда не попадают — они живут в Excel. А связи «пары» и коды хранятся только
на сервере, чтобы их нельзя было подделать, отредактировав свою таблицу.
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import date, datetime, timedelta
from pathlib import Path

INVITE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
INVITE_LEN = 6
INVITE_TTL = timedelta(days=7)


class Registry:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict = {"users": {}, "invites": {}}
        if path.exists():
            with path.open(encoding="utf-8") as fh:
                loaded = json.load(fh)
            self.data["users"] = loaded.get("users", {})
            self.data["invites"] = loaded.get("invites", {})

    # --- сохранение ------------------------------------------------------------
    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(self.data, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    # --- пользователи ------------------------------------------------------------
    def user(self, user_id: int) -> dict | None:
        return self.data["users"].get(str(user_id))

    def upsert(self, user_id: int, **fields) -> dict:
        rec = self.data["users"].setdefault(str(user_id), {"notify": True, "partner_id": None})
        rec.update(fields)
        self.save()
        return rec

    def remove(self, user_id: int) -> None:
        self.unlink(user_id)
        self.data["users"].pop(str(user_id), None)
        self.drop_invites(user_id)
        self.save()

    def ids(self) -> list[int]:
        return [int(k) for k in self.data["users"]]

    def name(self, user_id: int) -> str:
        rec = self.user(user_id) or {}
        return rec.get("name") or "Партнёр"

    def last_processed(self, user_id: int) -> date | None:
        value = (self.user(user_id) or {}).get("last_processed")
        return date.fromisoformat(value) if value else None

    def set_last_processed(self, user_id: int, day: date) -> None:
        self.upsert(user_id, last_processed=day.isoformat())

    # --- пары ------------------------------------------------------------------
    def partner(self, user_id: int) -> int | None:
        pid = (self.user(user_id) or {}).get("partner_id")
        if pid is None:
            return None
        # связь действительна, только если она взаимная
        other = self.user(int(pid)) or {}
        return int(pid) if other.get("partner_id") == user_id else None

    def link(self, a: int, b: int) -> None:
        self.data["users"].setdefault(str(a), {"notify": True})["partner_id"] = b
        self.data["users"].setdefault(str(b), {"notify": True})["partner_id"] = a
        self.drop_invites(a)
        self.drop_invites(b)
        self.save()

    def unlink(self, user_id: int) -> int | None:
        other = self.partner(user_id)
        rec = self.user(user_id)
        if rec:
            rec["partner_id"] = None
        if other is not None and self.user(other):
            self.user(other)["partner_id"] = None
        self.save()
        return other

    # --- приглашения -------------------------------------------------------------
    def create_invite(self, user_id: int, now: datetime) -> str:
        self.drop_invites(user_id, save=False)
        while True:
            code = "".join(secrets.choice(INVITE_ALPHABET) for _ in range(INVITE_LEN))
            if code not in self.data["invites"]:
                break
        self.data["invites"][code] = {"user_id": user_id, "created": now.isoformat(timespec="seconds")}
        self.save()
        return code

    def invite_owner(self, code: str, now: datetime) -> int | None:
        rec = self.data["invites"].get(code.strip().upper())
        if not rec:
            return None
        if now - datetime.fromisoformat(rec["created"]) > INVITE_TTL:
            return None
        return int(rec["user_id"])

    def drop_invites(self, user_id: int, save: bool = True) -> None:
        stale = [c for c, rec in self.data["invites"].items() if rec.get("user_id") == user_id]
        for code in stale:
            del self.data["invites"][code]
        if stale and save:
            self.save()
