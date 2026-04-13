import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import quote

from app.config import Settings
from app.db import AssistantEvaluationDispatchItem


@dataclass(frozen=True)
class AssistantDispatchResponse:
    ok: bool
    http_status: Optional[int]
    body: Optional[Dict[str, Any]]
    error: Optional[str]


class AssistantBridgeClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def dispatch(self, item: AssistantEvaluationDispatchItem) -> AssistantDispatchResponse:
        payload = self._build_payload(item)
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self._settings.assistant_bridge_url,
            data=body,
            method="POST",
            headers=self._build_headers(item.idempotency_key),
        )

        try:
            with urllib.request.urlopen(
                request,
                timeout=self._settings.assistant_dispatch_timeout_seconds,
            ) as response:
                raw_response = response.read().decode("utf-8", errors="replace")
                if raw_response:
                    try:
                        parsed = json.loads(raw_response)
                    except json.JSONDecodeError:
                        parsed = {"raw": raw_response}
                else:
                    parsed = {}
                if not isinstance(parsed, dict):
                    parsed = {"raw": parsed}
                return AssistantDispatchResponse(
                    ok=200 <= response.status < 300,
                    http_status=response.status,
                    body=parsed,
                    error=None,
                )
        except urllib.error.HTTPError as exc:
            raw_error = exc.read().decode("utf-8", errors="replace")
            parsed_error: Optional[Dict[str, Any]] = None
            if raw_error:
                try:
                    loaded = json.loads(raw_error)
                except json.JSONDecodeError:
                    loaded = {"error": raw_error}
                if isinstance(loaded, dict):
                    parsed_error = loaded
                else:
                    parsed_error = {"error": raw_error}
            return AssistantDispatchResponse(
                ok=False,
                http_status=exc.code,
                body=parsed_error,
                error=raw_error or str(exc),
            )
        except urllib.error.URLError as exc:
            return AssistantDispatchResponse(
                ok=False,
                http_status=None,
                body=None,
                error=str(exc.reason),
            )

    def _build_headers(self, idempotency_key: str) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Idempotency-Key": idempotency_key,
            "User-Agent": "email-manager/assistant-bridge",
        }
        if self._settings.assistant_shared_secret:
            headers["Authorization"] = f"Bearer {self._settings.assistant_shared_secret}"
        return headers

    def _build_payload(self, item: AssistantEvaluationDispatchItem) -> Dict[str, Any]:
        return {
            "event_type": "email.ingested",
            "payload_version": item.payload_version,
            "request": {
                "request_id": item.request_id,
                "email_message_id": item.email_message_id,
                "trigger_type": item.trigger_type,
                "trigger_reference": item.trigger_reference,
                "idempotency_key": item.idempotency_key,
            },
            "account": {
                "account_id": item.account_id,
                "email": item.account_email,
            },
            "email": {
                "gmail_message_id": item.gmail_message_id,
                "gmail_thread_id": item.gmail_thread_id,
                "history_id": item.history_id,
                "message_internal_at": (
                    item.message_internal_at.isoformat()
                    if item.message_internal_at
                    else None
                ),
                "sender_name": item.sender_name,
                "sender_email": item.sender_email,
                "sender_domain": item.sender_domain,
                "to_recipients": item.to_recipients,
                "cc_recipients": item.cc_recipients,
                "subject": item.subject,
                "snippet": item.snippet,
                "normalized_body_text": item.normalized_body_text,
                "labels": item.labels_json,
                "headers": item.headers_json,
                "raw_size_bytes": item.raw_size_bytes,
                "gmail_open_url": (
                    f"https://mail.google.com/mail/u/0/?authuser={quote(item.account_email, safe='')}"
                    f"#inbox/{item.gmail_thread_id or item.gmail_message_id}"
                ),
            },
            "callbacks": {
                "evaluation_result_url": self._settings.assistant_result_callback_url,
                "requeue_url": self._settings.assistant_requeue_callback_url,
            },
        }
