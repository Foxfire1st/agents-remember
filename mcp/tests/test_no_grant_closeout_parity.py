"""MIK-R94 E11: ordinary closeout/integration responses are grant-inert.

Before/after implementation parity with actual operations: an atomic master
is closed and integrated through the PUBLIC closeout apply and integrate
tools -- leaf chain then series (master) chain, no dry-run, no refusal
excerpts -- in three worlds: the pre-change baseline tree, the current tree
pristine, and the current tree with an unrelated grant. The exact six
responses are compared after redacting only volatile fields (commit SHAs,
tmp paths, timestamps). The grant machinery (rules 1-3) neither perturbs
ordinary routes nor leaves residue; the embedded master closeout/integration
checks are pinned to their exact content in all worlds.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from typing import Any

from agents_remember.application import worktree_tools
from agents_remember.application.worktree_services import (
    build_default_worktree_services,
)
from agents_remember.application.worktree_tool_requests import (
    CloseoutApproval,
    CloseoutCommitMessages,
)
from agents_remember.kernel.direct_execution_grants import (
    DirectExecutionGrant,
    grant_path_for,
    write_grant_file,
)
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.mcp.tools.worktree import worktree_closeout_preview_payload
from agents_remember.tasks import read_task_doc, write_task_doc
from agents_remember.worktrees.services import (
    bind_worktree_services,
    reset_worktree_services,
)
from agents_remember.worktrees.worktree_contract import load_contract
from test_closeout_queue import MASTER_A, MASTER_B, QueueFixture

ROOT = Path(__file__).resolve().parents[2]
BASELINE_COMMIT = "228fd5ca2ac26896a6cf4d362bcc63cf52853cca"

MESSAGES = CloseoutCommitMessages(code="Parity code.", memory="Parity memory.")
APPROVAL = CloseoutApproval(intent_note="Parity apply.")

_SHA = re.compile(r"\b[0-9a-f]{40}\b")
_TS = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?")

# The before/after driver runs against the pre-change baseline tree too, so it
# may only use APIs that predate this leaf's change (QueueFixture, public
# closeout/integrate tools, task-doc reads/writes).
_BASELINE_DRIVER = """\
import json
import sys
from pathlib import Path

def main() -> None:
    root = Path(sys.argv[1])
    out = Path(sys.argv[2])
    from test_closeout_queue import MASTER_A, QueueFixture
    from agents_remember.application import worktree_tools
    from agents_remember.application.worktree_services import build_default_worktree_services
    from agents_remember.application.worktree_tool_requests import CloseoutApproval, CloseoutCommitMessages
    from agents_remember.kernel.primitives.runtime_config import load_config
    from agents_remember.mcp.tools.worktree import worktree_closeout_preview_payload
    from agents_remember.tasks import read_task_doc, write_task_doc
    from agents_remember.worktrees.services import bind_worktree_services, reset_worktree_services
    from agents_remember.worktrees.worktree_contract import load_contract

    messages = CloseoutCommitMessages(code="Parity code.", memory="Parity memory.")
    approval = CloseoutApproval(intent_note="Parity apply.")
    bind_worktree_services(build_default_worktree_services())
    try:
        fixture = QueueFixture(root, atomic_a=True, memory_mode="external")
        contract = fixture.contracts[MASTER_A]
        master_path = contract.task_root / "task.json"
        document = read_task_doc(master_path)
        for row in document.subTasks:
            leaf_path = (contract.task_root / row.file).with_suffix(".json")
            leaf = read_task_doc(leaf_path)
            write_task_doc(leaf_path.parent, leaf.model_copy(update={"status": "Completed"}))
        write_task_doc(
            master_path.parent,
            document.model_copy(
                update={
                    "status": "Completed",
                    "subTasks": [row.model_copy(update={"status": "Completed"}) for row in document.subTasks],
                }
            ),
        )
        config = load_config(fixture.config_path)
        result = {}
        leaf_path = str(contract.contract_path)
        result["leaf_preview"] = worktree_closeout_preview_payload(config, leaf_path, messages)
        assert result["leaf_preview"].get("ok"), result["leaf_preview"]
        result["leaf_apply"] = worktree_tools.worktree_closeout_apply_tool(
            config, leaf_path, messages, approval
        )
        assert result["leaf_apply"].get("ok"), result["leaf_apply"]
        assert result["leaf_apply"].get("state") == "closed", result["leaf_apply"]
        result["leaf_integrated"] = worktree_tools.worktree_integrate_tool(
            config, contract_path=leaf_path
        )
        assert result["leaf_integrated"].get("ok"), result["leaf_integrated"]
        assert result["leaf_integrated"].get("state") == "integrated", result["leaf_integrated"]
        series_path = str(load_contract(fixture.tasks / "master-a" / "series-contract.md").contract_path)
        result["series_preview"] = worktree_closeout_preview_payload(config, series_path, messages)
        assert result["series_preview"].get("ok"), result["series_preview"]
        result["series_apply"] = worktree_tools.worktree_closeout_apply_tool(
            config, series_path, messages, approval
        )
        assert result["series_apply"].get("ok"), result["series_apply"]
        assert result["series_apply"].get("state") == "closed", result["series_apply"]
        result["series_integrated"] = worktree_tools.worktree_integrate_tool(
            config, contract_path=series_path
        )
        assert result["series_integrated"].get("ok"), result["series_integrated"]
        assert result["series_integrated"].get("state") == "integrated", result["series_integrated"]
        out.write_text(json.dumps(result, indent=1, sort_keys=True), encoding="utf-8")
    finally:
        reset_worktree_services()

main()
"""

PHASES = (
    "leaf_preview",
    "leaf_apply",
    "leaf_integrated",
    "series_preview",
    "series_apply",
    "series_integrated",
)


def _scrub(value: Any, roots: list[str]) -> Any:
    if isinstance(value, str):
        redacted = _SHA.sub("<sha>", value)
        redacted = _TS.sub("<ts>", redacted)
        for root in roots:
            redacted = redacted.replace(root, "<root>")
        return redacted
    if isinstance(value, dict):
        return {key: _scrub(item, roots) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub(item, roots) for item in value]
    return value


def _complete_master_documents(task_root: Path) -> None:
    """Mark a master and every canonical leaf row Completed, as the plan requires."""

    master_path = task_root / "task.json"
    document = read_task_doc(master_path)
    for row in document.subTasks:
        leaf_path = (task_root / row.file).with_suffix(".json")
        leaf = read_task_doc(leaf_path)
        write_task_doc(leaf_path.parent, leaf.model_copy(update={"status": "Completed"}))
    write_task_doc(
        master_path.parent,
        document.model_copy(
            update={
                "status": "Completed",
                "subTasks": [
                    row.model_copy(update={"status": "Completed"}) for row in document.subTasks
                ],
            }
        ),
    )


class NoGrantCloseoutParityTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="parity-")
        self.tmp_path = Path(self._tmpdir.name)
        self.addCleanup(self._tmpdir.cleanup)
        bind_worktree_services(build_default_worktree_services())
        self.addCleanup(reset_worktree_services)

    def _chain(self, config, leaf_path: str, series_path: str) -> dict[str, Any]:
        """The full leaf-then-series closeout/integrate chain; exact responses."""

        result: dict[str, Any] = {}
        result["leaf_preview"] = worktree_closeout_preview_payload(config, leaf_path, MESSAGES)
        self.assertTrue(result["leaf_preview"].get("ok"), result["leaf_preview"])
        result["leaf_apply"] = worktree_tools.worktree_closeout_apply_tool(
            config, leaf_path, MESSAGES, APPROVAL
        )
        self.assertTrue(result["leaf_apply"].get("ok"), result["leaf_apply"])
        self.assertEqual(result["leaf_apply"].get("state"), "closed", result["leaf_apply"])
        result["leaf_integrated"] = worktree_tools.worktree_integrate_tool(
            config, contract_path=leaf_path
        )
        self.assertTrue(result["leaf_integrated"].get("ok"), result["leaf_integrated"])
        self.assertEqual(
            result["leaf_integrated"].get("state"), "integrated", result["leaf_integrated"]
        )
        result["series_preview"] = worktree_closeout_preview_payload(config, series_path, MESSAGES)
        self.assertTrue(result["series_preview"].get("ok"), result["series_preview"])
        result["series_apply"] = worktree_tools.worktree_closeout_apply_tool(
            config, series_path, MESSAGES, APPROVAL
        )
        self.assertTrue(result["series_apply"].get("ok"), result["series_apply"])
        self.assertEqual(result["series_apply"].get("state"), "closed", result["series_apply"])
        result["series_integrated"] = worktree_tools.worktree_integrate_tool(
            config, contract_path=series_path
        )
        self.assertTrue(result["series_integrated"].get("ok"), result["series_integrated"])
        self.assertEqual(
            result["series_integrated"].get("state"),
            "integrated",
            result["series_integrated"],
        )
        return result

    def _world(self, name: str, *, with_unrelated_grant: bool) -> dict[str, Any]:
        """The atomic master chain on the current tree; exact responses."""

        root = self.tmp_path / name
        fixture = QueueFixture(root, atomic_a=True, memory_mode="external")
        contract = fixture.contracts[MASTER_A]
        _complete_master_documents(contract.task_root)
        config = load_config(fixture.config_path)
        if with_unrelated_grant:
            unrelated = fixture.leaf_refs[MASTER_B]
            write_grant_file(
                grant_path_for(fixture.coord, unrelated),
                DirectExecutionGrant(
                    leaf=unrelated,
                    granted_by="architect",
                    granted_at="2026-08-15T00:00:00+00:00",
                    granter_agent_id="parity",
                ),
            )
        series_path = str(
            load_contract(fixture.tasks / "master-a" / "series-contract.md").contract_path
        )
        return self._chain(config, str(contract.contract_path), series_path)

    def _baseline_world(self) -> dict[str, Any]:
        """The same atomic master chain on the pre-change baseline tree."""

        tree = self.tmp_path / "baseline-tree"
        tree.mkdir()
        archive = subprocess.run(
            ["git", "archive", BASELINE_COMMIT, "mcp"],
            cwd=ROOT,
            capture_output=True,
            check=True,
            timeout=120,
        ).stdout
        with tarfile.open(fileobj=BytesIO(archive)) as tar:
            tar.extractall(tree)
        # Guard: the extracted tree must predate the grant machinery, or this
        # would compare the implementation against itself.
        self.assertFalse(
            (
                tree / "mcp" / "src" / "agents_remember" / "kernel" / "direct_execution_grants.py"
            ).exists()
        )
        driver_path = self.tmp_path / "e11_driver.py"
        driver_path.write_text(_BASELINE_DRIVER, encoding="utf-8")
        out_path = self.tmp_path / "baseline.json"
        env = dict(
            os.environ,
            PYTHONPATH=os.pathsep.join(
                [
                    str(tree / "mcp" / "src"),
                    str(tree / "mcp" / "tests"),
                    str(tree / "mcp" / "test_support"),
                ]
            ),
        )
        subprocess.run(
            [
                sys.executable,
                str(driver_path),
                str(self.tmp_path / "baseline-world"),
                str(out_path),
            ],
            env=env,
            cwd=ROOT,
            check=True,
            timeout=300,
        )
        return json.loads(out_path.read_text(encoding="utf-8"))

    def test_before_after_implementation_parity(self) -> None:
        baseline = self._baseline_world()
        pristine = self._world("world-pristine", with_unrelated_grant=False)
        noisy = self._world("world-grant", with_unrelated_grant=True)
        roots = [
            str(self.tmp_path / "baseline-world"),
            str(self.tmp_path / "world-pristine"),
            str(self.tmp_path / "world-grant"),
        ]
        for phase in PHASES:
            with self.subTest(phase=phase):
                scrubbed = [_scrub(world[phase], roots) for world in (baseline, pristine, noisy)]
                self.assertEqual(scrubbed[0], scrubbed[1])
                self.assertEqual(scrubbed[0], scrubbed[2])

    def test_master_checks_read_unchanged(self) -> None:
        baseline = self._baseline_world()
        pristine = self._world("world-checks-a", with_unrelated_grant=False)
        noisy = self._world("world-checks-b", with_unrelated_grant=True)
        roots = [
            str(self.tmp_path / "baseline-world"),
            str(self.tmp_path / "world-checks-a"),
            str(self.tmp_path / "world-checks-b"),
        ]
        for world, label in ((baseline, "baseline"), (pristine, "pristine"), (noisy, "grant")):
            with self.subTest(world=label):
                series = {phase: world[phase] for phase in PHASES if phase.startswith("series_")}
                self.assertEqual(series["series_preview"].get("kind"), "series")
                self.assertEqual(series["series_apply"].get("kind"), "series")
                self.assertEqual(series["series_integrated"].get("kind"), "series")
                for response in series.values():
                    checks = _master_checks(response)
                    self.assertTrue(checks, response)
                self.assertEqual(
                    _scrub(_master_checks(series["series_apply"]), roots),
                    _scrub(_master_checks(pristine["series_apply"]), roots),
                )
                self.assertEqual(
                    _scrub(_master_checks(series["series_integrated"]), roots),
                    _scrub(_master_checks(pristine["series_integrated"]), roots),
                )


def _master_checks(response: Any) -> Any:
    """The embedded master closeout/integration checks of a public response."""

    if not isinstance(response, dict):
        return None
    checks = {
        key: response[key]
        for key in (
            "closeout_gate",
            "closeout_order",
            "closeout_status",
            "integration_reopen",
            "integration_status",
            "human_review_status",
            "source_lineage",
            "final_comparison_selection",
            "final_output_receipt",
        )
        if response.get(key) is not None
    }
    return checks or None
