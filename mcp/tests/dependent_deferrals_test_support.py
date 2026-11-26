"""Shared fixtures and compact builders for dependent-deferral tests (L52-R1..R8)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents_remember.application.knowledge_gate.gate import GateTrees
from agents_remember.application.knowledge_writer import Owner, WriteReport, WriteRequest
from agents_remember.application.knowledge_writer.writer import write_knowledge
from agents_remember.mcp.tools.knowledge import ReadToolRequest, knowledge_read_payload
from agents_remember.memory_quality.check import DriftCheckContext, run_memory_quality_check
from agents_remember.memory_quality.dependent_flags import DependentFlagScope
from agents_remember.models.knowledge_files import canonical_text
from agents_remember.models.knowledge_files.documents import (
    LAYOUT_MARKER_PATH,
    dependent_link_index,
    record_path,
)
from agents_remember.models.knowledge_files.records import DependentRecord
from knowledge_writer_test_support import CODE_FILE, World, build_world, commit_all, git

TASK_BUG = "261112-BUG"
LEAF_ORIGIN = "261112-BUG-L10"
LEAF_DONE = "261112-BUG-L01"
LEAF_ABANDONED = "261112-BUG-L02"
LEAF_50 = "261112-BUG-L50"
LEAF_51 = "261112-BUG-L51"
LEAF_52 = "261112-BUG-L52"
LEAF_55 = "261112-BUG-L55"
LEAF_56 = "261112-BUG-L56"
LEAF_60 = "261112-BUG-L60"
LEAF_61 = "261112-BUG-L61"

TASK_CAP = "261124-CAP"
LEAF_CAP_5 = "261124-CAP-L5"
LEAF_CAP_6 = "261124-CAP-L6"

SETTINGS_DOC = "docs/reference/settings-json.md"
HERMES_DOC = "docs/install/hermes.md"
HERMES_CODE = "mcp/src/agents_remember/install/hermes.py"
UNRELATED_CODE = "pkg/unrelated.py"
REPO_NAME = "agents-remember"


def _write_master_doc(
    coord_root: Path, folder_name: str, task_id: str, subtasks: list[dict[str, str]]
) -> None:
    folder = coord_root / "tasks" / REPO_NAME / folder_name
    folder.mkdir(parents=True, exist_ok=True)
    doc = {
        "schema": "ar-task-document/v1",
        "id": task_id,
        "slug": task_id.lower(),
        "title": f"Task {task_id}",
        "kind": "master",
        "status": "In Progress",
        "repo": REPO_NAME,
        "createdAt": "2026-11-12T10:00:00Z",
        "subTasks": subtasks,
    }
    (folder / "task.json").write_text(json.dumps(doc), encoding="utf-8")


def seed_tasks(
    coord_root: Path, *, cap_l5_status: str = "In Progress", cap_l6_status: str = "abandoned"
) -> None:
    _write_master_doc(
        coord_root,
        "261112_bug",
        TASK_BUG,
        [
            {"number": LEAF_DONE, "status": "Completed"},
            {"number": LEAF_ABANDONED, "status": "abandoned"},
            {"number": LEAF_ORIGIN, "status": "Completed"},
            {"number": LEAF_50, "status": "In Progress"},
            {"number": LEAF_51, "status": "In Progress"},
            {"number": LEAF_52, "status": "In Progress"},
            {"number": LEAF_55, "status": "In Progress"},
            {"number": LEAF_56, "status": "In Progress"},
            {"number": LEAF_60, "status": "Not Started"},
            {"number": LEAF_61, "status": "Not Started"},
        ],
    )
    _write_master_doc(
        coord_root,
        "261124_cap",
        TASK_CAP,
        [
            {"number": LEAF_CAP_5, "status": cap_l5_status},
            {"number": LEAF_CAP_6, "status": cap_l6_status},
        ],
    )


def build_deferral_world(tmp_path: Path) -> tuple[World, str]:
    world = build_world(tmp_path)
    (world.code / "docs/reference").mkdir(parents=True, exist_ok=True)
    (world.code / SETTINGS_DOC).write_text("# Settings\n\nBaseline settings.\n", encoding="utf-8")
    (world.code / UNRELATED_CODE).write_text("VALUE = 1\n", encoding="utf-8")
    code_head = commit_all(world.code, "add settings and unrelated")
    seed_tasks(world.root)
    return world, code_head


def write_dep(
    world: World,
    records: list[dict[str, Any]],
    *,
    rows: list[dict[str, Any]] | None = None,
    leaf: str = LEAF_50,
    **options: Any,
) -> WriteReport:
    task = str(options.get("task", TASK_BUG))
    return write_knowledge(
        WriteRequest(
            memory_root=world.memory,
            code_root=world.code,
            owner=Owner(task=task, kind="leaf", id=leaf),
            handoff_path="notes/handoff.json",
            document={"records": records, "history": list(rows or [])},
            commit=bool(options.get("commit", True)),
            authorization="test",
            coordination_root=world.root,
            repo_name=REPO_NAME,
            code_base=options.get("code_base"),
            worklist={"items": []},
        )
    )


def stored_dependent_path(memory: Path) -> Path:
    return next(memory.glob("knowledge/dependents/*.json"))


def stored_dependent(memory: Path) -> dict[str, Any]:
    return json.loads(stored_dependent_path(memory).read_text(encoding="utf-8"))


def create_dependent(
    world: World, *, code_base: str, leaf: str = LEAF_ORIGIN, doc_path: str = SETTINGS_DOC
) -> dict[str, Any]:
    report = write_dep(
        world,
        [
            {
                "key": "dep-1",
                "kind": "dependent",
                "slug": "settings-json",
                "fields": {
                    "document": doc_path,
                    "stands_on": [{"kind": "file", "path": CODE_FILE}],
                    "confirmed": {
                        "task": leaf,
                        "role": "curator",
                        "statement": "Settings doc matches the code.",
                    },
                },
            }
        ],
        leaf=leaf,
        code_base=code_base,
    )
    assert report.state == "written", report.problems
    commit_all(world.memory, "seed dependent")
    return stored_dependent(world.memory)


def defer_spec(
    passage: str, reason: str, condition: dict[str, Any], *, owner: str = TASK_BUG
) -> dict[str, Any]:
    return {"passage": passage, "reason": reason, "owner": owner, "condition": condition}


def defer_op(record_id: str, spec: dict[str, Any] | list[dict[str, Any]]) -> dict[str, Any]:
    return {"key": "op-defer", "kind": "dependent", "id": record_id, "fields": {"defer": spec}}


def redefer_op(
    record_id: str, target_id: str, reason: str, condition: dict[str, Any]
) -> dict[str, Any]:
    return {
        "key": "op-redefer",
        "kind": "dependent",
        "id": record_id,
        "fields": {"redefer": {"targetId": target_id, "reason": reason, "condition": condition}},
    }


def lift_op(record_id: str, target_id: str, reason: str) -> dict[str, Any]:
    return {
        "key": "op-lift",
        "kind": "dependent",
        "id": record_id,
        "fields": {"lift": {"targetId": target_id, "reason": reason}},
    }


def confirm_op(record_id: str, leaf: str, **options: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "document": options.get("doc_path", SETTINGS_DOC),
        "stands_on": [{"kind": "file", "path": options.get("stand_path", CODE_FILE)}],
        "confirmed": {
            "task": leaf,
            "role": "curator",
            "statement": options.get("statement", "Confirmed."),
        },
        **dict(options.get("extra_fields") or {}),
    }
    return {"key": "op-confirm", "kind": "dependent", "id": record_id, "fields": fields}


def row_op(
    record_id: str, disposition: str, reason: str = "Recorded.", *, effect: str | None = None
) -> dict[str, Any]:
    row: dict[str, Any] = {"subject": record_id, "disposition": disposition, "reason": reason}
    if effect is not None:
        row["effect"] = effect
    return row


def deferral_entry(deferral_id: str, condition: dict[str, str], **options: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": deferral_id,
        "passage": options.get("passage", "Deferred passage"),
        "reason": options.get("reason", "Pending follow-up."),
        "condition": condition,
        "owner": options.get("owner", TASK_BUG),
        "createdAt": "2026-11-20T10:00:00Z",
        "createdByLeaf": options.get("created_by", LEAF_50),
    }
    if options.get("file_blob") is not None:
        item["fileBlob"] = options["file_blob"]
    return item


def dependent_doc(
    record_id: str, *, stand_blob: str, confirmed_commit: str, **options: Any
) -> dict[str, Any]:
    confirmed_task = str(options.get("confirmed_task", LEAF_ORIGIN))
    doc: dict[str, Any] = {
        "schema": "ar-dependent/v1",
        "id": record_id,
        "revision": int(options.get("revision", 1)),
        "document": options.get("doc_path", SETTINGS_DOC),
        "stands_on": [
            {"kind": "file", "path": options.get("stand_path", CODE_FILE), "blob": stand_blob}
        ],
        "confirmed": {
            "task": confirmed_task,
            "role": "curator",
            "statement": "Document matches code.",
            "commit": confirmed_commit,
        },
        "origin": {"task": TASK_BUG, "leaf": confirmed_task},
    }
    if options.get("deferrals"):
        doc["deferrals"] = options["deferrals"]
    return doc


def write_dependent_file(memory: Path, slug: str, document: dict[str, Any]) -> str:
    rel = record_path("dependent", str(document["id"]), slug)
    target = memory / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(canonical_text(document), encoding="utf-8")
    return rel


def seed_deferred_world(
    tmp_path: Path, record_id: str, deferrals: list[dict[str, Any]]
) -> tuple[World, str, str, str]:
    """Build a world with one dependent holding ``deferrals`` committed at ``code_base``."""
    world, code_base = build_deferral_world(tmp_path)
    blob1 = git(world.code, "hash-object", CODE_FILE)
    resolved = [
        {**d, "fileBlob": blob1} if "file" in d["condition"] and "fileBlob" not in d else d
        for d in deferrals
    ]
    write_dependent_file(
        world.memory,
        "settings-json",
        dependent_doc(record_id, stand_blob=blob1, confirmed_commit=code_base, deferrals=resolved),
    )
    mem_base = commit_memory(world.memory, "seed deferral", code_commit=code_base)
    return world, code_base, mem_base, blob1


def layout_bytes_for(memory: Path, dependents: list[tuple[str, bytes]]) -> bytes:
    marker = json.loads((memory / LAYOUT_MARKER_PATH).read_text(encoding="utf-8"))
    links = dependent_link_index(
        (path, DependentRecord.model_validate_json(raw)) for path, raw in dependents
    )
    if links is None:
        marker.pop("dependent_links", None)
    else:
        marker["dependent_links"] = links
    return canonical_text(marker).encode("utf-8")


def commit_memory(memory: Path, message: str, *, code_commit: str | None = None) -> str:
    dependents = [
        (path.relative_to(memory).as_posix(), path.read_bytes())
        for path in sorted(memory.glob("knowledge/dependents/*.json"))
    ]
    (memory / LAYOUT_MARKER_PATH).write_bytes(layout_bytes_for(memory, dependents))
    full_msg = message if code_commit is None else f"{message}\n\nCode-Commit: {code_commit}"
    return commit_all(memory, full_msg)


def gate_trees(
    world: World, *, base_code: str, cand_code: str, base_mem: str, cand_mem: str
) -> GateTrees:
    return GateTrees(
        code_repository=world.code,
        code_tree=git(world.code, "rev-parse", f"{cand_code}^{{tree}}"),
        memory_repository=world.memory,
        memory_tree=git(world.memory, "rev-parse", f"{cand_mem}^{{tree}}"),
        validation_bases=(base_mem,),
        base_code_commit=base_code,
        coordination_root=world.root,
        repo_name=REPO_NAME,
    )


def run_mqc(world: World, *, owner: str | None, code_base: str) -> dict[str, Any]:
    return run_memory_quality_check(
        world.memory / "onboarding",
        drift_context=DriftCheckContext(
            code_repository_root=world.code,
            context=None,
            dependent_scope=DependentFlagScope(
                owner=owner,
                code_base=code_base,
                coordination_root=world.root,
                repo_name=REPO_NAME,
            ),
        ),
        include_report_only_findings=True,
    )


def read_dependents(world: World, *, stale_only: bool = False) -> dict[str, Any]:
    return knowledge_read_payload(
        ReadToolRequest(
            memory_root=str(world.memory),
            view="dependent",
            repository_root=str(world.code),
            stale_only=stale_only,
        ),
        coordination_root=str(world.root),
    )
