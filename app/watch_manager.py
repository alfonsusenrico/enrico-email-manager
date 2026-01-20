import datetime as dt
import logging
import threading
import time
from typing import Dict

from app.backoff import AccountBackoff
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
        self._auth_backoff = AccountBackoff(base_seconds=300, max_seconds=3600)

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
        if self._auth_backoff.should_skip(account.account_id):
            until = self._auth_backoff.next_ready_at(account.account_id)
            until_text = until.isoformat() if until else "unknown"
            logger.warning(
                "Skipping watch renewal for %s due to auth backoff until %s",
                account.email,
                until_text,
            )
            return
        last_history_id, watch_expiration = self._db.get_account_state(account.account_id)
        now = dt.datetime.now(dt.timezone.utc)
        should_refresh = (
            watch_expiration is None
            or watch_expiration.tzinfo is None
            or watch_expiration <= now + RENEW_WINDOW
        )
        if not should_refresh:
            return

        try:
            response = self._gmail_client.watch_inbox(
                refresh_token=account.refresh_token,
                topic_name=self._settings.gmail_watch_topic,
                label_ids=self._settings.gmail_watch_label_ids,
            )
        except Exception as exc:
            if self._gmail_client.is_auth_error(exc):
                delay = self._auth_backoff.record_failure(account.account_id)
                until = self._auth_backoff.next_ready_at(account.account_id)
                until_text = until.isoformat() if until else "unknown"
                logger.exception(
                    "Gmail auth error renewing watch for %s; backing off for %ds (until %s). "
                    "Refresh token may be expired or revoked.",
                    account.email,
                    delay,
                    until_text,
                )
                return
            raise
        self._auth_backoff.reset(account.account_id)
        history_id = int(response.get("historyId", last_history_id or 0))
        expiration_ms = int(response.get("expiration", 0))
        expiration = None
        if expiration_ms:
            expiration = dt.datetime.fromtimestamp(
                expiration_ms / 1000, tz=dt.timezone.utc
            )
        self._db.update_watch_info(account.account_id, history_id, expiration)
        logger.info("Watch renewed for %s", account.email)
