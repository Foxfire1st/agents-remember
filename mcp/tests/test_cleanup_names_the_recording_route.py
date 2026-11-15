"""The cleanup refusal names the route a merged master takes.

A master's work often lands as a merged pull request, which no local ref move records. The
refusal names `worktree_record_landing` for a series and `worktree_integrate` for a leaf.
"""

from __future__ import annotations

import sys
import tempfile
from dataclasses import replace
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.worktrees.modules.cleanup import _cleanup_integration_refusal
from test_record_landing import _fixture


def test_cleanup_of_an_unrecorded_master_names_the_recording_route() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        contract = _fixture(Path(tmp))
        assert contract.kind == "series"
        refusal = _cleanup_integration_refusal(contract)
        assert refusal is not None
        payload = refusal.payload
        assert payload["code"] == "integration-incomplete"
        assert "worktree_record_landing" in str(payload["error"])
        step = payload["nextStep"]
        assert isinstance(step, dict) and step["nextTool"] == "worktree_record_landing"

        # A leaf's refusal keeps the integrate route: a leaf's closeout is not a pull request.
        leaf = replace(contract, kind="leaf", leaf_id="LEAF-1")
        leaf_refusal = _cleanup_integration_refusal(leaf)
        assert leaf_refusal is not None
        leaf_step = leaf_refusal.payload["nextStep"]
        assert isinstance(leaf_step, dict) and leaf_step["nextTool"] == "worktree_integrate"
