"""The invoice app's rate limits (R-SEC-08, R-KUD-24).

The mechanism is core.web.ratelimit; this file says how hard each invoice
endpoint is limited. ``/submit`` and the bank-change endpoint decrypt bank
fields and send mail, and submitting is already idempotent, so this is not
about duplicate invoices but about somebody hammering them.
"""

from __future__ import annotations

from core.web.ratelimit import RateLimiter

__all__ = ["RateLimiter", "bank_limiter", "kudos_refresh_limiter", "submit_limiter"]

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
