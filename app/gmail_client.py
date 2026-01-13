import base64
import json
from dataclasses import dataclass
from typing import Dict, List, Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from bs4 import BeautifulSoup


@dataclass(frozen=True)
class GmailMessage:
    message_id: str
    thread_id: str
    sender_email: str
    sender_name: str
    subject: str
    snippet: str
    body_text: str


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

    def list_history(
        self, refresh_token: str, start_history_id: int, label_id: str
    ) -> Dict:
        service = self.build_service(refresh_token)
        response = service.users().history().list(
            userId="me",
            startHistoryId=start_history_id,
            historyTypes=["messageAdded"],
            labelId=label_id,
        ).execute()

        histories = response.get("history", [])
        next_page_token = response.get("nextPageToken")
        while next_page_token:
            page = (
                service.users()
                .history()
                .list(
                    userId="me",
                    startHistoryId=start_history_id,
                    historyTypes=["messageAdded"],
                    labelId=label_id,
                    pageToken=next_page_token,
                )
                .execute()
            )
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
        headers = {h["name"].lower(): h.get("value", "") for h in payload.get("headers", [])}
        sender = headers.get("from", "")
        sender_name, sender_email = self._parse_sender(sender)
        subject = headers.get("subject", "")
        snippet = message.get("snippet", "")
        body_text = self._extract_body_text(payload)

        return GmailMessage(
            message_id=message.get("id", message_id),
            thread_id=message.get("threadId", ""),
            sender_email=sender_email,
            sender_name=sender_name,
            subject=subject,
            snippet=snippet,
            body_text=body_text,
        )

    @staticmethod
    def _parse_sender(sender: str) -> tuple[str, str]:
        if "<" in sender and ">" in sender:
            name_part, email_part = sender.split("<", 1)
            return name_part.strip().strip('"'), email_part.strip(" >")
        return "", sender.strip()

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
        if isinstance(error, HttpError):
            return error.resp.status == 404
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
