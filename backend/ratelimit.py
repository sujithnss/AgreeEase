"""
Lightweight in-process rate limiting for the WhatsApp webhook. The main
cost risk on that endpoint isn't bandwidth -- it's that every accepted
message triggers a paid Groq API call (see extraction.py), so a spammy or
buggy sender can run up a real bill fast.

In-memory, not Redis -- consistent with keeping dependencies minimal (see
CLAUDE.md), and fine as long as this runs as a single process (the
Dockerfile's `uvicorn main:app` has no --workers flag). Resets on
restart, which just means the limit re-opens, not a correctness problem.

Two limits apply together:
  - per phone number: catches one number looping/spamming (bug or abuse
    from a number that otherwise passes signature verification)
  - global: catches a burst across many numbers at once
"""
import os
import time
from collections import defaultdict, deque

# `or "10"` (not a plain default= on .get()) so that a blank-but-present
# env var (e.g. copied from .env.example and left unfilled) falls back
# the same way an unset one does, instead of int("") raising.
PER_PHONE_LIMIT = int(os.environ.get("WEBHOOK_RATE_LIMIT_PER_PHONE") or "10")
PER_PHONE_WINDOW_SECONDS = 60
GLOBAL_LIMIT = int(os.environ.get("WEBHOOK_RATE_LIMIT_GLOBAL") or "60")
GLOBAL_WINDOW_SECONDS = 60

_per_phone_hits = defaultdict(deque)
_global_hits = deque()


def _prune(hits: deque, window_seconds: int, now: float) -> None:
    while hits and now - hits[0] > window_seconds:
        hits.popleft()


def allow_webhook_message(phone: str) -> bool:
    """True if this webhook message should be processed, False if it
    should be dropped for exceeding the per-phone or global limit. Only
    records the hit when allowed, so a rejected burst doesn't also count
    towards itself and self-perpetuate the block."""
    now = time.monotonic()
    _prune(_global_hits, GLOBAL_WINDOW_SECONDS, now)
    _prune(_per_phone_hits[phone], PER_PHONE_WINDOW_SECONDS, now)

    if len(_global_hits) >= GLOBAL_LIMIT or len(_per_phone_hits[phone]) >= PER_PHONE_LIMIT:
        return False

    _global_hits.append(now)
    _per_phone_hits[phone].append(now)
    return True
