import base64
import datetime as dt
import json
from dataclasses import dataclass
from email.utils import getaddresses, parseaddr
from typing import Dict, List, Optional

from bs4 import BeautifulSoup
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


@dataclass(frozen=True)
class GmailMessage:
    message_id: str
    thread_id: str
    sender_email: str
    sender_name: str
    sender_domain: str
    to_recipients: List[Dict[str, str]]
    cc_recipients: List[Dict[str, str]]
    subject: str
    snippet: str
    body_text: str
    label_ids: List[str]
    headers: Dict[str, str]
    message_internal_at: Optional[dt.datetime]
    raw_size_bytes: Optional[int]


class GmailClient:
    def __init__(self, client_secret_path: str, scopes: List[str]) -> None:
        self._client_info = self._load_client_config(client_secret_path)
        self._scopes = scopes

    def build_service(self, refresh_token: str):
        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri=self._client_info["token_uri"],
            client_id=self._client_info["client_id"],
            client_secret=self._client_info["client_secret"],
            scopes=self._scopes,
        )
        return build("gmail", "v1", credentials=creds, cache_discovery=False)

    def watch_inbox(self, refresh_token: str, topic_name: str, label_ids: List[str]) -> Dict:
        service = self.build_service(refresh_token)
        body = {
            "labelIds": label_ids,
            "topicName": topic_name,
        }
        return service.users().watch(userId="me", body=body).execute()

    def get_profile(self, refresh_token: str) -> Dict:
        service = self.build_service(refresh_token)
        return service.users().getProfile(userId="me").execute()

    def archive(self, refresh_token: str, message_id: str, thread_id: Optional[str]) -> None:
        service = self.build_service(refresh_token)
        body = {"removeLabelIds": ["INBOX"]}
        if thread_id:
            service.users().threads().modify(userId="me", id=thread_id, body=body).execute()
        else:
            service.users().messages().modify(userId="me", id=message_id, body=body).execute()

    def trash(self, refresh_token: str, message_id: str, thread_id: Optional[str]) -> None:
        service = self.build_service(refresh_token)
        if thread_id:
            service.users().threads().trash(userId="me", id=thread_id).execute()
        else:
            service.users().messages().trash(userId="me", id=message_id).execute()

    def unarchive(self, refresh_token: str, message_id: str, thread_id: Optional[str]) -> None:
        service = self.build_service(refresh_token)
        body = {"addLabelIds": ["INBOX"]}
        if thread_id:
            service.users().threads().modify(userId="me", id=thread_id, body=body).execute()
        else:
            service.users().messages().modify(userId="me", id=message_id, body=body).execute()

    def untrash(self, refresh_token: str, message_id: str, thread_id: Optional[str]) -> None:
        service = self.build_service(refresh_token)
        if thread_id:
            service.users().threads().untrash(userId="me", id=thread_id).execute()
        else:
            service.users().messages().untrash(userId="me", id=message_id).execute()

    def list_history(
        self, refresh_token: str, start_history_id: int, label_id: Optional[str] = None
    ) -> Dict:
        service = self.build_service(refresh_token)
        list_kwargs = {
            "userId": "me",
            "startHistoryId": start_history_id,
            "historyTypes": ["messageAdded"],
        }
        if label_id:
            list_kwargs["labelId"] = label_id
        response = service.users().history().list(**list_kwargs).execute()

        histories = response.get("history", [])
        next_page_token = response.get("nextPageToken")
        while next_page_token:
            list_kwargs["pageToken"] = next_page_token
            page = service.users().history().list(**list_kwargs).execute()
            histories.extend(page.get("history", []))
            next_page_token = page.get("nextPageToken")
            if page.get("historyId"):
                response["historyId"] = page.get("historyId")

        response["history"] = histories
        return response

    def get_message(self, refresh_token: str, message_id: str) -> GmailMessage:
        service = self.build_service(refresh_token)
        message = (
            service.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )

        payload = message.get("payload", {})
        headers = {
            header["name"].lower(): header.get("value", "")
            for header in payload.get("headers", [])
            if header.get("name")
        }
        sender_name, sender_email = self._parse_sender(headers.get("from", ""))
        sender_domain = self._extract_domain(sender_email)
        body_text = self._extract_body_text(payload)
        internal_date = self._parse_internal_date(message.get("internalDate"))

        return GmailMessage(
            message_id=message.get("id", message_id),
            thread_id=message.get("threadId", ""),
            sender_email=sender_email,
            sender_name=sender_name,
            sender_domain=sender_domain,
            to_recipients=self._parse_address_list(headers.get("to", "")),
            cc_recipients=self._parse_address_list(headers.get("cc", "")),
            subject=headers.get("subject", ""),
            snippet=message.get("snippet", ""),
            body_text=body_text,
            label_ids=list(message.get("labelIds", []) or []),
            headers=headers,
            message_internal_at=internal_date,
            raw_size_bytes=message.get("sizeEstimate"),
        )

    @staticmethod
    def _parse_sender(sender: str) -> tuple[str, str]:
        name, email_address = parseaddr(sender)
        return name.strip(), email_address.strip().lower()

    @staticmethod
    def _parse_address_list(raw_value: str) -> List[Dict[str, str]]:
        recipients: List[Dict[str, str]] = []
        for name, email_address in getaddresses([raw_value]):
            email_address = email_address.strip().lower()
            if not email_address:
                continue
            recipients.append(
                {
                    "name": name.strip(),
                    "email": email_address,
                }
            )
        return recipients

    @staticmethod
    def _extract_domain(email_address: str) -> str:
        if "@" not in email_address:
            return email_address
        return email_address.split("@", 1)[1]

    @staticmethod
    def _parse_internal_date(raw_value: Optional[str]) -> Optional[dt.datetime]:
        if not raw_value:
            return None
        try:
            return dt.datetime.fromtimestamp(
                int(raw_value) / 1000, tz=dt.timezone.utc
            )
        except (TypeError, ValueError, OSError):
            return None

    def _extract_body_text(self, payload: Dict) -> str:
        plain = self._find_part(payload, "text/plain")
        if plain:
            return plain

        html = self._find_part(payload, "text/html")
        if html:
            soup = BeautifulSoup(html, "html.parser")
            return soup.get_text("\n", strip=True)

        return ""

    def _find_part(self, payload: Dict, mime_type: str) -> Optional[str]:
        if payload.get("mimeType") == mime_type:
            return self._decode_body(payload.get("body", {}).get("data"))

        for part in payload.get("parts", []) or []:
            if part.get("mimeType") == mime_type:
                return self._decode_body(part.get("body", {}).get("data"))
            if part.get("parts"):
                nested = self._find_part(part, mime_type)
                if nested:
                    return nested
        return None

    @staticmethod
    def _decode_body(data: Optional[str]) -> str:
        if not data:
            return ""
        padding = "=" * (-len(data) % 4)
        decoded = base64.urlsafe_b64decode(data + padding)
        return decoded.decode("utf-8", errors="replace")

    @staticmethod
    def is_history_invalid(error: Exception) -> bool:
        return isinstance(error, HttpError) and error.resp.status == 404

    @staticmethod
    def is_message_not_found(error: Exception) -> bool:
        return isinstance(error, HttpError) and error.resp.status == 404

    @staticmethod
    def is_auth_error(error: Exception) -> bool:
        if isinstance(error, RefreshError):
            retryable = getattr(error, "retryable", False)
            return not retryable

        if isinstance(error, HttpError):
            if error.resp.status in (401, 403):
                return True
            content = getattr(error, "content", None)
            if content:
                if isinstance(content, (bytes, bytearray)):
                    content = content.decode("utf-8", errors="replace")
                try:
                    data = json.loads(content)
                except (TypeError, ValueError):
                    return False
                if isinstance(data, dict):
                    if data.get("error") == "invalid_grant":
                        return True
                    error_info = data.get("error")
                    if isinstance(error_info, dict):
                        status = error_info.get("status")
                        if status in ("UNAUTHENTICATED", "PERMISSION_DENIED"):
                            return True
                        errors = error_info.get("errors") or []
                        for entry in errors:
                            reason = entry.get("reason")
                            if reason in ("authError", "invalidCredentials", "forbidden"):
                                return True
        return False

    @staticmethod
    def is_retryable_error(error: Exception) -> bool:
        if isinstance(error, RefreshError):
            return bool(getattr(error, "retryable", False))

        if not isinstance(error, HttpError):
            return False

        if error.resp.status in (408, 429, 500, 502, 503, 504):
            return True

        content = getattr(error, "content", None)
        if not content:
            return False
        if isinstance(content, (bytes, bytearray)):
            content = content.decode("utf-8", errors="replace")
        try:
            data = json.loads(content)
        except (TypeError, ValueError):
            return False
        error_info = data.get("error")
        if not isinstance(error_info, dict):
            return False
        errors = error_info.get("errors") or []
        for entry in errors:
            reason = entry.get("reason")
            if reason in ("backendError", "internalError", "rateLimitExceeded", "userRateLimitExceeded"):
                return True
        return False

    @staticmethod
    def _load_client_config(path: str) -> Dict[str, str]:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        info = raw.get("web") or raw.get("installed")
        if not info:
            raise ValueError("OAuth client JSON missing 'web' or 'installed' section")
        return {
            "client_id": info["client_id"],
            "client_secret": info["client_secret"],
            "token_uri": info.get("token_uri", "https://oauth2.googleapis.com/token"),
        }
