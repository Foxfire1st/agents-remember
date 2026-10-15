"""The existing shared warmup record, progress, priority and process tracker."""

from __future__ import annotations

import contextlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from agents_remember.cli.paseo_status import HostStartIdentity
from agents_remember.cli.paseo_warmup_run import WARMUP_GIVE_UP_SECONDS, WarmupRun
from agents_remember.kernel.atomic_write import atomic_write_text
from agents_remember.kernel.file_lock import FileLease, try_file_lease

_RECORD_PREFIX = "ar-warmup-"
_RECORD_SUFFIX = ".json"
_SAFE_SERVER_ID = re.compile(r"[^A-Za-z0-9_-]")


def _safe_server_id(server_id: str) -> str:
    return _SAFE_SERVER_ID.sub("_", server_id)[:64] or "unknown"


@dataclass(frozen=True)
class WarmupClaim:
    """One loading pass's OS hold and exact existing-record generation."""

    path: Path
    server_id: str
    start_identity: HostStartIdentity
    generation: str
    lease: FileLease
    previous: dict[str, Any] | None

    def publish(self, payload: dict[str, Any]) -> None:
        if self.lease.closed:
            return
        write_warmup_record(
            self.path,
            {
                **payload,
                "serverId": self.server_id,
                "startIdentity": self.start_identity.record(),
                "generation": self.generation,
            },
        )

    def close(self) -> None:
        self.lease.close()


def claim_warmup_record(
    home: Path,
    server_id: str,
    start_identity: HostStartIdentity,
) -> WarmupClaim | None:
    """One nonblocking OS hold across all product servers for this runtime home.

    Existing settled outcomes apply only to their exact process birth and OS boot.
    An ended or crashed incomplete pass leaves the start eligible; no stale lock
    or claim file is removed to acquire ownership. The empty lock holds no data.
    """
    lease = try_file_lease(home / "ar-warmup", "host-agent loading")
    if lease is None:
        return None
    path = _warmup_record_path(home, server_id)
    try:
        record = read_warmup_record(path)
        if (
            record is not None
            and HostStartIdentity.from_record(record.get("startIdentity")) == start_identity
            and record.get("state") in ("finished", "given-up")
        ):
            lease.close()
            return None
        previous = (
            record
            if record is not None
            and HostStartIdentity.from_record(record.get("startIdentity")) == start_identity
            else None
        )
        claim = WarmupClaim(path, server_id, start_identity, uuid4().hex, lease, previous)
        claim.publish({"state": "running", "claimedAt": time.time()})
        for sibling in home.glob(f"{_RECORD_PREFIX}*{_RECORD_SUFFIX}"):
            if sibling != path and sibling.name != _PRIORITY_NAME:
                sibling.unlink(missing_ok=True)
        return claim
    except BaseException:
        lease.close()
        raise


def find_warmup_record(home: Path, server_id: str) -> Path | None:
    """The existing record file for this start, if any (the loser's read end, F28)."""

    path = home / f"{_RECORD_PREFIX}{_safe_server_id(server_id)}{_RECORD_SUFFIX}"
    return path if path.exists() else None


def read_warmup_record(path: Path) -> dict[str, Any] | None:
    """One record file's payload, or ``None`` when it cannot be read (stale-ok, F28)."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def write_warmup_record(path: Path, payload: dict[str, Any]) -> None:
    """Replace one record file atomically: a concurrent reader never sees half a write."""

    atomic_write_text(path, json.dumps(payload, ensure_ascii=False))


_PRIORITY_NAME = "ar-warmup-priority.json"


def request_warmup_priority(home: Path, agent_ids: list[str]) -> None:
    """Ask the loading server for these agents next, across processes (F28).

    One small file, rewritten whole each time (bounded): the owner reads it before each
    dispatch and front-loads the ids still pending. Best-effort; a missing home is silence.
    """

    with contextlib.suppress(OSError):
        write_warmup_record(
            home / _PRIORITY_NAME, {"agentIds": list(agent_ids), "updatedAt": time.time()}
        )


def read_warmup_priority(home: Path) -> list[str]:
    """The ids another server's clicks asked for, or nothing when there is no ask."""

    payload = read_warmup_record(home / _PRIORITY_NAME)
    if payload is None:
        return []
    asked = payload.get("agentIds")
    if not isinstance(asked, list) or not all(isinstance(one, str) for one in asked):
        return []
    return list(asked)


@dataclass
class WarmupTracker:
    """What the dashboard's one line and the panes read, for this process."""

    run: WarmupRun | None = None
    last_server_id: str | None = None
    claim: WarmupClaim | None = None
    last_start_identity: HostStartIdentity | None = None
    observed_start_identity: HostStartIdentity | None = None
    retire_reason: str | None = None
    observed_path: Path | None = None
    last_record: dict[str, Any] | None = None
    host_detail: str | None = None
    intake_detail: str | None = None
    finished_runs: int = 0
    home: Path | None = None
    host_away: bool = False
    host_returned: bool = False
    intake_failures: int = 0
    intake_first_failure_at: float | None = None

    def note_host_away(self) -> None:
        self.host_away = True

    def note_host_back(self) -> None:
        if self.host_away:
            self.host_returned = True
        self.host_away = False

    def note_intake_failure(self, detail: str) -> None:
        """One failed intake: reported distinctly, retried next poll (F27)."""

        self.intake_detail = detail
        if self.intake_first_failure_at is None:
            self.intake_first_failure_at = time.time()
        self.intake_failures += 1

    def intake_given_up(self, give_up_seconds: float = WARMUP_GIVE_UP_SECONDS) -> bool:
        """Whether this start failed its intake for the whole give-up window (F27)."""

        if self.intake_first_failure_at is None:
            return False
        return time.time() - self.intake_first_failure_at >= give_up_seconds

    def reset_intake(self) -> None:
        self.intake_detail = None
        self.intake_failures = 0
        self.intake_first_failure_at = None
        self.host_returned = False

    def give_up_intake(self, server_id: str, identity: HostStartIdentity, detail: str) -> None:
        """Abandon this start's intake after the give-up window (F27).

        The start is consumed with a stated give-up, so the pane says why no agent loads and
        the watch does not retry a start that cannot intake forever.
        """

        self.last_server_id = server_id
        self.last_start_identity = identity
        self.last_record = {
            "state": "given-up",
            "serverId": server_id,
            "reason": detail,
            "intakeFailures": self.intake_failures,
            "startIdentity": identity.record(),
            "endedAt": time.time(),
        }
        if self.home is not None:
            claim = claim_warmup_record(self.home, server_id, identity)
            if claim is not None:
                try:
                    claim.publish(self.last_record)
                finally:
                    claim.close()
        self.reset_intake()

    def note_run_finished(self, run: WarmupRun, claim: WarmupClaim | None) -> None:
        self._settle(run, claim, reason=None)

    def note_run_ended(self, run: WarmupRun, claim: WarmupClaim | None, reason: str) -> None:
        self._settle(run, claim, reason=reason)

    def _settle(self, run: WarmupRun, claim: WarmupClaim | None, *, reason: str | None) -> None:
        current = self.run is run
        record = {"state": "finished" if reason is None else "ended", **run.record()}
        if reason is not None:
            record["reason"] = reason
        try:
            if claim is not None:
                claim.publish(record)
        except OSError as error:
            reason = f"the run outcome could not be published: {error}"
            record["state"] = "ended"
            record["reason"] = reason
            self.intake_detail = reason
        finally:
            if claim is not None:
                claim.close()
        if current:
            self.finished_runs += 1
            self.last_record = record
            if claim is not None:
                self.last_record["startIdentity"] = claim.start_identity.record()
                self.last_start_identity = claim.start_identity if reason is None else None
                self.last_server_id = claim.server_id if reason is None else None
            self.run = None
            self.claim = None

    def write_progress(self) -> None:
        """Publish the current run's complete progress under its own live hold."""
        if self.run is None or self.claim is None:
            return
        run = self.run
        self.claim.publish(
            {
                "state": "running",
                **run.record(),
                "line": run.line(),
                "updatedAt": time.time(),
            }
        )

    def promote(self, agent_ids: list[str]) -> None:
        if self.run is not None:
            for agent_id in agent_ids:
                self.run.promote(agent_id)
        elif self.home is not None:
            # No local run: another server loads, so the click travels as a priority
            # ask (F28), best-effort.
            with contextlib.suppress(OSError):
                request_warmup_priority(self.home, agent_ids)

    def _observed_status(self) -> dict[str, Any] | None:
        """The other server's shared progress, when this one only watches (F28)."""

        if self.observed_path is None:
            return None
        record = read_warmup_record(self.observed_path)
        if record is None:
            return None
        if (
            self.observed_start_identity is not None
            and HostStartIdentity.from_record(record.get("startIdentity"))
            != self.observed_start_identity
        ):
            return None
        agents = record.get("agents")
        return {
            "running": record.get("state") == "running",
            "serverId": record.get("serverId"),
            "total": record.get("total"),
            "finished": record.get("finished"),
            "line": record.get("line") if record.get("state") == "running" else None,
            "pending": record.get("pending", []),
            "inFlight": record.get("inFlight", []),
            "agents": agents if isinstance(agents, dict) else {},
            "stoppedForMemory": record.get("stoppedForMemory") is True,
            "lastRecord": record if record.get("state") != "running" else self.last_record,
            "hostDetail": self.host_detail,
            "intakeDetail": self.intake_detail,
            "observed": True,
        }

    def status(self) -> dict[str, Any]:
        run = self.run
        if run is None:
            observed = self._observed_status()
            if observed is not None:
                return observed
            return {
                "running": False,
                "line": None,
                "pending": [],
                "inFlight": [],
                "agents": (
                    self.last_record.get("agents", {}) if isinstance(self.last_record, dict) else {}
                ),
                "stoppedForMemory": False,
                "lastRecord": self.last_record,
                "hostDetail": self.host_detail,
                "intakeDetail": self.intake_detail,
            }
        return {
            "running": True,
            "serverId": run.server_id,
            "total": run.total,
            "finished": run.finished,
            "line": run.line(),
            "pending": list(run._pending),
            "inFlight": run.in_flight,
            "agents": run.agent_states(),
            "stoppedForMemory": run.stopped_for_memory,
            "lastRecord": self.last_record,
            "hostDetail": self.host_detail,
            "intakeDetail": self.intake_detail,
        }


#: The process-wide tracker the watch loop drives and the routes read.
TRACKER = WarmupTracker()


def _warmup_record_path(home: Path, server_id: str) -> Path:
    return home / f"{_RECORD_PREFIX}{_safe_server_id(server_id)}{_RECORD_SUFFIX}"


def describe_tracker() -> dict[str, Any]:
    return TRACKER.status()
