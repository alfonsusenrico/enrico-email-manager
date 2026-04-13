import logging
import os

from app.assistant_api import AssistantApiServer
from app.assistant_bridge import AssistantBridgeClient
from app.config import ConfigError, GMAIL_SCOPES, load_settings
from app.db import Database
from app.gmail_client import GmailClient
from app.gmail_sync import AccountRuntime, GmailSyncService
from app.pubsub_worker import PubSubWorker
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

    gmail_client = GmailClient(
        client_secret_path=settings.gmail_oauth_client_secret_json,
        scopes=GMAIL_SCOPES,
    )
    assistant_bridge = AssistantBridgeClient(settings)

    sync_service = GmailSyncService(
        settings=settings,
        db=db,
        gmail_client=gmail_client,
        assistant_bridge=assistant_bridge,
        accounts=accounts,
    )
    watch_manager = WatchManager(settings, db, gmail_client, accounts)
    watch_manager.start()
    sync_service.start_background_workers()

    worker = PubSubWorker(settings.pubsub_subscription, sync_service)
    worker.start()

    server = AssistantApiServer((settings.app_host, settings.app_port), db, settings)
    logging.info(
        "Assistant API listening on %s:%s",
        settings.app_host,
        settings.app_port,
    )
    try:
        server.serve_forever()
    finally:
        sync_service.stop()
        worker.stop()
        server.server_close()


if __name__ == "__main__":
    main()
