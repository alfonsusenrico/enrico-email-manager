import json
import os
from dataclasses import dataclass
from typing import List, Optional

ASSISTANT_EVALUATIONS_PATH = "/assistant/evaluations"
ASSISTANT_REQUEUE_PATH = "/assistant/requeue"
HEALTHCHECK_PATH = "/healthz"
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


@dataclass(frozen=True)
class GmailAccountConfig:
    email: str
    refresh_token: str


@dataclass(frozen=True)
class Settings:
    gmail_watch_topic: str
    pubsub_subscription: str
    gmail_watch_label_ids: List[str]
    google_application_credentials: str
    gmail_oauth_client_secret_json: str
    gmail_accounts: List[GmailAccountConfig]
    assistant_bridge_url: str
    assistant_shared_secret: str
    public_base_url: str
    assistant_dispatch_timeout_seconds: int
    assistant_dispatch_batch_size: int
    assistant_dispatch_poll_seconds: int
    assistant_dispatch_max_attempts: int
    ingest_worker_batch_size: int
    ingest_worker_poll_seconds: int
    ingest_worker_max_attempts: int
    app_host: str
    app_port: int
    database_url: str

    @property
    def assistant_result_callback_url(self) -> Optional[str]:
        base = self.public_base_url.rstrip("/")
        if not base:
            return None
        return f"{base}{ASSISTANT_EVALUATIONS_PATH}"

    @property
    def assistant_requeue_callback_url(self) -> Optional[str]:
        base = self.public_base_url.rstrip("/")
        if not base:
            return None
        return f"{base}{ASSISTANT_REQUEUE_PATH}"


class ConfigError(ValueError):
    pass


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ConfigError(f"Missing required environment variable: {name}")
    return value


def _parse_accounts(raw_value: str) -> List[GmailAccountConfig]:
    try:
        data = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise ConfigError("GMAIL_ACCOUNTS_JSON must be valid JSON") from exc

    if not isinstance(data, list) or not data:
        raise ConfigError("GMAIL_ACCOUNTS_JSON must be a non-empty JSON array")

    accounts: List[GmailAccountConfig] = []
    for item in data:
        if not isinstance(item, dict):
            raise ConfigError("Each account entry must be a JSON object")
        email = item.get("email")
        refresh_token = item.get("refresh_token")
        if not email or not refresh_token:
            raise ConfigError("Each account must include email and refresh_token")
        accounts.append(GmailAccountConfig(email=email, refresh_token=refresh_token))
    return accounts


def _parse_label_ids(raw_value: str) -> List[str]:
    labels = [label.strip() for label in raw_value.split(",") if label.strip()]
    if not labels:
        raise ConfigError("GMAIL_WATCH_LABEL_IDS cannot be empty")
    return labels


def _parse_int(value: str, name: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc


def load_settings() -> Settings:
    return Settings(
        gmail_watch_topic=_require_env("GMAIL_WATCH_TOPIC"),
        pubsub_subscription=_require_env("PUBSUB_SUBSCRIPTION"),
        gmail_watch_label_ids=_parse_label_ids(
            os.getenv("GMAIL_WATCH_LABEL_IDS", "INBOX")
        ),
        google_application_credentials=_require_env("GOOGLE_APPLICATION_CREDENTIALS"),
        gmail_oauth_client_secret_json=_require_env("GMAIL_OAUTH_CLIENT_SECRET_JSON"),
        gmail_accounts=_parse_accounts(_require_env("GMAIL_ACCOUNTS_JSON")),
        assistant_bridge_url=_require_env("ASSISTANT_BRIDGE_URL"),
        assistant_shared_secret=os.getenv("ASSISTANT_SHARED_SECRET", "").strip(),
        public_base_url=_require_env("PUBLIC_BASE_URL"),
        assistant_dispatch_timeout_seconds=_parse_int(
            os.getenv("ASSISTANT_DISPATCH_TIMEOUT_SECONDS", "15"),
            "ASSISTANT_DISPATCH_TIMEOUT_SECONDS",
        ),
        assistant_dispatch_batch_size=_parse_int(
            os.getenv("ASSISTANT_DISPATCH_BATCH_SIZE", "10"),
            "ASSISTANT_DISPATCH_BATCH_SIZE",
        ),
        assistant_dispatch_poll_seconds=_parse_int(
            os.getenv("ASSISTANT_DISPATCH_POLL_SECONDS", "3"),
            "ASSISTANT_DISPATCH_POLL_SECONDS",
        ),
        assistant_dispatch_max_attempts=_parse_int(
            os.getenv("ASSISTANT_DISPATCH_MAX_ATTEMPTS", "20"),
            "ASSISTANT_DISPATCH_MAX_ATTEMPTS",
        ),
        ingest_worker_batch_size=_parse_int(
            os.getenv("INGEST_WORKER_BATCH_SIZE", "25"),
            "INGEST_WORKER_BATCH_SIZE",
        ),
        ingest_worker_poll_seconds=_parse_int(
            os.getenv("INGEST_WORKER_POLL_SECONDS", "2"),
            "INGEST_WORKER_POLL_SECONDS",
        ),
        ingest_worker_max_attempts=_parse_int(
            os.getenv("INGEST_WORKER_MAX_ATTEMPTS", "20"),
            "INGEST_WORKER_MAX_ATTEMPTS",
        ),
        app_host=os.getenv("APP_HOST", "0.0.0.0"),
        app_port=_parse_int(os.getenv("APP_PORT", "8080"), "APP_PORT"),
        database_url=_require_env("DATABASE_URL"),
    )
