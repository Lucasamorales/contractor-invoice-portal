"""Per-person rate limiting for the two endpoints that matter (R-SEC-08).

``/submit`` and the bank-change endpoint. Submitting is already idempotent, so
this is not about duplicate invoices; it is about somebody hammering the
endpoint that decrypts bank fields and sends mail.

**In-process, deliberately.** Cloud Run can run more than one instance, so the
limit is per instance rather than per service, and the real ceiling is the
limit times the instance count. For sixteen people on a service that scales to
zero that is almost always one instance, and the failure mode of being wrong is
that a few more requests get through — not that anything is lost or leaked.

A shared counter would mean a table and a write on every request, which is a
worse trade for this application. If the service ever runs at scale, this is
the thing to replace, and this comment is the reason it looks like this.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, status


class RateLimiter:
    """A fixed window per key. Not a token bucket: nothing here needs one."""

    def __init__(self, limit: int, window_seconds: float) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, *, now: float | None = None) -> None:
        """Record an attempt, or raise 429 if the key is over its limit."""
        moment = now if now is not None else time.monotonic()
        with self._lock:
            hits = self._hits[key]
            cutoff = moment - self.window
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= self.limit:
                retry_after = max(1, int(hits[0] + self.window - moment) + 1)
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="That was a lot of attempts. Wait a moment and try again.",
                    headers={"Retry-After": str(retry_after)},
                )
            hits.append(moment)

    def reset(self) -> None:
        """Test helper. Never called by the application."""
        with self._lock:
            self._hits.clear()


# One invoice a month means ten submissions in five minutes is already someone
# retrying hard rather than working. Bank changes are rarer still, and each one
# decrypts fields and queues mail to two people.
submit_limiter = RateLimiter(limit=10, window_seconds=300)
bank_limiter = RateLimiter(limit=5, window_seconds=300)
# The kudos refresh button (R-KUD-24). Google's read quota is nowhere near a
# concern at this size; what this stops is a stuck finger turning one useful
# press into fifty requests to somebody else's API. Concurrency is handled
# separately, by the advisory lock in kudos_sync.
kudos_refresh_limiter = RateLimiter(limit=6, window_seconds=300)
