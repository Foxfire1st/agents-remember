"""MIK-R55 rule 6: each computation audited in its own interpreter, never nested pytest."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = r"""
import json, os, sys, traceback
from pathlib import Path
from agents_remember.application.knowledge_worklist import leaf
from agents_remember.application.knowledge_worklist.leaf import CandidateTrees
from agents_remember.application.knowledge_gate import evaluate_leaf_gate
from agents_remember.application import review_tree_knowledge as review
from agents_remember.application.review_candidate_resolution import resolve_review_candidate
from agents_remember.kernel.recorded_reads import recorded_reads
from agents_remember.worktrees.worktree_contract import load_contract
from test_review_git_trees import build_world, git, REPO, MASTER, LEAF
from test_reviewer_worklist_reads import _packet_inputs

root, operation, mutation = Path(sys.argv[1]), sys.argv[2], sys.argv[3] == 'mutate'
world = build_world(root)
_packet_inputs(world)
world.edit()
(world.task_root / 'leaf.json').write_text(json.dumps({
    'id': LEAF, 'slug': 'leaf', 'title': 'Leaf', 'kind': 'subTask', 'repo': REPO,
    'createdAt': '2026-10-03T00:00:00Z'}))
contract = load_contract(world.contract())
resolved = resolve_review_candidate(world.config, REPO, MASTER, LEAF)
assert resolved.trees is not None, resolved
# Audit the reviewer computation owner in THIS test child, not an unaudited grandchild.
# The process transport is tested separately; this seam runs the identical leaf owner.
def inline(contract, *, candidate, base, processes=None):
    return leaf.leaf_worklist(contract, candidate=candidate, base=base, persist=False)
review.isolated_leaf_worklist = inline
unused = world.config.coordination_root / 'tasks' / REPO / 'audit-unused' / 'task.json'
unused.parent.mkdir(parents=True)
unused.write_bytes((world.task_root / 'leaf.json').read_bytes())
if mutation:
    real = leaf.leaf_expected_effects
    def unrecorded(contract):
        unused.read_bytes()
        return real(contract)
    leaf.leaf_expected_effects = unrecorded
observed = []
active = False
coord = world.config.coordination_root.absolute()
object_stores = tuple(Path(git(repo, 'rev-parse', '--path-format=absolute', '--git-path', 'objects'))
                      for repo in (world.code, world.memory))
def audit(event, args):
    if not active or event not in ('open', 'os.listdir', 'os.scandir'):
        return
    raw = args[0]
    if not isinstance(raw, (str, bytes, os.PathLike)):
        return
    path = Path(os.fsdecode(raw)).absolute()
    if not path.is_relative_to(coord):
        return
    if any(path.is_relative_to(store) for store in object_stores):
        return
    if event == 'open':
        mode, flags = args[1], args[2]
        if isinstance(mode, str) and not ('r' in mode or '+' in mode):
            return
        if mode is None and flags & os.O_WRONLY and not flags & os.O_RDWR:
            return
    observed.append((event, str(path), [f'{f.filename}:{f.lineno}:{f.name}'
                    for f in traceback.extract_stack(limit=8)]))
sys.addaudithook(audit)
examined = {}
with recorded_reads(examined=examined) as reads:
    active = True
    try:
        if operation == 'worklist':
            result = leaf.leaf_worklist(contract, persist=False)
            assert result is not None and result['state'] == 'complete', result
        elif operation == 'gate':
            result = evaluate_leaf_gate(contract, CandidateTrees(
                code=resolved.trees.record.code_candidate.tree,
                memory=resolved.trees.record.memory_candidate.tree), persist_worklist=False)
            assert result is not None and result.worklist['state'] == 'complete', result
        else:
            result = review._leaf_wide_parts(contract, resolved.trees)
            assert result.worklist is not None, result
    finally:
        active = False
missing = []
for event, path, stack in observed:
    key = path if event == 'open' else 'json-files:' + path
    if key not in reads and key not in examined:
        missing.append({'event': event, 'path': path, 'call': stack})
summary = {'operation': operation, 'observed': len(observed), 'reads': reads, 'examined': examined, 'unrecorded': missing}
(root / 'audit-result.json').write_text(json.dumps(summary, indent=2))
print(json.dumps(summary, indent=2))
assert observed and reads, 'audit computation had no read coverage'
assert not missing, missing
master = str(world.task_root / 'task.json')
assert any(path == master for _event, path, _stack in observed), 'ruled-out master was not opened'
assert master not in reads, 'ruled-out master polluted the kept dependency set'
assert examined[master].startswith('sha256:'), 'ruled-out bytes were not examined'
"""


def _run(
    tmp_path: Path, operation: str, *, mutate: bool = False
) -> subprocess.CompletedProcess[str]:
    root = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env["XDG_CONFIG_HOME"] = str(tmp_path / "config")
    env["PYTHONPATH"] = os.pathsep.join(
        str(root / relative) for relative in ("mcp/src", "mcp/test_support", "mcp/tests")
    )
    return subprocess.run(
        [sys.executable, "-c", _SCRIPT, str(tmp_path), operation, "mutate" if mutate else "normal"],
        env=env,
        cwd=root,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


@pytest.mark.parametrize("operation", ["worklist", "gate", "reviewer"])
def test_computation_reads_are_recorded_under_a_python_audit_hook(
    tmp_path: Path, operation: str
) -> None:
    result = _run(tmp_path, operation)
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_real_unrecorded_task_file_read_is_caught(tmp_path: Path) -> None:
    result = _run(tmp_path, "worklist", mutate=True)
    assert result.returncode != 0
    assert '"unrecorded": [' in result.stdout
    assert "audit-unused/task.json" in result.stdout and "unrecorded" in result.stderr
