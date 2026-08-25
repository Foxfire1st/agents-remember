"""Canonical published-quality fixture for acceptance-evidence consumer tests."""

from __future__ import annotations

import json
import tempfile
from collections.abc import Mapping
from pathlib import Path

from agents_remember.worktrees.modules.git import require_git
from agents_remember.worktrees.modules.quality import clean_executor as clean_quality_executor
from agents_remember.worktrees.modules.quality import gate as code_quality_gate


def publish_passing_quality_gate(
    target: code_quality_gate.QualityGateTarget,
    *,
    diff_base: str = "",
    plan: code_quality_gate.QualityGatePlan | None = None,
    invocation: str = "closeout-staged",
    attestation: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Publish the same immutable evidence surface a successful mocked gate promises."""

    del plan, invocation
    candidate_tree = require_git(target.code_worktree, ["write-tree"])
    with tempfile.TemporaryDirectory() as temporary:
        export = Path(temporary)
        (export / "clean-quality-results.json").write_text(
            json.dumps({"status": "passed", "exitCode": 0}) + "\n",
            encoding="utf-8",
        )
        clean_quality_executor._publish_reports(  # pyright: ignore[reportPrivateUsage]
            export,
            target.worktree_group / "reports",
            candidate_tree=candidate_tree,
            attestation=attestation,
        )
    return {
        "required": True,
        "passed": True,
        "command": "fixture: published passing Dagger generation",
        "diffBase": diff_base,
        "candidateTree": candidate_tree,
    }
