import datetime as dt
import logging
import threading
from dataclasses import dataclass
from typing import Dict, List, Optional

from app.assistant_bridge import AssistantBridgeClient, AssistantDispatchResponse
from app.backoff import AccountBackoff
from app.config import Settings
from app.db import AssistantEvaluationDispatchItem, Database, GmailIngestJob
from app.gmail_client import GmailClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AccountRuntime:
    account_id: int
    email: str
    refresh_token: str


class GmailSyncService:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        gmail_client: GmailClient,
        assistant_bridge: AssistantBridgeClient,
        accounts: Dict[str, AccountRuntime],
    ) -> None:
        self._settings = settings
        self._db = db
        self._gmail_client = gmail_client
        self._assistant_bridge = assistant_bridge
        self._accounts = accounts
        self._accounts_by_id = {account.account_id: account for account in accounts.values()}
        self._locks: Dict[str, threading.Lock] = {
            email: threading.Lock() for email in accounts
        }
        self._auth_backoff = AccountBackoff(base_seconds=300, max_seconds=3600)
        self._stop_event = threading.Event()
        self._ingest_thread: Optional[threading.Thread] = None
        self._dispatch_thread: Optional[threading.Thread] = None

    def start_background_workers(self) -> None:
        if not self._ingest_thread or not self._ingest_thread.is_alive():
            self._ingest_thread = threading.Thread(
                target=self._ingest_loop,
                daemon=True,
                name="gmail-ingest-worker",
            )
            self._ingest_thread.start()
            logger.info("Message ingest worker started")

        if not self._dispatch_thread or not self._dispatch_thread.is_alive():
            self._dispatch_thread = threading.Thread(
                target=self._dispatch_loop,
                daemon=True,
                name="assistant-dispatch-worker",
            )
            self._dispatch_thread.start()
            logger.info("Assistant dispatch worker started")

    def stop(self) -> None:
        self._stop_event.set()

    def handle_pubsub_event(
        self,
        email_address: str,
        history_id: int,
        pubsub_message_id: Optional[str] = None,
    ) -> bool:
        account = self._accounts.get(email_address)
        watch_event_id = self._db.insert_watch_event(
            account_id=account.account_id if account else None,
            email_address=email_address,
            pubsub_message_id=pubsub_message_id,
            history_id=history_id,
        )

        if not account:
            self._db.update_watch_event_status(
                watch_event_id,
                status="ignored_unknown_account",
            )
            logger.warning("No account configured for email: %s", email_address)
            return True

        lock = self._locks[email_address]
        with lock:
            if self._auth_backoff.should_skip(account.account_id):
                until = self._auth_backoff.next_ready_at(account.account_id)
                until_text = until.isoformat() if until else "unknown"
                self._db.update_watch_event_status(
                    watch_event_id,
                    status="skipped_auth_backoff",
                    error=f"auth backoff active until {until_text}",
                )
                logger.warning(
                    "Skipping sync for %s due to auth backoff until %s",
                    account.email,
                    until_text,
                )
                return True

            try:
                return self._sync_account(account, history_id, watch_event_id)
            except Exception as exc:
                error_text = str(exc)
                self._db.update_account_sync_state(
                    account.account_id,
                    sync_status="sync_error",
                    last_sync_error=error_text,
                )
                self._db.update_watch_event_status(
                    watch_event_id,
                    status="sync_error",
                    error=error_text,
                )
                raise

    def _sync_account(
        self,
        account: AccountRuntime,
        history_id: int,
        watch_event_id: int,
    ) -> bool:
        last_history_id, _ = self._db.get_account_state(account.account_id)
        start_history_id = last_history_id or history_id
        sync_run_id = self._db.start_sync_run(
            account_id=account.account_id,
            watch_event_id=watch_event_id,
            start_history_id=start_history_id,
        )
        self._db.update_account_sync_state(
            account.account_id,
            sync_status="syncing",
            last_sync_error=None,
        )

        try:
            label_id = (
                self._settings.gmail_watch_label_ids[0]
                if len(self._settings.gmail_watch_label_ids) == 1
                else None
            )
            response = self._gmail_client.list_history(
                account.refresh_token,
                start_history_id=start_history_id,
                label_id=label_id,
            )
        except Exception as exc:
            retryable = self._gmail_client.is_retryable_error(exc)
            if self._gmail_client.is_auth_error(exc):
                self._record_auth_error(account, "history sync")
                self._db.finish_sync_run(
                    sync_run_id,
                    status="auth_backoff",
                    end_history_id=last_history_id,
                    discovered_message_count=0,
                    queued_message_count=0,
                    error=str(exc),
                )
                self._db.update_watch_event_status(
                    watch_event_id,
                    status="auth_backoff",
                    error=str(exc),
                )
                return True

            if self._gmail_client.is_history_invalid(exc):
                logger.warning(
                    "History invalid for %s. Resetting history cursor.",
                    account.email,
                )
                try:
                    profile = self._gmail_client.get_profile(account.refresh_token)
                except Exception as profile_exc:
                    if self._gmail_client.is_auth_error(profile_exc):
                        self._record_auth_error(account, "profile fetch")
                        self._db.finish_sync_run(
                            sync_run_id,
                            status="auth_backoff",
                            end_history_id=last_history_id,
                            discovered_message_count=0,
                            queued_message_count=0,
                            error=str(profile_exc),
                        )
                        self._db.update_watch_event_status(
                            watch_event_id,
                            status="auth_backoff",
                            error=str(profile_exc),
                        )
                        return True
                    raise

                self._auth_backoff.reset(account.account_id)
                latest_history = int(profile.get("historyId", history_id))
                self._db.update_last_history_id(account.account_id, latest_history)
                self._db.update_account_sync_state(
                    account.account_id,
                    sync_status="recovered_gap",
                    last_sync_error="history invalid; advanced cursor to latest profile history id",
                )
                self._db.finish_sync_run(
                    sync_run_id,
                    status="history_recovered_with_gap",
                    end_history_id=latest_history,
                    discovered_message_count=0,
                    queued_message_count=0,
                    error="history invalid; advanced cursor to latest profile history id",
                )
                self._db.update_watch_event_status(
                    watch_event_id,
                    status="history_recovered_with_gap",
                    error="history invalid; advanced cursor to latest profile history id",
                )
                return True

            sync_status = "sync_retry" if retryable else "sync_failed"
            self._db.finish_sync_run(
                sync_run_id,
                status=sync_status,
                end_history_id=last_history_id,
                discovered_message_count=0,
                queued_message_count=0,
                error=str(exc),
            )
            self._db.update_account_sync_state(
                account.account_id,
                sync_status="sync_error",
                last_sync_error=str(exc),
            )
            self._db.update_watch_event_status(
                watch_event_id,
                status=sync_status,
                error=str(exc),
            )
            return not retryable

        self._auth_backoff.reset(account.account_id)

        message_ids = self._extract_message_ids(response)
        queued_count = self._db.enqueue_message_ingest_jobs(
            account_id=account.account_id,
            watch_event_id=watch_event_id,
            sync_run_id=sync_run_id,
            history_id=history_id,
            message_ids=message_ids,
        )
        latest_history_id = int(response.get("historyId", history_id))
        self._db.update_last_history_id(account.account_id, latest_history_id)
        self._db.finish_sync_run(
            sync_run_id,
            status="completed",
            end_history_id=latest_history_id,
            discovered_message_count=len(message_ids),
            queued_message_count=queued_count,
        )
        self._db.update_account_sync_state(
            account.account_id,
            sync_status="idle",
            last_sync_error=None,
            mark_success=True,
        )
        self._db.update_watch_event_status(
            watch_event_id,
            status="queued_for_ingest",
        )
        logger.info(
            "History sync for %s discovered %d messages and queued %d ingest jobs",
            account.email,
            len(message_ids),
            queued_count,
        )
        return True

    def _extract_message_ids(self, response: Dict) -> List[str]:
        message_ids: List[str] = []
        seen: set[str] = set()
        for history in response.get("history", []):
            for entry in history.get("messagesAdded", []):
                message = entry.get("message", {})
                message_id = message.get("id")
                if message_id and message_id not in seen:
                    seen.add(message_id)
                    message_ids.append(message_id)
        return message_ids

    def _ingest_loop(self) -> None:
        while not self._stop_event.is_set():
            jobs = self._db.lease_message_ingest_jobs(
                self._settings.ingest_worker_batch_size
            )
            if not jobs:
                self._stop_event.wait(self._settings.ingest_worker_poll_seconds)
                continue

            for job in jobs:
                if self._stop_event.is_set():
                    return
                try:
                    self._process_ingest_job(job)
                except Exception:
                    logger.exception(
                        "Unhandled error processing ingest job %s for message %s",
                        job.id,
                        job.gmail_message_id,
                    )

    def _process_ingest_job(self, job: GmailIngestJob) -> None:
        account = self._accounts_by_id.get(job.account_id)
        if not account:
            error_text = f"Missing runtime for account_id={job.account_id}"
            self._db.insert_message_failure(
                account_id=job.account_id,
                gmail_message_id=job.gmail_message_id,
                ingest_job_id=job.id,
                sync_run_id=job.sync_run_id,
                watch_event_id=job.watch_event_id,
                stage="message_fetch",
                error_type="missing_account_runtime",
                error=error_text,
            )
            self._db.fail_message_ingest_job(
                job.id,
                status="failed",
                error_type="missing_account_runtime",
                error=error_text,
            )
            return

        if self._auth_backoff.should_skip(account.account_id):
            until = self._auth_backoff.next_ready_at(account.account_id)
            delay_seconds = self._seconds_until(until) or 300
            self._db.retry_message_ingest_job(
                job.id,
                error_type="auth_backoff",
                error=f"auth backoff active until {until.isoformat() if until else 'unknown'}",
                delay_seconds=delay_seconds,
            )
            return

        try:
            gmail_message = self._gmail_client.get_message(
                account.refresh_token,
                job.gmail_message_id,
            )
        except Exception as exc:
            self._handle_ingest_fetch_error(account, job, exc)
            return

        self._auth_backoff.reset(account.account_id)

        try:
            self._db.upsert_email_message_and_queue_evaluation(
                account_id=account.account_id,
                history_id=job.history_id,
                gmail_message=gmail_message,
                trigger_type="email.ingested",
                trigger_reference=f"gmail_message_ingest_job:{job.id}",
            )
            self._db.mark_message_ingest_job_completed(job.id)
            logger.info(
                "Canonical email stored for %s message %s",
                account.email,
                job.gmail_message_id,
            )
        except Exception as exc:
            error_text = str(exc)
            self._db.insert_message_failure(
                account_id=job.account_id,
                gmail_message_id=job.gmail_message_id,
                ingest_job_id=job.id,
                sync_run_id=job.sync_run_id,
                watch_event_id=job.watch_event_id,
                stage="persist_email_message",
                error_type="persist_failed",
                error=error_text,
            )
            attempts_exhausted = job.attempt_count >= self._settings.ingest_worker_max_attempts
            if attempts_exhausted:
                self._db.fail_message_ingest_job(
                    job.id,
                    status="failed",
                    error_type="persist_failed",
                    error=error_text,
                )
                return

            self._db.retry_message_ingest_job(
                job.id,
                error_type="persist_failed",
                error=error_text,
                delay_seconds=self._retry_delay_seconds(job.attempt_count, base_seconds=10),
            )

    def _handle_ingest_fetch_error(
        self,
        account: AccountRuntime,
        job: GmailIngestJob,
        error: Exception,
    ) -> None:
        error_text = str(error)

        if self._gmail_client.is_auth_error(error):
            self._record_auth_error(account, "message fetch")
            delay_seconds = self._seconds_until(
                self._auth_backoff.next_ready_at(account.account_id)
            ) or 300
            self._db.insert_message_failure(
                account_id=job.account_id,
                gmail_message_id=job.gmail_message_id,
                ingest_job_id=job.id,
                sync_run_id=job.sync_run_id,
                watch_event_id=job.watch_event_id,
                stage="message_fetch",
                error_type="auth_error",
                error=error_text,
            )
            attempts_exhausted = job.attempt_count >= self._settings.ingest_worker_max_attempts
            if attempts_exhausted:
                self._db.fail_message_ingest_job(
                    job.id,
                    status="failed",
                    error_type="auth_error",
                    error=error_text,
                )
                return

            self._db.retry_message_ingest_job(
                job.id,
                error_type="auth_error",
                error=error_text,
                delay_seconds=delay_seconds,
            )
            return

        if self._gmail_client.is_message_not_found(error):
            self._db.insert_message_failure(
                account_id=job.account_id,
                gmail_message_id=job.gmail_message_id,
                ingest_job_id=job.id,
                sync_run_id=job.sync_run_id,
                watch_event_id=job.watch_event_id,
                stage="message_fetch",
                error_type="message_not_found",
                error=error_text,
            )
            self._db.fail_message_ingest_job(
                job.id,
                status="skipped",
                error_type="message_not_found",
                error=error_text,
            )
            logger.warning(
                "Skipping missing Gmail message %s for account %s",
                job.gmail_message_id,
                account.email,
            )
            return

        retryable = self._gmail_client.is_retryable_error(error)
        error_type = "retryable_fetch_error" if retryable else "unexpected_fetch_error"
        self._db.insert_message_failure(
            account_id=job.account_id,
            gmail_message_id=job.gmail_message_id,
            ingest_job_id=job.id,
            sync_run_id=job.sync_run_id,
            watch_event_id=job.watch_event_id,
            stage="message_fetch",
            error_type=error_type,
            error=error_text,
        )
        attempts_exhausted = job.attempt_count >= self._settings.ingest_worker_max_attempts
        if attempts_exhausted and not retryable:
            self._db.fail_message_ingest_job(
                job.id,
                status="failed",
                error_type=error_type,
                error=error_text,
            )
            return

        if attempts_exhausted:
            self._db.fail_message_ingest_job(
                job.id,
                status="failed",
                error_type=error_type,
                error=error_text,
            )
            return

        self._db.retry_message_ingest_job(
            job.id,
            error_type=error_type,
            error=error_text,
            delay_seconds=self._retry_delay_seconds(job.attempt_count, base_seconds=15),
        )

    def _dispatch_loop(self) -> None:
        while not self._stop_event.is_set():
            items = self._db.lease_evaluation_requests(
                self._settings.assistant_dispatch_batch_size
            )
            if not items:
                self._stop_event.wait(self._settings.assistant_dispatch_poll_seconds)
                continue

            for item in items:
                if self._stop_event.is_set():
                    return
                try:
                    self._dispatch_request(item)
                except Exception:
                    logger.exception(
                        "Unhandled error dispatching assistant evaluation request %s",
                        item.request_id,
                    )

    def _dispatch_request(self, item: AssistantEvaluationDispatchItem) -> None:
        response = self._assistant_bridge.dispatch(item)
        if not response.ok:
            error_text = response.error or "assistant dispatch failed"
            self._db.retry_evaluation_request(
                item.request_id,
                error=error_text,
                http_status=response.http_status,
                delay_seconds=self._retry_delay_seconds(item.attempt_count, base_seconds=15),
                attempts_exhausted=(
                    item.attempt_count >= self._settings.assistant_dispatch_max_attempts
                ),
            )
            logger.warning(
                "Assistant dispatch failed for request %s (status=%s): %s",
                item.request_id,
                response.http_status,
                error_text,
            )
            return

        inline_result = self._extract_inline_result(response)
        if inline_result:
            resolved_request_id = self._db.record_assistant_evaluation_result(
                request_id=item.request_id,
                idempotency_key=item.idempotency_key,
                decision=inline_result["decision"],
                importance=inline_result.get("importance"),
                reason_summary=inline_result.get("reason_summary"),
                surface_target=inline_result.get("surface_target"),
                assistant_trace_id=inline_result.get("assistant_trace_id"),
                raw_response=response.body or {},
            )
            logger.info(
                "Assistant request %s completed inline as %s",
                resolved_request_id or item.request_id,
                inline_result["decision"],
            )
            return

        self._db.acknowledge_evaluation_request(
            item.request_id,
            http_status=response.http_status,
        )
        logger.info("Assistant request %s acknowledged", item.request_id)

    def _extract_inline_result(
        self, response: AssistantDispatchResponse
    ) -> Optional[Dict[str, str]]:
        body = response.body or {}
        decision = body.get("decision")
        if not decision:
            return None
        return {
            "decision": str(decision),
            "importance": self._optional_str(body.get("importance")),
            "reason_summary": self._optional_str(body.get("reason_summary")),
            "surface_target": self._optional_str(body.get("surface_target")),
            "assistant_trace_id": self._optional_str(body.get("assistant_trace_id")),
        }

    def _record_auth_error(self, account: AccountRuntime, action: str) -> None:
        delay = self._auth_backoff.record_failure(account.account_id)
        until = self._auth_backoff.next_ready_at(account.account_id)
        until_text = until.isoformat() if until else "unknown"
        error_text = (
            f"Gmail auth error during {action}; backing off for {delay}s (until {until_text}). "
            "Refresh token may be expired or revoked."
        )
        logger.error("%s account=%s", error_text, account.email)
        self._db.update_account_sync_state(
            account.account_id,
            sync_status="auth_backoff",
            last_sync_error=error_text,
        )

    @staticmethod
    def _retry_delay_seconds(attempt_count: int, *, base_seconds: int) -> int:
        delay = base_seconds * (2 ** max(0, attempt_count - 1))
        return min(delay, 3600)

    @staticmethod
    def _seconds_until(ready_at: Optional[dt.datetime]) -> Optional[int]:
        if ready_at is None:
            return None
        now = dt.datetime.now(dt.timezone.utc)
        remaining = int((ready_at - now).total_seconds())
        return max(1, remaining)

    @staticmethod
    def _optional_str(value: object) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None
