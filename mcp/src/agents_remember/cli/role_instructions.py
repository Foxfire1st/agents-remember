"""Plain-text user instructions at the launch boundary; never a file or skill loader."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any


def instruction_metadata(record: dict[str, Any]) -> dict[str, Any]:
    return {
        **record,
        "blocks": [
            {k: v for k, v in block.items() if k != "deliveredText"} for block in record["blocks"]
        ],
    }


def launch_choice_metadata(choices: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(choices)
    notes = result.get("notes", {})
    text = notes.pop("value", None)
    if isinstance(text, str):
        notes["digest"] = "sha256:" + hashlib.sha256(text.encode()).hexdigest()
        notes["present"] = bool(text.strip())
        notes["baselineReplaced"] = (
            next((line.strip() for line in text.splitlines() if line.strip()), None)
            == "<overwrite>"
        )
    profile = result.get("profileRead", {}).get("profile", {})
    profile.pop("notes", None)
    return result


def compose_role_instructions(
    baseline: str, baseline_digest: str, choice: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    text = choice.get("value")
    if not isinstance(text, str) or not text.strip():
        return baseline, {
            "kind": "compiled-role-operation-capsule",
            "semanticDigest": baseline_digest,
            "baselineDigest": baseline_digest,
            "baselineReplaced": False,
            "userTextDigest": None,
            "blocks": [{"kind": "baseline", "digest": baseline_digest}],
        }
    lines = text.splitlines(keepends=True)
    first = next(i for i, line in enumerate(lines) if line.strip())
    replaced = lines[first].strip() == "<overwrite>"
    delivered = "".join(lines[:first] + lines[first + 1 :]) if replaced else text
    if replaced and not delivered.strip():
        raise ValueError("notes contains <overwrite> without instructions")
    raw_digest = "sha256:" + hashlib.sha256(text.encode()).hexdigest()
    block = {
        "kind": "user replacement" if replaced else "user addition",
        "source": choice.get("source"),
        "key": choice.get("key"),
        "digest": raw_digest,
        "deliveredText": delivered,
    }
    blocks = [block] if replaced else [{"kind": "baseline", "digest": baseline_digest}, block]
    digest = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                {"baseline": baseline_digest, "blocks": blocks}, sort_keys=True, ensure_ascii=False
            ).encode()
        ).hexdigest()
    )
    label = f"User {'replacement' if replaced else 'addition'} from {choice.get('source')} ({choice.get('key')}). The user's text holds where it differs from the baseline."
    instructions = ("" if replaced else baseline + "\n\n") + label + "\n\n" + delivered
    return instructions, {
        "kind": "user-replaced-role-instructions" if replaced else "capsule-with-user-addition",
        "semanticDigest": digest,
        "baselineDigest": baseline_digest,
        "baselineReplaced": replaced,
        "userTextDigest": raw_digest,
        "blocks": blocks,
    }
