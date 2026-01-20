import datetime as dt
import random
from typing import Dict, Hashable, Optional


class ExponentialBackoff:
    def __init__(
        self,
        *,
        base_seconds: float = 1.0,
        max_seconds: float = 60.0,
        jitter: float = 0.2,
    ) -> None:
        self._base_seconds = base_seconds
        self._max_seconds = max_seconds
        self._jitter = jitter
        self._attempts = 0

    def next_delay(self) -> float:
        self._attempts += 1
        delay = min(self._max_seconds, self._base_seconds * (2 ** (self._attempts - 1)))
        if self._jitter:
            jitter_span = delay * self._jitter
            delay = max(0.0, delay + random.uniform(-jitter_span, jitter_span))
        return delay

    def reset(self) -> None:
        self._attempts = 0


class AccountBackoff:
    def __init__(self, *, base_seconds: int = 300, max_seconds: int = 3600) -> None:
        self._base_seconds = base_seconds
        self._max_seconds = max_seconds
        self._attempts: Dict[Hashable, int] = {}
        self._until: Dict[Hashable, dt.datetime] = {}

    def should_skip(self, key: Hashable, now: Optional[dt.datetime] = None) -> bool:
        if now is None:
            now = dt.datetime.now(dt.timezone.utc)
        until = self._until.get(key)
        return bool(until and now < until)

    def record_failure(self, key: Hashable, now: Optional[dt.datetime] = None) -> int:
        if now is None:
            now = dt.datetime.now(dt.timezone.utc)
        attempts = self._attempts.get(key, 0) + 1
        self._attempts[key] = attempts
        delay = min(self._max_seconds, self._base_seconds * (2 ** (attempts - 1)))
        self._until[key] = now + dt.timedelta(seconds=delay)
        return delay

    def reset(self, key: Hashable) -> None:
        self._attempts.pop(key, None)
        self._until.pop(key, None)

    def next_ready_at(self, key: Hashable) -> Optional[dt.datetime]:
        return self._until.get(key)
