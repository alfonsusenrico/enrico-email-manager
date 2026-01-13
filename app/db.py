from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Iterable, Optional

import psycopg

from app.config import GmailAccountConfig


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
                return (row[0], row[1])

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

    def notification_exists(self, account_id: int, gmail_message_id: str) -> bool:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select 1
                      from notifications
                     where account_id = %s
                       and gmail_message_id = %s
                     limit 1
                    """,
                    (account_id, gmail_message_id),
                )
                return cur.fetchone() is not None

    def insert_notification_placeholder(
        self, account_id: int, gmail_message_id: str, gmail_thread_id: str, history_id: int
    ) -> Optional[int]:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into notifications (account_id, gmail_message_id, gmail_thread_id, history_id, status)
                    values (%s, %s, %s, %s, 'pending')
                    on conflict (account_id, gmail_message_id) do nothing
                    returning id
                    """,
                    (account_id, gmail_message_id, gmail_thread_id, history_id),
                )
                row = cur.fetchone()
            conn.commit()
        return row[0] if row else None

    def update_notification_details(
        self,
        notification_id: int,
        sender_email: str,
        sender_name: str,
        sender_key: str,
        subject: str,
        summary: str,
        category: str,
        confidence: float,
        telegram_chat_id: Optional[int],
        telegram_message_id: Optional[int],
        status: str,
    ) -> None:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    update notifications
                       set sender_email = %s,
                           sender_name = %s,
                           sender_key = %s,
                           subject = %s,
                           summary = %s,
                           category = %s,
                           confidence = %s,
                           telegram_chat_id = %s,
                           telegram_message_id = %s,
                           status = %s,
                           delivered_at = case when %s then now() else delivered_at end,
                           updated_at = now()
                     where id = %s
                    """,
                    (
                        sender_email,
                        sender_name,
                        sender_key,
                        subject,
                        summary,
                        category,
                        confidence,
                        telegram_chat_id,
                        telegram_message_id,
                        status,
                        status == "notified",
                        notification_id,
                    ),
                )
            conn.commit()

    def get_notification(self, notification_id: int) -> Optional["Notification"]:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select id,
                           account_id,
                           gmail_message_id,
                           gmail_thread_id,
                           sender_email,
                           sender_name,
                           sender_key,
                           subject,
                           summary,
                           category,
                           confidence,
                           status,
                           telegram_chat_id,
                           telegram_message_id
                      from notifications
                     where id = %s
                    """,
                    (notification_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return Notification(
                    id=row[0],
                    account_id=row[1],
                    gmail_message_id=row[2],
                    gmail_thread_id=row[3],
                    sender_email=row[4],
                    sender_name=row[5],
                    sender_key=row[6],
                    subject=row[7],
                    summary=row[8],
                    category=row[9],
                    confidence=row[10],
                    status=row[11],
                    telegram_chat_id=row[12],
                    telegram_message_id=row[13],
                )

    def update_notification_status(self, notification_id: int, status: str) -> None:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    update notifications
                       set status = %s,
                           archived_at = case when %s then now() else archived_at end,
                           trashed_at = case when %s then now() else trashed_at end,
                           updated_at = now()
                     where id = %s
                    """,
                    (
                        status,
                        status == "archived",
                        status == "trashed",
                        notification_id,
                    ),
                )
            conn.commit()

    def update_notification_category(
        self, notification_id: int, category: str, confidence: float
    ) -> None:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    update notifications
                       set category = %s,
                           confidence = %s,
                           updated_at = now()
                     where id = %s
                    """,
                    (category, confidence, notification_id),
                )
            conn.commit()

    def delete_notification(self, notification_id: int) -> None:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("delete from notifications where id = %s", (notification_id,))
            conn.commit()

    def insert_suppression(self, account_id: int, sender_key: str, category: str) -> None:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into suppressions (account_id, sender_key, category)
                    values (%s, %s, %s)
                    on conflict (account_id, sender_key, category) do nothing
                    """,
                    (account_id, sender_key, category),
                )
            conn.commit()

    def is_suppressed(self, account_id: int, sender_key: str, category: str) -> bool:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select 1
                      from suppressions
                     where account_id = %s
                       and sender_key = %s
                       and category = %s
                     limit 1
                    """,
                    (account_id, sender_key, category),
                )
                return cur.fetchone() is not None

    def upsert_usage_daily(
        self,
        account_id: Optional[int],
        model: str,
        usage_date: str,
        input_tokens: int,
        cached_input_tokens: int,
        output_tokens: int,
        input_cost: float,
        cached_input_cost: float,
        output_cost: float,
        total_cost: float,
    ) -> None:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into usage_daily (
                      account_id,
                      model,
                      usage_date,
                      input_tokens,
                      cached_input_tokens,
                      output_tokens,
                      input_cost_usd,
                      cached_input_cost_usd,
                      output_cost_usd,
                      total_cost_usd
                    )
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    on conflict (account_id, model, usage_date) do update
                      set input_tokens = usage_daily.input_tokens + excluded.input_tokens,
                          cached_input_tokens = usage_daily.cached_input_tokens + excluded.cached_input_tokens,
                          output_tokens = usage_daily.output_tokens + excluded.output_tokens,
                          input_cost_usd = usage_daily.input_cost_usd + excluded.input_cost_usd,
                          cached_input_cost_usd = usage_daily.cached_input_cost_usd + excluded.cached_input_cost_usd,
                          output_cost_usd = usage_daily.output_cost_usd + excluded.output_cost_usd,
                          total_cost_usd = usage_daily.total_cost_usd + excluded.total_cost_usd,
                          updated_at = now()
                    """,
                    (
                        account_id,
                        model,
                        usage_date,
                        input_tokens,
                        cached_input_tokens,
                        output_tokens,
                        input_cost,
                        cached_input_cost,
                        output_cost,
                        total_cost,
                    ),
                )
            conn.commit()

    def get_app_state(self, key: str) -> Optional[str]:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("select value from app_state where key = %s", (key,))
                row = cur.fetchone()
                return row[0] if row else None

    def set_app_state(self, key: str, value: str) -> None:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into app_state (key, value, updated_at)
                    values (%s, %s, now())
                    on conflict (key) do update
                      set value = excluded.value,
                          updated_at = now()
                    """,
                    (key, value),
                )
            conn.commit()

    def get_telegram_chat_id(self) -> Optional[int]:
        value = self.get_app_state("telegram_chat_id")
        return int(value) if value else None

    def set_telegram_chat_id(self, chat_id: int) -> None:
        self.set_app_state("telegram_chat_id", str(chat_id))


@dataclass(frozen=True)
class Notification:
    id: int
    account_id: int
    gmail_message_id: str
    gmail_thread_id: Optional[str]
    sender_email: Optional[str]
    sender_name: Optional[str]
    sender_key: Optional[str]
    subject: Optional[str]
    summary: Optional[str]
    category: Optional[str]
    confidence: Optional[float]
    status: str
    telegram_chat_id: Optional[int]
    telegram_message_id: Optional[int]
