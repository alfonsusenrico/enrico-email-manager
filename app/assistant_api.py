import hmac
import json
import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, cast

from app.config import (
    ASSISTANT_EVALUATIONS_PATH,
    ASSISTANT_REQUEUE_PATH,
    HEALTHCHECK_PATH,
    Settings,
)
from app.db import Database

logger = logging.getLogger(__name__)


class AssistantApiServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        db: Database,
        settings: Settings,
    ) -> None:
        super().__init__(server_address, AssistantApiHandler)
        self.db = db
        self.settings = settings


class AssistantApiHandler(BaseHTTPRequestHandler):
    server: AssistantApiServer

    def do_GET(self) -> None:
        if self.path != HEALTHCHECK_PATH:
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        self._write_json(
            HTTPStatus.OK,
            {
                "status": "ok",
                "service": "email-manager",
                "mode": "assistant-first",
            },
        )

    def do_POST(self) -> None:
        if not self._is_authorized():
            self._write_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return

        payload = self._read_json_body()
        if payload is None:
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_json"})
            return

        if self.path == ASSISTANT_EVALUATIONS_PATH:
            self._handle_evaluation_result(payload)
            return

        if self.path == ASSISTANT_REQUEUE_PATH:
            self._handle_requeue(payload)
            return

        self._write_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def _handle_evaluation_result(self, payload: Dict[str, Any]) -> None:
        request_id = payload.get("request_id")
        idempotency_key = payload.get("idempotency_key")
        decision = payload.get("decision")
        if not decision or (request_id is None and not idempotency_key):
            self._write_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "request_id or idempotency_key and decision are required"},
            )
            return

        resolved_request_id = self.server.db.record_assistant_evaluation_result(
            request_id=int(request_id) if request_id is not None else None,
            idempotency_key=str(idempotency_key) if idempotency_key else None,
            decision=str(decision),
            importance=_optional_str(payload.get("importance")),
            reason_summary=_optional_str(payload.get("reason_summary")),
            surface_target=_optional_str(payload.get("surface_target")),
            assistant_trace_id=_optional_str(payload.get("assistant_trace_id")),
            raw_response=payload,
        )
        if resolved_request_id is None:
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "request_not_found"})
            return

        self._write_json(
            HTTPStatus.OK,
            {
                "status": "recorded",
                "request_id": resolved_request_id,
            },
        )

    def _handle_requeue(self, payload: Dict[str, Any]) -> None:
        trigger_type = _optional_str(payload.get("trigger_type")) or "assistant.context_changed"
        trigger_reference = _optional_str(payload.get("trigger_reference"))
        if not trigger_reference:
            self._write_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "trigger_reference is required"},
            )
            return

        email_message_ids = payload.get("email_message_ids") or []
        gmail_message_ids = payload.get("gmail_message_ids") or []
        inserted = 0
        if email_message_ids:
            try:
                inserted = self.server.db.queue_re_evaluation_requests(
                    email_message_ids=[int(value) for value in email_message_ids],
                    trigger_type=trigger_type,
                    trigger_reference=trigger_reference,
                )
            except (TypeError, ValueError):
                self._write_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "email_message_ids must be integers"},
                )
                return
        elif gmail_message_ids:
            inserted = self.server.db.queue_re_evaluation_requests_by_gmail_ids(
                gmail_message_ids=[str(value) for value in gmail_message_ids],
                trigger_type=trigger_type,
                trigger_reference=trigger_reference,
            )
        else:
            self._write_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "email_message_ids or gmail_message_ids is required"},
            )
            return

        self._write_json(
            HTTPStatus.OK,
            {
                "status": "queued",
                "inserted": inserted,
            },
        )

    def _is_authorized(self) -> bool:
        secret = self.server.settings.assistant_shared_secret
        if not secret:
            return True
        auth_header = self.headers.get("Authorization", "")
        expected = f"Bearer {secret}"
        return hmac.compare_digest(auth_header, expected)

    def _read_json_body(self) -> Optional[Dict[str, Any]]:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None
        raw_body = self.rfile.read(content_length)
        if not raw_body:
            return {}
        try:
            data = json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        return cast(Dict[str, Any], data)

    def _write_json(self, status: HTTPStatus, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        logger.info("%s - %s", self.address_string(), format % args)


def _optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
