"""Dependent deferrals: writer ops, refusals, history rules, re-deferral, byte identity (L52-R1..R4)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import dependent_deferrals_test_support as s
from agents_remember.application.knowledge_currentness.observe import CodeTree, open_code_tree
from agents_remember.application.knowledge_gate.deferrals import (
    DEFERRAL_DUE_FILE,
    deferral_gate_findings,
)
from agents_remember.application.knowledge_gate.predicates import (
    GateContext,
    changed_document_open,
    record_row_open,
)
from agents_remember.application.knowledge_worklist.knowledge import KnowledgeSide
from agents_remember.application.knowledge_worklist.touched_records import touched_record_items
from agents_remember.application.knowledge_writer import Owner, WriteReport, WriteRequest
from agents_remember.application.knowledge_writer.code_anchors import CodeSnapshot
from agents_remember.application.knowledge_writer.writer import write_knowledge
from agents_remember.memory.knowledge_index import directory_snapshot, git_tree_snapshot
from agents_remember.memory_quality.knowledge_validator import CodePathSet, validate_tree
from agents_remember.memory_quality.knowledge_validator.trees import (
    knowledge_tree_from_directory,
    knowledge_tree_from_git,
)
from agents_remember.models.knowledge_files import canonical_text
from agents_remember.models.knowledge_files.documents import history_path
from agents_remember.models.knowledge_files.history import RecordRow
from agents_remember.models.knowledge_files.records import DependentRecord
from knowledge_writer_test_support import CODE_FILE, commit_all, git


def _last_row(memory: Path, leaf: str) -> dict[str, Any]:
    return json.loads((memory / history_path(leaf)).read_text(encoding="utf-8"))["rows"][-1]


def test_01_deferral_validation_refuses_invalid_inputs(tmp_path: Path) -> None:
    """Test 1: writer refuses missing task/file, unknown/terminal/master tasks, and blank fields."""

    world, code_base = s.build_deferral_world(tmp_path)
    dep_id = s.create_dependent(world, code_base=code_base)["id"]
    cases = [
        (
            "note-only",
            s.defer_spec("S1", "Stale.", {"note": "later"}),
            "at least one of 'task' or 'file'",
        ),
        ("empty-cond", s.defer_spec("S1", "Stale.", {}), "at least one of 'task' or 'file'"),
        ("unknown", s.defer_spec("S1", "Stale.", {"task": "261112-BUG-L999"}), "does not exist"),
        ("completed", s.defer_spec("S1", "Stale.", {"task": s.LEAF_DONE}), "terminal status"),
        ("abandoned", s.defer_spec("S1", "Stale.", {"task": s.LEAF_ABANDONED}), "terminal status"),
        ("master", s.defer_spec("S1", "Stale.", {"task": s.TASK_BUG}), "master task"),
        ("blank-passage", s.defer_spec("   ", "Stale.", {"task": s.LEAF_60}), "passage"),
        ("blank-reason", s.defer_spec("S1", "", {"task": s.LEAF_60}), "reason"),
    ]
    for label, spec, expected in cases:
        report = s.write_dep(
            world,
            [s.defer_op(dep_id, spec)],
            rows=[s.row_op(dep_id, "deferred")],
            code_base=code_base,
            commit=False,
        )
        assert report.state == "refused", f"{label} should be refused"
        assert expected in " ".join(p.render() for p in report.problems)


def test_02_defer_redefer_lift_preserve_confirmation_and_bump_revision(tmp_path: Path) -> None:
    """Test 2: defer, re-defer, and lift keep stands_on and confirmed identical and bump revision."""

    world, code_base = s.build_deferral_world(tmp_path)
    initial = s.create_dependent(world, code_base=code_base)
    dep_id = initial["id"]
    stands_b, conf_b = canonical_text(initial["stands_on"]), canonical_text(initial["confirmed"])
    assert initial["revision"] == 1

    # Defer -> rev 2
    spec = s.defer_spec("Install", "Wait for L60.", {"task": s.LEAF_60})
    r1 = s.write_dep(
        world,
        [s.defer_op(dep_id, spec)],
        rows=[s.row_op(dep_id, "deferred")],
        leaf=s.LEAF_50,
        code_base=code_base,
    )
    assert r1.state == "written", r1.problems
    s1 = s.stored_dependent(world.memory)
    d1 = s1["deferrals"][0]["id"]
    assert (
        s1["revision"] == 2
        and canonical_text(s1["stands_on"]) == stands_b
        and canonical_text(s1["confirmed"]) == conf_b
    )
    assert _last_row(world.memory, s.LEAF_50)["disposition"] == "deferred"
    assert _last_row(world.memory, s.LEAF_50)["deferralIds"] == [d1]
    commit_all(world.memory, "deferred")

    # Re-defer -> rev 3
    r2 = s.write_dep(
        world,
        [s.redefer_op(dep_id, d1, "Move to L61.", {"task": s.LEAF_61})],
        rows=[s.row_op(dep_id, "re_deferred")],
        leaf=s.LEAF_51,
        code_base=code_base,
    )
    assert r2.state == "written", r2.problems
    s2 = s.stored_dependent(world.memory)
    d2 = s2["deferrals"][0]["id"]
    assert d2 != d1 and s2["revision"] == 3
    assert canonical_text(s2["stands_on"]) == stands_b and canonical_text(s2["confirmed"]) == conf_b
    assert _last_row(world.memory, s.LEAF_51)["disposition"] == "re_deferred"
    assert _last_row(world.memory, s.LEAF_51)["deferralIds"] == [d2]
    commit_all(world.memory, "re-deferred")

    # Lift -> rev 4
    r3 = s.write_dep(
        world,
        [s.lift_op(dep_id, d2, "Verified accurate.")],
        rows=[s.row_op(dep_id, "deferral_lifted")],
        leaf=s.LEAF_52,
        code_base=code_base,
    )
    assert r3.state == "written", r3.problems
    s3 = s.stored_dependent(world.memory)
    assert s3["revision"] == 4 and "deferrals" not in s3
    assert canonical_text(s3["stands_on"]) == stands_b and canonical_text(s3["confirmed"]) == conf_b
    assert _last_row(world.memory, s.LEAF_52)["disposition"] == "deferral_lifted"
    assert _last_row(world.memory, s.LEAF_52)["deferralIds"] == [d2]


def test_03_history_validator_accepts_deferral_rows_and_rule5_gate_matches(tmp_path: Path) -> None:
    """Test 3: validator accepts deferral rows; record_row_open checks deferralOnly; rule-5 still requires changed."""

    world, code_base = s.build_deferral_world(tmp_path)
    dep_id = s.create_dependent(world, code_base=code_base)["id"]
    mem_base = git(world.memory, "rev-parse", "HEAD")

    spec = s.defer_spec("Defaults", "Wait for L60.", {"task": s.LEAF_60})
    r1 = s.write_dep(
        world,
        [s.defer_op(dep_id, spec)],
        rows=[s.row_op(dep_id, "deferred")],
        leaf=s.LEAF_51,
        code_base=code_base,
    )
    assert r1.state == "written", r1.problems

    val = validate_tree(
        knowledge_tree_from_directory(world.memory),
        bases=(knowledge_tree_from_git(world.memory, mem_base, label="base"),),
        code=CodePathSet(
            label="code", paths=frozenset({CODE_FILE, s.SETTINGS_DOC}), repository=world.code
        ),
    )
    assert val.refusals == ()

    base_side = KnowledgeSide.from_snapshot("base", git_tree_snapshot(world.memory, mem_base))
    cand_side = KnowledgeSide.from_snapshot("cand", directory_snapshot(world.memory))
    items = [
        i
        for i in touched_record_items(base_side, cand_side, owner=s.LEAF_51)
        if i["subject"] == dep_id
    ]
    assert len(items) == 1 and items[0]["facts"].get("deferralOnly") is True

    opened = open_code_tree(CodeTree(world.code, git(world.code, "rev-parse", "HEAD^{tree}")))
    hist = cand_side.history(s.LEAF_51)
    assert hist is not None
    assert record_row_open(items[0], GateContext(s.LEAF_51, hist, cand_side, opened)) is None

    def _ctx(row: RecordRow) -> GateContext:
        return GateContext(s.LEAF_51, hist.model_copy(update={"rows": (row,)}), cand_side, opened)

    for disp in ("re_deferred", "deferral_lifted"):
        row = RecordRow(
            id="ROW-D00001",
            subject=dep_id,
            disposition=disp,
            reason="Op.",
            items=(),
            revision=2,
            deferralIds=("DFR-ABC123",),
        )
        assert record_row_open(items[0], _ctx(row)) is None

    wrong = RecordRow(
        id="ROW-W00001",
        subject=dep_id,
        disposition="changed",
        reason="Wrong.",
        items=(),
        effect="clarify",
        revision=2,
    )
    assert record_row_open(items[0], _ctx(wrong)) is not None

    rule5 = {
        "subject": dep_id,
        "facts": {"document": s.SETTINGS_DOC, "confirmedTask": s.LEAF_ORIGIN},
    }
    for disp in ("deferred", "re_deferred", "deferral_lifted"):
        row = RecordRow(
            id="ROW-D00002",
            subject=dep_id,
            disposition=disp,
            reason="Defer.",
            items=(),
            revision=2,
            deferralIds=("DFR-ABC123",),
        )
        reason = changed_document_open(rule5, _ctx(row))
        assert reason is not None and "changed" in reason


def test_04_confirming_deferred_document_without_edit_is_refused_naming_deferral(
    tmp_path: Path,
) -> None:
    """Test 4: unedited confirmation of a deferred dependent is refused, naming each open deferral."""

    world, code_base = s.build_deferral_world(tmp_path)
    dep_id = s.create_dependent(world, code_base=code_base)["id"]
    specs = [
        s.defer_spec(
            "Auth timeout table", "Waiting on L60 refactor.", {"task": s.LEAF_60}, owner=s.TASK_BUG
        ),
        s.defer_spec(
            "Cache defaults",
            "Waiting on CAP L5 cache config.",
            {"task": s.LEAF_CAP_5},
            owner=s.TASK_CAP,
        ),
    ]
    assert (
        s.write_dep(
            world,
            [s.defer_op(dep_id, specs)],
            rows=[s.row_op(dep_id, "deferred")],
            leaf=s.LEAF_50,
            code_base=code_base,
        ).state
        == "written"
    )
    commit_all(world.memory, "two deferrals")
    d1, d2 = s.stored_dependent(world.memory)["deferrals"]

    refused = s.write_dep(
        world,
        [s.confirm_op(dep_id, s.LEAF_51, statement="Unedited confirm.")],
        rows=[s.row_op(dep_id, "changed", effect="clarify")],
        leaf=s.LEAF_51,
        code_base=code_base,
        commit=False,
    )
    assert refused.state == "refused"
    msg = " ".join(p.message for p in refused.problems)
    for d in (d1, d2):
        assert all(d[k] in msg for k in ("id", "passage", "reason", "owner"))


def test_05_partial_resolve_ends_one_deferral_keeps_other_and_record_stays_deferred(
    tmp_path: Path,
) -> None:
    """Test 5: editing doc + resolveDeferrals=[d1] drops d1, keeps d2; later unedited confirm still refuses."""

    world, code_base = s.build_deferral_world(tmp_path)
    dep_id = s.create_dependent(world, code_base=code_base)["id"]
    specs = [
        s.defer_spec("First passage", "Fixed in L51.", {"task": s.LEAF_51}),
        s.defer_spec("Second passage", "Waiting on L60.", {"task": s.LEAF_60}),
    ]
    assert (
        s.write_dep(
            world,
            [s.defer_op(dep_id, specs)],
            rows=[s.row_op(dep_id, "deferred")],
            leaf=s.LEAF_50,
            code_base=code_base,
        ).state
        == "written"
    )
    commit_all(world.memory, "seed two deferrals")
    d1, d2 = s.stored_dependent(world.memory)["deferrals"]

    (world.code / s.SETTINGS_DOC).write_text(
        "# Settings\n\nUpdated first passage.\n", encoding="utf-8"
    )
    code_l51 = commit_all(world.code, "edit first passage")
    r_partial = s.write_dep(
        world,
        [s.confirm_op(dep_id, s.LEAF_51, extra_fields={"resolveDeferrals": [d1["id"]]})],
        rows=[s.row_op(dep_id, "changed", "Resolved d1.", effect="clarify")],
        leaf=s.LEAF_51,
        code_base=code_base,
    )
    assert r_partial.state == "written", r_partial.problems
    commit_all(world.memory, "resolved d1")
    assert [one["id"] for one in s.stored_dependent(world.memory)["deferrals"]] == [d2["id"]]

    refused = s.write_dep(
        world,
        [s.confirm_op(dep_id, s.LEAF_52, statement="Unedited confirm.")],
        leaf=s.LEAF_52,
        code_base=code_l51,
        commit=False,
    )
    assert refused.state == "refused"
    msg = " ".join(p.message for p in refused.problems)
    assert d2["id"] in msg and d1["id"] not in msg


def test_10_redefer_refuses_note_only_allows_same_file_on_changed_blob_until_next_change(
    tmp_path: Path,
) -> None:
    """Test 10: note-only re-defer refuses; same-file re-defer succeeds on changed blob and waits for next move."""

    world, code_base = s.build_deferral_world(tmp_path)
    dep_id = s.create_dependent(world, code_base=code_base)["id"]
    blob1 = git(world.code, "hash-object", CODE_FILE)
    spec = s.defer_spec("Landing", "Wait for landing.py.", {"file": CODE_FILE, "note": "n1"})
    assert (
        s.write_dep(
            world,
            [s.defer_op(dep_id, spec)],
            rows=[s.row_op(dep_id, "deferred")],
            leaf=s.LEAF_50,
            code_base=code_base,
        ).state
        == "written"
    )
    commit_all(world.memory, "defer on file")
    d1 = s.stored_dependent(world.memory)["deferrals"][0]
    assert d1["fileBlob"] == blob1

    refused = s.write_dep(
        world,
        [s.redefer_op(dep_id, d1["id"], "Note-only.", {"file": CODE_FILE, "note": "n2"})],
        rows=[s.row_op(dep_id, "re_deferred")],
        leaf=s.LEAF_51,
        code_base=code_base,
        commit=False,
    )
    assert refused.state == "refused" and any(
        "blob is unchanged" in p.message for p in refused.problems
    )

    (world.code / CODE_FILE).write_text(
        (world.code / CODE_FILE).read_text() + "\n# v2\n", encoding="utf-8"
    )
    code_v2 = commit_all(world.code, "landing v2")
    blob2 = git(world.code, "hash-object", CODE_FILE)
    assert blob2 != blob1

    r_ok = s.write_dep(
        world,
        [
            s.redefer_op(
                dep_id, d1["id"], "Still pending at v2.", {"file": CODE_FILE, "note": "wait v3"}
            )
        ],
        rows=[s.row_op(dep_id, "re_deferred")],
        leaf=s.LEAF_51,
        code_base=code_base,
    )
    assert r_ok.state == "written", r_ok.problems
    d2 = s.stored_dependent(world.memory)["deferrals"][0]
    assert d2["id"] != d1["id"] and d2["fileBlob"] == blob2
    mem_v2 = commit_all(world.memory, "re-deferred at blob2")

    assert deferral_gate_findings(
        s.gate_trees(world, base_code=code_v2, cand_code=code_v2, base_mem=mem_v2, cand_mem=mem_v2),
        s.LEAF_52,
    ) == ([], [])
    (world.code / CODE_FILE).write_text(
        (world.code / CODE_FILE).read_text() + "# v3\n", encoding="utf-8"
    )
    code_v3 = commit_all(world.code, "landing v3")
    refusing, _ = deferral_gate_findings(
        s.gate_trees(world, base_code=code_v2, cand_code=code_v3, base_mem=mem_v2, cand_mem=mem_v2),
        s.LEAF_52,
    )
    assert [f.code for f in refusing] == [DEFERRAL_DUE_FILE] and d2["id"] in refusing[0].message


def test_11_w0t9v9_cap_and_bug_deferrals_partial_resolve_and_redefer_allows_closeout(
    tmp_path: Path,
) -> None:
    """Test 11: DPD-W0T9V9 CAP+BUG deferrals; 261124-CAP-L5 resolves CAP, re-defers BUG, passes gate."""

    world, code_base = s.build_deferral_world(tmp_path)
    blob_code = git(world.code, "hash-object", CODE_FILE)
    doc_blob_1 = git(world.code, "hash-object", s.SETTINGS_DOC)
    s.write_dependent_file(
        world.memory,
        "settings-json",
        s.dependent_doc("DPD-W0T9V9", stand_blob=blob_code, confirmed_commit=code_base),
    )
    s.commit_memory(world.memory, "seed DPD-W0T9V9", code_commit=code_base)

    specs = [
        s.defer_spec(
            "CAP provider table",
            "CAP L5 updates settings-json.md.",
            {"task": s.LEAF_CAP_5, "file": s.SETTINGS_DOC},
            owner=s.TASK_CAP,
        ),
        s.defer_spec(
            "BUG legacy key note",
            "BUG removes deprecated key later.",
            {"file": s.SETTINGS_DOC},
            owner=s.TASK_BUG,
        ),
    ]
    assert (
        s.write_dep(
            world,
            [s.defer_op("DPD-W0T9V9", specs)],
            rows=[s.row_op("DPD-W0T9V9", "deferred")],
            leaf=s.LEAF_50,
            code_base=code_base,
        ).state
        == "written"
    )
    mem_before = commit_all(world.memory, "seed CAP + BUG deferrals")
    cap_def = next(
        d for d in s.stored_dependent(world.memory)["deferrals"] if d["owner"] == s.TASK_CAP
    )
    bug_def = next(
        d for d in s.stored_dependent(world.memory)["deferrals"] if d["owner"] == s.TASK_BUG
    )
    assert cap_def["fileBlob"] == doc_blob_1 and bug_def["fileBlob"] == doc_blob_1

    (world.code / s.SETTINGS_DOC).write_text(
        "# Settings\n\nCAP provider limits documented.\n", encoding="utf-8"
    )
    code_after = commit_all(world.code, "CAP L5 updates settings-json.md")
    doc_blob_2 = git(world.code, "hash-object", s.SETTINGS_DOC)

    extra = {
        "resolveDeferrals": [cap_def["id"]],
        "redefer": {
            "targetId": bug_def["id"],
            "reason": "BUG passage still pending.",
            "condition": {"file": s.SETTINGS_DOC},
        },
    }
    r_cap = s.write_dep(
        world,
        [s.confirm_op("DPD-W0T9V9", s.LEAF_CAP_5, extra_fields=extra)],
        rows=[
            s.row_op("DPD-W0T9V9", "changed", "Resolved CAP, re-deferred BUG.", effect="clarify")
        ],
        leaf=s.LEAF_CAP_5,
        task=s.TASK_CAP,
        code_base=code_base,
    )
    assert r_cap.state == "written", r_cap.problems
    mem_after = commit_all(world.memory, "CAP L5 partial resolve + redefer")

    remaining = s.stored_dependent(world.memory)["deferrals"]
    assert (
        len(remaining) == 1
        and remaining[0]["owner"] == s.TASK_BUG
        and remaining[0]["fileBlob"] == doc_blob_2
    )
    assert deferral_gate_findings(
        s.gate_trees(
            world,
            base_code=code_base,
            cand_code=code_after,
            base_mem=mem_before,
            cand_mem=mem_after,
        ),
        s.LEAF_CAP_5,
    ) == ([], [])


def test_17_record_without_deferrals_is_byte_identical(tmp_path: Path) -> None:
    """Test 17: DependentRecord without deferrals and after lifting omits 'deferrals' byte-identically."""

    raw_pre_l52 = {
        "schema": "ar-dependent/v1",
        "id": "DPD-7K3F9Q",
        "revision": 1,
        "document": s.SETTINGS_DOC,
        "stands_on": [{"kind": "file", "path": CODE_FILE, "blob": "a" * 40}],
        "confirmed": {
            "task": s.LEAF_ORIGIN,
            "role": "curator",
            "statement": "Matches.",
            "commit": "b" * 40,
            "uncommitted": False,
        },
        "origin": {"task": s.TASK_BUG, "leaf": s.LEAF_ORIGIN},
    }
    parsed = DependentRecord.model_validate(raw_pre_l52)
    assert parsed.deferrals is None and "deferrals" not in parsed.to_document()
    assert canonical_text(parsed.to_document()).encode("utf-8") == canonical_text(
        raw_pre_l52
    ).encode("utf-8")

    world, code_base = s.build_deferral_world(tmp_path)
    initial = s.create_dependent(world, code_base=code_base)
    dep_id = initial["id"]
    assert "deferrals" not in initial

    spec = s.defer_spec("Temp", "Temp deferral.", {"task": s.LEAF_60})
    assert (
        s.write_dep(
            world,
            [s.defer_op(dep_id, spec)],
            rows=[s.row_op(dep_id, "deferred")],
            leaf=s.LEAF_50,
            code_base=code_base,
        ).state
        == "written"
    )
    d1 = s.stored_dependent(world.memory)["deferrals"][0]["id"]
    commit_all(world.memory, "temp defer")

    assert (
        s.write_dep(
            world,
            [s.lift_op(dep_id, d1, "Done.")],
            rows=[s.row_op(dep_id, "deferral_lifted")],
            leaf=s.LEAF_51,
            code_base=code_base,
        ).state
        == "written"
    )
    after_raw = s.stored_dependent_path(world.memory).read_bytes()
    after = json.loads(after_raw)
    assert "deferrals" not in after
    reconstructed = {
        **initial,
        "revision": after["revision"],
        **({"writer": after["writer"]} if "writer" in after else {}),
    }
    assert after_raw == canonical_text(reconstructed).encode("utf-8")


def test_lift_requires_deferral_lifted_row_and_surfaces_in_multi_op_write(
    tmp_path: Path,
) -> None:
    """Lifting refuses non-deferral_lifted or omitted rows and surfaces lifts in multi-op writes."""

    world, code_base = s.build_deferral_world(tmp_path)
    dep_id = s.create_dependent(world, code_base=code_base)["id"]
    spec = s.defer_spec("Timeout", "Waiting on L60.", {"task": s.LEAF_60})
    assert (
        s.write_dep(
            world,
            [s.defer_op(dep_id, spec)],
            rows=[s.row_op(dep_id, "deferred")],
            leaf=s.LEAF_50,
            code_base=code_base,
        ).state
        == "written"
    )
    commit_all(world.memory, "seed deferral for lift check")
    d1 = s.stored_dependent(world.memory)["deferrals"][0]["id"]

    # Wrong dispositions and missing row are refused with zero memory write.
    for rows, expected in (
        ([s.row_op(dep_id, "deferred")], "deferral_lifted"),
        ([s.row_op(dep_id, "re_deferred")], "deferral_lifted"),
        ([], "deferral_lifted"),
    ):
        refused = s.write_dep(
            world,
            [s.lift_op(dep_id, d1, "No longer stale.")],
            rows=rows,
            leaf=s.LEAF_51,
            code_base=code_base,
        )
        assert refused.state == "refused"
        msg = " ".join(p.message for p in refused.problems)
        assert d1 in msg and expected in msg
        assert [d["id"] for d in s.stored_dependent(world.memory)["deferrals"]] == [d1]

    # Multi-op deferral-only write (lift + defer) refuses 'deferred' and writes 'deferral_lifted'.
    multi_op = {
        "key": "op-multi",
        "kind": "dependent",
        "id": dep_id,
        "fields": {
            "lift": {"targetId": d1, "reason": "First passage verified."},
            "defer": s.defer_spec("Second passage", "Still waiting on L61.", {"task": s.LEAF_61}),
        },
    }
    refused_multi = s.write_dep(
        world,
        [multi_op],
        rows=[s.row_op(dep_id, "deferred")],
        leaf=s.LEAF_51,
        code_base=code_base,
    )
    assert refused_multi.state == "refused" and any(
        d1 in p.message and "deferral_lifted" in p.message for p in refused_multi.problems
    )

    written_multi = s.write_dep(
        world,
        [multi_op],
        rows=[s.row_op(dep_id, "deferral_lifted", "Lifted d1 and deferred second passage.")],
        leaf=s.LEAF_51,
        code_base=code_base,
    )
    assert written_multi.state == "written", written_multi.problems
    last = _last_row(world.memory, s.LEAF_51)
    assert last["disposition"] == "deferral_lifted" and last["deferralIds"] == [d1]
    remaining = s.stored_dependent(world.memory)["deferrals"]
    assert len(remaining) == 1 and remaining[0]["id"] != d1

    mqc = s.run_mqc(world, owner=s.LEAF_51, code_base=code_base)
    assert mqc["ok"] is True and mqc["findingCount"] == 0
    lifted = [f for f in mqc["reportOnlyFindings"] if f.get("kind") == "deferral_lifted"]
    assert len(lifted) == 1 and lifted[0]["deferralIds"] == [d1]


def test_new_dependent_with_confirmation_and_deferral_without_edit_is_refused(
    tmp_path: Path,
) -> None:
    """New dependent with confirmation + deferral refuses without doc edit, writes with edit or without deferral."""

    world, code_base = s.build_deferral_world(tmp_path)
    spec = s.defer_spec(
        "Install table", "Waiting on L60 refactor.", {"task": s.LEAF_60}, owner=s.TASK_BUG
    )
    new_with_defer = {
        "key": "dep-new",
        "kind": "dependent",
        "slug": "settings-json",
        "fields": {
            "document": s.SETTINGS_DOC,
            "stands_on": [{"kind": "file", "path": CODE_FILE}],
            "confirmed": {
                "task": s.LEAF_50,
                "role": "curator",
                "statement": "Confirming unedited doc with deferral.",
            },
            "defer": spec,
        },
    }

    # Unedited doc + confirmation + deferral -> refused naming the deferral; nothing written.
    refused = s.write_dep(world, [new_with_defer], leaf=s.LEAF_50, code_base=code_base)
    assert refused.state == "refused"
    msg = " ".join(p.message for p in refused.problems)
    assert "DFR-" in msg and "Install table" in msg and "Waiting on L60 refactor." in msg
    assert list(world.memory.glob("knowledge/dependents/*.json")) == []

    # Unedited doc + confirmation without deferral -> still writes cleanly.
    plain_doc = "docs/reference/plain.md"
    (world.code / plain_doc).write_text("# Plain\n\nBaseline plain doc.\n", encoding="utf-8")
    code_with_plain = commit_all(world.code, "add plain doc")
    new_without_defer = {
        "key": "dep-plain",
        "kind": "dependent",
        "slug": "plain",
        "fields": {
            "document": plain_doc,
            "stands_on": [{"kind": "file", "path": CODE_FILE}],
            "confirmed": {
                "task": s.LEAF_50,
                "role": "curator",
                "statement": "Confirming unedited doc without deferral.",
            },
        },
    }
    ok_plain = s.write_dep(world, [new_without_defer], leaf=s.LEAF_50, code_base=code_with_plain)
    assert ok_plain.state == "written", ok_plain.problems

    # Edited doc + confirmation + deferral -> writes with the deferral stored.
    (world.code / s.SETTINGS_DOC).write_text(
        "# Settings\n\nEdited settings baseline.\n", encoding="utf-8"
    )
    commit_all(world.code, "edit settings doc")
    ok_edited = s.write_dep(world, [new_with_defer], leaf=s.LEAF_50, code_base=code_with_plain)
    assert ok_edited.state == "written", ok_edited.problems
    stored = next(
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(world.memory.glob("knowledge/dependents/*.json"))
        if json.loads(path.read_text(encoding="utf-8"))["document"] == s.SETTINGS_DOC
    )
    assert len(stored["deferrals"]) == 1 and stored["deferrals"][0]["passage"] == "Install table"


def _write_crossing(
    world: Any,
    records: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    *,
    code_commit: str,
    commit: bool = True,
) -> WriteReport:
    crossing_id = "261112-BUG-crossing-7"
    return write_knowledge(
        WriteRequest(
            memory_root=world.memory,
            code_root=world.code,
            owner=Owner(task=s.TASK_BUG, kind="crossing", id=crossing_id),
            handoff_path="crossing-handoff.json",
            document={"records": records, "history": rows},
            commit=commit,
            authorization="test-crossing",
            coordination_root=world.root,
            repo_name=s.REPO_NAME,
            code_base=code_commit,
        ),
        code=CodeSnapshot.at_commit(world.code, code_commit),
    )


def test_crossing_write_resolves_deferred_dependent_preserving_before_deferrals(
    tmp_path: Path,
) -> None:
    """Crossing write at code_base == snapshot.commit resolves conflicted dependent and preserves deferrals."""

    d1 = s.deferral_entry("DFR-NDPEXV", {"task": s.LEAF_60}, passage="Auth defaults")
    world, _, _, blob1 = s.seed_deferred_world(tmp_path, "DPD-23C91T", [d1])
    (world.code / CODE_FILE).write_text(
        (world.code / CODE_FILE).read_text(encoding="utf-8") + "\n# settled\n", encoding="utf-8"
    )
    settled_commit = commit_all(world.code, "settled crossing merge")
    settled_blob = git(world.code, "hash-object", CODE_FILE)
    assert settled_blob != blob1

    dep_path = s.stored_dependent_path(world.memory)
    conflicted = json.loads(dep_path.read_text(encoding="utf-8"))
    for field_name in ("revision", "confirmed", "stands_on"):
        conflicted[field_name] = {
            "crossing-conflict": {"item": field_name, "reason": "both changed"}
        }
    dep_path.write_text(canonical_text(conflicted), encoding="utf-8")

    crossing = "261112-BUG-crossing-7"
    report = _write_crossing(
        world,
        [s.confirm_op("DPD-23C91T", crossing, statement="Resolved crossing conflict.")],
        [s.row_op("DPD-23C91T", "changed", "Resolved crossing conflict.", effect="clarify")],
        code_commit=settled_commit,
    )
    assert report.state == "written", report.problems
    stored = s.stored_dependent(world.memory)
    assert (
        stored["revision"] == 2
        and stored["confirmed"]["task"] == crossing
        and stored["confirmed"]["commit"] == settled_commit
        and stored["stands_on"][0]["blob"] == settled_blob
        and stored["deferrals"] == [d1]
    )


def test_crossing_write_adopts_validated_explicit_deferrals_and_clears_on_empty(
    tmp_path: Path,
) -> None:
    """Crossing write validates explicit merged deferrals (earlier or settled fileBlob), supports ops, and clears on []/None."""

    d_old = s.deferral_entry("DFR-CAA340", {"file": CODE_FILE}, passage="Old watch")
    d_keep = s.deferral_entry(
        "DFR-XP94XS", {"task": s.LEAF_60}, passage="Task watch", owner=s.TASK_CAP
    )
    world, _, _, blob1 = s.seed_deferred_world(tmp_path, "DPD-X6G7K3", [d_old])
    (world.code / CODE_FILE).write_text(
        (world.code / CODE_FILE).read_text(encoding="utf-8") + "\n# merged\n", encoding="utf-8"
    )
    settled_commit = commit_all(world.code, "settled code")
    blob2 = git(world.code, "hash-object", CODE_FILE)
    assert blob2 != blob1

    d_preserved = {**d_old, "fileBlob": blob1}
    d_updated = s.deferral_entry(
        "DFR-VPD222", {"file": CODE_FILE}, passage="Settled watch", file_blob=blob2
    )
    merged = [d_preserved, d_updated, d_keep]
    crossing = "261112-BUG-crossing-7"
    r1 = _write_crossing(
        world,
        [s.confirm_op("DPD-X6G7K3", crossing, extra_fields={"deferrals": merged})],
        [s.row_op("DPD-X6G7K3", "changed", "Merged deferrals.", effect="clarify")],
        code_commit=settled_commit,
    )
    assert r1.state == "written", r1.problems
    stored = s.stored_dependent(world.memory)
    assert [one["id"] for one in stored["deferrals"]] == ["DFR-CAA340", "DFR-VPD222", "DFR-XP94XS"]
    assert (
        stored["deferrals"][0]["fileBlob"] == blob1 and stored["deferrals"][1]["fileBlob"] == blob2
    )

    r_res = _write_crossing(
        world,
        [
            s.confirm_op(
                "DPD-X6G7K3",
                crossing,
                extra_fields={"deferrals": merged, "resolveDeferrals": ["DFR-XP94XS"]},
            )
        ],
        [s.row_op("DPD-X6G7K3", "changed", "Resolved one deferral.", effect="clarify")],
        code_commit=settled_commit,
    )
    assert r_res.state == "written", r_res.problems
    assert [one["id"] for one in s.stored_dependent(world.memory)["deferrals"]] == [
        "DFR-CAA340",
        "DFR-VPD222",
    ]

    for empty_value in ([], None):
        r_clear = _write_crossing(
            world,
            [s.confirm_op("DPD-X6G7K3", crossing, extra_fields={"deferrals": empty_value})],
            [s.row_op("DPD-X6G7K3", "changed", "Cleared deferrals.", effect="clarify")],
            code_commit=settled_commit,
        )
        assert r_clear.state == "written", r_clear.problems
        assert "deferrals" not in s.stored_dependent(world.memory)


def test_crossing_write_refuses_invalid_deferrals_and_preserves_leaf_refusals(
    tmp_path: Path,
) -> None:
    """Invalid crossing deferrals refuse cleanly; leaf direct-deferrals and unedited confirmations stay refused."""

    d1 = s.deferral_entry("DFR-VAK001", {"task": s.LEAF_60}, passage="Tracked passage")
    world, code_base, _, blob1 = s.seed_deferred_world(tmp_path, "DPD-VAK001", [d1])
    crossing = "261112-BUG-crossing-7"
    bad_cases: list[tuple[str, Any, str]] = [
        ("non-list", "not-a-list", "must be a list"),
        ("dict-not-list", d1, "must be a list"),
        ("non-object-item", ["bad"], "JSON object"),
        ("bad-schema-id", [{**d1, "id": "BAD-ID"}], "id"),
        ("file-without-blob", [s.deferral_entry("DFR-VAK002", {"file": CODE_FILE})], "fileBlob"),
        ("duplicate-id", [d1, {**d1, "passage": "Dup"}], "duplicate deferral id"),
        (
            "missing-file",
            [s.deferral_entry("DFR-VAK003", {"file": "missing/file.py"}, file_blob=blob1)],
            "not a tracked file",
        ),
    ]
    for label, bad_deferrals, expected in bad_cases:
        refused = _write_crossing(
            world,
            [s.confirm_op("DPD-VAK001", crossing, extra_fields={"deferrals": bad_deferrals})],
            [s.row_op("DPD-VAK001", "changed", "Invalid.", effect="clarify")],
            code_commit=code_base,
            commit=False,
        )
        assert refused.state == "refused", f"{label} should be refused"
        assert expected in " ".join(p.render() for p in refused.problems)

    leaf_direct = s.write_dep(
        world,
        [s.confirm_op("DPD-VAK001", s.LEAF_51, extra_fields={"deferrals": [d1]})],
        rows=[s.row_op("DPD-VAK001", "changed", effect="clarify")],
        leaf=s.LEAF_51,
        code_base=code_base,
        commit=False,
    )
    assert leaf_direct.state == "refused"
    assert "instead of setting 'deferrals' directly" in " ".join(
        p.message for p in leaf_direct.problems
    )

    leaf_unedited = s.write_dep(
        world,
        [s.confirm_op("DPD-VAK001", s.LEAF_51, statement="Unedited leaf confirm.")],
        rows=[s.row_op("DPD-VAK001", "changed", effect="clarify")],
        leaf=s.LEAF_51,
        code_base=code_base,
        commit=False,
    )
    assert leaf_unedited.state == "refused"
    assert "DFR-VAK001" in " ".join(p.message for p in leaf_unedited.problems)
