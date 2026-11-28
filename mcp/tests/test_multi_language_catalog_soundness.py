"""Table-driven soundness, qualifier, pure-move, and override proofs for the multi-language catalog."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from agents_remember.application.knowledge_currentness import observe as currentness_observe
from agents_remember.application.knowledge_writer.code_anchors import CodeSnapshot
from agents_remember.cli import __main__ as cli_main
from agents_remember.memory.knowledge import read_anchors
from agents_remember.memory.knowledge.read_anchor_memo import BLOB_DEFINITIONS
from agents_remember.memory.knowledge_index import Entry
from agents_remember.memory_quality import reference_state
from agents_remember.memory_quality.style.citations import (
    extents,
    grammars,
    language_cache,
    language_catalog,
    language_scanner,
)
from agents_remember.memory_quality.style.citations.language_catalog import LanguageCatalogError
from knowledge_conversion_test_support import commit_files, init_repository
from multi_language_soundness_fixtures import (
    CORE_PILOT_IDS,
    LONG_TAIL_FIXTURES,
    PILOT_FIXTURES,
)


def _shifted_source(language: str, source: str, shift_prefix: str) -> str:
    """Shift definitions downward while preserving language preamble requirements."""
    if language == "php":
        assert source.startswith("<?php\n")
        return "<?php\n// shifted line 1\n// shifted line 2\n\n" + source[len("<?php\n") :]
    if language == "racket":
        assert source.startswith("#lang racket\n")
        return "#lang racket\n; shifted 1\n; shifted 2\n\n" + source[len("#lang racket\n") :]
    return shift_prefix + source


def _write_sidecar(
    memory_root: Path,
    rel_path: str,
    *,
    blob: str,
    content: str,
    symbol: str,
) -> Path:
    sidecar_path = memory_root / "onboarding" / f"{rel_path}.json"
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    sidecar_path.write_text(
        json.dumps(
            {
                "schema": "ar-onboarding-file/v1",
                "path": rel_path,
                "references": {
                    "1": {
                        "targets": [
                            {
                                "kind": "code",
                                "anchor": {
                                    "blob": blob,
                                    "content": content,
                                    "locator": {"kind": "symbol", "name": symbol},
                                },
                            }
                        ]
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return sidecar_path


@dataclass(frozen=True)
class _PureMoveCase:
    rel_path: str
    language: str
    v1_source: str
    v2_source: str
    symbol: str


def _assert_pure_move_round_trip(
    repo: Path,
    memory_root: Path,
    case: _PureMoveCase,
) -> None:
    """Prove code_anchors, read_anchors, currentness, and citation_fix all agree across a pure move."""
    rel_path = case.rel_path
    language = case.language
    v1_source = case.v1_source
    v2_source = case.v2_source
    symbol = case.symbol
    commit_v1 = commit_files(repo, {rel_path: v1_source}, f"seed {language}")
    snap_v1 = CodeSnapshot.at_commit(repo, commit_v1)
    anchor_v1 = snap_v1.resolve(rel_path, {"kind": "symbol", "name": symbol})
    assert anchor_v1.locator.kind == "symbol"
    assert anchor_v1.locator.name == symbol

    # Verify read_anchors observes exact_recorded_blob at v1.
    claim_v1 = {
        "anchor_id": "00000000-0000-4000-8000-000000000001",
        "path": rel_path,
        "source_identity": {"object_id": anchor_v1.blob},
        "locator": {"kind": "symbol", "language": language, "qualified_name": symbol},
    }
    obs_v1 = read_anchors.observe_anchor(claim_v1, repository_root=repo, tree_id=snap_v1.tree)
    assert obs_v1.resolution == "exact_recorded_blob", (
        f"{language}: expected exact_recorded_blob at v1, got {obs_v1}"
    )
    assert obs_v1.resolved_ranges is not None and len(obs_v1.resolved_ranges) == 1
    v1_range = (obs_v1.resolved_ranges[0].start_line, obs_v1.resolved_ranges[0].end_line)

    sidecar_rel = f"onboarding/{rel_path}.json"
    _write_sidecar(
        memory_root,
        rel_path,
        blob=anchor_v1.blob,
        content=anchor_v1.content,
        symbol=symbol,
    )

    # Pure move: prepend comments/blank lines without altering the symbol's bytes.
    commit_v2 = commit_files(repo, {rel_path: v2_source}, f"pure-move {language}")
    snap_v2 = CodeSnapshot.at_commit(repo, commit_v2)
    anchor_v2 = snap_v2.resolve(rel_path, {"kind": "symbol", "name": symbol})
    assert anchor_v2.blob != anchor_v1.blob
    assert anchor_v2.content == anchor_v1.content, (
        f"{language}: pure move changed content digest for {symbol!r}"
    )

    # read_anchors resolves at v2 with shifted lines.
    claim_v2 = {
        "anchor_id": "00000000-0000-4000-8000-000000000002",
        "path": rel_path,
        "source_identity": {"object_id": anchor_v2.blob},
        "locator": {"kind": "symbol", "language": language, "qualified_name": symbol},
    }
    obs_v2 = read_anchors.observe_anchor(claim_v2, repository_root=repo, tree_id=snap_v2.tree)
    assert obs_v2.resolution == "exact_recorded_blob", (
        f"{language}: expected exact_recorded_blob at v2, got {obs_v2}"
    )
    assert obs_v2.resolved_ranges is not None and len(obs_v2.resolved_ranges) == 1
    v2_range = (obs_v2.resolved_ranges[0].start_line, obs_v2.resolved_ranges[0].end_line)
    assert v2_range[0] > v1_range[0], (
        f"{language}: expected shifted start line ({v1_range} -> {v2_range})"
    )
    assert (v2_range[1] - v2_range[0]) == (v1_range[1] - v1_range[0])

    # Currentness observes the v1 realization as current at v2 (unchanged content digest).
    opened_v2 = currentness_observe.open_code_tree(
        currentness_observe.CodeTree(repository=repo, tree=snap_v2.tree)
    )
    entry = Entry(
        id=f"RLZ-{language.upper()[:8]}",
        kind="realization",
        invariant="INV-MOVE01",
        path=rel_path,
        sidecar=sidecar_rel,
        document={
            "anchor": {
                "blob": anchor_v1.blob,
                "content": anchor_v1.content,
                "locator": {"kind": "symbol", "name": symbol},
            }
        },
    )
    assert currentness_observe.observe_entry(entry, opened_v2).state == "current"

    # citation_fix (reference_state.fix_references) refreshes the moved blob while preserving content.
    before_fix = reference_state.check_references(memory_root, repo, only=sidecar_rel)
    assert before_fix["states"] == {"current": 1}
    fixed = reference_state.fix_references(memory_root, repo, only=sidecar_rel)
    assert fixed["ok"] is True
    assert fixed["refreshedAnchors"] == 1
    assert fixed["stale"] == []
    repaired_doc = json.loads((memory_root / sidecar_rel).read_text(encoding="utf-8"))
    repaired_anchor = repaired_doc["references"]["1"]["targets"][0]["anchor"]
    assert repaired_anchor["blob"] == anchor_v2.blob
    assert repaired_anchor["content"] == anchor_v1.content
    after_fix = reference_state.check_references(memory_root, repo, only=sidecar_rel)
    assert after_fix["states"] == {"current": 1}


def test_catalog_declares_15_pilots_and_50_plus_language_pack_entries_with_honest_tiers() -> None:
    """Rule 1 / AC-1: hash-pinned catalog admits 15+ pilots and 50+ pack entries with honest tiers."""
    catalog = language_catalog.load_language_catalog()
    assert set(CORE_PILOT_IDS).issubset(catalog.languages)
    for pilot_id in CORE_PILOT_IDS:
        spec = catalog.languages[pilot_id]
        assert spec.provider_kind == "pypi_wheel"
        assert spec.guarantee_tier == "verified"
        assert spec.wheels, f"{pilot_id} must declare hash-pinned wheels"
        assert spec.tags_query_sha256, f"{pilot_id} must declare SHA-256 pinned tags.scm"

    pack_specs = [
        spec for spec in catalog.languages.values() if spec.provider_kind == "language_pack"
    ]
    assert len(pack_specs) >= 50
    assert catalog.language_pack is not None
    assert catalog.language_pack.wheels
    tiers = {spec.guarantee_tier for spec in pack_specs}
    assert tiers.issubset({"verified", "best_effort", "unverified"})
    assert "best_effort" in tiers, "at least one heuristic long-tail language must be best_effort"
    assert ".scm" in catalog.ambiguous_suffixes
    assert {".h", ".m", ".v"}.issubset(catalog.ambiguous_suffixes)

    fixture_pack_ids = {f.language for f in LONG_TAIL_FIXTURES}
    catalog_pack_ids = {spec.id for spec in pack_specs}
    assert fixture_pack_ids == catalog_pack_ids


def test_15_core_pilots_positive_binding_wrappers_and_negative_soundness() -> None:
    """Rule 2: all 15 pilots bind exact spans (including wrappers) with zero false definitions."""
    for fx in PILOT_FIXTURES:
        lines = extents.source_lines(fx.source.encode("utf-8"))
        bound = extents.definitions(fx.path, lines)
        for name, expected_span in fx.expected_spans.items():
            assert name in bound, (
                f"{fx.language}: missing expected symbol {name!r} in {list(bound)}"
            )
            assert len(bound[name]) == 1, f"{fx.language}: expected 1 span for {name!r}"
            got_span = (bound[name][0].start, bound[name][0].end)
            assert got_span == expected_span, (
                f"{fx.language} {name!r}: expected {expected_span}, got {got_span}"
            )
        for trap in fx.forbidden_names:
            assert trap not in bound, (
                f"{fx.language}: negative soundness violated — bound forbidden trap {trap!r}"
            )


def test_15_core_pilots_qualifier_policy_and_honest_refusal() -> None:
    """Rule 3: per-language qualifier policy resolves qualified symbols and refuses mismatches."""
    for fx in PILOT_FIXTURES:
        lines = extents.source_lines(fx.source.encode("utf-8"))
        bound = extents.definitions(fx.path, lines)
        grammar = grammars.grammar_of(fx.path)
        for query, expected_span in fx.qualified_cases.items():
            spans = extents.qualified_spans(query, bound, grammar=grammar)
            got = spans[0] if len(spans) == 1 else None
            assert got == expected_span, (
                f"{fx.language} qualifier {query!r}: expected {expected_span}, got {spans}"
            )


def test_15_core_pilots_pure_move_round_trip_code_anchors_read_anchors_and_citation_fix(
    tmp_path: Path,
) -> None:
    """Rule 2 / AC-1: pure-move round-trip agrees across code_anchors, read_anchors, citation_fix."""
    repo = tmp_path / "pilots-repo"
    memory_root = tmp_path / "pilots-memory"
    init_repository(repo)
    BLOB_DEFINITIONS.clear()
    try:
        for fx in PILOT_FIXTURES:
            moved = _shifted_source(fx.language, fx.source, fx.shift_prefix)
            _assert_pure_move_round_trip(
                repo,
                memory_root,
                _PureMoveCase(
                    rel_path=fx.path,
                    language=fx.language,
                    v1_source=fx.source,
                    v2_source=moved,
                    symbol=fx.move_symbol,
                ),
            )
    finally:
        BLOB_DEFINITIONS.clear()


def test_50_plus_language_pack_entries_positive_negative_and_pure_move_soundness(
    tmp_path: Path,
) -> None:
    """Rule 1/2 / AC-1: all 56 language_pack entries pass positive, negative, and pure-move checks."""
    repo = tmp_path / "long-tail-repo"
    memory_root = tmp_path / "long-tail-memory"
    init_repository(repo)
    BLOB_DEFINITIONS.clear()
    try:
        for fx in LONG_TAIL_FIXTURES:
            lines = extents.source_lines(fx.source.encode("utf-8"))
            bound = extents.definitions(fx.path, lines)
            assert fx.expected_symbol in bound, (
                f"{fx.language}: missing expected symbol {fx.expected_symbol!r}; got {list(bound)}"
            )
            for trap in fx.forbidden_names:
                assert trap not in bound, (
                    f"{fx.language}: negative soundness violated — bound forbidden trap {trap!r}"
                )
            moved = _shifted_source(fx.language, fx.source, fx.shift_prefix)
            _assert_pure_move_round_trip(
                repo,
                memory_root,
                _PureMoveCase(
                    rel_path=fx.path,
                    language=fx.language,
                    v1_source=fx.source,
                    v2_source=moved,
                    symbol=fx.expected_symbol,
                ),
            )
    finally:
        BLOB_DEFINITIONS.clear()


def test_ambiguous_suffixes_default_occurrence_only_and_resolve_with_languages_json_override(
    tmp_path: Path,
) -> None:
    """AC-2: .h/.m/.v/.scm stay occurrence-only by default and resolve when mapped in languages.json."""
    default_set = grammars.default_language_set()
    for path in ("include/api.h", "src/objc.m", "rtl/chip.v", "queries/tags.scm"):
        assert default_set.grammar_of(path) is None, f"{path} must remain ambiguous by default"

    repo = tmp_path / "header-override-repo"
    memory_root = tmp_path / "header-override-memory"
    init_repository(repo)
    c_header = "int header_compute(int x) {\n    return x + 1;\n}\n"
    cpp_header = "namespace core {\nclass Engine {\npublic:\n    int run() { return 1; }\n};\n}\n"
    config = {
        "suffixes": {".h": "c"},
        "globs": {"include/cpp/**/*.h": "cpp"},
    }
    commit_files(repo, {"languages.json": json.dumps(config, indent=2)}, "configure headers")

    repo_set = language_scanner.build_repository_language_set(repo, include_user_settings=False)
    assert repo_set.grammar_of("include/c/api.h") == "c"
    assert repo_set.grammar_of("include/cpp/engine.h") == "cpp"
    assert repo_set.grammar_of("src/objc.m") is None

    BLOB_DEFINITIONS.clear()
    try:
        _assert_pure_move_round_trip(
            repo,
            memory_root,
            _PureMoveCase(
                rel_path="include/c/api.h",
                language="c",
                v1_source=c_header,
                v2_source="// moved\n\n" + c_header,
                symbol="header_compute",
            ),
        )
        _assert_pure_move_round_trip(
            repo,
            memory_root,
            _PureMoveCase(
                rel_path="include/cpp/engine.h",
                language="cpp",
                v1_source=cpp_header,
                v2_source="// moved\n\n" + cpp_header,
                symbol="core::Engine::run",
            ),
        )
    finally:
        BLOB_DEFINITIONS.clear()


def test_catalog_schema_validation_for_guarantee_tier_qualifiers_and_warm_all_cli(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Catalog parser refuses malformed guaranteeTier/qualifiers; warm --all reports all warm."""
    raw: dict[str, Any] = json.loads(language_catalog.catalog_lock_path().read_text("utf-8"))

    bad_tier = json.loads(json.dumps(raw))
    bad_tier["languages"]["c"]["guaranteeTier"] = "gold"
    with pytest.raises(LanguageCatalogError, match="guaranteeTier"):
        language_catalog.parse_language_catalog(bad_tier, verify_queries=False)

    bad_qual = json.loads(json.dumps(raw))
    bad_qual["languages"]["c"]["qualifiers"] = {"separators": [""]}
    with pytest.raises(LanguageCatalogError, match=r"qualifiers\.separators"):
        language_catalog.parse_language_catalog(bad_qual, verify_queries=False)

    bad_sep_kinds = json.loads(json.dumps(raw))
    bad_sep_kinds["languages"]["ruby"]["qualifiers"] = {
        "separators": ["#"],
        "separatorKinds": {".": ["singleton_method"]},
    }
    with pytest.raises(LanguageCatalogError, match="separatorKinds"):
        language_catalog.parse_language_catalog(bad_sep_kinds, verify_queries=False)

    assert (
        cli_main.main(
            [
                "languages",
                "warm",
                "--all",
                "--cache-dir",
                str(language_cache.resolve_language_cache_root()),
                "--json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["failed"] == {}
    assert len(set(payload["alreadyCached"]) | set(payload["warmed"])) >= 75
