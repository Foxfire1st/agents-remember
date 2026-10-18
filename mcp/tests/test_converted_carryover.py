"""Converted carryover refuses incompatible formats before reading candidates (MIK-R56)."""

import hashlib
import json
import os
import subprocess
import time
from dataclasses import replace
from pathlib import Path

import pytest
from agents_remember.application.knowledge_worklist.leaf import recompute_leaf_worklist
from agents_remember.application.knowledge_writer import Owner, WriteRequest, write_knowledge
from agents_remember.application.memory_tools import (
    CarryoverSelection,
    memory_carryover_apply_tool,
    memory_carryover_plan_tool,
)
from agents_remember.application.task_docs.task_doc_tools import (
    TaskDocEdit,
    TaskDocTarget,
    task_doc_tool,
)
from agents_remember.application.tool_answer_log import tool_call_context
from agents_remember.application.worktree_tool_requests import (
    CloseoutApproval,
    CloseoutCommitMessages,
    StartExecution,
    TaskBases,
    TaskIdentity,
)
from agents_remember.application.worktree_tools import (
    worktree_closeout_apply_tool,
    worktree_start_tool,
)
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.kernel.primitives.tool_reports import write_tool_report
from agents_remember.mcp.tools.memory import (
    compact_carryover_payload,
    memory_carryover_apply_payload,
    memory_carryover_plan_payload,
)
from agents_remember.memory import carryover
from agents_remember.models.knowledge_files.anchor_content import content_identity
from agents_remember.models.knowledge_files.canonical import canonical_text
from agents_remember.models.knowledge_files.documents import LAYOUT_MARKER_PATH
from agents_remember.tasks import write_task_doc
from agents_remember.tasks.leaf_decisions import strict_leaf_doc
from agents_remember.worktrees.worktree_contract import load_contract
from test_memory_attribution_producers import _carryover_world
from test_worktree_support import git, init_repo


def test_plan_conversion_metadata_stays_in_full_report_not_public_answer(tmp_path):
    fixture, _contract, selection = _converted_world(tmp_path)
    plan = memory_carryover_plan_tool(fixture.cfg, selection)
    assert plan["conversion"]
    report = write_tool_report(fixture.cfg.coordination_root, "memory_carryover_plan", plan)
    compact = compact_carryover_payload(plan, report.as_posix())
    assert "conversion" not in compact
    assert json.loads(report.read_text())["conversion"] == plan["conversion"]
    assert (
        "publication" not in compact
        and json.loads(report.read_text())["publication"] == plan["publication"]
    )


def test_public_plan_logs_complete_review_facts_and_delivers_recovery_guidance(tmp_path):
    fixture, contract, selection = _converted_world(tmp_path)
    memory = contract.memory_worktree
    assert memory is not None
    source = Path(selection.source_memory)
    identity, _path, _record = _source_record_pair(source)
    full = memory_carryover_plan_tool(fixture.cfg, selection)
    before = _bytes(memory)
    with tool_call_context("memory_carryover_plan", config=fixture.cfg):
        delivered = memory_carryover_plan_payload(fixture.cfg, selection)
    details = [
        json.loads(path.read_text()) for path in fixture.coord.rglob("*memory_carryover_plan*.json")
    ]
    logged = next(value for value in details if "candidates" in value)
    assert logged["candidates"] == json.loads(json.dumps(full["candidates"]))
    assert logged["conversion"] == full["conversion"]
    assert logged["publication"] == full["publication"]
    feature = next(item for item in logged["candidates"] if item["source_path"] == "feature.py")
    assert feature["pair"] and feature["anchors"]["observations"]
    assert any(record["id"] == identity for record in feature["records"]["needed"])
    assert "history" in feature["records"]
    assert "curator" in delivered["next"]["summary"]
    assert "own rows" in delivered["next"]["summary"]
    assert "closeout" in delivered["next"]["summary"]
    assert "log" in delivered
    assert "candidates" not in delivered and "publication" not in delivered
    assert "reportPath" not in delivered and "reportPath" not in logged
    assert before == _bytes(memory)


@pytest.mark.parametrize("operation", ["memory_carryover_plan", "memory_carryover_apply"])
def test_carryover_adapter_logs_each_delivered_decision_field(tmp_path, operation):
    fixture, contract, selection = _converted_world(tmp_path)
    with tool_call_context(operation, config=fixture.cfg):
        if operation == "memory_carryover_plan":
            delivered = memory_carryover_plan_payload(fixture.cfg, selection)
        else:
            delivered = memory_carryover_apply_payload(
                fixture.cfg, selection, intent_note="recover the landed pair"
            )
    assert delivered["ok"], delivered
    logged = json.loads(Path(delivered["log"]).read_text())
    for key in ("decisions", "carriedPaths", "reportPath"):
        if key in delivered:
            assert key in logged, (key, delivered, logged)
            assert logged[key] == delivered[key]
    assert logged["candidates"] and logged["conversion"]
    assert logged["publication"]
    assert contract.memory_worktree is not None


@pytest.mark.parametrize("side", ["source", "target"])
def test_plan_adapter_returns_a_typed_legacy_refusal_without_writing_memory(tmp_path, side):
    fixture, contract, selection = _converted_world(tmp_path)
    memory = contract.memory_worktree
    assert memory is not None
    unconverted = Path(selection.source_memory) if side == "source" else memory
    (unconverted / LAYOUT_MARKER_PATH).unlink()
    before = _bytes(memory)
    with tool_call_context("memory_carryover_plan", config=fixture.cfg):
        delivered = memory_carryover_plan_payload(fixture.cfg, selection)
    assert not delivered["ok"] and delivered["code"] == "legacy-format", delivered
    assert "memory_carryover_plan" in delivered["message"]
    assert "convert" in delivered["nextAction"]
    assert _bytes(memory) == before


@pytest.mark.parametrize("bad_side", ["source", "target"])
def test_public_plan_validates_formats_before_code_or_candidates(tmp_path, monkeypatch, bad_side):
    fixture, contract, selection = _converted_world(tmp_path)
    root = Path(selection.source_memory) if bad_side == "source" else contract.memory_worktree
    assert root is not None
    (root / LAYOUT_MARKER_PATH).write_text("null")
    invalid = replace(selection, official_code_ref="no-such-code-tip")

    def unexpected(*_args, **_kwargs):
        pytest.fail("format refusal must precede code/candidate reads")

    monkeypatch.setattr(carryover, "head_commit", unexpected)
    monkeypatch.setattr(carryover, "ensure_clean", unexpected)
    monkeypatch.setattr(carryover, "changed_paths", unexpected)
    monkeypatch.setattr(carryover, "_record_context", unexpected)
    with pytest.raises((RuntimeError, ValueError), match=r"knowledge-convert.*crossing sync"):
        memory_carryover_plan_tool(fixture.cfg, invalid)


@pytest.mark.skipif(
    not os.environ.get("L56_REAL_PLAN_OUTPUT"),
    reason="Owner-admitted real-operand witness is explicitly selected",
)
def test_real_l47_plan_on_shared_clones_as_declared_pytest_process():
    scratch = Path("/var/tmp/mik-run3/L56")
    output = Path(os.environ["L56_REAL_PLAN_OUTPUT"])
    assert output.is_relative_to(scratch) and not output.exists()
    output.mkdir()
    source, target = scratch / "r56-source-memory-40ae", scratch / "r56-target-memory-068e"
    code = scratch / "ont-base-build"
    assert git(source, "rev-parse", "HEAD") == "40ae8395de72fd3d1cc275c03b723e6ff4a53fb3"
    assert git(target, "rev-parse", "HEAD") == "068e40447962057a797bad25737f2b6756d7b364"
    settings = scratch / "r56-real-native-plan/mcp-settings.json"
    request = carryover.CarryoverRequest(
        config_path=settings,
        target_contract_path=output / "uncreated-recovery-contract.md",
        code_repository_root=code,
        official_code_ref="e40d1f21660386f9b091ae5e6fee595a467d4cef",
        source_code_ref="e40d1f21660386f9b091ae5e6fee595a467d4cef",
        old_base="9547923129aa0b49d6b4f93a758f4fce70233745",
        target_memory=target,
        source_memory=source,
        code_repository_name="agents-remember",
    )
    before = {
        str(root): {
            "head": git(root, "rev-parse", "HEAD"),
            "index": git(root, "ls-files", "--stage"),
            "status": git(root, "status", "--porcelain"),
            "refs": git(root, "for-each-ref"),
        }
        for root in (source, target, code)
    }
    (output / "before.json").write_text(json.dumps(before, indent=2) + "\n")
    previous = json.loads((scratch / "r56-real-native-plan/native-plan.json").read_text())
    names = sorted(
        candidate["source_path"]
        for candidate in previous["candidates"]
        if "undeclared Agents Remember execution" in candidate.get("reason", "")
    )
    assert len(names) == 15
    started = time.monotonic()
    native = carryover.build_plan_for_request(request)
    seconds = time.monotonic() - started
    (output / "native-plan.json").write_text(json.dumps(native, indent=2) + "\n")
    by_name = {candidate["source_path"]: candidate for candidate in native["candidates"]}
    receipt = {
        "seconds": seconds,
        "candidateSource": carryover.__file__,
        "settings": str(settings),
        "beforeRefusedNames": names,
        "after": [by_name[name] for name in names],
        "counts": native["counts"],
        "missingPairCandidates": [
            candidate["source_path"]
            for candidate in native["candidates"]
            if "No such file or directory" in candidate.get("reason", "")
        ],
        "scope": "native plan only; no managed recovery leaf, apply or closeout claim",
    }
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    after = {
        str(root): {
            "head": git(root, "rev-parse", "HEAD"),
            "index": git(root, "ls-files", "--stage"),
            "status": git(root, "status", "--porcelain"),
            "refs": git(root, "for-each-ref"),
        }
        for root in (source, target, code)
    }
    (output / "after.json").write_text(json.dumps(after, indent=2) + "\n")
    assert after == before
    assert all(
        "undeclared Agents Remember execution" not in by_name[name].get("reason", "")
        for name in names
    )


@pytest.mark.usefixtures("worktree_services")
def test_recovery_leaf_owns_rows_and_one_memory_closeout(tmp_path):
    fixture, contract, selection = _converted_world(tmp_path)
    # The carryover-only fixture advanced its base; closeout also needs the real source ref there.
    git(fixture.code, "branch", "-f", contract.code_source_branch, contract.code_base_commit)
    found = strict_leaf_doc(contract.task_root, contract.leaf_id)
    assert found is not None
    path, document = found
    write_task_doc(path.parent, document.model_copy(update={"knowledgeMaintenanceScope": True}))
    _source_record_pair(Path(selection.source_memory))
    result = memory_carryover_apply_tool(
        fixture.cfg, selection, intent_note="recover before closeout"
    )
    assert result["ok"], result
    recomputed = recompute_leaf_worklist(contract)
    assert recomputed is not None
    assert [item["kind"] for item in recomputed[0]["items"]] == ["unassigned_invariant"]
    memory = contract.memory_worktree
    assert memory is not None
    head = git(memory, "rev-parse", "HEAD")
    before_rows = _bytes(memory)
    with pytest.raises(RuntimeError, match="gate"):
        worktree_closeout_apply_tool(
            fixture.cfg,
            contract.contract_path.as_posix(),
            CloseoutCommitMessages(
                code="Recovered official code", memory="Publish recovered memory"
            ),
            CloseoutApproval(intent_note="probe the still-open recovery item"),
        )
    assert git(memory, "rev-parse", "HEAD") == head and _bytes(memory) == before_rows
    written = write_knowledge(
        WriteRequest(
            memory_root=memory,
            code_root=contract.code_worktree,
            owner=Owner(task=contract.task_id, kind="leaf", id=contract.leaf_id),
            handoff_path="notes/recovery-rows.json",
            document={
                "history": [
                    {
                        "subject": "membership:INV-PPPPPP",
                        "disposition": "no_family",
                        "reason": "This recovered feature invariant belongs to no larger family guarantee.",
                    }
                ]
            },
            commit=True,
            worklist=recomputed[0],
        )
    )
    assert written.state == "written", written.render()
    assert git(memory, "rev-parse", "HEAD") == head
    closed = worktree_closeout_apply_tool(
        fixture.cfg,
        contract.contract_path.as_posix(),
        CloseoutCommitMessages(code="Recovered official code", memory="Publish recovered memory"),
        CloseoutApproval(intent_note="publish the recovery after its own rows"),
    )
    assert closed["ok"], json.dumps(closed, indent=2)
    committed = git(memory, "rev-parse", "HEAD")
    assert git(memory, "rev-list", "--count", f"{head}..{committed}") == "1"
    assert git(memory, "show", "-s", "--format=%(trailers:key=Code-Commit)", committed) == (
        "Code-Commit: " + git(contract.code_worktree, "rev-parse", "HEAD")
    )
    own = json.loads((memory / f"knowledge/history/{contract.leaf_id}.json").read_text())
    assert own["closed"] and own["rows"]


def _real_witness_custody():
    master = Path(
        "/home/firefox/projects/ar-coordination/tasks/agents-remember/260928_maintained-invariant-knowledge"
    )
    real_memory = Path("/home/firefox/projects/ar-coordination/memory-repos/ar-agents-remember")
    files = set(p for p in master.iterdir() if p.is_file())
    for folder in (master / "requirements", master / "enclosures"):
        files.update(p for p in folder.rglob("*") if p.is_file())
    return {
        "realMemoryRefs": git(real_memory, "for-each-ref"),
        "realTaskFolders": sorted(p.name for p in master.parent.iterdir() if p.is_dir()),
        "mikCanonicalFiles": {
            p.relative_to(master).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(files)
        },
    }


def _shared_real_clone(seed, destination, commit):
    assert seed.is_relative_to(Path("/var/tmp/mik-run3/L56"))
    subprocess.run(["git", "clone", "--shared", str(seed), str(destination)], check=True)
    git(destination, "switch", "-C", "main", commit)
    assert Path(git(destination, "remote", "get-url", "origin")).is_relative_to(
        Path("/var/tmp/mik-run3/L56")
    )


@pytest.mark.skipif(
    not os.environ.get("L56_REAL_APPLY_OUTPUT"),
    reason="Owner-admitted real apply witness is explicitly selected",
)
@pytest.mark.usefixtures("worktree_services")
def test_real_l47_public_apply_in_managed_scratch_recovery_leaf():
    scratch = Path("/var/tmp/mik-run3/L56")
    output = Path(os.environ["L56_REAL_APPLY_OUTPUT"])
    assert output.is_relative_to(scratch) and not output.exists()
    output.mkdir()
    before = _real_witness_custody()
    (output / "real-before.json").write_text(json.dumps(before, indent=2) + "\n")
    coordination, code = output / "coordination", output / "agents-remember"
    memory = coordination / "memory-repos/ar-agents-remember"
    _shared_real_clone(scratch / "ont-base-build", code, "e40d1f21660386f9b091ae5e6fee595a467d4cef")
    _shared_real_clone(scratch / "ont-memory", memory, "068e40447962057a797bad25737f2b6756d7b364")
    source_memory = coordination / "source-memory-40ae"
    _shared_real_clone(
        scratch / "ont-memory", source_memory, "40ae8395de72fd3d1cc275c03b723e6ff4a53fb3"
    )
    settings = output / "mcp-settings.json"
    settings.write_text(
        json.dumps(
            {
                "version": 1,
                "coordinationRoot": str(coordination),
                "workspaceRoot": str(output),
                "repositories": {"agents-remember": {"path": str(code)}},
                "providers": {},
            },
            indent=2,
        )
        + "\n"
    )
    config = load_config(settings)
    for repository in (code, memory):
        git(repository, "branch", "super", "main")
    target = TaskDocTarget(repo_id="agents-remember", task_name="r56-real-recovery")
    created = task_doc_tool(
        config,
        target,
        operation="create",
        edit=TaskDocEdit(
            fields={
                "id": "R56-REAL-MASTER",
                "slug": "task",
                "title": "Real scratch recovery",
                "kind": "master",
                "repo": "agents-remember",
                "createdAt": "2026-10-14T00:00:00+00:00",
                "executionNature": "organizational",
                "subTasks": [
                    {
                        "number": "R56-REAL-L1",
                        "name": "Recover L47",
                        "file": "recovery.md",
                        "status": "planning",
                    }
                ],
            }
        ),
    )
    assert created["ok"], created
    sprint = task_doc_tool(
        config,
        TaskDocTarget(repo_id="agents-remember", task_name="r56-real-sprint"),
        operation="create",
        edit=TaskDocEdit(
            fields={
                "id": "R56-REAL-SPRINT",
                "slug": "task",
                "title": "Scratch recovery sprint",
                "kind": "master",
                "repo": "agents-remember",
                "createdAt": "2026-10-14T00:00:00+00:00",
                "orchestrates": ["r56-real-recovery"],
                "integrationBranch": "super",
                "executionGraph": {
                    "nodes": [
                        {"repository": "agents-remember", "path": "r56-real-recovery/task.json"}
                    ],
                    "edges": [],
                },
            }
        ),
    )
    assert sprint["ok"], sprint
    leaf = task_doc_tool(
        config,
        replace(target, slug="recovery"),
        operation="create",
        edit=TaskDocEdit(
            fields={
                "id": "R56-REAL-L1",
                "slug": "recovery",
                "title": "Recover L47 memory",
                "kind": "subTask",
                "repo": "agents-remember",
                "master": "task.md",
                "createdAt": "2026-10-14T00:00:00+00:00",
                "knowledgeMaintenanceScope": True,
            }
        ),
    )
    assert leaf["ok"], leaf
    started = worktree_start_tool(
        config,
        TaskIdentity(
            repo_id="agents-remember",
            task_name="r56-real-recovery",
            worktree_name="r56-real-l1",
            leaf_id="R56-REAL-L1",
            parent_task="r56-real-recovery",
        ),
        bases=TaskBases(source_branch="super", memory_mode="external"),
        execution=StartExecution(skip_provider_setup=True),
    )
    (output / "start.json").write_text(json.dumps(started, indent=2) + "\n")
    assert started["ok"], started
    contract = load_contract(Path(started["contract_path"]))
    assert contract.memory_worktree is not None
    selection = CarryoverSelection(
        repo_id="agents-remember",
        contract_path=str(contract.contract_path),
        source_memory=str(source_memory),
        official_code_ref="main",
        source_code_ref="e40d1f21660386f9b091ae5e6fee595a467d4cef",
        old_base="9547923129aa0b49d6b4f93a758f4fce70233745",
        replace_existing=True,
    )
    plan = memory_carryover_plan_tool(config, selection)
    (output / "plan.json").write_text(json.dumps(plan, indent=2) + "\n")
    head = git(contract.memory_worktree, "rev-parse", "HEAD")
    memory_before = _bytes(contract.memory_worktree)
    applied = memory_carryover_apply_tool(
        config, selection, intent_note="recover the proven L47 pairs"
    )
    (output / "apply.json").write_text(json.dumps(applied, indent=2) + "\n")
    after = _real_witness_custody()
    (output / "real-after.json").write_text(json.dumps(after, indent=2) + "\n")
    receipt = {
        "source": carryover.__file__,
        "headBefore": head,
        "headAfter": git(contract.memory_worktree, "rev-parse", "HEAD"),
        "appliedState": applied.get("state"),
        "writtenPaths": applied.get("written_paths"),
        "realCustodyUnchanged": before == after,
        "worktreeBytesRestored": memory_before == _bytes(contract.memory_worktree),
        "selection": {"replace_existing": True, "include_review_required": []},
        "scope": "public plan/apply refusal and restore; success is the native rule-11 fixture",
    }
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    assert before == after
    assert not applied["ok"] and applied["state"] == "refused", applied
    assert applied["reason"] == "knowledge-validator-refused"
    assert any(
        violation["rule"] == "R27.2-new-record" and "INV-EX6T0F" in violation["message"]
        for violation in applied["violations"]
    )
    assert not applied["written_paths"]
    assert receipt["worktreeBytesRestored"]
    assert receipt["headBefore"] == receipt["headAfter"]


def _request(tmp_path: Path) -> carryover.CarryoverRequest:
    target, source = tmp_path / "target", tmp_path / "source"
    for root in (target, source):
        init_repo(root)
        marker = root / LAYOUT_MARKER_PATH
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text('{"schema":"ar-memory-layout/v2","conversion":"1"}\n')
    return carryover.CarryoverRequest(
        config_path=tmp_path / "settings.json",
        target_contract_path=tmp_path / "series-contract.md",
        code_repository_root=tmp_path / "code",
        official_code_ref="official",
        source_code_ref="source",
        old_base="base",
        target_memory=target,
        source_memory=source,
        code_repository_name="repo",
    )


def _bytes(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and ".git" not in p.relative_to(root).parts
    }


@pytest.mark.parametrize("side", ["source_memory", "target_memory"])
def test_plan_refuses_unconverted_side_before_code_or_candidate_reads(tmp_path, monkeypatch, side):
    request = _request(tmp_path)
    root = getattr(request, side)
    (root / LAYOUT_MARKER_PATH).unlink()
    before = (_bytes(request.target_memory), _bytes(request.source_memory))

    def forbidden(*args, **kwargs):
        raise AssertionError("code/candidate read preceded the format check")

    monkeypatch.setattr(carryover, "head_commit", forbidden)
    with pytest.raises(RuntimeError, match="knowledge-convert") as caught:
        carryover.build_plan_for_request(request)
    assert "crossing sync" in str(caught.value)
    assert (_bytes(request.target_memory), _bytes(request.source_memory)) == before


def test_plan_refuses_mismatched_conversion_before_code_reads(tmp_path, monkeypatch):
    request = _request(tmp_path)
    (request.source_memory / LAYOUT_MARKER_PATH).write_text(
        '{"schema":"ar-memory-layout/v2","conversion":"2"}\n'
    )
    monkeypatch.setattr(carryover, "head_commit", lambda *args: pytest.fail("read code"))
    with pytest.raises(RuntimeError, match="conversion-format") as caught:
        carryover.build_plan_for_request(request)
    assert "knowledge-convert" in str(caught.value) and "crossing sync" in str(caught.value)


@pytest.mark.parametrize("marker", ["{}", "{", '{"schema":"ar-memory-layout/v1","conversion":"1"}'])
def test_plan_names_unreadable_format_and_does_not_read_candidates(tmp_path, monkeypatch, marker):
    request = _request(tmp_path)
    (request.source_memory / LAYOUT_MARKER_PATH).write_text(marker)
    monkeypatch.setattr(carryover, "head_commit", lambda *args: pytest.fail("read code"))
    with pytest.raises(RuntimeError, match="layout"):
        carryover.build_plan_for_request(request)


def test_missing_memory_is_not_reported_as_legacy(tmp_path):
    request = _request(tmp_path)
    with pytest.raises(RuntimeError, match="does not exist") as caught:
        carryover.build_plan_for_request(replace(request, source_memory=tmp_path / "missing"))
    assert "code legacy-format" not in str(caught.value)


def _converted_world(tmp_path):
    fixture, contract, old_base, official, source = _carryover_world(tmp_path, converted=True)
    source = Path(source)
    document = {
        "schema": "ar-onboarding-file/v1",
        "path": "feature.py",
        "realizes": [],
        "references": {
            "1": {
                "targets": [
                    {
                        "kind": "code",
                        "anchor": {
                            "locator": {"kind": "file"},
                            "blob": git(fixture.code, "rev-parse", f"{official}:feature.py"),
                            "content": content_identity(b"FEATURE = 1\n"),
                        },
                    }
                ]
            }
        },
    }
    (source / "onboarding/feature.py.md").write_text(
        "# Feature\n\n## Evidence\n\n- Landed feature. [1]\n"
    )
    (source / "onboarding/feature.py.json").write_text(canonical_text(document))
    selection = CarryoverSelection(
        repo_id=contract.repo_name,
        contract_path=str(contract.contract_path),
        source_memory=str(source),
        official_code_ref="official",
        source_code_ref="source",
        old_base=old_base,
    )
    return fixture, contract, selection


def test_public_converted_apply_carries_pair_without_commit_and_repeats(tmp_path):
    fixture, contract, selection = _converted_world(tmp_path)
    memory = contract.memory_worktree
    assert memory is not None
    head = git(memory, "rev-parse", "HEAD")
    index_path = Path(git(memory, "rev-parse", "--path-format=absolute", "--git-path", "index"))
    index = index_path.read_bytes()
    plan = memory_carryover_plan_tool(fixture.cfg, selection)
    candidate = next(item for item in plan["candidates"] if item["source_path"] == "feature.py")
    assert candidate["kind"] == "file-sidecar" and candidate["decision"] == "auto-carry", candidate
    assert candidate["anchors"]["all_current"]
    first = memory_carryover_apply_tool(fixture.cfg, selection, intent_note="recover landed memory")
    assert first["ok"] and first["state"] == "carried-into-worktree", json.dumps(first, indent=2)
    assert "onboarding/feature.py.md" in first["written_paths"]
    assert "onboarding/feature.py.json" in first["written_paths"]
    assert first["worklist"] is not None
    assert git(memory, "rev-parse", "HEAD") == head
    assert index_path.read_bytes() == index
    repeated = memory_carryover_apply_tool(fixture.cfg, selection, intent_note="retry recovery")
    assert repeated["ok"] and repeated["state"] == "nothing-to-carryover", repeated
    assert git(memory, "rev-parse", "HEAD") == head


@pytest.mark.parametrize("kind", ["route-overview", "memory-only-doc", "entity-catalog"])
def test_public_plan_and_apply_keep_each_document_kind_with_its_sidecar(tmp_path, kind):
    fixture, contract, selection = _converted_world(tmp_path)
    source = Path(selection.source_memory)
    anchor = json.loads((source / "onboarding/feature.py.json").read_text())["references"]["1"][
        "targets"
    ][0]["anchor"]
    anchor["path"] = "feature.py"
    if kind == "route-overview":
        markdown, key = "onboarding/overview.md", "."
        document: dict[str, object] = {"schema": "ar-onboarding-route/v1", "path": key}
    else:
        key = "entity-catalog" if kind == "entity-catalog" else "notes"
        markdown = "onboarding/entities.md" if kind == "entity-catalog" else "onboarding/notes.md"
        document = {
            "schema": "ar-onboarding-file/v1",
            "path": "entities" if kind == "entity-catalog" else key,
            "realizes": [],
        }
    document["references"] = {"1": {"targets": [{"kind": "code", "anchor": anchor}]}}
    facts = markdown[:-3] + ".json"
    (source / markdown).write_text("# Carried document\n\nCurrent feature. [1]\n")
    (source / facts).write_text(canonical_text(document))
    plan = memory_carryover_plan_tool(fixture.cfg, selection)
    candidate = next(item for item in plan["candidates"] if item["source_path"] == key)
    assert candidate["kind"] == kind and candidate["anchors"]["all_current"], candidate
    if kind == "memory-only-doc":
        assert candidate["evidence"] == "memory-only-anchors-current", candidate
        assert candidate["decision"] == "auto-carry", candidate
    memory = contract.memory_worktree
    assert memory is not None
    head = git(memory, "rev-parse", "HEAD")
    result = memory_carryover_apply_tool(
        fixture.cfg,
        selection,
        intent_note="recover the paired document",
        include_review_required=[key],
    )
    assert result["ok"] and result["state"] == "carried-into-worktree", result
    assert {markdown, facts}.issubset(result["written_paths"])
    assert (memory / markdown).read_bytes() == (source / markdown).read_bytes()
    assert (memory / facts).read_bytes() == (source / facts).read_bytes()
    assert git(memory, "rev-parse", "HEAD") == head


def _source_record_pair(source):
    identity = "INV-PPPPPP"
    record = {
        "schema": "ar-invariant/v1",
        "id": identity,
        "revision": 1,
        "status": "accepted",
        "origin": {"task": "old"},
        "admission": {
            "criteria": ["prevents_costly_mistake"],
            "justification": "An incorrect feature leaves recovered memory describing code that did not land.",
        },
        "statement": "Feature holds.",
        "applicability": "Always.",
        "conditions": [],
        "exclusions": [],
        "supersedes": [],
    }
    path = f"knowledge/invariants/{identity}-feature.json"
    for suffix, data in ((".json", canonical_text(record)), (".md", "# Feature invariant\n")):
        destination = source / (path[:-5] + suffix)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(data)
    facts = source / "onboarding/feature.py.json"
    sidecar = json.loads(facts.read_text())
    anchor = sidecar["references"]["1"]["targets"][0]["anchor"]
    sidecar["realizes"] = [
        {
            "id": "RLZ-PPPPPP",
            "invariant": identity,
            "role": "enforcement",
            "rationale": "The feature enforces the invariant.",
            "anchor": anchor,
        }
    ]
    facts.write_text(canonical_text(sidecar))
    return identity, path, record


@pytest.mark.parametrize("target_record", ["missing", "different"])
def test_public_records_travel_but_differing_records_and_history_do_not(tmp_path, target_record):
    fixture, contract, selection = _converted_world(tmp_path)
    source, memory = Path(selection.source_memory), contract.memory_worktree
    assert memory is not None
    identity, path, record = _source_record_pair(source)
    history = source / "knowledge/history/branch.json"
    history.parent.mkdir(parents=True, exist_ok=True)
    history.write_text(
        canonical_text(
            {
                "schema": "ar-history/v1",
                "leaf": "branch",
                "closed": True,
                "rows": [
                    {
                        "id": "ROW-PPPPPP",
                        "subject": identity,
                        "revision": 1,
                        "disposition": "no_impact",
                        "reason": "Branch reading aid.",
                        "items": [],
                        "covers": [],
                    }
                ],
            }
        )
    )
    if target_record == "different":
        destination = memory / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(canonical_text({**record, "statement": "Target statement stays."}))
        destination.with_suffix(".md").write_text("# Target invariant\n")
    before, head = _bytes(memory), git(memory, "rev-parse", "HEAD")
    plan = memory_carryover_plan_tool(fixture.cfg, selection)
    candidate = next(item for item in plan["candidates"] if item["source_path"] == "feature.py")
    assert candidate["records"]["history"][0]["reading_aid_only"]
    result = memory_carryover_apply_tool(
        fixture.cfg,
        selection,
        intent_note="recover record dependency",
        include_review_required=["feature.py"],
    )
    if target_record == "different":
        assert (
            candidate["decision"] == "review-required"
            and candidate["records"]["conflicts"][0]["state"] == "record-differs"
        ), candidate
        assert not result["ok"] and result["state"] == "refused", result
        assert _bytes(memory) == before
    else:
        assert [item["id"] for item in candidate["records"]["needed"]] == [identity]
        assert result["ok"] and result["state"] == "carried-into-worktree", json.dumps(
            result, indent=2
        )
        assert {path, path[:-5] + ".md"}.issubset(result["written_paths"])
        assert (memory / path).read_bytes() == (source / path).read_bytes()
        assert not (memory / "knowledge/history/branch.json").exists()
    assert git(memory, "rev-parse", "HEAD") == head


@pytest.mark.parametrize("member", ["md", "json"])
@pytest.mark.parametrize("dirty", [False, True])
def test_public_document_member_cut_resumes_only_equal_bytes(tmp_path, member, dirty):
    fixture, contract, selection = _converted_world(tmp_path)
    memory = contract.memory_worktree
    assert memory is not None
    source = Path(selection.source_memory)
    existing = memory / f"onboarding/feature.py.{member}"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_bytes((source / existing.relative_to(memory)).read_bytes())
    if dirty:
        existing.write_bytes(existing.read_bytes() + b" \n")
    before, head = _bytes(memory), git(memory, "rev-parse", "HEAD")
    stamp = existing.stat().st_mtime_ns
    plan = memory_carryover_plan_tool(fixture.cfg, selection)
    candidate = next(item for item in plan["candidates"] if item["source_path"] == "feature.py")
    assert candidate["pair"]["target_missing"]
    assert candidate["decision"] == ("review-required" if dirty else "auto-carry"), candidate
    result = memory_carryover_apply_tool(fixture.cfg, selection, intent_note="resume document pair")
    assert existing.read_bytes() == before[existing.relative_to(memory).as_posix()]
    assert existing.stat().st_mtime_ns == stamp
    assert git(memory, "rev-parse", "HEAD") == head
    if dirty:
        assert _bytes(memory) == before
        assert not result["carried"] and not result["written_paths"]
    else:
        assert result["ok"] and result["state"] == "carried-into-worktree", result
        for suffix in ("md", "json"):
            path = f"onboarding/feature.py.{suffix}"
            assert (memory / path).read_bytes() == (source / path).read_bytes()
        assert existing.relative_to(memory).as_posix() not in result["payload_written_paths"]


@pytest.mark.parametrize("member", ["md", "json"])
def test_document_partial_retry_validates_whole_tree_and_names_existing_member(tmp_path, member):
    fixture, contract, selection = _converted_world(tmp_path)
    memory = contract.memory_worktree
    assert memory is not None
    source = Path(selection.source_memory)
    relative = f"onboarding/feature.py.{member}"
    (memory / relative).parent.mkdir(parents=True, exist_ok=True)
    (memory / relative).write_bytes((source / relative).read_bytes())
    (memory / "onboarding/notes.md").write_text("# Notes\n\nUnresolved reference. [999]\n")
    (memory / "onboarding/notes.json").write_text(
        canonical_text(
            {"schema": "ar-onboarding-file/v1", "path": "notes", "realizes": [], "references": {}}
        )
    )
    before, head = _bytes(memory), git(memory, "rev-parse", "HEAD")
    index = Path(git(memory, "rev-parse", "--path-format=absolute", "--git-path", "index"))
    staged = index.read_bytes()
    result = memory_carryover_apply_tool(
        fixture.cfg, selection, intent_note="retry full validation"
    )
    assert result["state"] == "refused" and result["reason"] == "knowledge-validator-refused"
    assert result["route_index_refresh"]["state"] == "refreshed"
    assert relative in result["carried_paths_differ_from_head"]
    assert _bytes(memory) == before and git(memory, "rev-parse", "HEAD") == head
    assert index.read_bytes() == staged and not result["written_paths"]
