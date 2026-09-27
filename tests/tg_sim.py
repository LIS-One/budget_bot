"""Имитация Telegram Bot API для сквозных тестов: бот работает по-настоящему, но без сети.

FakeTelegram подменяет HTTP-слой python-telegram-bot: запоминает все вызовы API и отвечает
правдоподобными объектами. Sim отправляет боту «сообщения» и «нажатия кнопок» от имени
пользователей и помогает находить кнопки в последних ответах бота.
"""

from __future__ import annotations

import itertools
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from telegram import Update
from telegram.request import BaseRequest

BOT_USER = {"id": 999000, "is_bot": True, "first_name": "Budget", "username": "test_budget_bot"}


@dataclass
class Sent:
    method: str
    params: dict
    message_id: int | None = None
    files: dict = field(default_factory=dict)

    @property
    def text(self) -> str:
        """Текст/подпись сообщения; неразрывные пробелы заменены обычными — так проще сравнивать."""
        raw = self.params.get("text") or self.params.get("caption") or ""
        return raw.replace("\u00a0", " ")

    @property
    def buttons(self) -> list[dict]:
        markup = self.params.get("reply_markup")
        if isinstance(markup, str):
            markup = json.loads(markup)
        if not markup or "inline_keyboard" not in markup:
            return []
        return [b for row in markup["inline_keyboard"] for b in row]


class FakeTelegram(BaseRequest):
    def __init__(self):
        self.sent: list[Sent] = []
        self.messages: dict[tuple[int, int], dict] = {}
        self.ids = itertools.count(1000)
        self.files: dict[str, bytes] = {}
        self.file_ids = itertools.count(1)

    async def initialize(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    @property
    def read_timeout(self):
        return 5.0

    def _message(self, chat_id: int, message_id: int, **extra) -> dict:
        msg = {"message_id": message_id, "date": int(time.time()), "chat": {"id": chat_id, "type": "private"},
               "from": BOT_USER}
        msg.update(extra)
        self.messages[(chat_id, message_id)] = msg
        return msg

    async def do_request(self, url, method, request_data=None, read_timeout=None, write_timeout=None,
                         connect_timeout=None, pool_timeout=None):
        if "/file/bot" in url:
            path = url.split("/file/bot", 1)[1].split("/", 1)[1]
            return 200, self.files[path]
        api = url.rsplit("/", 1)[1]
        params = dict(request_data.parameters) if request_data else {}
        files = {}
        if request_data and request_data.contains_files:
            for key, value in (request_data.multipart_data or {}).items():
                files[key] = value[1] if isinstance(value, tuple) else value
        result = self._handle(api, params, files)
        return 200, json.dumps({"ok": True, "result": result}).encode()

    def _handle(self, api: str, params: dict, files: dict):
        chat_id = params.get("chat_id")
        chat_id = int(chat_id) if chat_id is not None else None
        entry = Sent(api, params, files=files)
        self.sent.append(entry)
        if api == "getMe":
            return BOT_USER
        if api == "getUpdates":
            return []
        if api == "sendMessage":
            mid = next(self.ids)
            entry.message_id = mid
            return self._message(chat_id, mid, text=params.get("text", ""))
        if api in ("editMessageText", "editMessageMedia", "editMessageReplyMarkup"):
            mid = int(params["message_id"])
            entry.message_id = mid
            msg = self.messages.get((chat_id, mid)) or self._message(chat_id, mid)
            if api == "editMessageText":
                msg["text"] = params.get("text", "")
            if api == "editMessageMedia":
                msg["photo"] = [{"file_id": "p", "file_unique_id": "p", "width": 10, "height": 10}]
            return msg
        if api == "sendPhoto":
            mid = next(self.ids)
            entry.message_id = mid
            return self._message(chat_id, mid, caption=params.get("caption", ""),
                                 photo=[{"file_id": "p", "file_unique_id": "p", "width": 10, "height": 10}])
        if api == "sendDocument":
            mid = next(self.ids)
            entry.message_id = mid
            fid = f"doc{next(self.file_ids)}"
            content = next(iter(files.values()), b"")
            self.files[f"documents/{fid}.xlsx"] = content
            entry.files["content"] = content
            return self._message(chat_id, mid, caption=params.get("caption", ""),
                                 document={"file_id": fid, "file_unique_id": fid, "file_name": "budget.xlsx",
                                           "file_size": len(content)})
        if api == "getFile":
            fid = params["file_id"]
            path = f"documents/{fid}.xlsx"
            return {"file_id": fid, "file_unique_id": fid, "file_size": len(self.files.get(path, b"")),
                    "file_path": path}
        if api == "deleteMessage":
            self.messages.pop((chat_id, int(params["message_id"])), None)
            return True
        return True


class Sim:
    def __init__(self, app, fake: FakeTelegram, now: datetime):
        self.app = app
        self.fake = fake
        self.now = now
        self.update_ids = itertools.count(1)
        self.user_msg_ids = itertools.count(1)
        self.mark = 0

    def clock(self) -> datetime:
        return self.now

    @staticmethod
    def user(uid: int, name: str) -> dict:
        return {"id": uid, "is_bot": False, "first_name": name, "username": name.lower()}

    def _tick(self) -> None:
        """Каждое действие пользователя — на минуту позже предыдущего, как в жизни."""
        self.now = self.now + timedelta(minutes=1)

    def _new(self) -> list[Sent]:
        out = self.fake.sent[self.mark:]
        self.mark = len(self.fake.sent)
        return out

    async def text(self, uid: int, name: str, text: str) -> list[Sent]:
        self._new()
        self._tick()
        message = {"message_id": next(self.user_msg_ids), "date": int(time.time()),
                   "chat": {"id": uid, "type": "private", "first_name": name}, "from": self.user(uid, name),
                   "text": text}
        if text.startswith("/"):
            message["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
        await self.app.process_update(Update.de_json({"update_id": next(self.update_ids), "message": message},
                                                     self.app.bot))
        return self._new()

    async def press(self, uid: int, name: str, message_id: int, data: str) -> list[Sent]:
        self._new()
        self._tick()
        msg = self.fake.messages.get((uid, message_id)) or {
            "message_id": message_id, "date": int(time.time()), "chat": {"id": uid, "type": "private"}, "text": ""}
        query = {"id": str(next(self.update_ids)), "from": self.user(uid, name), "chat_instance": "ci",
                 "data": data, "message": msg}
        await self.app.process_update(Update.de_json({"update_id": next(self.update_ids), "callback_query": query},
                                                     self.app.bot))
        return self._new()

    async def document(self, uid: int, name: str, content: bytes, file_name: str = "budget.xlsx") -> list[Sent]:
        self._new()
        self._tick()
        fid = f"up{next(self.fake.file_ids)}"
        self.fake.files[f"documents/{fid}.xlsx"] = content
        message = {"message_id": next(self.user_msg_ids), "date": int(time.time()),
                   "chat": {"id": uid, "type": "private"}, "from": self.user(uid, name),
                   "document": {"file_id": fid, "file_unique_id": fid, "file_name": file_name,
                                "file_size": len(content)}}
        await self.app.process_update(Update.de_json({"update_id": next(self.update_ids), "message": message},
                                                     self.app.bot))
        return self._new()


def last_with_buttons(sent: list[Sent]) -> Sent:
    for s in reversed(sent):
        if s.buttons:
            return s
    raise AssertionError("в ответе бота нет кнопок: " + " | ".join(x.method + ":" + x.text[:60] for x in sent))


def button(sent: list[Sent], text_part: str) -> tuple[int, str]:
    """(message_id, callback_data) кнопки, в подписи которой есть text_part."""
    for s in reversed(sent):
        for b in s.buttons:
            if text_part in b["text"] and "callback_data" in b:
                return s.message_id, b["callback_data"]
    labels = [b["text"] for s in sent for b in s.buttons]
    raise AssertionError(f"кнопка «{text_part}» не найдена среди {labels}")


def texts(sent: list[Sent]) -> str:
    return "\n---\n".join(s.text for s in sent if s.text)
