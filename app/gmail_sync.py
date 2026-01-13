import datetime as dt
import logging
import threading
from dataclasses import dataclass
from typing import Dict, List, Optional

from app.categories import CATEGORY_ENUM
from app.config import Settings
from app.db import Database
from app.gmail_client import GmailClient
from app.openai_client import OpenAIClient
from app.telegram_client import TelegramClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AccountRuntime:
    account_id: int
    email: str
    refresh_token: str


class GmailSyncService:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        gmail_client: GmailClient,
        openai_client: OpenAIClient,
        telegram_client: TelegramClient,
        accounts: Dict[str, AccountRuntime],
    ) -> None:
        self._settings = settings
        self._db = db
        self._gmail_client = gmail_client
        self._openai_client = openai_client
        self._telegram_client = telegram_client
        self._accounts = accounts
        self._locks: Dict[str, threading.Lock] = {
            email: threading.Lock() for email in accounts
        }

    def handle_pubsub_event(self, email_address: str, history_id: int) -> None:
        account = self._accounts.get(email_address)
        if not account:
            logger.warning("No account configured for email: %s", email_address)
            return

        lock = self._locks[email_address]
        with lock:
            self._sync_account(account, history_id)

    def _sync_account(self, account: AccountRuntime, history_id: int) -> None:
        last_history_id, _ = self._db.get_account_state(account.account_id)
        start_history_id = last_history_id or history_id

        try:
            response = self._gmail_client.list_history(
                account.refresh_token,
                start_history_id=start_history_id,
                label_id=self._settings.gmail_watch_label_ids[0],
            )
        except Exception as exc:
            if self._gmail_client.is_history_invalid(exc):
                logger.warning(
                    "History invalid for %s. Resetting history cursor.", account.email
                )
                profile = self._gmail_client.get_profile(account.refresh_token)
                latest_history = int(profile.get("historyId", history_id))
                self._db.update_last_history_id(account.account_id, latest_history)
                return
            raise

        message_ids = self._extract_message_ids(response)
        logger.info(
            "History sync for %s: %d new messages", account.email, len(message_ids)
        )

        for message_id in message_ids:
            self._process_message(account, message_id, history_id)

        latest_history_id = response.get("historyId")
        if latest_history_id:
            self._db.update_last_history_id(account.account_id, int(latest_history_id))

    def _extract_message_ids(self, response: Dict) -> List[str]:
        message_ids: List[str] = []
        seen: set[str] = set()
        for history in response.get("history", []):
            for entry in history.get("messagesAdded", []):
                message = entry.get("message", {})
                message_id = message.get("id")
                if message_id and message_id not in seen:
                    seen.add(message_id)
                    message_ids.append(message_id)
        return message_ids

    def _process_message(self, account: AccountRuntime, message_id: str, history_id: int) -> None:
        if self._db.notification_exists(account.account_id, message_id):
            return

        placeholder_id: Optional[int] = None
        try:
            gmail_message = self._gmail_client.get_message(
                account.refresh_token, message_id
            )
            placeholder_id = self._db.insert_notification_placeholder(
                account.account_id,
                gmail_message.message_id,
                gmail_message.thread_id,
                history_id,
            )
            if not placeholder_id:
                return

            email_text = self._build_email_text(gmail_message)
            llm_result = self._openai_client.summarize(
                email_text=email_text,
                categories=CATEGORY_ENUM,
                max_input_tokens=self._settings.llm_max_input_tokens,
            )
            self._record_usage(account, llm_result.usage)
            category = (
                llm_result.category
                if llm_result.category in CATEGORY_ENUM
                else "Other"
            )
            confidence = llm_result.confidence

            sender_key = gmail_message.sender_email.lower()
            suppressed = self._db.is_suppressed(
                account.account_id, sender_key=sender_key, category=category
            )
            if suppressed and confidence >= self._settings.llm_low_confidence_threshold:
                self._db.update_notification_details(
                    notification_id=placeholder_id,
                    sender_email=gmail_message.sender_email,
                    sender_name=gmail_message.sender_name,
                    sender_key=sender_key,
                    subject=gmail_message.subject,
                    summary=llm_result.summary,
                    category=category,
                    confidence=confidence,
                    telegram_chat_id=None,
                    telegram_message_id=None,
                    status="suppressed",
                )
                return

            chat_id = self._db.get_telegram_chat_id()
            if not chat_id:
                logger.warning("Telegram chat ID not set; skipping notification.")
                self._db.update_notification_details(
                    notification_id=placeholder_id,
                    sender_email=gmail_message.sender_email,
                    sender_name=gmail_message.sender_name,
                    sender_key=sender_key,
                    subject=gmail_message.subject,
                    summary=llm_result.summary,
                    category=category,
                    confidence=confidence,
                    telegram_chat_id=None,
                    telegram_message_id=None,
                    status="pending",
                )
                return

            low_confidence = confidence < self._settings.llm_low_confidence_threshold
            note = "Low confidence - please choose a category below." if low_confidence else None
            message_text = self._telegram_client.format_message(
                sender_name=gmail_message.sender_name,
                sender_email=gmail_message.sender_email,
                summary=llm_result.summary,
                category=category,
                note=note,
            )
            open_url = self._telegram_client.build_open_url(gmail_message.thread_id)
            keyboard = self._telegram_client.build_keyboard(
                notification_id=placeholder_id,
                open_url=open_url,
                include_categories=low_confidence,
                categories=CATEGORY_ENUM,
            )
            result = self._telegram_client.send_message(
                chat_id=chat_id,
                text=message_text,
                reply_markup=keyboard,
            )

            self._db.update_notification_details(
                notification_id=placeholder_id,
                sender_email=gmail_message.sender_email,
                sender_name=gmail_message.sender_name,
                sender_key=sender_key,
                subject=gmail_message.subject,
                summary=llm_result.summary,
                category=category,
                confidence=confidence,
                telegram_chat_id=chat_id,
                telegram_message_id=result.message_id,
                status="notified",
            )

        except Exception:
            if placeholder_id:
                self._db.delete_notification(placeholder_id)
            raise

    def _build_email_text(self, message) -> str:
        parts = [
            f"From: {message.sender_name} <{message.sender_email}>"
            if message.sender_name
            else f"From: {message.sender_email}",
        ]
        if message.snippet:
            parts.append(f"Snippet: {message.snippet}")
        if message.body_text:
            parts.append("Body:")
            parts.append(message.body_text)
        return "\n".join(parts)

    def _record_usage(self, account: AccountRuntime, usage) -> None:
        if not usage:
            return

        def _get_value(source, key: str, default: int = 0) -> int:
            if isinstance(source, dict):
                value = source.get(key, default)
            else:
                value = getattr(source, key, default)
            return default if value is None else int(value)

        def _get_cached_tokens(source) -> int:
            if isinstance(source, dict):
                details = source.get("input_tokens_details")
                if isinstance(details, dict):
                    cached = details.get("cached_tokens")
                    if cached is not None:
                        return int(cached)
                cached = source.get("input_tokens_cached") or source.get("cached_input_tokens")
                return int(cached) if cached is not None else 0

            details = getattr(source, "input_tokens_details", None)
            if details is not None:
                cached = getattr(details, "cached_tokens", 0)
                return int(cached) if cached is not None else 0
            return 0

        input_tokens = _get_value(usage, "input_tokens", 0)
        cached_input_tokens = _get_cached_tokens(usage)
        output_tokens = _get_value(usage, "output_tokens", 0)

        input_cost = (input_tokens / 1_000_000) * self._settings.openai_price_input_per_1m
        cached_input_cost = (
            cached_input_tokens / 1_000_000
        ) * self._settings.openai_price_cached_input_per_1m
        output_cost = (
            output_tokens / 1_000_000
        ) * self._settings.openai_price_output_per_1m
        total_cost = input_cost + cached_input_cost + output_cost

        usage_date = dt.date.today().isoformat()
        self._db.upsert_usage_daily(
            account_id=account.account_id,
            model=self._settings.openai_model,
            usage_date=usage_date,
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            output_tokens=output_tokens,
            input_cost=input_cost,
            cached_input_cost=cached_input_cost,
            output_cost=output_cost,
            total_cost=total_cost,
        )
