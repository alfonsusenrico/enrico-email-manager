import unittest

from tests.stubs import install_dependency_stubs

install_dependency_stubs()

from app.config import GmailAccountConfig, Settings
from app.gmail_sync import AccountRuntime, GmailSyncService


def _settings() -> Settings:
    return Settings(
        gmail_watch_topic="projects/test/topics/watch",
        pubsub_subscription="projects/test/subscriptions/watch",
        gmail_watch_label_ids=["INBOX"],
        google_application_credentials="secrets/service_account.json",
        gmail_oauth_client_secret_json="secrets/client_secret.json",
        gmail_accounts=[GmailAccountConfig(email="user@example.com", refresh_token="token")],
        assistant_dispatch_enabled=True,
        assistant_bridge_url="https://openclaw.example/internal/email-manager/events",
        assistant_shared_secret="",
        public_base_url="https://email-manager.example",
        assistant_dispatch_timeout_seconds=15,
        assistant_dispatch_batch_size=10,
        assistant_dispatch_poll_seconds=3,
        assistant_dispatch_max_attempts=20,
        ingest_worker_batch_size=25,
        ingest_worker_poll_seconds=2,
        ingest_worker_max_attempts=20,
        app_host="0.0.0.0",
        app_port=8080,
        database_url="postgres://postgres:postgres@db:5432/email_manager?sslmode=disable",
    )


class FakeDatabase:
    def __init__(self) -> None:
        self.watch_statuses: list[str] = []
        self.sync_run_statuses: list[str] = []
        self.last_history_id: int | None = None
        self.queued_message_ids: list[str] = []

    def insert_watch_event(self, **kwargs):
        return 1

    def update_watch_event_status(self, watch_event_id: int, **kwargs):
        self.watch_statuses.append(kwargs["status"])

    def get_account_state(self, account_id: int):
        return (10, None)

    def start_sync_run(self, **kwargs):
        return 5

    def update_account_sync_state(self, account_id: int, **kwargs):
        return None

    def finish_sync_run(self, sync_run_id: int, **kwargs):
        self.sync_run_statuses.append(kwargs["status"])

    def enqueue_message_ingest_jobs(self, **kwargs):
        self.queued_message_ids.extend(kwargs["message_ids"])
        return len(kwargs["message_ids"])

    def update_last_history_id(self, account_id: int, history_id: int):
        self.last_history_id = history_id


class FakeAssistantBridge:
    pass


class FakeGmailClient:
    def __init__(self, *, response=None, retryable=False):
        self._response = response
        self._retryable = retryable

    def list_history(self, refresh_token: str, start_history_id: int, label_id=None):
        if isinstance(self._response, Exception):
            raise self._response
        return self._response

    def is_auth_error(self, error: Exception) -> bool:
        return False

    def is_history_invalid(self, error: Exception) -> bool:
        return False

    def is_retryable_error(self, error: Exception) -> bool:
        return self._retryable


class GmailSyncServicePubSubDispositionTest(unittest.TestCase):
    def _service(self, gmail_client: FakeGmailClient, db: FakeDatabase) -> GmailSyncService:
        account = AccountRuntime(
            account_id=1,
            email="user@example.com",
            refresh_token="refresh-token",
        )
        return GmailSyncService(
            settings=_settings(),
            db=db,
            gmail_client=gmail_client,
            assistant_bridge=FakeAssistantBridge(),
            accounts={"user@example.com": account},
        )

    def test_retryable_history_error_requests_pubsub_retry(self) -> None:
        db = FakeDatabase()
        service = self._service(
            FakeGmailClient(response=RuntimeError("temporary error"), retryable=True),
            db,
        )

        should_ack = service.handle_pubsub_event("user@example.com", 200, "msg-1")

        self.assertFalse(should_ack)
        self.assertIn("sync_retry", db.watch_statuses)
        self.assertIn("sync_retry", db.sync_run_statuses)

    def test_permanent_history_error_is_recorded_without_poison_retry(self) -> None:
        db = FakeDatabase()
        service = self._service(
            FakeGmailClient(response=RuntimeError("permanent error"), retryable=False),
            db,
        )

        should_ack = service.handle_pubsub_event("user@example.com", 200, "msg-1")

        self.assertTrue(should_ack)
        self.assertIn("sync_failed", db.watch_statuses)
        self.assertIn("sync_failed", db.sync_run_statuses)

    def test_successful_sync_queues_message_ids_and_acknowledges(self) -> None:
        db = FakeDatabase()
        service = self._service(
            FakeGmailClient(
                response={
                    "historyId": "250",
                    "history": [
                        {
                            "messagesAdded": [
                                {"message": {"id": "m-1"}},
                                {"message": {"id": "m-2"}},
                                {"message": {"id": "m-1"}},
                            ]
                        }
                    ],
                }
            ),
            db,
        )

        should_ack = service.handle_pubsub_event("user@example.com", 200, "msg-1")

        self.assertTrue(should_ack)
        self.assertEqual(db.queued_message_ids, ["m-1", "m-2"])
        self.assertEqual(db.last_history_id, 250)
        self.assertIn("queued_for_ingest", db.watch_statuses)
        self.assertIn("completed", db.sync_run_statuses)
