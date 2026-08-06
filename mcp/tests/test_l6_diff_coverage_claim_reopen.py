"""L6 closeout coverage tests for claim-reopen evaluation branches."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.memory_quality.style.citations import (
    claim_reopen,
    extents,
    model,
)
from agents_remember.memory_quality.style.citations.claim_reopen import (
    Candidate,
    LocalSource,
    SourceViews,
    anchor_change,
    dependency_changes,
)


def _citation(path: str = "a.py") -> model.Citation:
    return model.Citation(text=f"{path}:1-1", path=path, start=1, end=1)


def _source(current: list[str] | None = None, historical: list[str] | None = None) -> LocalSource:
    return LocalSource(
        citation=_citation(),
        kind="code commit",
        current=current,
        historical=historical,
        provenance_label="code commit abc",
    )


def _candidate(fingerprint: str, *, start: int = 1) -> Candidate:
    return Candidate(
        source=_source(),
        extent=extents.Extent(start, start, extents.DEFINITION),
        fingerprint=fingerprint,
    )


class FakeViews:
    def __init__(self, before: list[Candidate], now: list[Candidate]) -> None:
        self.before = before
        self.now = now

    def candidates(self, _anchor: model.Anchor, _source: LocalSource, *, historical: bool):
        return self.before if historical else self.now


class TestSourceViewsCandidates:
    def test_none_lines_and_cache(self) -> None:
        views = SourceViews()
        anchor = model.Anchor(kind=model.SYMBOL, text="f")
        source = _source(historical=None)
        assert views.candidates(anchor, source, historical=True) == []
        source = _source(current=["def f():\n", "    pass\n"])
        first = views.candidates(anchor, source, historical=False)
        second = views.candidates(anchor, source, historical=False)
        assert first == second and len(first) == 1
        assert len(views.cache) == 1


class TestAnchorChange:
    def test_all_empty_with_dependency_sources(self) -> None:
        assert anchor_change(
            model.Anchor(kind=model.SYMBOL, text="f"),
            [_source()],
            cast(SourceViews, FakeViews([], [])),
            dependency_sources=True,
        ) == ([], [], [])

    def test_non_unique_historical(self) -> None:
        changed, surfaced, invalid = anchor_change(
            model.Anchor(kind=model.SYMBOL, text="f"),
            [_source()],
            cast(
                SourceViews,
                FakeViews([_candidate("a"), _candidate("b", start=2)], [_candidate("a")]),
            ),
            dependency_sources=False,
        )
        assert changed == [] and surfaced == [] and invalid and "resolved 2 times" in invalid[0]

    def test_missing_now(self) -> None:
        changed, surfaced, _invalid = anchor_change(
            model.Anchor(kind=model.SYMBOL, text="f"),
            [_source()],
            cast(SourceViews, FakeViews([_candidate("a")], [])),
            dependency_sources=False,
        )
        assert changed and surfaced == [] and "no longer resolves" in changed[0]

    def test_non_unique_now(self) -> None:
        changed, surfaced, invalid = anchor_change(
            model.Anchor(kind=model.SYMBOL, text="f"),
            [_source()],
            cast(
                SourceViews,
                FakeViews([_candidate("a")], [_candidate("a"), _candidate("b", start=2)]),
            ),
            dependency_sources=False,
        )
        assert changed == [] and surfaced == [] and invalid and "resolves 2 times now" in invalid[0]

    def test_unchanged_and_changed(self) -> None:
        assert anchor_change(
            model.Anchor(kind=model.SYMBOL, text="f"),
            [_source()],
            cast(SourceViews, FakeViews([_candidate("same")], [_candidate("same")])),
            dependency_sources=False,
        ) == ([], [], [])

    def test_changed_construct_with_current_citation_surfaces_report_only(self) -> None:
        changed, surfaced, invalid = anchor_change(
            model.Anchor(kind=model.SYMBOL, text="f"),
            [_source()],
            cast(SourceViews, FakeViews([_candidate("old")], [_candidate("new")])),
            dependency_sources=False,
        )
        assert changed == [] and invalid == []
        assert surfaced and "changed structurally" in surfaced[0]

    def test_changed_construct_with_stale_range_is_enforced(self) -> None:
        changed, surfaced, invalid = anchor_change(
            model.Anchor(kind=model.SYMBOL, text="f"),
            [_source()],
            cast(SourceViews, FakeViews([_candidate("old")], [_candidate("new", start=5)])),
            dependency_sources=False,
        )
        assert surfaced == [] and invalid == []
        assert changed and "changed structurally" in changed[0]


class TestDependencyChanges:
    def test_unsupported_ecosystem(self) -> None:
        evaluation = cast(
            claim_reopen.Evaluation,
            SimpleNamespace(
                code_commit="abc",
                histories=SimpleNamespace(dependency_versions=lambda *a: (None, None, None)),
            ),
        )
        changed, invalid = dependency_changes([_citation("x.txt")], evaluation)
        assert changed == [] and invalid and "no resolved-version namespace" in invalid[0]

    def test_dependency_version_branches(self) -> None:
        before = SimpleNamespace(version="1.0", surface="mcp/requirements.txt")
        now = SimpleNamespace(version="1.1", surface="mcp/requirements.txt")

        def versions(package: str, ecosystem: str, commit: str):
            if package == "err":
                return None, None, "boom"
            if package == "missing":
                return before, None, None
            if package == "changed":
                return before, now, None
            return before, before, None

        evaluation = cast(
            claim_reopen.Evaluation,
            SimpleNamespace(
                code_commit="abc", histories=SimpleNamespace(dependency_versions=versions)
            ),
        )
        citations = [
            _citation("err/__init__.py"),
            _citation("missing/__init__.py"),
            _citation("changed/__init__.py"),
            _citation("same/__init__.py"),
        ]
        changed, invalid = dependency_changes(citations, evaluation)
        assert any("boom" in item for item in invalid)
        assert any("no resolved version" in item for item in invalid)
        assert any("1.0 -> 1.1" in item for item in changed)
