"""Local ULID minting for observer event and lifecycle ids.

A ULID is 48 bits of millisecond timestamp + 80 bits of randomness, rendered as
26 Crockford Base32 characters. Because the timestamp is the high-order prefix,
lexicographic string order equals creation-time order -- which is what lets the
event substrate merge per-lifecycle logs chronologically.

We only ever mint and string-compare ids (never parse foreign ULIDs or extract
their timestamps), so this stays a tiny, dependency-free generator. It is also
deliberately *stateless*: `new_ulid` is called from both the request thread and
the lifecycle heartbeat thread, and `os.urandom` / `time.time_ns` are
thread-safe, so no lock is needed. Within a single millisecond two ids share no
ordering guarantee, which is fine: order *within* one lifecycle is the JSONL
append order, and the id is only the cross-lifecycle merge/unique key.

(stdlib `uuid.uuid7` would supersede this once the Python floor reaches 3.14;
keeping minting in one module makes that a one-function swap.)
"""

from __future__ import annotations

import os
import time

# Crockford Base32: digits + uppercase minus I, L, O, U (ambiguity-free).
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ULID_LEN = 26


def new_ulid(*, now_ms: int | None = None) -> str:
    """Mint a 26-char Crockford Base32 ULID; lexicographically time-sortable.

    `now_ms` overrides the timestamp source for deterministic tests.
    """
    millis = now_ms if now_ms is not None else time.time_ns() // 1_000_000
    value = (millis << 80) | int.from_bytes(os.urandom(10), "big")
    chars = ["0"] * _ULID_LEN
    for index in range(_ULID_LEN - 1, -1, -1):
        chars[index] = _CROCKFORD[value & 0x1F]
        value >>= 5
    return "".join(chars)
