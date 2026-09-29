"""MIK-R24 rules 1-4 and 6: the conversion command over fixture cards and a legacy database.

The fixture (``knowledge_conversion_test_support``) holds a card with each table kind, a row naming
several anchors and sources, placeholder rows, a quoted anchor, a removed path, a range past the end,
a URL, a whole-file source, a test source, marker-shaped prose and an Update History section; a route
overview; and a legacy database with a two-revision invariant, a family and claims -- one of them on
a file whose card is gone.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

import pytest
from agents_remember.cli.__main__ import main as cli_main
from agents_remember.memory.conversion import convert as convert_module
from agents_remember.memory.conversion.cards import EVIDENCE_HEADING
from agents_remember.memory.conversion.citations import ANCHOR_NOTE_LABEL, unbound_anchor_text
from agents_remember.memory.conversion.code_objects import CodeObjects
from agents_remember.memory.conversion.convert import (
    ConversionRefused,
    ConversionVersionError,
    convert_memory,
)
from agents_remember.memory.conversion.inputs import memory_from_directory
from agents_remember.memory_quality.knowledge_validator.report import (
    ValidationReport,
    Violation,
)
from agents_remember.memory_quality.style.citations import model
from agents_remember.memory_quality.style.document_shape import inline_scan
from agents_remember.models.knowledge_files.anchor_content import content_identity, range_bytes
from agents_remember.models.knowledge_files.ids import derived_realization_id, derived_record_id
from knowledge_conversion_test_support import (
    APP,
    APP_CARD,
    APP_SOURCE,
    OTHER_CARD,
    ROUTE_CARD,
    STRAY_ROW,
    CodeFixture,
    code_repository,
    git,
    legacy_database,
    memory_repository,
)

APP_SIDECAR = f"onboarding/{APP}.json"


@pytest.fixture
def repositories(tmp_path: Path) -> tuple[CodeFixture, Path]:
    code = code_repository(tmp_path / "code")
    memory = tmp_path / "memory"
    memory_repository(memory, code)
    return code, memory


def _convert(code: CodeFixture, memory: Path) -> convert_module.ConversionOutcome:
    return convert_memory(
        memory_from_directory(memory), CodeObjects(code.root), paired_commit=code.head
    )


def _json(outcome: convert_module.ConversionOutcome, path: str) -> dict:
    return json.loads(outcome.files[path])


def test_a_card_becomes_prose_one_evidence_section_and_numbered_references(
    repositories: tuple[CodeFixture, Path],
) -> None:
    code, memory = repositories
    outcome = _convert(code, memory)

    markdown = outcome.files[APP_CARD].decode("utf-8")
    assert "| Field | Value |" not in markdown and "lastVerifiedCommitHash" not in markdown
    assert "## Update History" not in markdown and "line(s)" not in markdown
    assert "callers read the result as signals\\[0]." in markdown  # escaped, not a marker
    assert markdown.count("## Evidence") == 1
    assert "### Docs References\n\nNo docs are configured.\n\n" in markdown
    # The placeholder rows produce no reference; their sentences stay as prose.
    assert "No configured external domain-documentation evidence.\n- The guide" in markdown
    assert markdown.endswith(
        f"- The whole guide. [8]\n\n{STRAY_ROW}\n\n### Cross-Repo References\n\n"
        "No additional configured cross-repository evidence is claimed.\n"
    )
    assert "- alpha and beta compute the total. [2]" in markdown
    assert "| Finding |" not in markdown
    other = outcome.files[OTHER_CARD].decode("utf-8")
    assert other == "# src/pkg/other.py\n\n## Purpose\n\nNo evidence here.\n"
    assert "onboarding/src/pkg/other.py.json" not in outcome.files  # nothing machine-relevant

    references = _json(outcome, APP_SIDECAR)["references"]
    assert sorted(references, key=int) == [str(number) for number in range(1, 9)]
    two = references["2"]
    # Two symbols; both ranges lie around them, so they add no target (rule 1).
    assert [target["anchor"]["locator"] for target in two["targets"]] == [
        {"kind": "symbol", "name": "alpha"},
        {"kind": "symbol", "name": "beta"},
    ]
    alpha = two["targets"][0]["anchor"]
    assert "path" not in alpha  # the sidecar's own file
    assert alpha["blob"] == git(code.root, "rev-parse", f"{code.first}:{APP}")
    assert alpha["content"] == content_identity(range_bytes(APP_SOURCE.encode(), 4, 5))
    assert references["3"]["targets"][0]["anchor"]["locator"] == {
        "kind": "symbol",
        "name": "method",
    }
    assert references["4"]["targets"][0]["anchor"]["locator"] == {
        "kind": "line_range",
        "start": 5,
        "end": 5,
    }  # a quoted anchor keeps its range
    assert references["5"]["targets"] == [{"kind": "unresolved", "text": "src/pkg/gone.py:1-3"}]
    # No anchor text is lost: what no symbol target carries is kept in the note, as written.
    assert references["4"]["note"] == (
        'A quoted literal keeps its range.\n\nAnchor: "return value + 1"'
    )
    assert references["5"]["note"] == "A removed file stays visible.\n\nAnchor: `gone`"
    assert references["1"]["note"] == 'The guide explains the flow.\n\nAnchor: "The flow is"'
    assert two["note"] == "alpha and beta compute the total."  # both anchors are targets
    # A separator inside a quoted literal is kept byte for byte; only bound symbols are cut out.
    assert unbound_anchor_text('"f(\\"x\\");"; `alpha`; `gone`', {"alpha"}) == (
        '"f(\\"x\\");"; `gone`'
    )
    assert references["6"]["targets"] == [{"kind": "unresolved", "text": f"{APP}:90-95"}]
    assert references["7"]["targets"][0]["kind"] == "test"
    assert references["7"]["targets"][0]["anchor"]["path"] == "tests/test_app.py"
    assert references["8"]["targets"][0]["anchor"]["locator"] == {"kind": "file"}
    assert references["1"]["targets"][1] == {
        "kind": "external",
        "document": {"document": "https://example.invalid/spec"},
    }

    route = _json(outcome, "onboarding/src/overview.json")
    assert route["path"] == "src"
    assert route["references"]["1"]["targets"][0]["anchor"]["path"] == APP
    assert "### Repo-Internal References" in outcome.files[ROUTE_CARD].decode("utf-8")

    report = outcome.report
    assert report["before"]["realRows"] == 9 and report["before"]["placeholderRows"] == 2
    assert report["after"]["references"] == 9
    assert report["after"]["unresolvedByReason"] == {"path-absent": 1, "range-outside-file": 1}
    assert report["fallbackAnchoredCards"] == [ROUTE_CARD]  # it names no verified commit
    assert report["governingOverviewNotNearest"] == [
        {
            "card": OTHER_CARD,
            "governingOverview": "../../overview.md",
            "nearest": "onboarding/src/overview.md",
        }
    ]
    assert report["validation"]["refusing"] == 0
    _assert_listed(report, code, memory)


def _assert_listed(report: dict, code: CodeFixture, memory: Path) -> None:
    """The report names abbreviated anchor commits and leftover citation-shaped rows."""

    assert report["abbreviatedAnchorCommits"] == [
        {"card": OTHER_CARD, "lastVerifiedCommitHash": code.first[:10], "resolved": [code.first]}
    ]
    stray = next(
        number
        for number, line in enumerate((memory / APP_CARD).read_text().split("\n"), start=1)
        if line == STRAY_ROW
    )
    assert report["strayCitationRows"] == [{"card": APP_CARD, "lines": [stray]}]


def test_the_database_exports_head_records_and_entries_in_their_recorded_blobs(
    repositories: tuple[CodeFixture, Path],
) -> None:
    code, memory = repositories
    outcome = _convert(code, memory)

    one = derived_record_id("invariant", "inv-one")
    two = derived_record_id("invariant", "inv-two")
    record = _json(outcome, f"knowledge/invariants/{one}-FIX-I-1.json")
    assert record["revision"] == 2  # the head is the second revision of its chain
    assert record["statement"] == "Alpha adds one."
    assert record["conditions"] == ["Must hold."]
    assert record["origin"] == {
        "task": "260101-FIX",
        "leaf": "260101-FIX-L3",
        "legacyId": "inv-one",
        "handoff": {"evidence": ["Hand-off kind: clause.", "Evidence: measured."]},
    }
    assert record["admission"] == "legacy-unassessed"
    second = _json(outcome, f"knowledge/invariants/{two}-FIX-I-2-two.json")
    assert second["status"] == "accepted"
    # A leaf document's task is learned from the parenthesised leaf of the same task directory.
    assert second["origin"]["leaf"] == "260101-FIX-L05"
    family = next(path for path in outcome.files if path.startswith("knowledge/families/"))
    assert _json(outcome, family)["members"] == sorted([one, two])
    assert _json(outcome, family)["routes"] == []

    realizes = _json(outcome, APP_SIDECAR)["realizes"]
    by_name = {entry["rationale"]: entry for entry in realizes}
    assert set(by_name) == {"Alpha is the adder.", "Beta calls alpha."}  # the old claim is gone
    assert by_name["Beta calls alpha."]["role"] == "support"  # incidental, per MIK-R12's ruling
    alpha = by_name["Alpha is the adder."]
    assert alpha["id"] == derived_realization_id(
        "inv-one", APP, {"kind": "symbol", "name": "alpha"}
    )
    assert alpha["anchor"]["blob"] == code.app_blob
    orphan = _json(outcome, "onboarding/src/pkg/deleted.py.json")
    assert orphan["realizes"][0]["anchor"]["blob"] == code.deleted_blob
    assert orphan["references"] == {}
    assert outcome.report["after"]["markdownlessSidecars"] == ["onboarding/src/pkg/deleted.py.json"]
    assert outcome.report["after"]["realizationStatesAtPairedCommit"] == {
        "current": 2,
        "stale:path-absent": 1,
    }


def test_conversion_is_deterministic_version_pinned_and_a_no_op_once_converted(
    repositories: tuple[CodeFixture, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, memory = repositories
    twin = tmp_path / "twin"
    shutil.copytree(memory, twin)
    args = ["--code", str(code.root), "--code-commit", code.head]

    assert cli_main(["knowledge-convert", str(memory), *args, "--version", "2"]) == 1
    assert "version '2' is not reproduced by this build" in capsys.readouterr().out
    assert git(memory, "status", "--porcelain") == ""

    assert cli_main(["knowledge-convert", str(memory), *args]) == 0
    assert cli_main(["knowledge-convert", str(twin), *args]) == 0
    capsys.readouterr()
    first = {
        p.relative_to(memory): p.read_bytes()
        for p in memory.rglob("*")
        if ".git" not in p.parts and p.is_file()
    }
    second = {
        p.relative_to(twin): p.read_bytes()
        for p in twin.rglob("*")
        if ".git" not in p.parts and p.is_file()
    }
    assert first == second

    assert cli_main(["knowledge-convert", str(memory), *args]) == 0
    assert "already converted" in capsys.readouterr().out
    git(memory, "add", "-A")
    git(memory, "commit", "-q", "-m", "converted")
    # Against its own converted commit, the exported entry at a deleted path is carried: reported.
    validate = ["knowledge-validate", str(memory), "--code", str(code.root), "--base", "HEAD"]
    assert cli_main(validate) == 0
    assert "passes: 0 violation(s)" in capsys.readouterr().out
    with pytest.raises(ConversionVersionError):
        convert_memory(
            memory_from_directory(twin),
            CodeObjects(code.root),
            paired_commit=code.head,
            version="0",
        )


def test_a_refused_conversion_writes_nothing_and_names_what_failed(
    repositories: tuple[CodeFixture, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, memory = repositories
    (memory / "knowledge.sqlite").unlink()
    legacy_database(memory / "knowledge.sqlite", code, colliding=True)
    git(memory, "commit", "-q", "-am", "colliding claims")

    status = cli_main(["knowledge-convert", str(memory), "--code", str(code.root)])
    assert status == 1
    assert "collides: legacy claims c1 and c4" in capsys.readouterr().out
    assert git(memory, "status", "--porcelain") == ""

    (memory / "knowledge.sqlite").unlink()
    legacy_database(memory / "knowledge.sqlite", code)
    # An abbreviated anchor commit that names two commits refuses, naming the card: no fallback.
    monkeypatch.setattr(
        CodeObjects, "commits_with_prefix", lambda self, prefix: ("a" * 40, "b" * 40)
    )
    with pytest.raises(ConversionRefused, match="ambiguous in the code object store") as ambiguous:
        convert_memory(
            memory_from_directory(memory), CodeObjects(code.root), paired_commit=code.head
        )
    assert ambiguous.value.failing == (OTHER_CARD,)
    monkeypatch.undo()
    refusal = ValidationReport(
        candidate="x",
        violations=(Violation(APP_SIDECAR, "references", "R22.1-shape", "broken", False),),
    )
    monkeypatch.setattr(convert_module, "validate_tree", lambda *args, **kwargs: refusal)
    with pytest.raises(ConversionRefused) as refused:
        convert_memory(
            memory_from_directory(memory), CodeObjects(code.root), paired_commit=code.head
        )
    assert refused.value.failing == (APP_SIDECAR,)
    assert git(memory, "status", "--porcelain") == "M knowledge.sqlite"  # the test's own rewrite


def test_a_converted_card_renders_back_to_its_content_without_history(
    repositories: tuple[CodeFixture, Path],
) -> None:
    code, memory = repositories
    original = (memory / APP_CARD).read_text(encoding="utf-8")
    outcome = _convert(code, memory)

    rendered = render_legacy(outcome.files[APP_CARD].decode("utf-8"), _json(outcome, APP_SIDECAR))

    kept = original.split("## Update History")[0].rstrip("\n").split("\n")
    # Every prose line and heading of the original (tables aside) comes back, in order.
    prose = [line for line in kept if line.strip() and not line.startswith("|")]
    back = [line for line in rendered.split("\n") if line.strip() and not line.startswith("|")]
    placeholders = {
        "No configured external domain-documentation evidence.",
        "No additional configured cross-repository evidence is claimed.",
    }
    assert [line for line in back if line not in placeholders] == prose
    assert "callers read the result as signals[0]." in rendered
    assert "| alpha and beta compute the total. | `alpha`; `beta` | src/pkg/app.py |" in rendered
    # Anchor text no target carries comes back from the note (the rule 1 ruling).
    assert f'| A quoted literal keeps its range. | "return value + 1" | {APP}:5-5 |' in rendered
    assert "| A removed file stays visible. | `gone` | src/pkg/gone.py:1-3 |" in rendered
    # A placeholder row was never a reference: its sentence is prose on both sides.
    assert "No configured external domain-documentation evidence." in rendered


def conversion_digest(files: dict[str, bytes]) -> str:
    """SHA-256 over every converted path and its bytes, in path order."""

    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(path.encode("utf-8") + b"\0" + files[path] + b"\0")
    return digest.hexdigest()


# Conversion-format version 1, pinned (MIK-R24 rule 6). A later build reproduces version 1 byte for
# byte or refuses it: if this digest changes, the change -- in cards, citations, the export, the
# marker escaping or the shipped extractor (qualified_spans, extents.definitions, a grammar bump) --
# must ship as a new conversion-format version, never as a new digest for "1".
VERSION_1_FIXTURE_DIGEST = "2e3b3110179ae21e37f47fb5223f2f275f16b26b775ff45dd2219d9ecd56ad2c"


def test_conversion_format_version_1_reproduces_its_pinned_bytes(
    repositories: tuple[CodeFixture, Path], tmp_path: Path
) -> None:
    code, memory = repositories
    outcome = _convert(code, memory)
    assert conversion_digest(outcome.files) == VERSION_1_FIXTURE_DIGEST


# --------------------------------------------------------------------------------------------------
# Back to prose (expected evidence: "converting a sample back to prose, without history, reproduces
# the original content"). Evidence-only, so it lives with its test rather than in the product: the
# ``## Evidence`` section unfolds into its ``##`` reference sections, each run of ``- <finding> [n]``
# becomes a ``| Finding | Anchor | Source |`` table (anchor text kept in ``note`` comes back into the
# anchor cell), and escaped ``\\[n]`` is written ``[n]`` again.
# --------------------------------------------------------------------------------------------------

_BULLET: Final = re.compile(r"^- (?P<finding>.*?) ?\[(?P<number>[1-9][0-9]*)\]$")
_ESCAPED: Final = re.compile(r"\\(\[[0-9]+\])")
_PROMOTABLE: Final = re.compile(r"^#(#{2,6})(\s)")
TABLE_HEADER: Final = ("| Finding | Anchor | Source |", "| --- | --- | --- |")


def _cells(reference: Mapping[str, Any], own: str | None) -> tuple[str, str]:
    anchors: list[str] = []
    sources: list[str] = []
    for target in reference.get("targets", []):
        kind = target.get("kind")
        if kind in {"code", "test"}:
            anchor = target["anchor"]
            path = anchor.get("path", own) or ""
            locator = anchor["locator"]
            if locator["kind"] == "symbol":
                anchors.append(f"`{locator['name']}`")
                sources.append(path)
            elif locator["kind"] == "line_range":
                sources.append(f"{path}:{locator['start']}-{locator['end']}")
            else:
                sources.append(path)
        elif kind == "unresolved":
            sources.append(str(target["text"]))
        elif kind == "external":
            sources.append(str(target["document"]["document"]))
        else:
            anchors.append(f"`{target.get('id', kind)}`")
    note = str(reference.get("note", ""))
    label = note.rfind(ANCHOR_NOTE_LABEL)
    if label != -1 and (label == 0 or note[label - 2 : label] == "\n\n"):
        anchors.append(note[label + len(ANCHOR_NOTE_LABEL) :])
    return "; ".join(dict.fromkeys(anchors)) or "—", "; ".join(dict.fromkeys(sources)) or "—"


def _unescaped(line: str) -> str:
    """``line`` with each ``\\[n]`` outside inline code written ``[n]`` again."""

    masked = model.masked(line)
    for match in reversed(list(_ESCAPED.finditer(masked))):
        line = line[: match.start()] + line[match.start() + 1 :]
    return line


def _unfolded(lines: list[str], unfenced: set[int]) -> list[tuple[str, bool]]:
    """``lines`` with the ``## Evidence`` heading removed and its subsections promoted a level."""

    unfolded: list[tuple[str, bool]] = []
    in_evidence = False
    skip_blank = False
    for index, line in enumerate(lines):
        prose = index in unfenced
        if skip_blank and line == "":
            skip_blank = False
            continue
        skip_blank = False
        if prose and line.startswith("## "):
            in_evidence = line.strip() == EVIDENCE_HEADING
            if in_evidence:
                skip_blank = True
                continue
        promoted = _PROMOTABLE.sub(lambda match: f"{match.group(1)}{match.group(2)}", line, count=1)
        unfolded.append((promoted if in_evidence and prose else line, prose))
    return unfolded


def render_legacy(markdown: str, sidecar: Mapping[str, Any] | None) -> str:
    """The legacy-shaped Markdown of a converted card and its sidecar."""

    document = dict(sidecar or {})
    references = dict(document.get("references", {}))
    own = document.get("path") if document.get("schema") == "ar-onboarding-file/v1" else None
    lines = markdown.rstrip("\n").split("\n")
    unfenced = {index for index, _ in inline_scan.unfenced_lines(lines)}
    output: list[str] = []
    table_open = False
    for text, prose in _unfolded(lines, unfenced):
        match = _BULLET.match(text) if prose else None
        if match is not None and match["number"] in references:
            if not table_open:
                output.extend(TABLE_HEADER)
                table_open = True
            anchor, source = _cells(references[match["number"]], own)
            output.append(f"| {match['finding']} | {anchor} | {source} |")
            continue
        table_open = False
        output.append(_unescaped(text) if prose else text)
    while output and output[-1] == "":
        output.pop()
    return "\n".join(output) + "\n"
