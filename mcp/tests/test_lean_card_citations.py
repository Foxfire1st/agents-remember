"""Lean citations in live cards and the byte-pinned legacy converter."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from agents_remember.application import memory_tools
from agents_remember.application.memory_tools import CitationOperationScope, _converted_citation_fix
from agents_remember.kernel.memory_init import LAYOUT_MARKER_TEXT
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.memory.conversion.code_objects import CodeObjects
from agents_remember.memory.conversion.convert import MemoryInput, convert_memory
from agents_remember.memory_quality.reference_state import check_references, git_blob_id
from agents_remember.memory_quality.style.citations import cells
from agents_remember.models.knowledge_files.anchor_content import content_identity, range_bytes
from agents_remember.models.knowledge_files.canonical import canonical_text
from knowledge_conversion_test_support import commit_files, init_repository
from test_knowledge_conversion import conversion_digest

PYTHON = "src/example.py"
LEAN = "src/example.lean"
CARD = f"onboarding/{PYTHON}.md"
SIDECAR = f"onboarding/{PYTHON}.json"
LAYOUT = "knowledge/layout.json"
BASE_CONVERSION_DIGEST = "caad6b1fe6a9e1ba0b3332f845258e9d71688fcb98636b76b805aea8ab8ce473"


@pytest.fixture
def code(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "code"
    init_repository(repository)
    commit = commit_files(
        repository,
        {
            PYTHON: "class N:\n    def item(self):\n        return 2\n\n\ndef plain():\n    return 1\n",
            LEAN: "namespace N\n\ndef item : Nat :=\n  1\n\nend N\n\ndef plain := true\n",
        },
        "Lean card citation inputs",
    )
    return repository, commit


def _card(anchors: str, sources: str) -> str:
    return (
        f"# {PYTHON}\n\n## Evidence\n\n"
        "| Finding | Anchor | Source |\n| --- | --- | --- |\n"
        f"| The declarations. | {anchors} | {sources} |\n"
    )


def _legacy_lean_digest(repository: Path, commit: str) -> str:
    memory = MemoryInput(
        label="legacy Lean citation",
        files={CARD: _card("`N.item`; `item`", f"{LEAN}:3-4").encode("utf-8")},
        database=None,
    )
    result = convert_memory(memory, CodeObjects(repository), paired_commit=commit, version="1")
    return conversion_digest(result.files)


def _author_card(memory: Path, repository: Path, anchors: str, sources: str) -> None:
    (memory / LAYOUT).parent.mkdir(parents=True)
    (memory / LAYOUT).write_text(LAYOUT_MARKER_TEXT, encoding="utf-8")
    (memory / CARD).parent.mkdir(parents=True)
    (memory / CARD).write_text(_card(anchors, sources), encoding="utf-8")
    fixed = _converted_citation_fix(
        memory, repository, CitationOperationScope(document=f"{PYTHON}.md"), dry_run=False
    )
    assert fixed["ok"] is True


def test_live_card_tries_extended_lean_names_only_against_lean_sources(
    tmp_path: Path, code: tuple[Path, str]
) -> None:
    repository, commit = code
    memory = tmp_path / "memory"
    _author_card(memory, repository, "`N.item`; `plain`", f"{PYTHON}:6-7; {LEAN}:3-4")
    reference = json.loads((memory / SIDECAR).read_bytes())["references"]["1"]
    anchors = [target["anchor"] for target in reference["targets"]]
    assert [(anchor.get("path", PYTHON), anchor["locator"]) for anchor in anchors] == [
        (LEAN, {"kind": "symbol", "name": "N.item"}),
        (PYTHON, {"kind": "symbol", "name": "plain"}),
    ]
    observed = check_references(memory, repository, only=SIDECAR)
    assert observed["states"] == {"current": 2}
    assert observed["reportOnlyFindings"] == []

    # A warmed live symbol lookup must not alter repeatable legacy conversion.
    blob = CodeObjects(repository).tree(commit)[LEAN]
    assert CodeObjects(repository).symbol_span(LEAN, blob, "item") == (3, 4)
    assert _legacy_lean_digest(repository, commit) == BASE_CONVERSION_DIGEST

    lean_source = repository / LEAN
    original = lean_source.read_text(encoding="utf-8")
    lean_source.write_text("\n" + original, encoding="utf-8")
    assert check_references(memory, repository, only=SIDECAR)["states"] == {"current": 2}
    lean_source.write_text("\n" + original.replace("  1", "  2"), encoding="utf-8")
    assert check_references(memory, repository, only=SIDECAR)["states"] == {
        "current": 1,
        "stale": 1,
    }


def test_live_card_resolves_a_lean_symbol_from_a_bare_source(
    tmp_path: Path, code: tuple[Path, str]
) -> None:
    repository, _ = code
    memory = tmp_path / "memory"
    _author_card(memory, repository, "`N.item`; `plain`", f"{LEAN}; {PYTHON}:6-7")

    reference = json.loads((memory / SIDECAR).read_bytes())["references"]["1"]
    anchors = [target["anchor"] for target in reference["targets"]]
    assert [(anchor.get("path", PYTHON), anchor["locator"]) for anchor in anchors] == [
        (LEAN, {"kind": "symbol", "name": "N.item"}),
        (PYTHON, {"kind": "symbol", "name": "plain"}),
        (LEAN, {"kind": "file"}),
    ]
    assert reference["note"] == "The declarations."
    assert check_references(memory, repository, only=SIDECAR)["states"] == {"current": 3}


def test_a_dotted_name_without_a_lean_source_stays_unchecked() -> None:
    claim = cells.parse_row(1, "`N.item`", f"{PYTHON}:2-3")
    assert claim.anchors == ()
    assert claim.unchecked_spans == 1


_SCOPE_SOURCE = b"""import ScopeCommands

namespace Outer
section
  importedScope
def before : Nat := 1
end
  Outer.importedScope
def after : Nat := 2
end Outer
"""


def _record_scope_references(
    memory: Path, source_path: str, source: bytes, locators: list[tuple[dict[str, str | int], int]]
) -> Path:
    marker = memory / LAYOUT
    marker.parent.mkdir(parents=True)
    marker.write_text(LAYOUT_MARKER_TEXT, encoding="utf-8")
    sidecar = memory / f"onboarding/{source_path}.json"
    sidecar.parent.mkdir(parents=True)
    blob = git_blob_id(source)
    references = {
        str(number): {
            "targets": [
                {
                    "kind": "code",
                    "anchor": {
                        "locator": locator,
                        "blob": blob,
                        "content": content_identity(range_bytes(source, line, line)),
                    },
                }
            ]
        }
        for number, (locator, line) in enumerate(locators, start=1)
    }
    sidecar.write_text(
        canonical_text(
            {
                "schema": "ar-onboarding-file/v1",
                "path": source_path,
                "references": references,
                "realizes": [],
            }
        ),
        encoding="utf-8",
    )
    return sidecar


@pytest.fixture
def scope_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "code"
    repository.mkdir()
    (repository / "ScopeCommands.lean").write_text(
        'macro "importedScope" : command => `(command| #check Nat)\n'
        'macro "Outer.importedScope" : command => `(command| #check Nat)\n',
        encoding="utf-8",
    )
    return repository


def test_citation_check_reports_an_uncertain_lean_scope_even_on_the_recorded_blob(
    tmp_path: Path, scope_repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = scope_repository
    memory = tmp_path / "memory"
    source = repository / "Imported.lean"
    sidecar = _record_scope_references(
        memory,
        source.name,
        _SCOPE_SOURCE,
        [
            ({"kind": "symbol", "name": "Outer.before"}, 6),
            ({"kind": "symbol", "name": "Outer.after"}, 9),
            ({"kind": "line_range", "start": 9, "end": 9}, 9),
        ],
    )
    recorded = sidecar.read_bytes()
    scope = SimpleNamespace(repo_id="fixture", code_root=repository, onboarding_root=sidecar.parent)
    monkeypatch.setattr(memory_tools, "_memory_scope", lambda _config, **_options: scope)

    for data in (_SCOPE_SOURCE, _SCOPE_SOURCE + b"\n-- A newer working blob.\n"):
        source.write_bytes(data)
        report = memory_tools.citation_check_tool(
            cast(McpRuntimeConfig, None), repo_id="fixture", contract_path="fixture.md"
        )
        assert report["ok"] is True
        assert report["states"] == {"current": 2, "unverifiable": 1}
        assert report["findings"] == []
        [finding] = report["reportOnlyFindings"]
        assert finding["state"] == "unverifiable"
        assert finding["path"] == sidecar.relative_to(memory).as_posix()
        assert finding["target"] == source.name
        assert finding["field"] == "references.2.targets.0"
        assert "Outer.after" in finding["message"]
        assert "scope" in finding["message"]
        assert sidecar.read_bytes() == recorded


@pytest.mark.parametrize("changed_blob", [False, True], ids=["recorded-blob", "changed-blob"])
def test_citation_check_keeps_a_verified_full_name_before_lean_scope_uncertainty(
    tmp_path: Path, scope_repository: Path, monkeypatch: pytest.MonkeyPatch, changed_blob: bool
) -> None:
    recorded_source = b"""import ScopeCommands
namespace Safe
def middle : Nat := 55
end Safe
namespace Outer
section
  importedScope
def before : Nat := 1
end
  Outer.importedScope
def middle : Nat := 2
"""
    source = scope_repository / "Imported.lean"
    memory = tmp_path / "memory"
    locators: list[tuple[dict[str, str | int], int]] = [
        ({"kind": "symbol", "name": "Safe.middle"}, 3),
        ({"kind": "symbol", "name": "Outer.before"}, 8),
        ({"kind": "symbol", "name": "middle"}, 11),
        ({"kind": "symbol", "name": "Outer.middle"}, 11),
    ]
    expected_states = {"current": 2, "unverifiable": 2}
    expected_findings = [
        ("references.3.targets.0", "unverifiable"),
        ("references.4.targets.0", "unverifiable"),
    ]
    working_source = recorded_source
    if changed_blob:
        # Keep a recorded anchor for a declaration removed from the working blob.
        recorded_source += b"def _root_.Never.middle : Nat := 99\n"
        locators.append(({"kind": "symbol", "name": "Never.middle"}, 12))
        expected_states["stale"] = 1
        expected_findings.append(("references.5.targets.0", "stale:unresolved"))
    source.write_bytes(working_source)
    sidecar = _record_scope_references(memory, source.name, recorded_source, locators)
    recorded = sidecar.read_bytes()
    scope = SimpleNamespace(
        repo_id="fixture", code_root=scope_repository, onboarding_root=sidecar.parent
    )
    monkeypatch.setattr(memory_tools, "_memory_scope", lambda _config, **_options: scope)

    report = memory_tools.citation_check_tool(
        cast(McpRuntimeConfig, None), repo_id="fixture", contract_path="fixture.md"
    )

    assert report["ok"] is True
    assert report["findings"] == []
    findings = report["reportOnlyFindings"]
    assert [(finding["field"], finding["state"]) for finding in findings] == expected_findings
    assert "'middle'" in findings[0]["message"]
    assert "'Outer.middle'" in findings[1]["message"]
    assert report["states"] == expected_states
    assert all(finding["target"] == source.name for finding in findings)
    assert sidecar.read_bytes() == recorded


def test_legacy_lean_citation_preserves_base_conversion_bytes(code: tuple[Path, str]) -> None:
    assert _legacy_lean_digest(*code) == BASE_CONVERSION_DIGEST
