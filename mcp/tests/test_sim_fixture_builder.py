"""The rich sim fixture must ship the worktree dirs its live contracts record (L11R-1).

L11 renders a leaf on the tasks surface ONLY while its worktree physically exists,
stat'ed at snapshot time. A fixture that records worktree paths without creating the
directories therefore replays as an empty Hangar — the builder must materialize the
dirs for every live (cleanup: pending) leaf contract and must NOT materialize them
for landed/abandoned ones, or the hidden states would stop being exercised.
"""

from __future__ import annotations

import importlib.util
import re
import tempfile
import unittest
from pathlib import Path

_BUILDER = Path(__file__).parent / "fixtures" / "build_rich_sim.py"
_WORKTREE_LINE = re.compile(r"^\s+worktree: (.+)$", re.MULTILINE)
_CLEANUP_LINE = re.compile(r"^\s+cleanup: (.+)$", re.MULTILINE)


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_rich_sim", _BUILDER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RichSimWorktreeTruthTests(unittest.TestCase):
    def test_live_leaf_contracts_ship_their_worktree_dirs(self) -> None:
        builder = _load_builder()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "fixture"
            builder.main(out)
            live = hidden = 0
            for contract in out.glob("tasks/*/*/enclosures/*/series-contract.md"):
                text = contract.read_text(encoding="utf-8")
                cleanup = _CLEANUP_LINE.search(text)
                worktrees = [Path(m) for m in _WORKTREE_LINE.findall(text)]
                self.assertTrue(worktrees, f"no worktree lines in {contract}")
                assert cleanup is not None
                if cleanup.group(1).strip() == "pending":
                    live += 1
                    for path in worktrees:
                        self.assertTrue(path.is_dir(), f"live leaf missing worktree dir: {path}")
                else:
                    hidden += 1
                    for path in worktrees:
                        self.assertFalse(
                            path.exists(), f"landed/abandoned leaf must stay dir-less: {path}"
                        )
            self.assertGreater(live, 0, "fixture lost its live leaves")
            self.assertGreater(hidden, 0, "fixture lost its landed/abandoned leaves")
