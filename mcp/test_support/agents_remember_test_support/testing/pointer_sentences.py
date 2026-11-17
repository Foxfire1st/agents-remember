"""Parse pointer targets and sentence wording without loading admission policy."""

from __future__ import annotations

import os
import re

from agents_remember_test_support.testing.manifest_step_derivation import action_of

_SERVED = "l-01-agent-lifecycles/"
_SENTENCE_END = re.compile(r"[.!?](?:\*\*)?(?:\s|$)")


def resolve_target(raw: str, source: str) -> str | None:
    raw = raw.strip("<>")
    if raw.startswith("{{detail:"):
        return raw[len("{{detail:") :].partition("}}")[0]
    if raw.startswith("skill://"):
        return raw.split(_SERVED, 1)[-1].partition("#")[0]
    if raw.startswith("#"):
        return source
    if "://" in raw or raw.startswith("mailto:"):
        return None
    return os.path.normpath(os.path.join(os.path.dirname(source), raw.partition("#")[0]))


def _norm(text: str) -> str:
    text = re.sub(r"\[((?:[^\[\]]|\[[^\[\]]*\])*)\]\([^)]*\)", r"\1", text)
    return re.sub(r"\s+", " ", text).strip(" *_-:;,").lower()


def action(line: str, start: int, end: int) -> str:
    if line.lstrip().startswith("|"):
        return "row: " + _norm(line.strip().strip("|").split("|", 1)[0])
    action, head = action_of(line, start)
    if action is not None:
        return action
    head = _norm(head)
    if head:
        return head
    tail = line[end:]
    stop = _SENTENCE_END.search(tail)
    return "opens: " + _norm(tail[: stop.start() if stop else len(tail)])
