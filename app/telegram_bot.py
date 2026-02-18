import asyncio
import logging
from typing import Dict, Optional

from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from app.categories import CATEGORY_ENUM
from app.config import Settings, WEBHOOK_PATH
from app.db import Database, Notification
from app.gmail_client import GmailClient
from app.gmail_sync import AccountRuntime
from app.telegram_client import TelegramClient

logger = logging.getLogger(__name__)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    chat_id = update.effective_chat.id
    db: Database = context.application.bot_data["db"]
    settings: Settings = context.application.bot_data["settings"]
    if not _is_authorized(update, settings):
        logger.warning("Unauthorized /start from user %s", _safe_user_id(update))
        if update.message:
            await update.message.reply_text("Not authorized.")
        return
    db.set_telegram_chat_id(chat_id)
    if update.message:
        await update.message.reply_text("Chat registered. You're all set.")


def _parse_notification_id(value: str) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _notification_open_url(
    telegram_client: TelegramClient, notification: Notification, account_email: str
) -> str:
    target_id = notification.gmail_thread_id or notification.gmail_message_id
    return telegram_client.build_open_url(target_id, account_email=account_email)


def _format_notification(
    telegram_client: TelegramClient,
    notification: Notification,
    *,
    category: Optional[str] = None,
    status: Optional[str] = None,
    note: Optional[str] = None,
) -> str:
    resolved_category = category or notification.category or "Other"
    return telegram_client.format_message(
        sender_name=notification.sender_name or "",
        sender_email=notification.sender_email or "",
        summary=notification.summary or "",
        category=resolved_category,
        status=status,
        note=note,
        importance=notification.importance,
    )


def _low_confidence(notification: Notification, settings: Settings) -> bool:
    confidence = notification.confidence or 0.0
    return confidence < settings.llm_low_confidence_threshold


def _sender_domain(sender_email: Optional[str]) -> str:
    if not sender_email:
        return ""
    value = sender_email.lower()
    return value.split("@", 1)[1] if "@" in value else value


def _is_query_match(query, notification: Notification) -> bool:
    if not query.message:
        return False
    if notification.telegram_message_id and (
        query.message.message_id != notification.telegram_message_id
    ):
        logger.warning("Callback message ID mismatch for notification %s", notification.id)
        return False
    if notification.telegram_chat_id and (
        query.message.chat_id != notification.telegram_chat_id
    ):
        logger.warning("Callback chat mismatch for notification %s", notification.id)
        return False
    return True


def _safe_user_id(update: Update) -> str:
    if update.effective_user:
        return str(update.effective_user.id)
    return "unknown"


def _is_authorized(update: Update, settings: Settings) -> bool:
    if not settings.telegram_allowed_user_ids:
        return True
    user = update.effective_user
    if not user:
        return False
    return user.id in settings.telegram_allowed_user_ids


async def _get_notification(
    db: Database, notification_id: int
) -> Optional[Notification]:
    return await asyncio.to_thread(db.get_notification, notification_id)


async def _update_message(
    query,
    text: str,
    reply_markup,
) -> None:
    if query.message:
        await query.edit_message_text(text=text, reply_markup=reply_markup)


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()
    action, *parts = query.data.split(":")

    db: Database = context.application.bot_data["db"]
    settings: Settings = context.application.bot_data["settings"]
    gmail_client: GmailClient = context.application.bot_data["gmail_client"]
    telegram_client: TelegramClient = context.application.bot_data["telegram_client"]
    accounts_by_id: Dict[int, AccountRuntime] = context.application.bot_data[
        "accounts_by_id"
    ]
    if not _is_authorized(update, settings):
        logger.warning("Unauthorized callback from user %s", _safe_user_id(update))
        return

    if action == "c":
        if len(parts) != 2:
            return
        notification_id = _parse_notification_id(parts[0])
        category_idx = _parse_notification_id(parts[1])
        if notification_id is None or category_idx is None:
            return
        if category_idx < 0 or category_idx >= len(CATEGORY_ENUM):
            return

        notification = await _get_notification(db, notification_id)
        if not notification or not _is_query_match(query, notification):
            return

        account_runtime = accounts_by_id.get(notification.account_id)
        if not account_runtime:
            logger.warning("Missing account runtime for notification %s", notification.id)
            return

        category = CATEGORY_ENUM[category_idx]
        await asyncio.to_thread(
            db.update_notification_category, notification.id, category, 1.0
        )

        open_url = _notification_open_url(
            telegram_client, notification, account_runtime.email
        )
        keyboard = telegram_client.build_keyboard(
            notification_id=notification.id,
            open_url=open_url,
            include_categories=False,
            categories=CATEGORY_ENUM,
        )
        message_text = _format_notification(
            telegram_client,
            notification,
            category=category,
            note=None,
        )
        await _update_message(query, message_text, keyboard)
        return

    if len(parts) < 1:
        return
    notification_id = _parse_notification_id(parts[0])
    if notification_id is None:
        return
    notification = await _get_notification(db, notification_id)
    if not notification or not _is_query_match(query, notification):
        return

    account_runtime = accounts_by_id.get(notification.account_id)
    if not account_runtime:
        logger.warning("Missing account runtime for notification %s", notification.id)
        return
    refresh_token = account_runtime.refresh_token

    if action == "a":
        await asyncio.to_thread(
            gmail_client.archive,
            refresh_token,
            notification.gmail_message_id,
            notification.gmail_thread_id,
        )
        await asyncio.to_thread(db.update_notification_status, notification.id, "archived")
        open_url = _notification_open_url(
            telegram_client, notification, account_runtime.email
        )
        keyboard = telegram_client.build_open_with_undo_keyboard(open_url, f"u:{notification.id}:a")
        message_text = _format_notification(
            telegram_client, notification, status="Archived"
        )
        await _update_message(query, message_text, keyboard)
        return

    if action == "mi":
        await asyncio.to_thread(db.update_notification_importance, notification.id, "high")
        notification = await _get_notification(db, notification.id)
        if not notification:
            return
        include_categories = _low_confidence(notification, settings)
        keyboard = telegram_client.build_keyboard(
            notification_id=notification.id,
            open_url=_notification_open_url(
                telegram_client, notification, account_runtime.email
            ),
            include_categories=include_categories,
            categories=CATEGORY_ENUM,
        )
        message_text = _format_notification(
            telegram_client, notification, status="Marked Important"
        )
        await _update_message(query, message_text, keyboard)
        return

    if action == "t":
        keyboard = telegram_client.build_keyboard(
            notification_id=notification.id,
            open_url=_notification_open_url(
                telegram_client, notification, account_runtime.email
            ),
            include_categories=False,
            categories=CATEGORY_ENUM,
            confirm_trash=True,
        )
        await query.edit_message_reply_markup(reply_markup=keyboard)
        return

    if action == "tcan":
        include_categories = _low_confidence(notification, settings)
        keyboard = telegram_client.build_keyboard(
            notification_id=notification.id,
            open_url=_notification_open_url(
                telegram_client, notification, account_runtime.email
            ),
            include_categories=include_categories,
            categories=CATEGORY_ENUM,
        )
        await query.edit_message_reply_markup(reply_markup=keyboard)
        return

    if action == "tc":
        await asyncio.to_thread(
            gmail_client.trash,
            refresh_token,
            notification.gmail_message_id,
            notification.gmail_thread_id,
        )
        await asyncio.to_thread(db.update_notification_status, notification.id, "trashed")
        open_url = _notification_open_url(
            telegram_client, notification, account_runtime.email
        )
        keyboard = telegram_client.build_open_with_undo_keyboard(open_url, f"u:{notification.id}:t")
        message_text = _format_notification(
            telegram_client, notification, status="Trashed"
        )
        await _update_message(query, message_text, keyboard)
        return

    if action == "n":
        picker = telegram_client.build_not_interested_picker(notification.id)
        await query.edit_message_reply_markup(reply_markup=picker)
        return

    if action == "ncan":
        include_categories = _low_confidence(notification, settings)
        keyboard = telegram_client.build_keyboard(
            notification_id=notification.id,
            open_url=_notification_open_url(
                telegram_client, notification, account_runtime.email
            ),
            include_categories=include_categories,
            categories=CATEGORY_ENUM,
        )
        await query.edit_message_reply_markup(reply_markup=keyboard)
        return

    if action == "ns":
        if len(parts) < 2:
            return
        scope_key = parts[1]
        sender_key = notification.sender_key or (notification.sender_email or "").lower()
        sender_domain = _sender_domain(notification.sender_email)
        category_key = notification.category or "Other"

        if scope_key == "ss" and sender_key:
            await asyncio.to_thread(db.insert_suppression, notification.account_id, "sender", sender_key, "")
        elif scope_key == "sd" and sender_domain:
            await asyncio.to_thread(db.insert_suppression, notification.account_id, "domain", sender_domain, "")
        elif scope_key == "sc" and sender_key:
            await asyncio.to_thread(
                db.insert_suppression,
                notification.account_id,
                "sender_category",
                sender_key,
                category_key,
            )

        await asyncio.to_thread(db.update_notification_status, notification.id, "not_interested")
        open_url = _notification_open_url(
            telegram_client, notification, account_runtime.email
        )
        keyboard = telegram_client.build_open_with_undo_keyboard(open_url, f"u:{notification.id}:n")
        message_text = _format_notification(
            telegram_client, notification, status="Not-Interested"
        )
        await _update_message(query, message_text, keyboard)
        return

    if action == "u":
        if len(parts) < 2:
            return
        undo_target = parts[1]
        if undo_target == "a":
            await asyncio.to_thread(
                gmail_client.unarchive,
                refresh_token,
                notification.gmail_message_id,
                notification.gmail_thread_id,
            )
        elif undo_target == "t":
            await asyncio.to_thread(
                gmail_client.untrash,
                refresh_token,
                notification.gmail_message_id,
                notification.gmail_thread_id,
            )
        elif undo_target == "n":
            sender_key = notification.sender_key or (notification.sender_email or "").lower()
            sender_domain = _sender_domain(notification.sender_email)
            await asyncio.to_thread(
                db.clear_notification_suppressions,
                notification.account_id,
                sender_key,
                sender_domain,
                notification.category or "Other",
            )
        else:
            return

        await asyncio.to_thread(db.update_notification_status, notification.id, "notified")
        include_categories = _low_confidence(notification, settings)
        keyboard = telegram_client.build_keyboard(
            notification_id=notification.id,
            open_url=_notification_open_url(
                telegram_client, notification, account_runtime.email
            ),
            include_categories=include_categories,
            categories=CATEGORY_ENUM,
        )
        message_text = _format_notification(
            telegram_client, notification, status="Restored"
        )
        await _update_message(query, message_text, keyboard)
        return


def run_webhook(
    settings: Settings,
    db: Database,
    gmail_client: GmailClient,
    telegram_client: TelegramClient,
    accounts_by_id: Dict[int, AccountRuntime],
) -> None:
    application = Application.builder().token(settings.telegram_bot_token).build()
    application.bot_data["db"] = db
    application.bot_data["settings"] = settings
    application.bot_data["gmail_client"] = gmail_client
    application.bot_data["telegram_client"] = telegram_client
    application.bot_data["accounts_by_id"] = accounts_by_id

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CallbackQueryHandler(callback_handler))

    webhook_url = settings.telegram_webhook_url
    url_path = WEBHOOK_PATH.lstrip("/")
    secret_token = settings.telegram_webhook_secret_token or None

    application.run_webhook(
        listen=settings.app_host,
        port=settings.app_port,
        webhook_url=webhook_url,
        url_path=url_path,
        secret_token=secret_token,
        drop_pending_updates=True,
    )
