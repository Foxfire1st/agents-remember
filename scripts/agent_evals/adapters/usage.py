"""Native accounting only: absent counters never become zero or guessed totals."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from .models import NativeCapture


def _count(value: object) -> bool:
    return type(value) is int and value >= 0


def native_usage(capture: NativeCapture) -> dict[str, Any]:
    """Keep raw provider components and report an independently known total."""
    if capture.harness == "codex":
        components, total, reason = _codex(capture)
    elif capture.harness == "hermes":
        components, total, reason = _hermes(capture)
    else:
        components, total, reason = _claude(capture)
    return {
        "schema": "ar-native-usage/v1",
        "harness": capture.harness,
        "native_session_id": capture.session_id,
        "components": components,
        "total_tokens": total,
        "unknown_reason": reason,
        "raw_export_sha256": capture.to_dict()["raw_export_sha256"],
        "source_files": [
            {"path": item.path, "sha256": item.sha256} for item in capture.source_files
        ],
    }


def _codex(capture: NativeCapture) -> tuple[object, int | None, str | None]:
    snapshots = []
    for row in capture.raw_records:
        payload = row.get("payload")
        if (
            row.get("type") != "event_msg"
            or not isinstance(payload, dict)
            or payload.get("type") != "token_count"
        ):
            continue
        info = payload.get("info")
        if isinstance(info, dict) and isinstance(info.get("total_token_usage"), dict):
            snapshots.append(info["total_token_usage"])
    if not snapshots:
        return [], None, "native Codex cumulative total_token_usage absent"
    latest = snapshots[-1]
    total = latest.get("total_tokens")
    return (
        latest,
        total if _count(total) else None,
        None
        if _count(total)
        else "native Codex total_tokens absent/invalid; component sums are not substituted",
    )


def _hermes(capture: NativeCapture) -> tuple[object, int | None, str | None]:
    # Verified 2026-10-29 against Hermes agent/usage_pricing.py CanonicalUsage:
    # prompt=input+cache_read+cache_write; total=prompt+output. Reasoning is
    # already within output and must not be added again. hermes_state_usage.py
    # update_token_counts stores these canonical counters in the sessions row.
    # Active-history resets in hermes_state_messages.py and compression.py reset
    # message_count/tool_call_count only. agent/turn_usage.py persists per-call
    # token deltas and session_persistence.py flushes them at turn exit. Those
    # counters survive in-place rewind/compaction, but a compression child has
    # a separate session row: one row cannot establish whole-lineage spend.
    rows = [
        item["row"]
        for item in capture.raw_records
        if item.get("table") == "sessions"
        and isinstance(item.get("row"), dict)
        and item["row"].get("id") == capture.session_id
    ]
    if len(rows) != 1:
        return [], None, "exact native Hermes session usage row absent/ambiguous"
    row = rows[0]
    fields = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
    components = {name: row.get(name) for name in (*fields, "reasoning_tokens", "api_call_count")}
    components.update(
        parent_session_id=row.get("parent_session_id"), end_reason=row.get("end_reason")
    )
    if not all(_count(row.get(name)) for name in fields):
        return components, None, "native Hermes canonical usage counters incomplete"
    total = sum(row[name] for name in fields)
    components["native_session_total_tokens"] = total
    if row.get("parent_session_id") or row.get("end_reason") == "compression":
        return (
            components,
            None,
            "native Hermes session lineage is incomplete for full-attempt usage; selected session counters preserve only that session's spend",
        )
    if total == 0 and not (type(row.get("api_call_count")) is int and row["api_call_count"] > 0):
        return (
            components,
            None,
            "native Hermes default zero counters have no API accounting evidence",
        )
    return components, total, None


def _claude_cost(value: object) -> Decimal | None:
    if type(value) not in (int, float):
        return None
    amount = Decimal(str(value))
    return amount if amount.is_finite() and amount >= 0 else None


def _claude_session_total(final: dict[str, Any]) -> tuple[int | None, str | None]:
    models = final.get("modelUsage")
    if not isinstance(models, dict) or not models:
        return (
            None,
            "native Claude session modelUsage absent/incomplete; final call is not substituted",
        )
    fields = ("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens")
    if not all(
        isinstance(model, dict) and all(_count(model.get(name)) for name in fields)
        for model in models.values()
    ):
        return None, "native Claude session modelUsage counters incomplete"
    costs = [_claude_cost(model.get("costUSD")) for model in models.values()]
    expected = _claude_cost(final.get("total_cost_usd"))
    if expected is None or any(value is None for value in costs):
        return None, "native Claude session costUSD/total_cost_usd absent/invalid"
    actual = sum((value for value in costs if value is not None), Decimal(0))
    # JSON floats can round at the last decimal place; allow only 1e-12
    # relative or absolute USD, far below any meaningful partial-call cost.
    tolerance = max(Decimal("1e-12"), max(actual, expected) * Decimal("1e-12"))
    if abs(actual - expected) > tolerance:
        return None, "native Claude modelUsage costUSD sum differs from total_cost_usd"
    return sum(model[name] for model in models.values() for name in fields), None


def _claude(capture: NativeCapture) -> tuple[object, int | None, str | None]:
    # Assistant counters are message placeholders. The final result's modelUsage
    # is the session aggregate; its top-level usage remains final-call evidence.
    messages: dict[str, dict[str, Any]] = {}
    unidentified: list[dict[str, Any]] = []
    for row in capture.raw_records:
        message = row.get("message")
        if (
            row.get("type") != "assistant"
            or not isinstance(message, dict)
            or not isinstance(message.get("usage"), dict)
        ):
            continue
        if isinstance(message.get("id"), str) and message["id"]:
            messages[message["id"]] = message["usage"]
        else:
            unidentified.append(message["usage"])
    results = []
    for row in capture.raw_records:
        if (
            row.get("type") == "result"
            and row.get("session_id") == capture.session_id
            and row not in results
        ):
            results.append(row)
    components = {
        "by_message_id": messages,
        "unidentified_messages": unidentified,
        "final_call_usage": [row.get("usage") for row in results],
        "session_model_usage": [row.get("modelUsage") for row in results],
        "session_total_cost_usd": [row.get("total_cost_usd") for row in results],
        "assistant_output_tokens_are_placeholders": True,
    }
    if len(results) != 1:
        return (
            components,
            None,
            "native Claude final result absent/ambiguous; assistant output counters are placeholders",
        )
    total, reason = _claude_session_total(results[0])
    return components, total, reason
