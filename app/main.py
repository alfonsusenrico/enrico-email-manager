import logging
import os

from app.config import ConfigError, GMAIL_SCOPES, load_settings
from app.gmail_client import GmailClient
from app.gmail_sync import AccountRuntime, GmailSyncService
from app.openai_client import OpenAIClient
from app.db import Database
from app.pubsub_worker import PubSubWorker
from app.telegram_bot import run_webhook
from app.telegram_client import TelegramClient
from app.watch_manager import WatchManager


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    try:
        settings = load_settings()
    except ConfigError as exc:
        logging.error("Configuration error: %s", exc)
        raise SystemExit(1)

    db = Database(settings.database_url)
    account_ids = db.ensure_accounts(
        settings.gmail_accounts, settings.gmail_watch_label_ids
    )
    accounts = {
        account.email: AccountRuntime(
            account_id=account_ids[account.email],
            email=account.email,
            refresh_token=account.refresh_token,
        )
        for account in settings.gmail_accounts
    }
    accounts_by_id = {runtime.account_id: runtime for runtime in accounts.values()}

    gmail_client = GmailClient(
        client_secret_path=settings.gmail_oauth_client_secret_json,
        scopes=GMAIL_SCOPES,
    )
    openai_client = OpenAIClient(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
    )
    telegram_client = TelegramClient(settings.telegram_bot_token)

    sync_service = GmailSyncService(
        settings=settings,
        db=db,
        gmail_client=gmail_client,
        openai_client=openai_client,
        telegram_client=telegram_client,
        accounts=accounts,
    )
    watch_manager = WatchManager(settings, db, gmail_client, accounts)
    watch_manager.start()

    worker = PubSubWorker(settings.pubsub_subscription, sync_service)
    worker.start()

    run_webhook(settings, db, gmail_client, telegram_client, accounts_by_id)


if __name__ == "__main__":
    main()
