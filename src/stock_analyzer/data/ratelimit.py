"""Token-bucket rate limiter. CLAUDE.md §3.1 rule 5: all providers go through one."""

from __future__ import annotations

import threading
import time


class TokenBucket:
    def __init__(self, rate_per_second: float, capacity: int) -> None:
        if rate_per_second <= 0 or capacity <= 0:
            raise ValueError("rate_per_second and capacity must be positive")
        self._rate = rate_per_second
        self._capacity = float(capacity)
        self._tokens = float(capacity)
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """Block until a token is available, then consume it."""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
            self._last_refill = now
            if self._tokens < 1.0:
                wait_seconds = (1.0 - self._tokens) / self._rate
                time.sleep(wait_seconds)
                self._tokens = 0.0
                self._last_refill = time.monotonic()
            else:
                self._tokens -= 1.0
