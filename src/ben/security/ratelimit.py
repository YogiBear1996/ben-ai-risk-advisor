"""Per-user token-bucket rate limiter (in-process; use Redis if you run several replicas)."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable


class RateLimiter:
    def __init__(
        self, per_minute: int, burst: int, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.rate = per_minute / 60.0
        self.capacity = float(max(burst, 1))
        self.enabled = per_minute > 0
        self._clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        if not self.enabled:
            return True
        with self._lock:
            now = self._clock()
            tokens, last = self._buckets.get(key, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - last) * self.rate)
            allowed = tokens >= 1.0
            self._buckets[key] = (tokens - 1.0 if allowed else tokens, now)
            if len(self._buckets) > 10_000:  # drop idle buckets
                cutoff = now - self.capacity / max(self.rate, 1e-9)
                self._buckets = {k: v for k, v in self._buckets.items() if v[1] >= cutoff}
            return allowed
