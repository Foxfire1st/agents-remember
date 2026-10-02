"""A bounded in-process memo of the gate's verdicts over exact trees (MIK-R09; L09 ruling, gap 4).

One contract-scoped memory-quality run evaluates the gate up to three times over the same candidate
(the count, then twice through the closeout validator), and closeout admission evaluates it again.
Each evaluation recomputes the worklist from its four sides, so a repeated one over identical
inputs reproduces the identical verdict. The memo keeps that verdict, keyed by everything the
evaluation reads:

* the exact captured code tree C and memory tree K_C (content-addressed Git tree IDs);
* the contract's identity: its path and the SHA-256 of its bytes, which carry B and every branch;
* the parent line's memory tip, the validator's comparison base and the line K_B pairs on;
* the SHA-256 of the leaf's task document (its maintenance scope and declared effects);
* the build that produced the verdict (``measuring_build_stamp``).

A changed input is a different key, so a stale verdict is never served; the gate still recomputes
for every new candidate (the always-recompute rule, carried from L11).

**Inputs outside the trees.** A reconsideration link (MIK-R14) reads the approval state of its
requirement endpoint from the owning task's ``requirements/manifest.json`` and hands the owner the
linked packet to resolve; the onboarding gate falls back to the coordination settings when the memory
worktree has none. None of these is in any tree. The evaluation records every such file it read,
with the SHA-256 of the bytes read or ``absent`` (:func:`agents_remember.kernel.recorded_reads.
recorded_reads`), and the verdict is kept with that read set. Before a kept verdict is reused, each
recorded file is hashed again: any difference -- a newly approved version, a manifest that appeared
or vanished, edited settings -- is a miss, and the gate recomputes. A file read twice with different
identities during one evaluation makes the verdict unkeepable.

**Bounds, belt and braces.** At most :data:`CAPACITY` verdicts are kept, least recently used first
out, and none is served after :data:`MAX_AGE_SECONDS`.

**What is never kept.** A verdict that carries an unreadable input or a run that failed (any
``incomplete`` finding, an unreadable validator input, or a worklist that is not ``complete``) is
never memoised: the next evaluation reads again. A refused verdict over complete inputs is a
deterministic answer and is kept like a pass.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

from agents_remember.application.knowledge_worklist.leaf import CandidateTrees
from agents_remember.application.runtime.startup import measuring_build_stamp
from agents_remember.kernel.recorded_reads import CONFLICTING, file_identity
from agents_remember.memory.knowledge.read_anchor_memo import BoundedMemo
from agents_remember.tasks.leaf_decisions import LeafDocumentUnresolved, strict_leaf_doc
from agents_remember.worktrees.worktree_contract import WorktreeContract

if TYPE_CHECKING:
    from agents_remember.application.knowledge_gate.gate import GateResult

__all__ = ["CAPACITY", "GATE_MEMO", "MAX_AGE_SECONDS", "GateMemoKey", "memo_key", "remember"]

CAPACITY: Final = 16
MAX_AGE_SECONDS: Final = 900.0


@dataclass(frozen=True)
class GateMemoKey:
    """Everything one gate evaluation reads, as exact identities."""

    code_tree: str
    memory_tree: str
    contract: str
    parent_memory_tip: str | None
    leaf_memory_head: str | None
    task_document: str
    build: str


@dataclass(frozen=True)
class _Kept:
    """One kept verdict: when it was computed, and the requirement files it read, as read."""

    at: float
    result: GateResult
    reads: tuple[tuple[str, str], ...]

    def still_read_the_same(self) -> bool:
        return all(file_identity(Path(path)) == seen for path, seen in self.reads)


GATE_MEMO: Final[BoundedMemo[GateMemoKey, _Kept]] = BoundedMemo(CAPACITY)
"""The kept verdicts."""


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def memo_key(
    contract: WorktreeContract,
    candidate: CandidateTrees,
    parent_memory_tip: str | None,
    leaf_memory_head: str | None = None,
) -> GateMemoKey | None:
    """The key of one evaluation, or ``None`` when an input cannot be identified (no memo then).

    The verdict reads two memory commits beside the candidate: the parent line's tip (the
    validator's base) and the leaf's own ``HEAD`` (whose closed history files are frozen).
    """

    try:
        contract_bytes = contract.contract_path.read_bytes()
        found = strict_leaf_doc(contract.task_root, contract.leaf_id or contract.task_name)
        document = b"" if found is None else found[0].read_bytes()
        build = json.dumps(measuring_build_stamp(), sort_keys=True, separators=(",", ":"))
    except (OSError, LeafDocumentUnresolved, RuntimeError, ValueError):
        return None
    return GateMemoKey(
        code_tree=candidate.code,
        memory_tree=candidate.memory,
        contract=f"{contract.contract_path.as_posix()}@{_digest(contract_bytes)}",
        parent_memory_tip=parent_memory_tip,
        leaf_memory_head=leaf_memory_head,
        task_document=_digest(document),
        build=build,
    )


def remembered(key: GateMemoKey, *, now: float | None = None) -> GateResult | None:
    """The verdict kept for ``key`` if it is younger than :data:`MAX_AGE_SECONDS`."""

    held = GATE_MEMO.get(key)
    if held is None:
        return None
    kept = held[0]
    if (time.monotonic() if now is None else now) - kept.at > MAX_AGE_SECONDS:
        return None
    if not kept.still_read_the_same():
        return None  # an approval-state file changed since: recompute
    return kept.result


def remember(key: GateMemoKey, result: GateResult, reads: dict[str, str]) -> None:
    """Keep a verdict over complete inputs (:attr:`GateResult.memoisable`) with its read set."""

    if result.memoisable and CONFLICTING not in reads.values():
        GATE_MEMO.put(key, _Kept(time.monotonic(), result, tuple(sorted(reads.items()))))
