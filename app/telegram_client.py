import asyncio
import threading
from dataclasses import dataclass
from typing import Iterable, Optional
from urllib.parse import quote

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup


@dataclass(frozen=True)
class TelegramSendResult:
    message_id: int


class TelegramClient:
    def __init__(self, bot_token: str) -> None:
        self._bot = Bot(token=bot_token)
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._ready.wait()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()

    def send_message(
        self,
        chat_id: int,
        text: str,
        reply_markup: InlineKeyboardMarkup,
    ) -> TelegramSendResult:
        future = asyncio.run_coroutine_threadsafe(
            self._bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup,
                disable_web_page_preview=True,
            ),
            self._loop,
        )
        message = future.result()
        return TelegramSendResult(message_id=message.message_id)

    def format_message(
        self,
        sender_name: str,
        sender_email: str,
        summary: str,
        category: str,
        status: Optional[str] = None,
        note: Optional[str] = None,
        importance: Optional[str] = None,
    ) -> str:
        sender_display = sender_name.strip()
        if not sender_display:
            sender_display = sender_email or "Unknown Sender"

        status_icon = "✅"
        if status:
            lowered = status.lower()
            if "trash" in lowered:
                status_icon = "🗑️"
            elif "archive" in lowered:
                status_icon = "🗂️"
            elif "not-interested" in lowered:
                status_icon = "🚫"
        lines = [
            f"📩 From: {sender_display}",
            f"🏷️ Category: {category}",
        ]
        if importance:
            lines.append(f"🔥 Importance: {importance}")
        if status:
            lines.append(f"{status_icon} Status: {status}")
        if note:
            lines.append(f"⚠️ {note}")
        lines.extend(["", f"🤖 {summary}"])
        return "\n".join(lines)

    @staticmethod
    def build_open_url(thread_id: str, account_email: Optional[str] = None) -> str:
        if account_email:
            safe_email = quote(account_email, safe="")
            base_url = f"https://mail.google.com/mail/u/0/?authuser={safe_email}"
        else:
            base_url = "https://mail.google.com/mail/u/0/"
        return f"{base_url}#inbox/{thread_id}"

    @staticmethod
    def build_keyboard(
        notification_id: int,
        open_url: str,
        include_categories: bool,
        categories: Iterable[str],
        confirm_trash: bool = False,
    ) -> InlineKeyboardMarkup:
        if confirm_trash:
            return InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "Confirm Trash", callback_data=f"tc:{notification_id}"
                        ),
                        InlineKeyboardButton(
                            "Cancel", callback_data=f"tcan:{notification_id}"
                        ),
                    ]
                ]
            )

        rows = [
            [
                InlineKeyboardButton("Open", url=open_url),
                InlineKeyboardButton("Archive", callback_data=f"a:{notification_id}"),
                InlineKeyboardButton("Trash", callback_data=f"t:{notification_id}"),
                InlineKeyboardButton("Not-Interested", callback_data=f"n:{notification_id}"),
            ],
            [
                InlineKeyboardButton("Mark Important", callback_data=f"mi:{notification_id}"),
            ],
        ]

        if include_categories:
            rows.extend(
                TelegramClient._build_category_rows(notification_id, list(categories))
            )

        return InlineKeyboardMarkup(rows)

    @staticmethod
    def build_open_only_keyboard(open_url: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([[InlineKeyboardButton("Open", url=open_url)]])

    @staticmethod
    def build_open_with_undo_keyboard(open_url: str, undo_data: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [[InlineKeyboardButton("Open", url=open_url), InlineKeyboardButton("Undo", callback_data=undo_data)]]
        )

    @staticmethod
    def build_not_interested_picker(notification_id: int) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("Mute sender+category", callback_data=f"ns:{notification_id}:sc")],
                [InlineKeyboardButton("Mute sender (all)", callback_data=f"ns:{notification_id}:ss")],
                [InlineKeyboardButton("Mute domain (all)", callback_data=f"ns:{notification_id}:sd")],
                [InlineKeyboardButton("Cancel", callback_data=f"ncan:{notification_id}")],
            ]
        )

    @staticmethod
    def build_inbox_url(account_email: Optional[str] = None) -> str:
        if account_email:
            safe_email = quote(account_email, safe="")
            return f"https://mail.google.com/mail/u/0/?authuser={safe_email}#inbox"
        return "https://mail.google.com/mail/u/0/#inbox"

    @staticmethod
    def _build_category_rows(
        notification_id: int, categories: list[str], per_row: int = 2
    ) -> list[list[InlineKeyboardButton]]:
        rows: list[list[InlineKeyboardButton]] = []
        for idx, category in enumerate(categories):
            if idx % per_row == 0:
                rows.append([])
            rows[-1].append(
                InlineKeyboardButton(category, callback_data=f"c:{notification_id}:{idx}")
            )
        return rows
