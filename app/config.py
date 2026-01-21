import json
import os
from dataclasses import dataclass
from typing import List

WEBHOOK_PATH = "/telegram/webhook"
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
    openai_api_key: str
    openai_model: str
    openai_price_input_per_1m: float
    openai_price_cached_input_per_1m: float
    openai_price_output_per_1m: float
    llm_max_input_tokens: int
    llm_low_confidence_threshold: float
    telegram_bot_token: str
    telegram_webhook_base_url: str
    telegram_webhook_secret_token: str
    telegram_allowed_user_ids: List[int]
    app_host: str
    app_port: int
    database_url: str

    @property
    def telegram_webhook_url(self) -> str:
        base = self.telegram_webhook_base_url.rstrip("/")
        return f"{base}{WEBHOOK_PATH}"


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


def _parse_float(value: str, name: str) -> float:
    try:
        return float(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number") from exc


def _parse_int_list(raw_value: str, name: str) -> List[int]:
    if not raw_value:
        return []
    values: List[int] = []
    for part in raw_value.split(","):
        item = part.strip()
        if not item:
            continue
        try:
            values.append(int(item))
        except ValueError as exc:
            raise ConfigError(f"{name} must be a comma-separated list of integers") from exc
    return values


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
        openai_api_key=_require_env("OPENAI_API_KEY"),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-5-mini"),
        openai_price_input_per_1m=_parse_float(
            _require_env("OPENAI_PRICE_INPUT_PER_1M"),
            "OPENAI_PRICE_INPUT_PER_1M",
        ),
        openai_price_cached_input_per_1m=_parse_float(
            _require_env("OPENAI_PRICE_CACHED_INPUT_PER_1M"),
            "OPENAI_PRICE_CACHED_INPUT_PER_1M",
        ),
        openai_price_output_per_1m=_parse_float(
            _require_env("OPENAI_PRICE_OUTPUT_PER_1M"),
            "OPENAI_PRICE_OUTPUT_PER_1M",
        ),
        llm_max_input_tokens=_parse_int(
            os.getenv("LLM_MAX_INPUT_TOKENS", "12000"),
            "LLM_MAX_INPUT_TOKENS",
        ),
        llm_low_confidence_threshold=_parse_float(
            os.getenv("LLM_LOW_CONFIDENCE_THRESHOLD", "0.8"),
            "LLM_LOW_CONFIDENCE_THRESHOLD",
        ),
        telegram_bot_token=_require_env("TELEGRAM_BOT_TOKEN"),
        telegram_webhook_base_url=_require_env("TELEGRAM_WEBHOOK_BASE_URL"),
        telegram_webhook_secret_token=os.getenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", "").strip(),
        telegram_allowed_user_ids=_parse_int_list(
            os.getenv("TELEGRAM_ALLOWED_USER_IDS", ""),
            "TELEGRAM_ALLOWED_USER_IDS",
        ),
        app_host=os.getenv("APP_HOST", "0.0.0.0"),
        app_port=_parse_int(os.getenv("APP_PORT", "8080"), "APP_PORT"),
        database_url=_require_env("DATABASE_URL"),
    )
