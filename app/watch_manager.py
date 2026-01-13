import datetime as dt
import logging
import threading
import time
from typing import Dict

from app.config import Settings
from app.db import Database
from app.gmail_client import GmailClient
from app.gmail_sync import AccountRuntime

logger = logging.getLogger(__name__)

RENEW_WINDOW = dt.timedelta(hours=24)
LOOP_INTERVAL_SECONDS = 900


class WatchManager:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        gmail_client: GmailClient,
        accounts: Dict[str, AccountRuntime],
    ) -> None:
        self._settings = settings
        self._db = db
        self._gmail_client = gmail_client
        self._accounts = accounts
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("Watch manager started")

    def _run(self) -> None:
        while True:
            for account in self._accounts.values():
                try:
                    self._ensure_watch(account)
                except Exception:
                    logger.exception("Watch renewal failed for %s", account.email)
            time.sleep(LOOP_INTERVAL_SECONDS)

    def _ensure_watch(self, account: AccountRuntime) -> None:
        last_history_id, watch_expiration = self._db.get_account_state(account.account_id)
        now = dt.datetime.now(dt.timezone.utc)
        should_refresh = (
            watch_expiration is None
            or watch_expiration.tzinfo is None
            or watch_expiration <= now + RENEW_WINDOW
        )
        if not should_refresh:
            return

        response = self._gmail_client.watch_inbox(
            refresh_token=account.refresh_token,
            topic_name=self._settings.gmail_watch_topic,
            label_ids=self._settings.gmail_watch_label_ids,
        )
        history_id = int(response.get("historyId", last_history_id or 0))
        expiration_ms = int(response.get("expiration", 0))
        expiration = None
        if expiration_ms:
            expiration = dt.datetime.fromtimestamp(
                expiration_ms / 1000, tz=dt.timezone.utc
            )
        self._db.update_watch_info(account.account_id, history_id, expiration)
        logger.info("Watch renewed for %s", account.email)
