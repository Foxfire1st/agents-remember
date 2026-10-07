"""One exact-tree reviewer worklist, through pipes and the existing leaf computation owner."""

from __future__ import annotations

import atexit
import gc
import json
import os
import sys
import time
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any, get_args, get_origin, get_type_hints

from agents_remember.application.knowledge_worklist.leaf import (
    CandidateTrees,
    CapturedBase,
    leaf_worklist,
)
from agents_remember.kernel.git_command import shared_blob_reads
from agents_remember.kernel.recorded_reads import recorded_reads, replay_reads
from agents_remember.kernel.reviewer_worklist_process import (
    EXIT_BUILD_MISMATCH,
    OPERATION,
    ReviewerWorklistProcesses,
    WorklistBuildMismatch,
    WorklistProcessError,
    arm_child_lifetime,
    build_identity,
    request_digest,
)
from agents_remember.serving.build_info import process_serving_build
from agents_remember.worktrees.worktree_contract import WorktreeContract

# The owner for a caller that supplies none (tests, scripts). The dashboard composes its own and
# shuts it down with the app; this one is stopped when its process ends.
DIRECT_WORKLIST_PROCESSES = ReviewerWorklistProcesses()
atexit.register(DIRECT_WORKLIST_PROCESSES.shutdown)


def isolated_leaf_worklist(
    contract: WorktreeContract,
    *,
    candidate: CandidateTrees,
    base: CapturedBase,
    processes: ReviewerWorklistProcesses | None = None,
) -> dict[str, Any] | None:
    """Call the one leaf owner in another interpreter and import its exact consumed-byte reads."""

    payload = json.loads(
        json.dumps(
            {"contract": asdict(contract), "candidate": asdict(candidate), "base": asdict(base)},
            default=_path,
        )
    )
    source = build_identity(
        process_serving_build().payload().model_dump(mode="json", exclude_none=True)
    )
    answer = (processes or DIRECT_WORKLIST_PROCESSES).compute(payload, source)
    replay_reads(answer["reads"])
    if answer["error"] is not None:
        raise WorklistProcessError(f"reviewer worklist computation failed: {answer['error']}")
    return answer["document"]


def _path(value: object) -> str:
    if isinstance(value, Path):
        return value.as_posix()
    raise TypeError(f"not a worklist input: {type(value).__name__}")


def _contract(document: dict[str, Any]) -> WorktreeContract:
    if set(document) != {field.name for field in fields(WorktreeContract)}:
        raise WorklistProcessError("reviewer worklist request has invalid admitted contract fields")
    hints = get_type_hints(WorktreeContract)
    values: dict[str, Any] = {
        name: Path(value)
        if value is not None and (hints[name] is Path or Path in get_args(hints[name]))
        else tuple(value)
        if value is not None and get_origin(hints[name]) is tuple
        else value
        for name, value in document.items()
    }
    return WorktreeContract(**values)


def _run(request: dict[str, Any]) -> dict[str, Any]:
    digest = request.pop("request")
    if request.get("operation") != OPERATION or request_digest(request) != digest:
        raise WorklistProcessError("reviewer worklist request identity is invalid")
    source = build_identity(
        process_serving_build().payload().model_dump(mode="json", exclude_none=True)
    )
    if source != request["source"]:
        raise WorklistBuildMismatch("reviewer worklist child imported a different source or build")
    payload = request["payload"]
    contract = _contract(payload["contract"])
    candidate = CandidateTrees(**payload["candidate"])
    base = CapturedBase(**payload["base"])
    started = time.monotonic()
    error = None
    with recorded_reads() as reads, shared_blob_reads():
        try:
            document = leaf_worklist(contract, persist=False, candidate=candidate, base=base)
        except Exception as failure:
            document = None
            error = f"{type(failure).__name__}: {failure}"
    return {
        "operation": OPERATION,
        "request": digest,
        "source": source,
        "module": Path(__file__).resolve().as_posix(),
        "pid": os.getpid(),
        "document": document,
        "reads": reads,
        "computation": [started, time.monotonic()],
        "error": error,
    }


def main() -> None:
    arm_child_lifetime()
    gc.disable()  # the short-lived child alone owns the worklist parse and its objects
    request = json.load(sys.stdin)
    try:
        result = _run(request)
    except WorklistBuildMismatch as mismatch:
        print(mismatch, file=sys.stderr)
        raise SystemExit(EXIT_BUILD_MISMATCH) from mismatch
    json.dump(result, sys.stdout)


if __name__ == "__main__":
    main()
