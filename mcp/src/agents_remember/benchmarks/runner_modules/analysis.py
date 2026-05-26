from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents_remember.benchmarks.runner_modules.constants import TOKEN_KEYS
from agents_remember.benchmarks.runner_modules.manifest import load_json


def walk_values(value: Any) -> list[Any]:
    values: list[Any] = []
    pending = [value]
    while pending:
        current = pending.pop()
        values.append(current)
        if isinstance(current, dict):
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
    return values


def collect_strings(value: Any, keys: set[str]) -> list[str]:
    found: list[str] = []
    for current in walk_values(value):
        if isinstance(current, dict):
            found.extend(
                child for key, child in current.items() if key in keys and isinstance(child, str)
            )
    return found


def update_token_metrics(event: Any, metrics: dict[str, Any]) -> None:
    for current in walk_values(event):
        if not isinstance(current, dict):
            continue
        for key, value in current.items():
            update_token_metric(metrics, key, value)


def update_token_metric(metrics: dict[str, Any], key: str, value: Any) -> None:
    metric_key = TOKEN_KEYS.get(key)
    if metric_key and isinstance(value, int):
        metrics[metric_key] = max(int(metrics.get(metric_key, 0)), value)


def empty_jsonl_metrics(path: Path) -> dict[str, Any]:
    return {
        "jsonl_size_bytes": path.stat().st_size,
        "event_count": 0,
        "command_event_count": 0,
        "errors": [],
        "final_answer": "",
    }


def iter_jsonl_events(path: Path, metrics: dict[str, Any]) -> list[Any]:
    events: list[Any] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError as error:
            metrics["errors"].append(f"invalid jsonl line: {error}")
    return events


def event_has_command_marker(event: Any) -> bool:
    raw = json.dumps(event, sort_keys=True)
    return any(marker in raw for marker in ("exec_command", "tool_call", '"cmd"', '"command"'))


def update_final_answer(metrics: dict[str, Any], event: Any) -> None:
    for candidate in collect_strings(event, {"content", "text", "message"}):
        if len(candidate) > len(str(metrics.get("final_answer", ""))):
            metrics["final_answer"] = candidate


def update_event_metrics(metrics: dict[str, Any], event: Any) -> None:
    metrics["event_count"] += 1
    if event_has_command_marker(event):
        metrics["command_event_count"] += 1
    update_token_metrics(event, metrics)
    metrics["errors"].extend(
        error for error in collect_strings(event, {"error", "stderr"}) if error
    )
    update_final_answer(metrics, event)


def analyze_jsonl(path: Path) -> dict[str, Any]:
    metrics = empty_jsonl_metrics(path)
    for event in iter_jsonl_events(path, metrics):
        update_event_metrics(metrics, event)
    return metrics


def load_metadata(jsonl_path: Path) -> dict[str, Any]:
    metadata_path = jsonl_path.with_suffix(".metadata.json")
    if not metadata_path.exists():
        return {}
    try:
        return load_json(metadata_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def analyze_run_root(run_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for jsonl_path in sorted(run_root.rglob("*.jsonl")):
        metadata = load_metadata(jsonl_path)
        metrics = analyze_jsonl(jsonl_path)
        row = {
            "path": jsonl_path,
            "prompt": metadata.get("prompt", jsonl_path.parent.parent.name),
            "variant": metadata.get("variant", jsonl_path.parent.name),
            "repetition": metadata.get("repetition", jsonl_path.stem),
            "duration_seconds": metadata.get("durationSeconds"),
            "exit_code": metadata.get("exitCode"),
            **metrics,
        }
        rows.append(row)
    return rows


def range_text(values: list[Any]) -> str:
    clean = [value for value in values if isinstance(value, (int, float))]
    if not clean:
        return "n/a"
    low = min(clean)
    high = max(clean)
    if low == high:
        return str(low)
    return f"{low} - {high}"


def grouped(rows: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    result: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (str(row.get("prompt", "unknown")), str(row.get("variant", "unknown")))
        result.setdefault(key, []).append(row)
    return result


def summary_numeric_keys() -> list[str]:
    return [
        "duration_seconds",
        "event_count",
        "command_event_count",
        "input_tokens",
        "fresh_input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "jsonl_size_bytes",
    ]


def exit_code_text(group_rows: list[dict[str, Any]]) -> str:
    exit_codes = sorted(
        {str(row.get("exit_code")) for row in group_rows if row.get("exit_code") is not None}
    )
    return ", ".join(exit_codes) if exit_codes else "n/a"


def append_metric_rows(lines: list[str], group_rows: list[dict[str, Any]]) -> None:
    for key in summary_numeric_keys():
        lines.append(f"| {key} | {range_text([row.get(key) for row in group_rows])} |")
    lines.append(f"| exit_code | {exit_code_text(group_rows)} |")
    lines.append("")


def append_run_rows(lines: list[str], group_rows: list[dict[str, Any]]) -> None:
    lines.extend(["| Run | Duration | JSONL Size | Errors |", "| --- | ---: | ---: | --- |"])
    for row in group_rows:
        error_count = len(row.get("errors", []))
        lines.append(
            f"| {row.get('repetition')} | {row.get('duration_seconds', 'n/a')} | "
            f"{row.get('jsonl_size_bytes', 'n/a')} | {error_count} |"
        )
    lines.append("")


def append_group_summary(
    lines: list[str],
    prompt: str,
    variant: str,
    group_rows: list[dict[str, Any]],
) -> None:
    lines.extend([f"## {prompt} / {variant}", "", "| Metric | Range |", "| --- | --- |"])
    append_metric_rows(lines, group_rows)
    append_run_rows(lines, group_rows)


def summary_header(run_root: Path) -> list[str]:
    return [
        f"# Benchmark Summary: {run_root.name}",
        "",
        f"Run root: `{run_root}`",
        "",
    ]


def summary_markdown(run_root: Path, rows: list[dict[str, Any]]) -> str:
    lines = summary_header(run_root)
    if not rows:
        lines.extend(["No JSONL files found.", ""])
        return "\n".join(lines)
    for (prompt, variant), group_rows in grouped(rows).items():
        append_group_summary(lines, prompt, variant, group_rows)
    return "\n".join(lines)


def write_summary(run_root: Path, rows: list[dict[str, Any]]) -> Path:
    summary_path = run_root / "summary.md"
    summary_path.write_text(summary_markdown(run_root, rows), encoding="utf-8")
    return summary_path
