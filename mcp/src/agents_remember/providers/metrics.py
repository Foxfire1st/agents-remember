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

DURABILITY: THIS LOG IS ON ``ar-durable-store/1.0`` (260731-EFA-L5)

``metrics.jsonl`` is written by BOTH long-lived processes and rewritten whole by one of
them, which is the same shape as the six ``controlplane/`` logs and the same defect. The MCP
process appends index-lifecycle rows on its provider-setup thread
(``providers/provider_setup.py`` ``_record_index_state``, reached from ``worktree_start`` /
``runtime_install``) while the dashboard's ``_metrics_loop`` (``serving/app.py``) runs
``record`` and then ``compact`` — a tail rewrite. Measured before this change, appenders against
that loop lost records the store had reported written, and tore a line doing it; the
``.compact.tmp`` / ``.tmp`` names also carried no pid, so two rewriters could ``os.replace`` each
other's temp away.

NO LOSS RATE IS QUOTED HERE, and that is deliberate rather than an omission. The percentages this
leaf measured move with a pacing that nothing in the suite records, so they are not a property of
any shape a reader can re-run — ``mcp/tests/test_provider_store_durability.py`` says exactly that
about its own figures, and measures different ones for the same named shape. What reproduces is
the DIRECTION: this log lost accepted records and now loses none, and
``ProviderHarnessSensitivityTests`` re-establishes that on every run against a ``git archive`` of
the base commit.

Every append and every rewrite here now takes ``metrics.jsonl``'s lock through
:func:`~agents_remember.controlplane.durable_store.exclusive_access`, and the one destructive
rewrite is :func:`~agents_remember.controlplane.durable_store.rewrite_lines` — pid-scoped
temp, fsynced file and directory, and a refusal if the caller is not holding the lock. See
:data:`PROVIDER_METRICS_OWNERSHIP` for who compacts and why the read stays tolerant.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agents_remember.controlplane.durable_store import (
    SCHEMA_VERSION,
    StoreOwnership,
    append_line,
    exclusive_access,
    rewrite_lines,
    schema_version_supported,
)
from agents_remember.providers.context import ContextProviderError
from agents_remember.providers.lifecycle.command_runner import run_command
from agents_remember.providers.lifecycle.docker_runtime import docker_command

PROVIDER_METRICS_SCHEMA = "ar-provider-metrics-sample/v1"
PROVIDER_INDEX_STATE_SCHEMA = "ar-provider-index-state/v1"

PROVIDER_METRICS_OWNERSHIP = StoreOwnership(
    store="provider-metrics",
    writers=("mcp", "dashboard"),
    compaction_owner="dashboard",
    rationale=(
        "Both processes append: the dashboard's _metrics_loop records container samples "
        "(serving/app.py) and the MCP process appends index-lifecycle rows from the "
        "provider-setup thread (providers/provider_setup.py _record_index_state). Compaction "
        "belongs to the dashboard because the reclaim is a step of that same sampling loop -- "
        "it is the only thing that knows the log just grew -- and because the MCP process has "
        "no reason to remove a row it does not read. NOT the operator-inbox exception: that "
        "store earned compaction_owner=None because BOTH processes must physically remove "
        "rows and neither removal can travel to the other process without the decision it "
        "implements. Nothing in the MCP process removes a metrics row at all, so a single "
        "owner is available here and is therefore required. The owner is enforced "
        "structurally rather than by a runtime predicate: compact() has exactly one caller and "
        "it is inside the dashboard's loop."
    ),
)
"""Who writes and who compacts ``metrics.jsonl``.

Declared here rather than in ``controlplane/durable_store.py`` for two reasons. The register in
that module is titled for the six control-plane logs and is the CONTRACT, not a directory of
every store in the tree; and 260731-EFA-L5 has a second worker in ``controlplane/`` closing the
gate replay window, so a store outside that folder states its own ownership beside the store it
describes. The contract itself is imported, never re-implemented.
"""

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


def _stamped(payload: dict[str, Any]) -> dict[str, Any]:
    """One row carrying its ``schemaVersion``. The store stamps it; a caller cannot override it.

    ``ar-durable-store/1.0``: MAJOR.MINOR, unknown major refused, unknown minor accepted, and an
    ABSENT field means ``1.0``. That last part is what makes this additive rather than a
    migration — every row already on disk was written by this same build under the same meaning,
    so :func:`_parse_row` reads an existing ``metrics.jsonl`` unchanged and the field only starts
    appearing on rows written from now on.
    """
    return {**payload, "schemaVersion": SCHEMA_VERSION}


def _parse_row(text: str) -> dict[str, Any] | None:
    """One stored row, or ``None`` when it is blank, torn, not an object, or a newer major.

    TOLERANT, per row, and that is a decision rather than an inheritance. The leaf's rule is that
    a store may read tolerantly only if it carries no authority, because a tolerant read that
    feeds a rewrite drops the unreadable row PERMANENTLY. Neither applies here, and both halves
    are worth stating:

    * NOTHING IS DECIDED ON THE PRESENCE OF A METRICS ROW. The distinguishing property of an
      authority log is that the ABSENCE of a row reads as "the thing it records never happened",
      which then permits what the row existed to refuse — a dropped ``applied`` gate marker
      re-opens a replay window. This log has no such marker. Its one consumer that mutates
      anything, the degradation detector's critical failsafe (``providers/degradation.py``),
      re-derives the whole state machine from a ROLLING WINDOW OF LIVE SAMPLES every 30s and
      consumes nothing; a row lost to a torn line costs at most one tick's accuracy and the next
      sample corrects it. That is the same self-correcting category the contract already accepts
      for attention-dismissals and supervisor cooldowns, and it is a structural property of the
      consumer, not the "it is only telemetry" hand-wave.
    * NOTHING THIS RETURNS IS EVER WRITTEN BACK. :meth:`ProviderMetricsStore.compact` reclaims
      from a RAW byte tail (:func:`_tail_lines`) and drops rows by age alone, so a row this
      skipped is skipped for one call and stays on disk — the permanent-drop cost the rule warns
      about is not on the table at all.

    If either of those ever changes — a decision keyed to a specific row, or a rewrite that
    filters by parsing — this must become a strict read IN THE SAME CHANGE.
    """
    if not text.strip():
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    version = payload.get("schemaVersion", SCHEMA_VERSION)
    if not isinstance(version, str) or not schema_version_supported(version):
        return None
    return payload


class ProviderMetricsStore:
    """Append-log + rolling-current store under the observer root.

    ``metrics.jsonl`` accretes samples for trend/statistics consumers;
    ``metrics-current.json`` is the cheap always-fresh read for status packets
    and the projection. Both are written through ``ar-durable-store/1.0``: every append and
    every rewrite holds ``metrics.jsonl``'s lock, so an append landing inside a compaction is
    serialized rather than discarded, and the temp files are pid-scoped and fsynced. A torn
    append line still only costs one sample row to a reader, because the read is tolerant
    per row (:func:`_parse_row` says why that is safe here and what would end it).
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
        """Append one container sample and republish it as the rolling current state.

        Both files are written under ONE hold of the log's lock, so ``metrics-current.json``
        always names a row that is really in ``metrics.jsonl``. LOCK ORDER, and it is the only
        place two of these locks are held at once: the log's lock is taken FIRST and the
        current-state file's inside it, never the other way round.
        """
        PROVIDER_METRICS_OWNERSHIP.check_declared_writer()
        line = json.dumps(_stamped(snapshot.to_payload()), sort_keys=True)
        with exclusive_access(self.log_path, PROVIDER_METRICS_OWNERSHIP):
            append_line(self.log_path, line)
            with exclusive_access(self.current_path, PROVIDER_METRICS_OWNERSHIP):
                rewrite_lines(self.current_path, [line], PROVIDER_METRICS_OWNERSHIP)

    def record_index_state(self, payload: dict[str, Any]) -> None:
        """Append one index-lifecycle row (seed catch-up, staleness) to the log.

        260707-HFX-L2: rides the same JSONL as the container samples — the
        schema field tells consumers (degradation detector, statistics board)
        apart; the rolling current-state file stays container-only.

        This is the MCP process's write, and the one that made the two-process pairing real:
        it runs on the provider-setup thread while the dashboard is compacting the same log.
        """
        PROVIDER_METRICS_OWNERSHIP.check_declared_writer()
        row = {"schema": PROVIDER_INDEX_STATE_SCHEMA, **payload}
        row.setdefault("sampledAt", datetime.now(UTC).isoformat())
        with exclusive_access(self.log_path, PROVIDER_METRICS_OWNERSHIP):
            append_line(self.log_path, json.dumps(_stamped(row), sort_keys=True))

    def read_current(self) -> dict[str, Any] | None:
        """The newest recorded sample, or ``None`` when there is none this build can read."""
        try:
            text = self.current_path.read_text(encoding="utf-8")
        except OSError:
            return None
        return _parse_row(text)

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
        bounded EOF tail (O(retain_rows)), never a whole-file fold.

        260731-EFA-L5: the stat, the tail read and the rewrite are all inside ONE hold of the
        log's lock, and that is the whole fix. Holding it only around the rewrite would leave the
        tail a stale choice — every row appended after it was read is not in the temp file and is
        gone at ``os.replace``, which is the lost update wearing a lock. The old docstring called
        that acceptable ("a sample racing the atomic replace is acceptable"); it was not a
        tolerance — accepted records were lost and now none are. No rate is put on that here, for
        the reason this module's header gives; the assertion that re-establishes the direction is
        ``test_provider_store_durability.py::ProviderHarnessSensitivityTests``. The byte-budget
        stat costs one uncontended ``flock`` on the sampler's 30s tick.

        The reclaim drops rows BY AGE and never by content: :func:`_tail_lines` keeps raw lines,
        so a row no reader could parse is retained here rather than quietly deleted."""
        path = self.log_path
        budget = (
            max_bytes if max_bytes is not None else retain_rows * _APPROX_METRICS_LINE_BYTES * 2
        )
        with exclusive_access(path, PROVIDER_METRICS_OWNERSHIP):
            try:
                size = path.stat().st_size
            except OSError:
                return 0
            if size <= budget:
                return 0
            kept = _tail_lines(path, retain_rows)
            if not kept:
                return 0
            rewrite_lines(path, kept, PROVIDER_METRICS_OWNERSHIP)
            return len(kept)

    def read_recent(self, limit: int = 120) -> list[dict[str, Any]]:
        """The newest ``limit`` samples, oldest first; unreadable rows skipped per row.

        CS-6 D2 (260707-HFX2-L12): reads a bounded tail from the end of the file
        instead of ``read_text().splitlines()`` on the whole log, so a metrics.jsonl
        that grows for months does not cost O(filesize) on the 30s degradation
        sampler + per-request status hot paths.

        Deliberately LOCK-FREE, like every other read in this contract: a reader that took the
        log's lock would queue the dashboard's status route behind a compaction, and the worst a
        concurrent append can cost this read is the torn last line :func:`_parse_row` skips.
        """
        rows: list[dict[str, Any]] = []
        for line in _tail_lines(self.log_path, limit):
            row = _parse_row(line)
            if row is not None:
                rows.append(row)
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
        # 260718-CHATS-L5F R6: a slow/hung docker daemon is a bounded, expected transient — return
        # an error-annotated empty sample (like the dockerless branch) instead of letting a
        # ``subprocess.TimeoutExpired`` escape and pollute the daemon log with a full traceback
        # every sampling interval (the developer's image1 metrics_loop noise).
        allow_timeout=True,
    )
    if ps.get("timedOut"):
        return MetricsSnapshot(
            schema=PROVIDER_METRICS_SCHEMA,
            sampledAt=sampled_at,
            error=f"docker ps timed out after {timeout}s",
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
        name for name, row in rows.items() if str(row.get("State") or "").lower() == "running"
    )
    stats = (
        _stats_rows(docker=docker, cwd=cwd, timeout=timeout, names=running_names)
        if running_names
        else {}
    )
    containers = [_container_sample(name, rows[name], stats.get(name)) for name in sorted(rows)]
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
