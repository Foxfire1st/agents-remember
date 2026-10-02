"""MIK-R24 rules 5 and 9: the master line's toolchain reads the text format, and unconverted memory
is read as legacy-format and refused elsewhere once its official line is converted.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

import pytest
from agents_remember.application import memory_tools
from agents_remember.application.memory_quality import census as census_module
from agents_remember.application.memory_quality.census import (
    PreparedMemoryCensus,
    census_curator_candidates,
)
from agents_remember.application.memory_quality.census_base import census_comparison
from agents_remember.application.memory_quality.converted_base import (
    context_check_base,
    converted_check_base,
)
from agents_remember.application.memory_tools import CitationOperationScope, memory_init_tool
from agents_remember.application.read_files import read_ar_files_tool
from agents_remember.application.worktree_services import MemoryQualityAdapter
from agents_remember.kernel import memory_init
from agents_remember.kernel.coordination_context_resolver import StorageSettings
from agents_remember.kernel.memory_init import LAYOUT_MARKER_TEXT
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.mcp.tools import memory as memory_tool_payloads
from agents_remember.memory.conversion import card_authoring
from agents_remember.memory.conversion.card_authoring import author_card_references
from agents_remember.memory.conversion.code_objects import CodeObjects
from agents_remember.memory.conversion.convert import convert_memory
from agents_remember.memory.conversion.inputs import memory_from_directory, write_changed
from agents_remember.memory_quality.check import DriftCheckContext, run_memory_quality_check
from agents_remember.memory_quality.converted_cards import converted_card_metadata
from agents_remember.memory_quality.converted_check import converted_knowledge_check
from agents_remember.memory_quality.final_certification.catalog import (
    ReadinessProjectionInput,
    final_catalog_readiness,
)
from agents_remember.memory_quality.knowledge_validator.trees import (
    CodeDirectory,
    knowledge_tree_from_directory,
)
from agents_remember.memory_quality.knowledge_validator.validator import validate_tree
from agents_remember.memory_quality.memory_census import build_memory_census
from agents_remember.memory_quality.memory_census_scope import (
    MemoryCensusPathChange,
    MemoryCensusScope,
)
from agents_remember.memory_quality.reference_state import check_references, fix_references
from agents_remember.models.knowledge_files.canonical import canonical_text
from agents_remember.models.knowledge_files.ids import derived_record_id
from agents_remember.observer import reset_ambient
from agents_remember.worktrees.knowledge_crossing import unconverted_line_refusal
from agents_remember.worktrees.modules.git import worktree_candidate_tree
from knowledge_conversion_test_support import (
    APP,
    APP_SIDECAR,
    APP_SOURCE,
    ROUTE_CARD,
    CodeFixture,
    code_repository,
    git,
    memory_repository,
)
from test_read_ar_files import REPO, _build_context, _make_config, _write_route_index


def _convert_in_place(memory: Path, code: CodeFixture) -> None:
    outcome = convert_memory(
        memory_from_directory(memory), CodeObjects(code.root), paired_commit=code.head
    )
    write_changed(memory, outcome.changed)


def test_read_ar_files_returns_legacy_format_or_resolved_references(tmp_path: Path) -> None:
    code = code_repository(tmp_path / "workspace" / REPO)
    memory = tmp_path / "memory"
    memory_repository(memory, code)
    onboarding = memory / "onboarding"
    _write_route_index(onboarding, "", covered=[APP])
    config = _make_config(tmp_path, code.root)
    context = _build_context(
        code.root, onboarding, coordination_root=config.coordination_root, storage_mode="external"
    )
    reset_ambient()

    def read() -> dict:
        return read_ar_files_tool(config, repo_id=REPO, files=[{"path": APP}], _context=context)

    legacy = read()
    assert legacy["files"][0]["format"] == "legacy-format"
    assert "references" not in legacy["files"][0] and "sidecar" not in legacy["files"][0]
    assert legacy["published_intent"]["state"] == "legacy-format"

    _convert_in_place(memory, code)
    sidecar_path = onboarding / f"{APP}.json"
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    invariant = derived_record_id("invariant", "inv-one")
    sidecar["references"]["9"] = {"targets": [{"kind": "invariant", "id": invariant}]}
    sidecar_path.write_text(canonical_text(sidecar), encoding="utf-8")

    payload = read()
    # The converted tree's knowledge section is the derived index's (MIK-R23 rule 6), read through
    # this same route; test_knowledge_index_reuse measures that block's content.
    block = payload["published_intent"]
    assert block["state"] == "recorded", block
    assert block["memoryTree"]["indexState"] == "complete"
    statements = {row.get("statement") for row in block["seeds"][0]["rows"]}  # leaf (MIK-R01)
    assert "Alpha adds one." in statements  # the exported invariant realized in this file
    converted = payload["files"][0]
    assert converted["format"] == "text/v2"
    assert "- alpha and beta compute the total. [2]" in converted["onboarding"]
    assert converted["sidecar"]["path"] == APP
    by_number = {reference["number"]: reference for reference in converted["references"]}
    assert by_number[2]["note"] == "alpha and beta compute the total."
    assert by_number[2]["targets"][0]["anchor"]["path"] == APP  # filled in for the own file
    assert by_number[9]["targets"][0]["record"]["statement"] == "Alpha adds one."


def test_references_are_checked_and_only_mechanical_moves_are_fixed(tmp_path: Path) -> None:
    code = code_repository(tmp_path / "code")
    memory = tmp_path / "memory"
    memory_repository(memory, code)
    _convert_in_place(memory, code)
    assert check_references(memory, code.root)["reportOnlyFindings"] == []

    shifted = '"""App."""\n# one\n# two\n' + APP_SOURCE.split('"""App."""\n', 1)[1]
    (code.root / APP).write_text(shifted.replace("total * 2", "total * 3"), encoding="utf-8")
    before = check_references(memory, code.root)
    assert before["ok"] is True and before["findingCount"] == 0  # stale is never a finding
    stale = {(item["field"], item["state"]) for item in before["reportOnlyFindings"]}
    assert {state for _, state in stale} == {"stale"}

    fixed = fix_references(memory, code.root)

    references = json.loads((memory / f"onboarding/{APP}.json").read_text())["references"]
    assert references["4"]["targets"][0]["anchor"]["locator"] == {
        "kind": "line_range",
        "start": 7,
        "end": 7,
    }  # the literal moved two lines down and was re-found exactly once
    assert fixed["refreshedAnchors"] >= 3
    after = {(item["path"], item["field"]) for item in fixed["stale"]}
    # beta's body changed: that is the curator's to refresh, in the card and in the route overview.
    assert after == {
        (f"onboarding/{APP}.json", "references.2.targets.1"),
        ("onboarding/src/overview.json", "references.1.targets.0"),
    }


def test_memory_quality_reads_the_converted_format(tmp_path: Path) -> None:
    code = code_repository(tmp_path / "code")
    memory = tmp_path / "memory"
    memory_repository(memory, code)
    _convert_in_place(memory, code)
    git(memory, "add", "-A")
    git(memory, "commit", "-q", "-m", "converted")
    result = run_memory_quality_check(
        memory / "onboarding",
        drift_context=DriftCheckContext(code_repository_root=code.root, context=None),
    )

    assert result["ok"] is True, result["findings"]
    assert result["checks"]["knowledge.converted"]["status"] == "converted"
    assert result["checks"]["style.update_history.history_order"]["status"] == (
        "not-applicable-converted"
    )
    assert "integrity.onboarding_drift_check.summary" not in result["checks"]

    # L37 P1c, C2: the final catalog's drift item is that slot's result under its converted name.
    # It read ``fail`` with no finding, because nothing is stored under the drift check's name.
    def drift_item(checks: dict[str, Any]) -> dict[str, Any]:
        projected = final_catalog_readiness(ReadinessProjectionInput(executed_checks=checks))
        (item,) = (
            one for one in cast(list[dict[str, Any]], projected["items"])
            if one["item"]["itemId"] == "integrity.onboarding_drift_check.summary"
        )  # fmt: skip
        return item

    passing = drift_item(result["checks"])
    assert (passing["status"], passing["findingCount"]) == ("pass", 0)
    refused = {**result["checks"]["knowledge.converted"], "ok": False, "findingCount": 2}
    failing = drift_item({**result["checks"], "knowledge.converted": refused})
    assert (failing["status"], failing["findingCount"]) == ("fail", 2)
    absent = drift_item({})  # a run that executed neither spelling is still a failure
    assert (absent["status"], absent["findingCount"]) == ("fail", 0)


def test_an_unconverted_leaf_is_refused_only_once_its_official_line_is_converted(
    tmp_path: Path,
) -> None:
    code = code_repository(tmp_path / "code")
    memory = tmp_path / "memory"
    memory_repository(memory, code)
    leaf = tmp_path / "leaf"
    git(memory, "worktree", "add", "-q", "-b", "leaf", str(leaf))

    def refusal() -> str | None:
        return unconverted_line_refusal(
            memory_worktree=leaf,
            memory_repository=memory,
            official_branch="main",
            operation="knowledge-ingest",
        )

    assert refusal() is None  # before the cutover every line is unconverted: nothing changes
    _convert_in_place(memory, code)
    git(memory, "add", "-A")
    git(memory, "commit", "-q", "-m", "convert the official line")

    refused = refusal()
    assert refused is not None and "crossing sync" in refused and "worktree_sync" in refused
    assert (
        unconverted_line_refusal(
            memory_worktree=memory, memory_repository=memory, official_branch="main", operation="x"
        )
        is None
    )


def test_memory_init_creates_new_memory_in_the_text_format_only(tmp_path: Path) -> None:
    root = tmp_path / "world"
    git_root = root / "repo"
    git_root.mkdir(parents=True)
    git(git_root, "init", "-q", "-b", "main")
    (root / "coordination").mkdir()
    settings = root / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "version": 1,
                "coordinationRoot": (root / "coordination").as_posix(),
                "workspaceRoot": root.as_posix(),
                "repositories": {"repo": {}},
            }
        ),
        encoding="utf-8",
    )
    config = load_config(settings)
    assert (
        canonical_text({"schema": "ar-memory-layout/v2", "conversion": "1"}) == LAYOUT_MARKER_TEXT
    )

    failed_init = {"returncode": 128, "stderr": "fatal: cannot init"}
    with mock.patch.object(memory_init, "_git_init_result", return_value=failed_init):
        failed = memory_init_tool(config, repo_id="repo", initial_branch="main")
    assert (failed["ok"], failed["layoutMarker"]) == (False, "not-created")  # L24 nit, at L37

    created = memory_init_tool(config, repo_id="repo", initial_branch="main")
    memory = Path(str(created["memoryRoot"]))
    assert created["layoutMarker"] == "created"
    assert (memory / "knowledge/layout.json").read_text(encoding="utf-8") == LAYOUT_MARKER_TEXT

    (memory / "knowledge/layout.json").unlink()
    (memory / "onboarding/legacy.py.md").write_text("# legacy\n", encoding="utf-8")
    repaired = memory_init_tool(config, repo_id="repo", initial_branch="main")
    assert repaired["layoutMarker"] == "unconverted-existing-memory"
    assert not (memory / "knowledge/layout.json").exists()


# --------------------------------------------------------------------------------------------------
# P1b: the memory census on a converting leaf, and converted-card authoring
# --------------------------------------------------------------------------------------------------


def _tree(repository: Path) -> str:
    with TemporaryDirectory() as scratch:
        return worktree_candidate_tree(repository, Path(scratch) / "index")


def _census(memory: Path, head: str, compared: str, changed: tuple[str, ...]) -> Any:
    """The census of the memory worktree's working tree against ``compared`` (K_B, or its
    conversion), for a leaf whose code changed ``changed``."""

    candidate = _tree(memory)
    listed = git(memory, "diff", "--name-only", compared, candidate).splitlines()
    pair = SimpleNamespace(
        memoryRoot=str(memory),
        onboardingRoot=str(memory / "onboarding"),
        repoId="demo",
        codeRoot=str(memory),
    )
    scope = SimpleNamespace(
        pair_identity=pair,
        memory_baseline_commit=head,
        memory_candidate_tree=candidate,
        memory_comparison_tree=compared,
        working_changes=(),
        working_paths=changed,
        committed_paths=(),
        memory_paths=tuple(listed),
        memory_changes=tuple(MemoryCensusPathChange("M", path, path) for path in listed),
    )
    result = build_memory_census(cast(MemoryCensusScope, scope), settings=StorageSettings())
    return SimpleNamespace(scope=scope, result=result)


def test_the_census_compares_a_converting_leaf_with_its_converted_base(tmp_path: Path) -> None:
    """MIK-R24 rule 7 for the census (L37 P1b): a converted card's source comes from the converted
    format, and the conversion itself is no task edit; a sidecar counts only beyond its anchors'
    mechanical fields (MIK-R30 rule 3)."""

    code = code_repository(tmp_path / "code")
    memory = tmp_path / "memory"
    head = memory_repository(memory, code)
    compare = census_comparison(
        cast(
            Any,
            SimpleNamespace(
                code_repo_path=code.root,
                code_base_commit=code.head,
                coordination_root=tmp_path / "coordination",
            ),
        )
    )
    assert compare(memory, head, _tree(memory)) == head  # an unconverted candidate: K_B itself
    _convert_in_place(memory, code)
    compared = compare(memory, head, _tree(memory))
    assert git(memory, "diff", "--name-only", compared, _tree(memory)) == ""  # the conversion
    for kept in ("knowledge.sqlite", ".gitignore"):  # not a converted kind: K_B's own blob
        assert git(memory, "rev-parse", f"{compared}:{kept}") == git(
            memory, "rev-parse", f"{head}:{kept}"
        )

    census = _census(memory, head, compared, (APP,))
    assert census.result.blockers == ()
    rows = {row.identity.memoryRootRelativePath: row for row in census.result.rows}
    assert set(rows) == {f"onboarding/{APP}.md", ROUTE_CARD}
    assert all("task-edited-onboarding" not in row.reasons for row in rows.values())
    candidates = census_curator_candidates(cast(PreparedMemoryCensus, census))
    assert {(one.sourceFile, one.classification) for one in candidates} == {
        (APP, "file-sidecar"),
        ("src", "route-overview"),
    }
    unconverted_comparison = _census(memory, head, head, (APP,))
    assert len(unconverted_comparison.result.rows) > len(rows)  # every card read as edited

    sidecar = json.loads((memory / APP_SIDECAR).read_text(encoding="utf-8"))
    sidecar["references"]["1"]["targets"][0]["anchor"]["blob"] = "0" * 40  # mechanical only
    (memory / APP_SIDECAR).write_text(canonical_text(sidecar), encoding="utf-8")
    mechanical = _census(memory, head, compared, ())
    assert mechanical.result.rows == ()
    sidecar["references"]["1"]["note"] = "A curator's new note."
    (memory / APP_SIDECAR).write_text(canonical_text(sidecar), encoding="utf-8")
    counted = _census(memory, head, compared, ())
    assert [row.reasons for row in counted.result.rows] == [("task-edited-onboarding",)]


def test_the_census_is_captured_and_rechecked_against_the_comparison_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both captures of the census scope -- before the run and before the report is published --
    compare against the contract's comparison base, so they agree with each other."""

    contract = SimpleNamespace(worktree_group=tmp_path)
    compare = object()
    captured: list[object] = []
    scope = SimpleNamespace(code_input=None, to_payload=lambda: {})

    def capture(_contract: object, *, code_input: object = None, comparison: object) -> object:
        captured.append(comparison)
        return scope

    monkeypatch.setattr(census_module, "load_contract", lambda _path: contract)
    monkeypatch.setattr(
        census_module, "census_comparison", lambda one: compare if one is contract else None
    )
    monkeypatch.setattr(census_module, "capture_memory_census_scope", capture)
    monkeypatch.setattr(census_module, "_prepared_code_input", lambda _scope: None)
    empty = SimpleNamespace(rows=(), blockers=(), unonboarded=(), model_dump=lambda mode: {})
    monkeypatch.setattr(census_module, "build_memory_census", lambda _scope, settings: empty)
    memory_scope = SimpleNamespace(
        pair_identity=SimpleNamespace(contractPath="contract.md"),
        context=SimpleNamespace(storage=None),
    )
    prepared = census_module.prepare_memory_census(cast(Any, memory_scope))
    assert prepared is not None
    scope.pair_identity = memory_scope.pair_identity  # type: ignore[attr-defined]
    census_module.publish_memory_census(prepared, detail_limit=1)
    assert captured == [compare, compare]


def test_a_converted_cards_kind_and_source_come_from_its_place_and_its_sidecar() -> None:
    route = canonical_text({"schema": "ar-onboarding-route/v1", "path": "src", "references": {}})
    assert converted_card_metadata("onboarding/overview.md", "onboarding", None) == {
        "doc_type": "repo-overview",
        "sourceRoute": ".",
    }
    assert converted_card_metadata("onboarding/src/overview.md", "onboarding", route) == {
        "doc_type": "route-local-overview",
        "sourceRoute": "src",
    }
    elsewhere = canonical_text(
        {"schema": "ar-onboarding-route/v1", "path": "lib", "references": {}}
    )
    assert (
        converted_card_metadata("onboarding/src/overview.md", "onboarding", elsewhere)[
            "sourceRoute"
        ]
        == "lib"
    )  # the route sidecar's path is the identity when it differs from the place
    assert converted_card_metadata("onboarding/entities.md", "onboarding", None) == {
        "doc_type": "repo-entity-catalog"
    }
    declared = canonical_text(
        {"schema": "ar-onboarding-file/v1", "path": "src/a.py", "references": {}, "realizes": []}
    )
    assert converted_card_metadata("onboarding/src/b.py.md", "onboarding", declared) == {
        "doc_type": "file-level-onboarding",
        "path": "src/a.py",  # the sidecar's path is the identity; the census then names the mismatch
    }
    assert (
        converted_card_metadata("onboarding/src/b.py.md", "onboarding", route)["path"] == "src/b.py"
    )


EXTRA = "src/pkg/extra.py"
EXTRA_SOURCE = "def extra(value):\n    return value + 1\n"


def test_citation_rows_author_a_converted_cards_sidecar_with_resolved_anchors(
    tmp_path: Path,
) -> None:
    """L37 P1b: a curator writes a converted card's evidence as a citation table and the fixer
    authors it -- a new card's sidecar, and a reference re-authored by its number."""

    code = code_repository(tmp_path / "code")
    memory = tmp_path / "memory"
    memory_repository(memory, code)
    with pytest.raises(ValueError, match="not converted"):
        author_card_references(memory, code.root)
    _convert_in_place(memory, code)
    (code.root / EXTRA).write_text(EXTRA_SOURCE, encoding="utf-8")
    untouched = (memory / f"onboarding/{APP}.md").read_bytes()
    card = memory / f"onboarding/{EXTRA}.md"
    card.write_text(
        f"# {EXTRA}\n\n## Governing Overview\n\n[Nearest governing overview](../overview.md)\n\n"
        "## Evidence\n\n| Finding | Anchor | Source |\n| --- | --- | --- |\n"
        f"| Extra adds one to `x[0]`, so y[1] grows. | `extra` | {EXTRA}:1-2 |\n"
        "| Not cited yet. | n/a | n/a |\n"
        f"| The whole module. | | {EXTRA} |\n",
        encoding="utf-8",
    )
    scope = CitationOperationScope()
    report = memory_tools._converted_citation_fix(memory, code.root, scope, dry_run=False)
    authoring = report["authoring"]
    assert report["ok"] is True and authoring["refused"] == []
    assert (authoring["authoredReferences"], authoring["createdSidecars"]) == (
        2,
        [f"onboarding/{EXTRA}.json"],
    )
    text = card.read_text(encoding="utf-8")
    assert "| Finding |" not in text
    assert (
        "- Extra adds one to `x[0]`, so y\\[1] grows. [1]\nNot cited yet.\n- The whole module. [2]"
        in text
    )  # marker-shaped prose is escaped, so [1] is the only marker of the line
    sidecar = json.loads((memory / f"onboarding/{EXTRA}.json").read_text(encoding="utf-8"))
    assert (sidecar["schema"], sidecar["path"], sidecar["realizes"]) == (
        "ar-onboarding-file/v1",
        EXTRA,
        [],
    )
    first = sidecar["references"]["1"]["targets"][0]
    assert first["kind"] == "code" and "path" not in first["anchor"]  # its own file
    assert first["anchor"]["locator"] == {"kind": "symbol", "name": "extra"}
    assert sidecar["references"]["2"]["targets"][0]["anchor"]["locator"] == {"kind": "file"}
    assert (memory / f"onboarding/{APP}.md").read_bytes() == untouched  # no table: not touched
    report = validate_tree(
        knowledge_tree_from_directory(memory),
        code=CodeDirectory(label="code", root=code.root),
    )
    assert [one for one in report.refusals if EXTRA in str(one)] == []

    (code.root / EXTRA).write_text(EXTRA_SOURCE.replace("+ 1", "+ 2"), encoding="utf-8")
    card.write_text(
        text.replace(
            "- Extra adds one to `x[0]`, so y\\[1] grows. [1]",
            "| Finding | Anchor | Source |\n| --- | --- | --- |\n"
            f"| Extra adds two. [1] | `extra` | {EXTRA}:1-2 |",
        ),
        encoding="utf-8",
    )
    again = author_card_references(memory, code.root, only=f"{EXTRA}.md")
    assert (again["authoredReferences"], again["reauthoredReferences"]) == (0, 1)
    refreshed = json.loads((memory / f"onboarding/{EXTRA}.json").read_text(encoding="utf-8"))
    assert set(refreshed["references"]) == {"1", "2"}
    assert refreshed["references"]["1"]["note"] == "Extra adds two."
    assert refreshed["references"]["1"] != sidecar["references"]["1"]  # re-resolved at the tree
    assert "- Extra adds two. [1]" in card.read_text(encoding="utf-8")

    broken = memory / "onboarding/src/pkg/other.py.json"
    broken.write_text(canonical_text({"schema": "ar-onboarding-file/v1", "references": {}}))
    other = memory / "onboarding/src/pkg/other.py.md"
    before = other.read_text(encoding="utf-8")
    other.write_text(
        before + f"\n| Finding | Anchor | Source |\n| --- | --- | --- |\n| X. | | {EXTRA} |\n"
    )
    fixed = memory_tools._converted_citation_fix(memory, code.root, scope, dry_run=False)
    assert fixed["ok"] is False  # a refused card fails the fixer run, by name
    refused = fixed["authoring"]
    assert [one["card"] for one in refused["refused"]] == ["onboarding/src/pkg/other.py.md"]
    assert "| X. |" in other.read_text(encoding="utf-8")  # nothing of it written


def test_one_converted_card_is_fixed_by_its_document_alone_and_legacy_keeps_the_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L37 ruling 2026-10-01T10:06:02: on converted memory ``citation_fix`` takes ``--document``
    alone and touches that one card; on unconverted memory a document still needs its snapshot."""

    code = code_repository(tmp_path / "code")
    memory = tmp_path / "memory"
    memory_repository(memory, code)
    leaf = SimpleNamespace(
        repo_id="demo", onboarding_root=memory / "onboarding", code_root=code.root
    )
    monkeypatch.setattr(memory_tools, "_leaf_memory_writer_scope", lambda *_a, **_k: leaf)
    one = CitationOperationScope(document=f"{EXTRA}.md")  # shaped for either format

    def fix(scope: CitationOperationScope) -> dict[str, Any]:
        return memory_tools.citation_fix_tool(
            cast(Any, None), repo_id="demo", contract_path="contract.md", operation_scope=scope
        )

    legacy = mock.Mock(side_effect=AssertionError("the legacy fixer ran without a snapshot"))
    monkeypatch.setattr(memory_tools.fixer, "fix_onboarding_root", legacy)
    monkeypatch.setattr(memory_tools, "_citation_trees", legacy)
    with pytest.raises(ValueError, match="--document and --expected-snapshot together"):
        fix(one)  # unconverted: the pairing rule stands
    with pytest.raises(ValueError, match="together"):
        memory_tools.citation_migrate_tool(
            cast(Any, None), repo_id="demo", contract_path="contract.md", operation_scope=one
        )
    with pytest.raises(ValueError, match="together"):
        CitationOperationScope(expected_snapshot="a" * 64)  # a snapshot never comes alone

    _convert_in_place(memory, code)
    (code.root / EXTRA).write_text(EXTRA_SOURCE, encoding="utf-8")
    table = (
        f"| Finding | Anchor | Source |\n| --- | --- | --- |\n| Extra. | `extra` | {EXTRA}:1-2 |\n"
    )
    (memory / f"onboarding/{EXTRA}.md").write_text(f"# {EXTRA}\n\n{table}", encoding="utf-8")
    pending = memory / "onboarding/src/pkg/other.py.md"
    pending.write_text(pending.read_text(encoding="utf-8") + f"\n{table}", encoding="utf-8")
    # A mechanical move elsewhere: a tree-wide run would re-record the app card's anchors.
    (code.root / APP).write_text("# moved\n" + APP_SOURCE, encoding="utf-8")
    elsewhere = (memory / APP_SIDECAR).read_bytes()

    fixed = fix(one)
    assert fixed["ok"] is True and fixed["status"] == "converted"
    assert fixed["authoring"]["authoredCards"] == [f"onboarding/{EXTRA}.md"]
    assert "| Finding |" in pending.read_text(encoding="utf-8")  # another card: not authored
    assert (memory / APP_SIDECAR).read_bytes() == elsewhere  # and not refreshed
    assert set(fixed["rewrittenSidecars"]) <= {f"onboarding/{EXTRA}.json"}
    legacy.assert_not_called()
    assert fix(CitationOperationScope())["authoring"]["authoredCards"] == [
        "onboarding/src/pkg/other.py.md"
    ]  # tree-wide stays the default
    assert (memory / APP_SIDECAR).read_bytes() != elsewhere


# --------------------------------------------------------------------------------------------------
# Review R3: the converted check's base, and the fixer's refusals
# --------------------------------------------------------------------------------------------------


def _rules(findings: list[dict[str, Any]]) -> list[str]:
    return sorted(str(one.get("rule")) for one in findings)


def test_the_converted_check_compares_an_unconverted_head_through_its_converted_base(
    tmp_path: Path,
) -> None:
    """Review R3-1 (MIK-R24 rule 7): with ``HEAD`` unconverted, the ``knowledge.converted`` check
    validates against ``HEAD``'s conversion, so a carried reference to a file the candidate deleted
    or moved is reported, never refused as a newly written anchor."""

    code = code_repository(tmp_path / "code")
    memory = tmp_path / "memory"
    head = memory_repository(memory, code)
    _convert_in_place(memory, code)
    (code.root / "docs/guide.md").unlink()  # one cited source deleted
    (code.root / "tests/test_app.py").rename(code.root / "tests/test_moved.py")  # one moved

    without = converted_knowledge_check(memory, code.root)
    assert set(_rules(without["findings"])) == {"R22.6-anchor-path"}  # every anchor read as new
    leaf = SimpleNamespace(
        contract=SimpleNamespace(code_base_commit=code.first), quality_code_root=code.root
    )
    coordination = tmp_path / "coordination"
    base = converted_check_base(cast(Any, leaf), coordination)
    checked = converted_knowledge_check(memory, code.root, base=base)
    assert checked["ok"] is True and checked["findings"] == []
    carried = [
        one for one in checked["reportOnlyFindings"] if one.get("rule") == "R22.6-carried-stale"
    ]
    assert len(carried) == len(without["findings"])  # the same anchors, reported
    (cached,) = (coordination / "runtime" / "knowledge-worklist-bases").glob("*.json.gz")
    key = json.loads(gzip.decompress(cached.read_bytes()))["key"]
    assert (key[0], key[2]) == (
        head,
        code.first,
    )  # the gate's key: K_B, and B for a trailerless K_B

    context = SimpleNamespace(coordination_root=coordination, contract_path=None)
    closeout = context_check_base(code.root, context)
    assert closeout is not None  # the closeout's quality phases get the same base
    quality = run_memory_quality_check(
        memory / "onboarding",
        drift_context=MemoryQualityAdapter().drift_context(code.root, context, 50),
    )
    assert quality["ok"] is True, quality["findings"]
    assert context_check_base(code.root, SimpleNamespace()) is None  # no coordination root: no port

    unborn = tmp_path / "unborn-code"
    unborn.mkdir()
    git(unborn, "init", "-q")
    nothing = SimpleNamespace(contract=None, quality_code_root=unborn)
    unbuilt = converted_knowledge_check(
        memory, code.root, base=converted_check_base(cast(Any, nothing), tmp_path / "other")
    )
    assert unbuilt["ok"] is False and "R24.7-converted-base" in _rules(unbuilt["findings"])


def _card_with_rows(memory: Path, source: str, *rows: str) -> Path:
    card = memory / f"onboarding/{source}.md"
    table = "| Finding | Anchor | Source |\n| --- | --- | --- |\n" + "".join(
        f"{row}\n" for row in rows
    )
    card.write_text(f"# {source}\n\n## Evidence\n\n{table}", encoding="utf-8")
    return card


def test_authoring_numbers_after_existing_references_dry_runs_and_validates(tmp_path: Path) -> None:
    """Review R3-2: a new row never overwrites a reference (Z09), a dry run writes nothing (Z12),
    and a sidecar that would not parse is refused before anything is written (Z20)."""

    code = code_repository(tmp_path / "code")
    memory = tmp_path / "memory"
    memory_repository(memory, code)
    _convert_in_place(memory, code)
    (code.root / EXTRA).write_text(EXTRA_SOURCE, encoding="utf-8")
    app_card = memory / f"onboarding/{APP}.md"
    before = json.loads((memory / APP_SIDECAR).read_text(encoding="utf-8"))
    highest = max(int(number) for number in before["references"])
    row = f"| Extra is new. | `extra` | {EXTRA}:1-2 |"
    app_card.write_text(
        app_card.read_text(encoding="utf-8")
        + f"\n| Finding | Anchor | Source |\n| --- | --- | --- |\n{row}\n",
        encoding="utf-8",
    )
    untouched = (app_card.read_bytes(), (memory / APP_SIDECAR).read_bytes())

    preview = author_card_references(memory, code.root, dry_run=True)
    assert (preview["dryRun"], preview["authoredReferences"]) == (True, 1)
    assert (app_card.read_bytes(), (memory / APP_SIDECAR).read_bytes()) == untouched  # Z12

    author_card_references(memory, code.root)
    after = json.loads((memory / APP_SIDECAR).read_text(encoding="utf-8"))
    assert set(after["references"]) == {*before["references"], str(highest + 1)}  # Z09
    assert {n: after["references"][n] for n in before["references"]} == before["references"]
    assert f"- Extra is new. [{highest + 1}]" in app_card.read_text(encoding="utf-8")

    card = _card_with_rows(memory, EXTRA, f"| {'x' * 20001} | `extra` | {EXTRA}:1-2 |")
    written = card.read_bytes()
    refused = author_card_references(memory, code.root)  # Z20: the note exceeds the sidecar's shape
    assert [one["card"] for one in refused["refused"]] == [f"onboarding/{EXTRA}.md"]
    assert "would not parse" in refused["refused"][0]["reason"]
    assert card.read_bytes() == written and not (memory / f"onboarding/{EXTRA}.json").exists()


def test_the_fixer_checks_every_card_first_and_refuses_by_name(tmp_path: Path) -> None:
    """Review R3-3: an unparseable sidecar is a named refusal, in the authoring and in the
    re-recording, and nothing is written until every card has been checked."""

    code = code_repository(tmp_path / "code")
    memory = tmp_path / "memory"
    memory_repository(memory, code)
    _convert_in_place(memory, code)
    (code.root / EXTRA).write_text(EXTRA_SOURCE, encoding="utf-8")
    row = f"| Extra. | `extra` | {EXTRA}:1-2 |"
    first = _card_with_rows(memory, "src/pkg/a_first.py", row)
    last = _card_with_rows(memory, "src/pkg/z_last.py", row)
    originals = (first.read_bytes(), last.read_bytes())

    # (a) Nothing is written until every card is checked: a failure on the last card leaves the
    # first one, already authored in memory, unwritten.
    calls: list[str] = []
    resolve = card_authoring.row_reference

    def failing(citation: Any, context: Any, tally: Any) -> Any:
        calls.append(context.card)
        if context.card.endswith("z_last.py.md"):
            raise RuntimeError("the last card cannot be resolved")
        return resolve(citation, context, tally)

    with (
        mock.patch.object(card_authoring, "row_reference", side_effect=failing, autospec=False),
        pytest.raises(RuntimeError),
    ):
        author_card_references(memory, code.root)
    assert len(calls) == 2 and (first.read_bytes(), last.read_bytes()) == originals
    assert not (memory / "onboarding/src/pkg/a_first.py.json").exists()

    # (a) A sidecar that is not JSON, and one that is not shaped as a sidecar: named, never raised.
    (memory / "onboarding/src/pkg/a_first.py.json").write_text("{ not json", encoding="utf-8")
    (memory / "onboarding/src/pkg/z_last.py.json").write_text(
        canonical_text({"schema": "ar-onboarding-file/v1", "path": "x", "references": [1]})
    )
    fixed = memory_tools._converted_citation_fix(
        memory, code.root, CitationOperationScope(), dry_run=False
    )
    assert fixed["ok"] is False
    refused = {one["card"]: one["reason"] for one in fixed["authoring"]["refused"]}
    assert set(refused) == {"onboarding/src/pkg/a_first.py.md", "onboarding/src/pkg/z_last.py.md"}
    assert all("does not parse" in reason for reason in refused.values())
    assert set(fixed["unreadableSidecars"]) == {
        "onboarding/src/pkg/a_first.py.json",
        "onboarding/src/pkg/z_last.py.json",
    }
    assert (first.read_bytes(), last.read_bytes()) == originals
    assert fix_references(memory, code.root, dry_run=True)["ok"] is False  # named, and not ok
    one_card = fix_references(memory, code.root, only=APP_SIDECAR)
    assert one_card["ok"] is True and one_card["unreadableSidecars"] == []  # others are not read
    assert check_references(memory, code.root)["unreadableSidecars"]  # reported, not raised


def test_a_leftover_evidence_line_is_refused_and_only_the_named_card_loses_references(
    tmp_path: Path,
) -> None:
    """Review R3-3: a row that re-authors ``[n]`` beside a line still citing ``[n]`` is refused,
    never silently re-pointed; uncited references are removed from the named card only."""

    code = code_repository(tmp_path / "code")
    memory = tmp_path / "memory"
    memory_repository(memory, code)
    _convert_in_place(memory, code)
    (code.root / EXTRA).write_text(EXTRA_SOURCE, encoding="utf-8")
    row = f"| Extra. | `extra` | {EXTRA}:1-2 |"
    card = _card_with_rows(memory, EXTRA, row, f"| The whole module. | | {EXTRA} |")
    author_card_references(memory, code.root)
    text = card.read_text(encoding="utf-8")
    assert "- Extra. [1]\n- The whole module. [2]" in text
    card.write_text(
        text
        + f"\n| Finding | Anchor | Source |\n| --- | --- | --- |\n| Extra, again. [1] | `extra` | {EXTRA}:1-2 |\n",
        encoding="utf-8",
    )
    stale = card.read_bytes()
    leftover = author_card_references(memory, code.root)
    assert "still cites it" in leftover["refused"][0]["reason"]
    assert card.read_bytes() == stale

    # Removing a reference: delete its line, then run the fixer on that card. A tree-wide run never
    # removes one, even from a card it authors.
    again = f"| Finding | Anchor | Source |\n| --- | --- | --- |\n| Extra, more. | `extra` | {EXTRA}:1-2 |\n"
    card.write_text(text.replace("- The whole module. [2]\n", "") + f"\n{again}", encoding="utf-8")
    sidecar = memory / f"onboarding/{EXTRA}.json"
    tree_wide = author_card_references(memory, code.root)
    assert (tree_wide["authoredReferences"], tree_wide["removedReferences"]) == (1, 0)
    assert set(json.loads(sidecar.read_text())["references"]) == {"1", "2", "3"}
    named = author_card_references(memory, code.root, only=f"{EXTRA}.md")
    assert named["removedReferences"] == 1
    assert set(json.loads(sidecar.read_text())["references"]) == {"1", "3"}  # never renumbered


def test_one_documents_fix_reports_its_own_stale_references_and_the_response_is_bounded(
    tmp_path: Path,
) -> None:
    """L37 P1c, C1: a fix of one document lists that document's stale references, not the tree's;
    and the MCP response caps every list, says so, and keeps the full counts."""

    code = code_repository(tmp_path / "code")
    memory = tmp_path / "memory"
    memory_repository(memory, code)
    _convert_in_place(memory, code)
    (code.root / APP).write_text(APP_SOURCE.replace("total * 2", "total * 3"), encoding="utf-8")
    overview = "onboarding/src/overview.json"
    tree_wide = {item["path"] for item in fix_references(memory, code.root)["stale"]}
    assert tree_wide == {APP_SIDECAR, overview}  # beta's body changed: stale in both
    for sidecar in (APP_SIDECAR, overview):
        one = fix_references(memory, code.root, only=sidecar)
        assert {item["path"] for item in one["stale"]} == {sidecar}
    other = fix_references(memory, code.root, only="onboarding/src/pkg/other.py.json")
    assert other["ok"] is True and other["stale"] == []  # a current card reports nothing
    assert check_references(memory, code.root, only=overview)["states"] == {"stale": 1}

    limit = memory_tool_payloads.MAX_INLINE_CITATION_ITEMS
    many = [{"path": f"onboarding/{index}.json"} for index in range(limit + 70)]
    full = {
        "ok": True,
        "status": "converted",
        "stale": many,
        "rewrittenSidecars": [one["path"] for one in many],
        "unreadableSidecars": [],
        "authoring": {
            "authoredCards": ["a.md"] * (limit + 1),
            "unresolvedTargets": many,
            "refused": [],
            "dryRun": False,
        },
    }
    with mock.patch.object(memory_tool_payloads, "citation_fix_tool", return_value=full):
        sent = memory_tool_payloads.citation_fix_payload(
            cast(Any, None), "demo", contract_path="contract.md"
        )
    assert (len(sent["stale"]), sent["staleCount"]) == (limit, limit + 70)
    assert sent["stale"] == many[:limit] and sent["unreadableSidecarsCount"] == 0
    assert (len(sent["rewrittenSidecars"]), sent["rewrittenSidecarsCount"]) == (limit, limit + 70)
    assert (
        sent["truncated"] == ["stale", "rewrittenSidecars"] and "first 50" in sent["truncatedNote"]
    )
    assert sent["authoring"]["truncated"] == ["authoredCards", "unresolvedTargets"]
    assert sent["authoring"]["authoredCardsCount"] == limit + 1
    assert sent["authoring"]["unresolvedTargetsCount"] == limit + 70
    assert len(sent["authoring"]["unresolvedTargets"]) == limit
    small = {"ok": True, "status": "converted", "stale": many[:3], "rewrittenSidecars": []}
    bounded = memory_tool_payloads.bounded_citation_fix(small)
    assert (
        bounded["stale"] == many[:3] and bounded["staleCount"] == 3 and "truncated" not in bounded
    )
    legacy = {"ok": True, "findings": many}  # an unconverted tree's result keeps its own shape
    assert memory_tool_payloads.bounded_citation_fix(legacy) is legacy


def test_two_tables_with_no_blank_line_between_them_are_refused_by_name(tmp_path: Path) -> None:
    """L37 P1c, C3: the table reader ends a table only at a blank line, so a second table directly
    below one reads as its rows. Where either holds citations the card is refused, naming the
    line, and nothing is written; a card whose tables hold no citations is left alone."""

    code = code_repository(tmp_path / "code")
    memory = tmp_path / "memory"
    memory_repository(memory, code)
    _convert_in_place(memory, code)
    (code.root / EXTRA).write_text(EXTRA_SOURCE, encoding="utf-8")
    header = "| Finding | Anchor | Source |\n| --- | --- | --- |\n"
    row = f"| Extra. | `extra` | {EXTRA}:1-2 |\n"
    plain = "| Name | Meaning |\n| --- | --- |\n| a | b |\n"
    two = memory / f"onboarding/{EXTRA}.md"
    two.write_text(f"# {EXTRA}\n\n{header}{row}{header}{row}", encoding="utf-8")
    below = memory / "onboarding/src/pkg/below.py.md"
    below.write_text(f"# below\n\n{plain}{header}{row}", encoding="utf-8")
    untouched = memory / "onboarding/src/pkg/plain.py.md"
    untouched.write_text(f"# plain\n\n{plain}{plain}", encoding="utf-8")
    before = {path: path.read_bytes() for path in (two, below, untouched)}

    report = author_card_references(memory, code.root)
    refused = {one["card"]: one["reason"] for one in report["refused"]}
    assert set(refused) == {f"onboarding/{EXTRA}.md", "onboarding/src/pkg/below.py.md"}
    assert (
        "line 7 is a table delimiter row inside the table that starts at line 3"
        in refused[f"onboarding/{EXTRA}.md"]
    )
    assert "Put a blank line above line 6" in refused[f"onboarding/{EXTRA}.md"]
    assert "line 7 is a table delimiter row" in refused["onboarding/src/pkg/below.py.md"]
    assert report["authoredCards"] == [] and report["authoredReferences"] == 0
    assert {path: path.read_bytes() for path in before} == before  # nothing written
    assert not (memory / f"onboarding/{EXTRA}.json").exists()

    two.write_text(f"# {EXTRA}\n\n{header}{row}\n{header}{row}", encoding="utf-8")
    below.write_text(f"# below\n\n{plain}\n{header}{row}", encoding="utf-8")
    # A body row whose cells are single dashes is a placeholder row, not a delimiter (review R5-6).
    dashes = memory / "onboarding/src/pkg/dashes.py.md"
    dashes.write_text(f"# dashes\n\n{header}{row}| - | - | - |\n{row}", encoding="utf-8")
    authored = author_card_references(memory, code.root)
    assert authored["refused"] == [] and authored["authoredReferences"] == 5
    assert two.read_text(encoding="utf-8") == f"# {EXTRA}\n\n- Extra. [1]\n\n- Extra. [2]\n"
    assert dashes.read_text(encoding="utf-8") == "# dashes\n\n- Extra. [1]\n-\n- Extra. [2]\n"
    assert untouched.read_bytes() == before[untouched]


@pytest.fixture(autouse=True)
def _ambient() -> None:
    reset_ambient()
