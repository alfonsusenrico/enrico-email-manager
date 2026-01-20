import json
import logging
import threading
import time
from typing import Optional

from google.cloud import pubsub_v1

from app.backoff import ExponentialBackoff
from app.gmail_sync import GmailSyncService

logger = logging.getLogger(__name__)


class PubSubWorker:
    def __init__(self, subscription_path: str, sync_service: GmailSyncService) -> None:
        self._subscription_path = subscription_path
        self._sync_service = sync_service
        self._subscriber = pubsub_v1.SubscriberClient()
        self._streaming_future: Optional[pubsub_v1.subscriber.futures.StreamingPullFuture] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._backoff = ExponentialBackoff(base_seconds=2.0, max_seconds=60.0)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("Pub/Sub worker started")

    def stop(self) -> None:
        self._stop_event.set()
        if self._streaming_future:
            self._streaming_future.cancel()
        self._subscriber.close()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._streaming_future = self._subscriber.subscribe(
                    self._subscription_path,
                    callback=self._handle_message,
                )
                self._streaming_future.result()
                self._backoff.reset()
            except Exception:
                if self._stop_event.is_set():
                    return
                delay = self._backoff.next_delay()
                logger.exception(
                    "Pub/Sub streaming stopped; restarting in %.1fs",
                    delay,
                )
                time.sleep(delay)

    def _handle_message(self, message: pubsub_v1.subscriber.message.Message) -> None:
        try:
            payload = json.loads(message.data.decode("utf-8"))
            email_address = payload.get("emailAddress")
            history_id = payload.get("historyId")
            if not email_address or not history_id:
                logger.warning("Invalid Pub/Sub payload: %s", payload)
                message.ack()
                return
            self._sync_service.handle_pubsub_event(email_address, int(history_id))
        except Exception:
            logger.exception("Failed to parse Pub/Sub message")
            message.nack()
            return

        message.ack()
