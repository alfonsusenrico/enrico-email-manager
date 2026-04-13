import json
import types
import unittest

from tests.stubs import install_dependency_stubs

install_dependency_stubs()

from googleapiclient.errors import HttpError

from app.gmail_client import GmailClient


def _http_error(status: int, payload: dict | None = None) -> HttpError:
    response = types.SimpleNamespace(status=status, reason="error")
    content = json.dumps(payload or {}).encode("utf-8")
    return HttpError(response, content, uri="https://example.test")


class GmailClientErrorClassificationTest(unittest.TestCase):
    def test_message_not_found_is_detected(self) -> None:
        error = _http_error(404, {"error": {"message": "Not Found"}})
        self.assertTrue(GmailClient.is_message_not_found(error))

    def test_retryable_http_error_is_detected(self) -> None:
        error = _http_error(
            429,
            {
                "error": {
                    "errors": [{"reason": "rateLimitExceeded"}],
                }
            },
        )
        self.assertTrue(GmailClient.is_retryable_error(error))

    def test_auth_error_is_detected(self) -> None:
        error = _http_error(
            403,
            {
                "error": {
                    "status": "PERMISSION_DENIED",
                    "errors": [{"reason": "forbidden"}],
                }
            },
        )
        self.assertTrue(GmailClient.is_auth_error(error))
