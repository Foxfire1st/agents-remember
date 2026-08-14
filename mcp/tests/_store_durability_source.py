"""Source-tree pinning helpers for the cross-process durability harness."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

# 260731-EFA-L5 base commit. The fix landed concurrently in the original worktree, so a
# reproducible baseline must measure an archive of this commit rather than wall-clock state.
BASE_COMMIT = "e52edaf5b655f495580efd93306afdf922b19b51"
REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS_PATH = Path(__file__).with_name("_store_durability.py")

# 260713-TES-L1: the harness driver imports the current module names, while the pinned base
# predates the supervisor -> agent-notifier rename. Applying the behavior-neutral rename lets
# the current driver exercise the base-commit store implementations byte-for-byte.
_RENAMED_SOURCE_FILES = {
    "agents_remember/controlplane/supervisor_signals.py": (
        "agents_remember/controlplane/agent_notifier_signals.py"
    ),
    "agents_remember/serving/supervisor.py": "agents_remember/serving/agent_notifier.py",
    "agents_remember/serving/supervisor_heartbeat.py": (
        "agents_remember/serving/agent_notifier_heartbeat.py"
    ),
    "agents_remember/serving/supervisor_models.py": (
        "agents_remember/serving/agent_notifier_models.py"
    ),
    "agents_remember/serving/_supervisor_actions.py": (
        "agents_remember/serving/_agent_notifier_actions.py"
    ),
    "agents_remember/serving/_supervisor_evaluation.py": (
        "agents_remember/serving/_agent_notifier_evaluation.py"
    ),
}


def _apply_rename_shim_to_base_tree(source_root: Path) -> None:
    """Rename supervisor modules and identifiers in an extracted base tree."""
    for old_rel, new_rel in _RENAMED_SOURCE_FILES.items():
        old_path = source_root / old_rel
        if not old_path.is_file():
            continue
        new_path = source_root / new_rel
        new_path.parent.mkdir(parents=True, exist_ok=True)
        old_path.rename(new_path)
    for path in source_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "supervisor" not in text.lower():
            continue
        updated = (
            text.replace("_supervisor_actions", "_agent_notifier_actions")
            .replace("_supervisor_evaluation", "_agent_notifier_evaluation")
            .replace("_supervisor_context", "_agent_notifier_context")
            .replace("_supervisor_loop", "_agent_notifier_loop")
            .replace("_supervisor_heartbeat_payload", "_agent_notifier_heartbeat_payload")
            .replace("_supervisor_banner", "_agent_notifier_banner")
            .replace("run_supervisor_sweep", "run_agent_notifier_sweep")
            .replace("supervisor_staleness_banner", "agent_notifier_staleness_banner")
            .replace("supervisor_heartbeat", "agent_notifier_heartbeat")
            .replace("supervisor_signals", "agent_notifier_signals")
            .replace("supervisor_models", "agent_notifier_models")
            .replace("supervisor_signal", "agent_notifier_signal")
            .replace("KNOWN_SUPERVISOR_FIELDS", "KNOWN_AGENT_NOTIFIER_FIELDS")
            .replace("DEFAULT_SUPERVISOR_", "DEFAULT_AGENT_NOTIFIER_")
            .replace("_parse_supervisor", "_parse_agent_notifier")
            .replace("_require_supervisor_floor_seconds", "_require_agent_notifier_floor_seconds")
            .replace("Supervisor", "AgentNotifier")
            .replace("SUPERVISOR_SIGNAL_OWNERSHIP", "AGENT_NOTIFIER_SIGNAL_OWNERSHIP")
            .replace("SUPERVISOR_SIGNAL_SCHEMA", "AGENT_NOTIFIER_SIGNAL_SCHEMA")
        )
        if updated != text:
            path.write_text(updated, encoding="utf-8")


def extract_base_commit_tree(destination: Path, *, repo: Path = REPO_ROOT) -> Path:
    """Archive the pinned base commit's package into ``destination``.

    This deliberately uses an archive rather than creating a nested Git worktree inside the
    coordination worktree that is running the measurement.
    """
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / "base.tar"
    with archive.open("wb") as handle:
        subprocess.run(
            ["git", "archive", BASE_COMMIT, "mcp/src/agents_remember"],
            cwd=repo,
            stdout=handle,
            stderr=subprocess.PIPE,
            check=True,
        )
    # ``tar`` avoids the stdlib extractor's 3.14 filter-migration warning, which the suite
    # correctly promotes to an error.
    subprocess.run(
        ["tar", "-xf", str(archive), "-C", str(destination)],
        capture_output=True,
        check=True,
    )
    archive.unlink()
    source_root = destination / "mcp" / "src"
    _apply_rename_shim_to_base_tree(source_root)
    return source_root


def run_against_source(
    source_root: Path, config: dict[str, Any]
) -> dict[str, Any]:  # pragma: no cover
    """Run the harness in a fresh interpreter pinned to ``source_root``."""
    work = Path(config["root"])
    work.mkdir(parents=True, exist_ok=True)
    config_path = work / "harness-config.json"
    out_path = work / "harness-result.json"
    payload = {**config, "source_root": str(source_root), "out": str(out_path)}
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    environment = {**os.environ, "PYTHONPATH": str(source_root)}
    completed = subprocess.run(
        [sys.executable, str(HARNESS_PATH), str(config_path)],
        env=environment,
        capture_output=True,
        text=True,
        timeout=config.get("timeout", 120.0) * 4,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"harness against {source_root} exited {completed.returncode}: "
            f"{completed.stdout}\n{completed.stderr}"
        )
    return json.loads(out_path.read_text(encoding="utf-8"))
