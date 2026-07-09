"""Central provider containment metrics: sampled by the MCP, consumed fleet-wide.

Containment R4 (260707-HFX-L1, developer ruling 2026-07-07): the MCP observes
per-stack uptime, reliability, and resource pressure so degradation is a
detectable state instead of a post-mortem. Samples land in one store under the
observer root; `provider_status` / diagnostics / the dashboard projection read
them; the degradation protocol (260707-HFX-L7) evaluates them. The dashboard
statistics board (260703_statistics-component) consumes the same rows later.

The sampler is deliberately dockerless-safe and read-only: a missing docker
binary or a stopped daemon yields an error-annotated empty sample, never a
launch or a crash — status must stay legal while providers are disabled.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agents_remember.providers.context import ContextProviderError
from agents_remember.providers.lifecycle.command_runner import run_command
from agents_remember.providers.lifecycle.docker_runtime import docker_command

PROVIDER_METRICS_SCHEMA = "ar-provider-metrics-sample/v1"
PROVIDER_INDEX_STATE_SCHEMA = "ar-provider-index-state/v1"

# Ownership labels every provider container carries (identity.provider_ownership_labels);
# the sampler discovers stacks by label so it needs no settings at all — a
# leftover stack from a dead session is exactly what must stay observable.
OWNERSHIP_LABEL_KEY = "agents-remember.provider"
INSTANCE_LABEL_KEY = "agents-remember.instance-id"

DEFAULT_SAMPLE_INTERVAL_SECONDS = 30.0
DOCKER_SAMPLE_TIMEOUT_SECONDS = 20

# Byte-window growth step for the bounded tail reader; read backward from EOF in
# chunks until enough complete lines are in hand rather than materialising the
# whole (unbounded) append-only log.
_TAIL_BLOCK_BYTES = 16384

# 260707-HFX2-L12 F5: retention for metrics.jsonl. ~30s container samples -> 10k rows is ~3.5 days,
# well past what read_recent (<=500) and the statistics board need. Compaction only fires past a
# hysteresis byte budget so the common 30s call is an O(1) stat, and it tails the newest rows
# (O(retain) read+write) rather than folding the whole file.
PROVIDER_METRICS_RETAIN_ROWS = 10_000
_APPROX_METRICS_LINE_BYTES = 512


def _tail_lines(path: Path, limit: int) -> list[str]:
    """Return up to the last ``limit`` complete lines of ``path`` without reading it all.

    Seeks backward from EOF in growing blocks until ``limit`` line boundaries are
    found (or the file start is reached), so the cost is O(limit x line width), not
    O(filesize). The possibly-partial leading line of a mid-file read is dropped.
    Returns ``[]`` for a missing/empty file or any OS error.
    """
    if limit <= 0:
        return []
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            pos = handle.tell()
            data = b""
            while pos > 0 and data.count(b"\n") <= limit:
                step = min(_TAIL_BLOCK_BYTES, pos)
                pos -= step
                handle.seek(pos)
                data = handle.read(step) + data
    except OSError:
        return []
    text = data.decode("utf-8", errors="replace")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()  # trailing newline
    if pos > 0 and lines:
        lines = lines[1:]  # first line may be truncated by the mid-file start
    return lines[-limit:]


@dataclass(frozen=True)
class ContainerSample:
    """One provider container's state at sample time."""

    name: str
    provider: str
    instance: str
    running: bool
    restarts: int
    mem_bytes: int | None = None
    mem_limit_bytes: int | None = None
    cpu_percent: float | None = None


@dataclass(frozen=True)
class MetricsSnapshot:
    """One sampling pass over every labeled provider container on the host."""

    schema: str
    sampledAt: str
    containers: list[ContainerSample] = field(default_factory=list)
    error: str | None = None

    @property
    def running_count(self) -> int:
        return sum(1 for container in self.containers if container.running)

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["runningCount"] = self.running_count
        return payload


class ProviderMetricsStore:
    """Append-log + rolling-current store under the observer root.

    ``metrics.jsonl`` accretes samples for trend/statistics consumers;
    ``metrics-current.json`` is the cheap always-fresh read for status packets
    and the projection. Writes are replace-atomic like the sibling observer
    stores; a torn append line only costs one sample row to a reader that
    skips invalid lines.
    """

    def __init__(self, coordination_root: Path) -> None:
        self._root = coordination_root / "logs" / "observer" / "providers"

    @property
    def log_path(self) -> Path:
        return self._root / "metrics.jsonl"

    @property
    def current_path(self) -> Path:
        return self._root / "metrics-current.json"

    def record(self, snapshot: MetricsSnapshot) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        line = json.dumps(snapshot.to_payload(), sort_keys=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        tmp = self.current_path.with_name(self.current_path.name + ".tmp")
        tmp.write_text(line + "\n", encoding="utf-8")
        os.replace(tmp, self.current_path)

    def record_index_state(self, payload: dict[str, Any]) -> None:
        """Append one index-lifecycle row (seed catch-up, staleness) to the log.

        260707-HFX-L2: rides the same JSONL as the container samples — the
        schema field tells consumers (degradation detector, statistics board)
        apart; the rolling current-state file stays container-only.
        """
        self._root.mkdir(parents=True, exist_ok=True)
        row = {"schema": PROVIDER_INDEX_STATE_SCHEMA, **payload}
        row.setdefault("sampledAt", datetime.now(UTC).isoformat())
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")

    def read_current(self) -> dict[str, Any] | None:
        try:
            return json.loads(self.current_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def read_recent_index_states(self, limit: int = 20) -> list[dict[str, Any]]:
        """The newest index-lifecycle rows (260707-HFX-L2), oldest first."""
        rows = [
            row
            for row in self.read_recent(limit=500)
            if row.get("schema") == PROVIDER_INDEX_STATE_SCHEMA
        ]
        return rows[-limit:]

    def compact(
        self,
        *,
        retain_rows: int = PROVIDER_METRICS_RETAIN_ROWS,
        max_bytes: int | None = None,
    ) -> int:
        """Reclaim metrics.jsonl to its newest `retain_rows` rows; return rows kept (0 if skipped).

        260707-HFX2-L12 F5/CS-6 D3: cheap enough to call every sampler tick — a byte-budget stat
        guards the common case (O(1)), and when the log exceeds ~2x the target it is rewritten from a
        bounded EOF tail (O(retain_rows)), never a whole-file fold. Metrics are lossy-tolerant (a
        sample racing the atomic replace is acceptable, same as the torn-line tolerance in read)."""
        path = self.log_path
        try:
            size = path.stat().st_size
        except OSError:
            return 0
        budget = max_bytes if max_bytes is not None else retain_rows * _APPROX_METRICS_LINE_BYTES * 2
        if size <= budget:
            return 0
        kept = _tail_lines(path, retain_rows)
        if not kept:
            return 0
        self._root.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".compact.tmp")
        tmp.write_text("\n".join(kept) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        return len(kept)

    def read_recent(self, limit: int = 120) -> list[dict[str, Any]]:
        """The newest ``limit`` samples, oldest first; invalid lines skipped.

        CS-6 D2 (260707-HFX2-L12): reads a bounded tail from the end of the file
        instead of ``read_text().splitlines()`` on the whole log, so a metrics.jsonl
        that grows for months does not cost O(filesize) on the 30s degradation
        sampler + per-request status hot paths.
        """
        rows: list[dict[str, Any]] = []
        for line in _tail_lines(self.log_path, limit):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows


def sample_provider_containers(
    *, cwd: Path, timeout: int = DOCKER_SAMPLE_TIMEOUT_SECONDS
) -> MetricsSnapshot:
    """One read-only sampling pass over labeled provider containers."""
    sampled_at = datetime.now(UTC).isoformat()
    try:
        docker = docker_command()
    except ContextProviderError as error:
        # Dockerless host: an error-annotated empty sample, never a crash — the
        # sampling loop and status packet must stay legal without docker.
        return MetricsSnapshot(
            schema=PROVIDER_METRICS_SCHEMA, sampledAt=sampled_at, error=str(error)
        )
    ps = run_command(
        [
            docker,
            "ps",
            "--all",
            "--filter",
            f"label={OWNERSHIP_LABEL_KEY}",
            "--format",
            "{{json .}}",
        ],
        cwd=cwd,
        timeout=timeout,
    )
    if ps["returncode"] != 0:
        return MetricsSnapshot(
            schema=PROVIDER_METRICS_SCHEMA,
            sampledAt=sampled_at,
            error=f"docker ps failed: {str(ps.get('stderr') or '').strip()[:300]}",
        )
    rows = _json_rows(str(ps.get("stdout") or ""), key="Names")
    # Review note: `docker stats` fed a stopped name can fail the WHOLE command
    # and blind every pressure number — only running containers get sampled.
    running_names = sorted(
        name
        for name, row in rows.items()
        if str(row.get("State") or "").lower() == "running"
    )
    stats = (
        _stats_rows(docker=docker, cwd=cwd, timeout=timeout, names=running_names)
        if running_names
        else {}
    )
    containers = [
        _container_sample(name, rows[name], stats.get(name)) for name in sorted(rows)
    ]
    return MetricsSnapshot(
        schema=PROVIDER_METRICS_SCHEMA, sampledAt=sampled_at, containers=containers
    )


def _json_rows(stdout: str, *, key: str) -> dict[str, dict[str, Any]]:
    """docker's ``--format {{json .}}`` line stream keyed by ``key``; bad lines skipped."""
    rows: dict[str, dict[str, Any]] = {}
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        name = str(row.get(key) or "")
        if name:
            rows[name] = row
    return rows


def _stats_rows(
    *, docker: str, cwd: Path, timeout: int, names: list[str]
) -> dict[str, dict[str, Any]]:
    """One ``docker stats --no-stream`` pass; a failure costs the numbers, never the sample."""
    result = run_command(
        [docker, "stats", "--no-stream", "--format", "{{json .}}", *names],
        cwd=cwd,
        timeout=timeout,
    )
    if result["returncode"] != 0:
        return {}
    return _json_rows(str(result.get("stdout") or ""), key="Name")


def _container_sample(
    name: str, ps_row: dict[str, Any], stats_row: dict[str, Any] | None
) -> ContainerSample:
    labels = _parse_labels(str(ps_row.get("Labels") or ""))
    mem_bytes, mem_limit = _parse_mem_usage(str((stats_row or {}).get("MemUsage") or ""))
    return ContainerSample(
        name=name,
        provider=labels.get(OWNERSHIP_LABEL_KEY, ""),
        instance=labels.get(INSTANCE_LABEL_KEY, ""),
        running=str(ps_row.get("State") or "").lower() == "running",
        restarts=_parse_restarts(str(ps_row.get("Status") or "")),
        mem_bytes=mem_bytes,
        mem_limit_bytes=mem_limit,
        cpu_percent=_parse_percent(str((stats_row or {}).get("CPUPerc") or "")),
    )


def _parse_labels(raw: str) -> dict[str, str]:
    labels: dict[str, str] = {}
    for part in raw.split(","):
        key, sep, value = part.partition("=")
        if sep:
            labels[key.strip()] = value.strip()
    return labels


def _parse_restarts(status: str) -> int:
    # docker ps Status strings carry "(N)" only for restarting states; the
    # durable restart count needs inspect, which is too heavy per tick — the
    # degradation detector treats restart-loop detection as state-change based.
    if "Restarting" in status:
        return 1
    return 0


_UNIT_FACTORS = {
    "b": 1,
    "kb": 1000,
    "kib": 1024,
    "mb": 1000**2,
    "mib": 1024**2,
    "gb": 1000**3,
    "gib": 1024**3,
}


def _parse_bytes(value: str) -> int | None:
    text = value.strip().lower()
    if not text:
        return None
    for unit in sorted(_UNIT_FACTORS, key=len, reverse=True):
        if text.endswith(unit):
            try:
                return int(float(text[: -len(unit)]) * _UNIT_FACTORS[unit])
            except ValueError:
                return None
    return None


def _parse_mem_usage(raw: str) -> tuple[int | None, int | None]:
    used_text, _, limit_text = raw.partition("/")
    return _parse_bytes(used_text), _parse_bytes(limit_text)


def _parse_percent(raw: str) -> float | None:
    text = raw.strip().rstrip("%")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None
