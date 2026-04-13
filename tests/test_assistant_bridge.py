import datetime as dt
import unittest

from tests.stubs import install_dependency_stubs

install_dependency_stubs()

from app.assistant_bridge import AssistantBridgeClient
from app.config import GmailAccountConfig, Settings
from app.db import AssistantEvaluationDispatchItem


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
        assistant_shared_secret="shared-secret",
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


class AssistantBridgePayloadTest(unittest.TestCase):
    def test_payload_contains_callbacks_and_message_context(self) -> None:
        client = AssistantBridgeClient(_settings())
        item = AssistantEvaluationDispatchItem(
            request_id=10,
            email_message_id=99,
            account_id=7,
            account_email="user@example.com",
            gmail_message_id="gmail-message-1",
            gmail_thread_id="gmail-thread-1",
            history_id=123,
            message_internal_at=dt.datetime(2026, 4, 13, 12, 0, tzinfo=dt.timezone.utc),
            sender_name="Sender",
            sender_email="sender@example.com",
            sender_domain="example.com",
            to_recipients=[{"name": "User", "email": "user@example.com"}],
            cc_recipients=[],
            subject="Subject",
            snippet="Snippet",
            normalized_body_text="Body",
            labels_json=["INBOX"],
            headers_json={"subject": "Subject"},
            raw_size_bytes=2048,
            trigger_type="email.ingested",
            trigger_reference="gmail_message_ingest_job:1",
            idempotency_key="email.ingested:7:gmail-message-1",
            payload_version="v1",
            attempt_count=1,
        )

        payload = client._build_payload(item)

        self.assertEqual(payload["event_type"], "email.ingested")
        self.assertEqual(
            payload["callbacks"]["evaluation_result_url"],
            "https://email-manager.example/assistant/evaluations",
        )
        self.assertEqual(
            payload["callbacks"]["requeue_url"],
            "https://email-manager.example/assistant/requeue",
        )
        self.assertEqual(payload["request"]["idempotency_key"], item.idempotency_key)
        self.assertEqual(payload["email"]["gmail_message_id"], "gmail-message-1")
        self.assertIn("authuser=user%40example.com", payload["email"]["gmail_open_url"])
