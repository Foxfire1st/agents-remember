"""Claude session accounting uses native aggregate counters and cost corroboration."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from agent_evals.adapters.models import NativeCapture
from agent_evals.adapters.usage import native_usage


@pytest.fixture
def real_result() -> dict:
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/judge-claude-usage-real.json").read_text()
    )
    assert (
        fixture["sourceSha256"]
        == "984a40d052b9b333fbfc17e6d9363e924bf47696a5451c3a70fcd7e5d9b9a02b"
    )
    return fixture["finalResultRecord"]


def usage(*rows: dict) -> dict:
    return native_usage(NativeCapture("claude", rows[0]["session_id"], (), (), rows, ()))


def test_real_multiturn_session_uses_model_aggregate_and_keeps_final_call(real_result) -> None:
    before = copy.deepcopy(real_result)
    result = usage(real_result)
    assert result["total_tokens"] == 4_404_033
    assert result["unknown_reason"] is None
    assert result["components"]["session_model_usage"] == [real_result["modelUsage"]]
    assert result["components"]["final_call_usage"] == [real_result["usage"]]
    assert result["components"]["session_total_cost_usd"] == [real_result["total_cost_usd"]]
    assert real_result == before


def test_missing_model_usage_never_substitutes_final_call(real_result) -> None:
    del real_result["modelUsage"]
    result = usage(real_result)
    assert result["total_tokens"] is None
    assert "session modelUsage absent" in result["unknown_reason"]
    assert result["components"]["final_call_usage"] == [real_result["usage"]]


def test_model_cost_mismatch_is_unknown_with_raw_counters_preserved(real_result) -> None:
    real_result["modelUsage"]["claude-opus-5-5"]["costUSD"] -= 0.01
    result = usage(real_result)
    assert result["total_tokens"] is None
    assert "modelUsage costUSD sum differs from total_cost_usd" in result["unknown_reason"]
    assert result["components"]["session_model_usage"] == [real_result["modelUsage"]]


def test_all_models_are_summed_with_explicit_float_precision_tolerance(real_result) -> None:
    fields = ("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens")
    real_result["modelUsage"] = {
        "first": {**dict.fromkeys(fields, 1), "costUSD": 0.1},
        "second": {**dict.fromkeys(fields, 2), "costUSD": 0.2},
    }
    real_result["total_cost_usd"] = 0.1 + 0.2
    result = usage(real_result, copy.deepcopy(real_result))
    assert result["total_tokens"] == 12
    assert result["unknown_reason"] is None


@pytest.mark.parametrize("bad", [None, {}, {"model": {}}, {"model": {"inputTokens": True}}])
def test_missing_or_incomplete_aggregate_never_uses_known_final_call(real_result, bad) -> None:
    real_result["modelUsage"] = bad
    assert usage(real_result)["total_tokens"] is None


@pytest.mark.parametrize("cost", [None, True, "9.652614800000002", -1.0, float("inf")])
def test_invalid_cost_cannot_corroborate_a_session(real_result, cost) -> None:
    real_result["total_cost_usd"] = cost
    result = usage(real_result)
    assert result["total_tokens"] is None
    assert "cost" in result["unknown_reason"]


def test_distinct_final_results_are_ambiguous_even_when_final_call_usage_matches(
    real_result,
) -> None:
    other = copy.deepcopy(real_result)
    other["modelUsage"]["claude-opus-5-5"]["outputTokens"] += 1
    result = usage(real_result, other)
    assert result["total_tokens"] is None
    assert "absent/ambiguous" in result["unknown_reason"]
