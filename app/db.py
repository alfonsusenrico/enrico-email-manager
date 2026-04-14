from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Sequence

import psycopg
from psycopg.types.json import Jsonb

from app.config import GmailAccountConfig
from app.gmail_client import GmailMessage


@dataclass(frozen=True)
class GmailIngestJob:
    id: int
    account_id: int
    gmail_message_id: str
    history_id: Optional[int]
    watch_event_id: Optional[int]
    sync_run_id: Optional[int]
    attempt_count: int


@dataclass(frozen=True)
class AssistantEvaluationDispatchItem:
    request_id: int
    email_message_id: int
    account_id: int
    account_email: str
    gmail_message_id: str
    gmail_thread_id: str
    history_id: Optional[int]
    message_internal_at: Optional[datetime]
    sender_name: Optional[str]
    sender_email: Optional[str]
    sender_domain: Optional[str]
    to_recipients: List[Dict[str, str]]
    cc_recipients: List[Dict[str, str]]
    subject: Optional[str]
    snippet: Optional[str]
    normalized_body_text: Optional[str]
    labels_json: List[str]
    headers_json: Dict[str, str]
    raw_size_bytes: Optional[int]
    trigger_type: str
    trigger_reference: Optional[str]
    idempotency_key: str
    payload_version: str
    attempt_count: int


class Database:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def connect(self):
        return psycopg.connect(self._database_url)

    def ensure_accounts(
        self, accounts: Iterable[GmailAccountConfig], watch_label_ids: Iterable[str]
    ) -> Dict[str, int]:
        ids: Dict[str, int] = {}
        with self.connect() as conn:
            with conn.cursor() as cur:
                for account in accounts:
                    cur.execute(
                        """
                        insert into gmail_accounts (email, watch_label_ids)
                        values (%s, %s)
                        on conflict (email) do update
                          set watch_label_ids = excluded.watch_label_ids,
                              updated_at = now()
                        returning id
                        """,
                        (account.email, list(watch_label_ids)),
                    )
                    row = cur.fetchone()
                    if row:
                        ids[account.email] = row[0]
            conn.commit()
        return ids

    def get_account_state(self, account_id: int) -> tuple[Optional[int], Optional[datetime]]:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "select last_history_id, watch_expiration from gmail_accounts where id = %s",
                    (account_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None, None
                return row[0], row[1]

    def update_watch_info(
        self, account_id: int, history_id: Optional[int], expiration: Optional[datetime]
    ) -> None:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    update gmail_accounts
                       set watch_expiration = %s,
                           last_history_id = coalesce(last_history_id, %s),
                           last_watch_error = null,
                           updated_at = now()
                     where id = %s
                    """,
                    (expiration, history_id, account_id),
                )
            conn.commit()

    def update_last_history_id(self, account_id: int, history_id: int) -> None:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    update gmail_accounts
                       set last_history_id = %s,
                           updated_at = now()
                     where id = %s
                    """,
                    (history_id, account_id),
                )
            conn.commit()

    def update_account_sync_state(
        self,
        account_id: int,
        *,
        sync_status: str,
        last_sync_error: Optional[str] = None,
        mark_success: bool = False,
    ) -> None:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    update gmail_accounts
                       set sync_status = %s,
                           last_sync_error = %s,
                           last_successful_sync_at = case when %s then now() else last_successful_sync_at end,
                           updated_at = now()
                     where id = %s
                    """,
                    (sync_status, last_sync_error, mark_success, account_id),
                )
            conn.commit()

    def update_account_watch_error(self, account_id: int, error: str) -> None:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    update gmail_accounts
                       set last_watch_error = %s,
                           updated_at = now()
                     where id = %s
                    """,
                    (error, account_id),
                )
            conn.commit()

    def insert_watch_event(
        self,
        *,
        account_id: Optional[int],
        email_address: str,
        pubsub_message_id: Optional[str],
        history_id: int,
    ) -> int:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into gmail_watch_events (
                      account_id,
                      email_address,
                      pubsub_message_id,
                      gmail_history_id,
                      status
                    )
                    values (%s, %s, %s, %s, 'received')
                    returning id
                    """,
                    (account_id, email_address, pubsub_message_id, history_id),
                )
                row = cur.fetchone()
            conn.commit()
        if not row:
            raise RuntimeError("Failed to insert gmail_watch_event")
        return int(row[0])

    def update_watch_event_status(
        self,
        watch_event_id: int,
        *,
        status: str,
        error: Optional[str] = None,
        mark_processed: bool = True,
    ) -> None:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    update gmail_watch_events
                       set status = %s,
                           error = %s,
                           processed_at = case when %s then now() else processed_at end
                     where id = %s
                    """,
                    (status, error, mark_processed, watch_event_id),
                )
            conn.commit()

    def start_sync_run(
        self,
        *,
        account_id: int,
        watch_event_id: Optional[int],
        start_history_id: int,
    ) -> int:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into gmail_sync_runs (
                      account_id,
                      watch_event_id,
                      start_history_id,
                      status
                    )
                    values (%s, %s, %s, 'running')
                    returning id
                    """,
                    (account_id, watch_event_id, start_history_id),
                )
                row = cur.fetchone()
            conn.commit()
        if not row:
            raise RuntimeError("Failed to create sync run")
        return int(row[0])

    def finish_sync_run(
        self,
        sync_run_id: int,
        *,
        status: str,
        end_history_id: Optional[int],
        discovered_message_count: int,
        queued_message_count: int,
        error: Optional[str] = None,
    ) -> None:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    update gmail_sync_runs
                       set status = %s,
                           end_history_id = %s,
                           discovered_message_count = %s,
                           queued_message_count = %s,
                           error = %s,
                           finished_at = now()
                     where id = %s
                    """,
                    (
                        status,
                        end_history_id,
                        discovered_message_count,
                        queued_message_count,
                        error,
                        sync_run_id,
                    ),
                )
            conn.commit()

    def enqueue_message_ingest_jobs(
        self,
        *,
        account_id: int,
        watch_event_id: Optional[int],
        sync_run_id: Optional[int],
        history_id: int,
        message_ids: Sequence[str],
    ) -> int:
        if not message_ids:
            return 0

        inserted = 0
        with self.connect() as conn:
            with conn.cursor() as cur:
                for message_id in message_ids:
                    cur.execute(
                        """
                        insert into gmail_message_ingest_jobs (
                          account_id,
                          watch_event_id,
                          sync_run_id,
                          gmail_message_id,
                          history_id,
                          status
                        )
                        values (%s, %s, %s, %s, %s, 'queued')
                        on conflict (account_id, gmail_message_id) do nothing
                        returning id
                        """,
                        (account_id, watch_event_id, sync_run_id, message_id, history_id),
                    )
                    if cur.fetchone():
                        inserted += 1
            conn.commit()
        return inserted

    def lease_message_ingest_jobs(self, limit: int) -> List[GmailIngestJob]:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select id
                      from gmail_message_ingest_jobs
                     where (
                            (status = 'queued' and next_attempt_at <= now())
                         or (status = 'processing' and updated_at <= now() - interval '5 minutes')
                     )
                     order by next_attempt_at asc, created_at asc
                     for update skip locked
                     limit %s
                    """,
                    (limit,),
                )
                ids = [row[0] for row in cur.fetchall()]
                if not ids:
                    conn.commit()
                    return []

                cur.execute(
                    """
                    update gmail_message_ingest_jobs
                       set status = 'processing',
                           attempt_count = attempt_count + 1,
                           updated_at = now()
                     where id = any(%s)
                    returning id,
                              account_id,
                              gmail_message_id,
                              history_id,
                              watch_event_id,
                              sync_run_id,
                              attempt_count
                    """,
                    (ids,),
                )
                rows = cur.fetchall()
            conn.commit()
        return [
            GmailIngestJob(
                id=row[0],
                account_id=row[1],
                gmail_message_id=row[2],
                history_id=row[3],
                watch_event_id=row[4],
                sync_run_id=row[5],
                attempt_count=row[6],
            )
            for row in rows
        ]

    def mark_message_ingest_job_completed(self, job_id: int) -> None:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    update gmail_message_ingest_jobs
                       set status = 'completed',
                           last_error_type = null,
                           last_error = null,
                           completed_at = now(),
                           updated_at = now()
                     where id = %s
                    """,
                    (job_id,),
                )
            conn.commit()

    def retry_message_ingest_job(
        self,
        job_id: int,
        *,
        error_type: str,
        error: str,
        delay_seconds: int,
    ) -> None:
        next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    update gmail_message_ingest_jobs
                       set status = 'queued',
                           last_error_type = %s,
                           last_error = %s,
                           next_attempt_at = %s,
                           updated_at = now()
                     where id = %s
                    """,
                    (error_type, error, next_attempt_at, job_id),
                )
            conn.commit()

    def fail_message_ingest_job(
        self,
        job_id: int,
        *,
        status: str,
        error_type: str,
        error: str,
    ) -> None:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    update gmail_message_ingest_jobs
                       set status = %s,
                           last_error_type = %s,
                           last_error = %s,
                           completed_at = now(),
                           updated_at = now()
                     where id = %s
                    """,
                    (status, error_type, error, job_id),
                )
            conn.commit()

    def insert_message_failure(
        self,
        *,
        account_id: int,
        gmail_message_id: str,
        ingest_job_id: Optional[int],
        sync_run_id: Optional[int],
        watch_event_id: Optional[int],
        stage: str,
        error_type: str,
        error: str,
    ) -> None:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into gmail_message_failures (
                      account_id,
                      gmail_message_id,
                      ingest_job_id,
                      sync_run_id,
                      watch_event_id,
                      stage,
                      error_type,
                      error
                    )
                    values (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        account_id,
                        gmail_message_id,
                        ingest_job_id,
                        sync_run_id,
                        watch_event_id,
                        stage,
                        error_type,
                        error,
                    ),
                )
            conn.commit()

    def upsert_email_message_and_queue_evaluation(
        self,
        *,
        account_id: int,
        history_id: Optional[int],
        gmail_message: GmailMessage,
        trigger_type: str,
        trigger_reference: Optional[str],
        queue_evaluation: bool = True,
    ) -> tuple[int, Optional[int]]:
        idempotency_key = f"{trigger_type}:{account_id}:{gmail_message.message_id}"
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into email_messages (
                      account_id,
                      gmail_message_id,
                      gmail_thread_id,
                      history_id,
                      message_internal_at,
                      sender_name,
                      sender_email,
                      sender_domain,
                      to_recipients,
                      cc_recipients,
                      subject,
                      snippet,
                      normalized_body_text,
                      labels_json,
                      headers_json,
                      raw_size_bytes,
                      ingest_status,
                      ingested_at,
                      updated_at
                    )
                    values (
                      %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                      'ingested', now(), now()
                    )
                    on conflict (account_id, gmail_message_id) do update
                      set gmail_thread_id = excluded.gmail_thread_id,
                          history_id = excluded.history_id,
                          message_internal_at = excluded.message_internal_at,
                          sender_name = excluded.sender_name,
                          sender_email = excluded.sender_email,
                          sender_domain = excluded.sender_domain,
                          to_recipients = excluded.to_recipients,
                          cc_recipients = excluded.cc_recipients,
                          subject = excluded.subject,
                          snippet = excluded.snippet,
                          normalized_body_text = excluded.normalized_body_text,
                          labels_json = excluded.labels_json,
                          headers_json = excluded.headers_json,
                          raw_size_bytes = excluded.raw_size_bytes,
                          ingest_status = 'ingested',
                          updated_at = now()
                    returning id
                    """,
                    (
                        account_id,
                        gmail_message.message_id,
                        gmail_message.thread_id,
                        history_id,
                        gmail_message.message_internal_at,
                        gmail_message.sender_name,
                        gmail_message.sender_email,
                        gmail_message.sender_domain,
                        Jsonb(gmail_message.to_recipients),
                        Jsonb(gmail_message.cc_recipients),
                        gmail_message.subject,
                        gmail_message.snippet,
                        gmail_message.body_text,
                        Jsonb(gmail_message.label_ids),
                        Jsonb(gmail_message.headers),
                        gmail_message.raw_size_bytes,
                    ),
                )
                email_row = cur.fetchone()
                if not email_row:
                    raise RuntimeError("Failed to upsert canonical email message")
                email_message_id = int(email_row[0])

                request_row = None
                if queue_evaluation:
                    cur.execute(
                        """
                        insert into assistant_evaluation_requests (
                          email_message_id,
                          trigger_type,
                          trigger_reference,
                          idempotency_key,
                          payload_version,
                          status,
                          next_attempt_at,
                          queued_at,
                          updated_at
                        )
                        values (%s, %s, %s, %s, 'v1', 'queued', now(), now(), now())
                        on conflict (idempotency_key) do nothing
                        returning id
                        """,
                        (
                            email_message_id,
                            trigger_type,
                            trigger_reference,
                            idempotency_key,
                        ),
                    )
                    request_row = cur.fetchone()
            conn.commit()

        return email_message_id, int(request_row[0]) if request_row else None

    def lease_evaluation_requests(self, limit: int) -> List[AssistantEvaluationDispatchItem]:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select id
                      from assistant_evaluation_requests
                     where (
                            (status = 'queued' and next_attempt_at <= now())
                         or (status = 'dispatching' and updated_at <= now() - interval '5 minutes')
                     )
                     order by next_attempt_at asc, queued_at asc
                     for update skip locked
                     limit %s
                    """,
                    (limit,),
                )
                ids = [row[0] for row in cur.fetchall()]
                if not ids:
                    conn.commit()
                    return []

                cur.execute(
                    """
                    update assistant_evaluation_requests
                       set status = 'dispatching',
                           attempt_count = attempt_count + 1,
                           dispatched_at = now(),
                           updated_at = now()
                     where id = any(%s)
                    returning id,
                              email_message_id,
                              trigger_type,
                              trigger_reference,
                              idempotency_key,
                              payload_version,
                              attempt_count
                    """,
                    (ids,),
                )
                request_rows = cur.fetchall()
                request_map = {row[0]: row for row in request_rows}

                cur.execute(
                    """
                    select aer.id,
                           em.id,
                           em.account_id,
                           ga.email,
                           em.gmail_message_id,
                           em.gmail_thread_id,
                           em.history_id,
                           em.message_internal_at,
                           em.sender_name,
                           em.sender_email,
                           em.sender_domain,
                           em.to_recipients,
                           em.cc_recipients,
                           em.subject,
                           em.snippet,
                           em.normalized_body_text,
                           em.labels_json,
                           em.headers_json,
                           em.raw_size_bytes
                      from assistant_evaluation_requests aer
                      join email_messages em on em.id = aer.email_message_id
                      join gmail_accounts ga on ga.id = em.account_id
                     where aer.id = any(%s)
                    """,
                    (ids,),
                )
                payload_rows = cur.fetchall()
            conn.commit()

        items: List[AssistantEvaluationDispatchItem] = []
        for row in payload_rows:
            request_row = request_map[row[0]]
            items.append(
                AssistantEvaluationDispatchItem(
                    request_id=row[0],
                    email_message_id=row[1],
                    account_id=row[2],
                    account_email=row[3],
                    gmail_message_id=row[4],
                    gmail_thread_id=row[5],
                    history_id=row[6],
                    message_internal_at=row[7],
                    sender_name=row[8],
                    sender_email=row[9],
                    sender_domain=row[10],
                    to_recipients=list(row[11] or []),
                    cc_recipients=list(row[12] or []),
                    subject=row[13],
                    snippet=row[14],
                    normalized_body_text=row[15],
                    labels_json=list(row[16] or []),
                    headers_json=dict(row[17] or {}),
                    raw_size_bytes=row[18],
                    trigger_type=request_row[2],
                    trigger_reference=request_row[3],
                    idempotency_key=request_row[4],
                    payload_version=request_row[5],
                    attempt_count=request_row[6],
                )
            )
        return items

    def retry_evaluation_request(
        self,
        request_id: int,
        *,
        error: str,
        http_status: Optional[int],
        delay_seconds: int,
        attempts_exhausted: bool,
    ) -> None:
        next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
        status = "failed" if attempts_exhausted else "queued"
        completed_at = datetime.now(timezone.utc) if attempts_exhausted else None
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    update assistant_evaluation_requests
                       set status = %s,
                           last_http_status = %s,
                           last_error = %s,
                           last_error_at = now(),
                           next_attempt_at = case when %s then next_attempt_at else %s end,
                           completed_at = %s,
                           updated_at = now()
                     where id = %s
                    """,
                    (
                        status,
                        http_status,
                        error,
                        attempts_exhausted,
                        next_attempt_at,
                        completed_at,
                        request_id,
                    ),
                )
            conn.commit()

    def acknowledge_evaluation_request(
        self,
        request_id: int,
        *,
        http_status: Optional[int],
    ) -> None:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    update assistant_evaluation_requests
                       set status = 'acknowledged',
                           acked_at = now(),
                           last_http_status = %s,
                           last_error = null,
                           last_error_at = null,
                           updated_at = now()
                     where id = %s
                       and status <> 'completed'
                    """,
                    (http_status, request_id),
                )
            conn.commit()

    def record_assistant_evaluation_result(
        self,
        *,
        request_id: Optional[int],
        idempotency_key: Optional[str],
        decision: str,
        importance: Optional[str],
        reason_summary: Optional[str],
        surface_target: Optional[str],
        assistant_trace_id: Optional[str],
        raw_response: Dict,
    ) -> Optional[int]:
        with self.connect() as conn:
            with conn.cursor() as cur:
                if request_id is not None:
                    cur.execute(
                        "select id from assistant_evaluation_requests where id = %s",
                        (request_id,),
                    )
                elif idempotency_key:
                    cur.execute(
                        """
                        select id
                          from assistant_evaluation_requests
                         where idempotency_key = %s
                        """,
                        (idempotency_key,),
                    )
                else:
                    raise ValueError("request_id or idempotency_key is required")

                row = cur.fetchone()
                if not row:
                    conn.commit()
                    return None

                resolved_request_id = int(row[0])
                cur.execute(
                    """
                    insert into assistant_evaluation_results (
                      request_id,
                      decision,
                      importance,
                      reason_summary,
                      surface_target,
                      assistant_trace_id,
                      raw_response_json
                    )
                    values (%s, %s, %s, %s, %s, %s, %s)
                    on conflict (request_id) do update
                      set decision = excluded.decision,
                          importance = excluded.importance,
                          reason_summary = excluded.reason_summary,
                          surface_target = excluded.surface_target,
                          assistant_trace_id = excluded.assistant_trace_id,
                          raw_response_json = excluded.raw_response_json
                    """,
                    (
                        resolved_request_id,
                        decision,
                        importance,
                        reason_summary,
                        surface_target,
                        assistant_trace_id,
                        Jsonb(raw_response),
                    ),
                )
                cur.execute(
                    """
                    update assistant_evaluation_requests
                       set status = 'completed',
                           acked_at = coalesce(acked_at, now()),
                           completed_at = now(),
                           last_error = null,
                           last_error_at = null,
                           updated_at = now()
                     where id = %s
                    """,
                    (resolved_request_id,),
                )
            conn.commit()
        return resolved_request_id

    def queue_re_evaluation_requests(
        self,
        *,
        email_message_ids: Sequence[int],
        trigger_type: str,
        trigger_reference: str,
    ) -> int:
        if not email_message_ids:
            return 0

        inserted = 0
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select id, account_id, gmail_message_id
                      from email_messages
                     where id = any(%s)
                    """,
                    (list(email_message_ids),),
                )
                rows = cur.fetchall()
                for row in rows:
                    email_message_id = int(row[0])
                    account_id = int(row[1])
                    gmail_message_id = str(row[2])
                    idempotency_key = (
                        f"{trigger_type}:{trigger_reference}:{account_id}:{gmail_message_id}"
                    )
                    cur.execute(
                        """
                        insert into assistant_evaluation_requests (
                          email_message_id,
                          trigger_type,
                          trigger_reference,
                          idempotency_key,
                          payload_version,
                          status,
                          next_attempt_at,
                          queued_at,
                          updated_at
                        )
                        values (%s, %s, %s, %s, 'v1', 'queued', now(), now(), now())
                        on conflict (idempotency_key) do nothing
                        returning id
                        """,
                        (
                            email_message_id,
                            trigger_type,
                            trigger_reference,
                            idempotency_key,
                        ),
                    )
                    if cur.fetchone():
                        inserted += 1
            conn.commit()
        return inserted

    def queue_re_evaluation_requests_by_gmail_ids(
        self,
        *,
        gmail_message_ids: Sequence[str],
        trigger_type: str,
        trigger_reference: str,
    ) -> int:
        if not gmail_message_ids:
            return 0

        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select id
                      from email_messages
                     where gmail_message_id = any(%s)
                    """,
                    (list(gmail_message_ids),),
                )
                email_message_ids = [int(row[0]) for row in cur.fetchall()]
            conn.commit()
        return self.queue_re_evaluation_requests(
            email_message_ids=email_message_ids,
            trigger_type=trigger_type,
            trigger_reference=trigger_reference,
        )
