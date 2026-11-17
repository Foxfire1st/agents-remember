"""Read stable pointer policy together with its separately generated wording."""

from __future__ import annotations

import json
from pathlib import Path


def wording_policy() -> tuple[dict, dict, dict, tuple]:
    """Project exception identities into the detector's current wording vocabulary."""
    root = Path(__file__).resolve().parents[4] / "skills/l-01-agent-lifecycles"
    policy = json.loads((root / "pointer-actions.json").read_text())
    wording = json.loads((root / "pointer-actions-derived.json").read_text())["actions"]
    distinct = {
        target: (
            frozenset(row["wording"] for action in entry["actions"] for row in wording[action]),
            entry["reason"],
        )
        for target, entry in policy["distinctActions"].items()
    }
    sources: dict[str, dict[str, frozenset[str]]] = {}
    for target, actions in policy["actionSources"].items():
        by_word: dict[str, set[str]] = {}
        for action, admitted_sources in actions.items():
            for row in wording[action]:
                by_word.setdefault(row["wording"], set()).update(admitted_sources)
        sources[target] = {word: frozenset(allowed) for word, allowed in by_word.items()}
    multiple = {
        target: frozenset(
            (entry["source"], row["wording"])
            for entry in entries
            for row in wording[entry["action"]]
            if row["source"] == entry["source"]
        )
        for target, entries in policy["multiLinkSentences"].items()
    }
    exclusive = tuple(
        frozenset(row["wording"] for action in group for row in wording[action])
        for group in policy["exclusive"]
    ) + tuple(frozenset(row["wording"] for row in rows) for rows in wording.values())
    return distinct, sources, multiple, exclusive
