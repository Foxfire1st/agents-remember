"""Filled Curator layouts use the real citation writer, publication gate and catalog readers.

The evidence is the current adoption scanner, copied byte-for-byte into a disposable Git repo.
These are commit-gate tests, not a running role, closeout or landing simulation.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from agents_remember.application.memory_tools import (
    CitationOperationScope,
    _converted_citation_fix,
)
from agents_remember.memory_quality.converted_cards import card_sidecar_path
from agents_remember.memory_quality.integrity.onboarding_drift_check.entities import (
    parse_entity_census_rows,
)
from agents_remember.memory_quality.knowledge_validator.commit_route import GitKnowledgeValidation
from agents_remember.memory_quality.legacy_cards import find_legacy_cards
from agents_remember.memory_quality.style.document_shape import entity_catalog_alignment
from agents_remember.worktrees.modules.onboarding import (
    compute_git_blob_set_fingerprint,
    entity_fingerprint_plan_problems,
    entity_fingerprint_refresh_plan_for_context,
)
from agents_remember.worktrees.services import LeafPublication
from knowledge_conversion_test_support import code_repository, commit_files, git, init_repository
from knowledge_validator_test_support import (
    CODE_PATHS,
    fixture_code_contents,
    fixture_tree_files,
    write_tree,
)

REPO = Path(__file__).resolve().parents[2]
TEMPLATES = REPO / "skills/l-01-agent-lifecycles/templates"
SOURCE_INPUT = REPO / "mcp/src/agents_remember/memory_quality/legacy_cards.py"
SOURCE = "src/pkg/legacy_cards.py"
_AR_EVIDENCE_INPUTS = (
    "skills/l-01-agent-lifecycles/templates/file-card.md",
    "skills/l-01-agent-lifecycles/templates/repo-overview.md",
    "skills/l-01-agent-lifecycles/templates/route-local-overview.md",
    "skills/l-01-agent-lifecycles/templates/repo-entity-catalog.md",
)
FILE_CARD = f"{SOURCE}.md"
ROOT_OVERVIEW = "overview.md"
ROUTE_OVERVIEW = "src/overview.md"
DOCUMENTS = (FILE_CARD, ROOT_OVERVIEW, ROUTE_OVERVIEW)
FINDING = "The adoption scan returns old-format onboarding card paths without editing the cards."
ENTITY = "Onboarding card"


def _filled(name: str, replacements: dict[str, str]) -> str:
    text = (TEMPLATES / name).read_text(encoding="utf-8")
    assert text.startswith("<!-- ")
    comment, separator, text = text.partition("-->\n")
    assert comment and separator
    for placeholder, value in replacements.items():
        assert placeholder in text, (name, placeholder)
        text = text.replace(placeholder, value)
    assert re.search(r"<[^>]+>", text) is None, name
    return text


def _evidence() -> dict[str, str]:
    return {
        "<source path>": SOURCE,
        "<defining symbol>": "find_legacy_cards",
        "<start>": "1",
        "<end>": str(len(SOURCE_INPUT.read_text(encoding="utf-8").splitlines())),
        "<What the checked evidence establishes.>": FINDING,
    }


def _file_card() -> str:
    return _filled(
        "file-card.md",
        {
            **_evidence(),
            "<area name>": "Card adoption",
            "<relative path to the governing overview>": "overview.md",
            "<Explain this file's job and the boundary it owns.>": "Find old-format onboarding cards at the adoption boundary.",
            "<Explain the important logic, conventions, contracts, failure cases and limits that a "
            "reader cannot safely infer from names alone.>": "The scan skips hidden paths and unreadable cards and returns sorted matches.",
            "<Explain the internal callers and dependencies, documentation contracts and "
            "cross-repository behavior that affect this file. Distinguish the file's work from "
            "its neighbours.>": "Adoption owns this scan; knowledge validation owns unreadable-file refusals.",
        },
    )


def _repo_overview() -> str:
    return _filled(
        "repo-overview.md",
        {
            **_evidence(),
            "<repository>": "Adoption evidence fixture",
            "<Explain the repository's purpose, responsibilities and deployment model.>": "This isolated source tree carries the actual onboarding adoption scanner.",
            "<Name the entry areas, files, identifiers or commands that help a reader narrow the "
            "next source read.>": "Start at src/pkg/legacy_cards.py and find_legacy_cards.",
            "<Explain the components, how they interact and the important flows. Include a "
            "diagram when it helps.>": "The scanner reads Markdown and returns matching paths.",
            "<area>": "Card adoption",
            "<source route>": "src",
            "<what belongs here>": "Source used to inspect onboarding cards",
            "<area overview>": "Source overview",
            "<relative overview path>": "src/overview.md",
            "<why this file matters>": "Owns the old-format card scan",
            "<file card>": "Scanner card",
            "<relative card path>": f"src/pkg/{Path(FILE_CARD).name}",
            "<Explain the repository-wide invariants, important exclusions, ownership and "
            "failure boundaries.>": "The scanner reports matches and does not edit cards.",
            "<Describe the useful build, run and verification entrypoints and what each "
            "establishes.>": "The registered layout tests author citations and validate publication.",
            "<Explain relevant internal, cross-repository and external documentation contracts. "
            "Cite their evidence in the final section.>": "The scanner's module documentation names adoption as its owning boundary.",
            "<Record deferred areas, moved or removed coverage and unresolved findings that "
            "affect this overview. Distinguish checked facts from questions.>": "Only card adoption is described; unrelated fixture source is outside this card.",
        },
    )


def _route_overview() -> str:
    return _filled(
        "route-local-overview.md",
        {
            **_evidence(),
            "<area>": "Card adoption source",
            "<parent area>": "Repository",
            "<relative path to the parent overview>": "../overview.md",
            "<Explain this subtree's purpose and scope as an area in its own right.>": "This source area contains the old-format onboarding card scanner.",
            "<Name the useful entry files, symbols, configuration keys and error strings that "
            "narrow the next read.>": "Read pkg/legacy_cards.py and find_legacy_cards.",
            "<Explain what belongs here, what belongs elsewhere, and the relevant neighbouring "
            "area links.>": "The scanner reports old format; it does not replace validator checks.",
            "<Explain the structures, state and interactions that make this area work.>": "The scan collects memory-relative Markdown paths without changing source state.",
            "<Describe the important flows and the files that carry each step.>": "The scanner reads non-hidden onboarding Markdown and returns matches.",
            "<why this file matters>": "Owns the old-format card scan",
            "<file card>": "Scanner card",
            "<relative card path>": f"pkg/{Path(FILE_CARD).name}",
            "<current or deferred, with reason>": "Current: scanner source was read",
            "<Explain local invariants, boundaries and non-obvious failure cases.>": "Unreadable cards are skipped here; the validator owns their refusal.",
            "<Explain internal dependencies, cross-repository boundaries and documentation that "
            "affect this area. Put their evidence in the final section.>": "The module documentation relates this scanner to baseline adoption.",
            "<child route>": "src/pkg",
            "<child area>": "Package",
            "<relative child overview path>": "pkg/overview.md",
            "<Explain how to navigate this area, its primary cross-cutting explanations and any "
            "unresolved or deferred coverage.>": "Follow the scanner card for details; unrelated fixture files are not covered.",
        },
    )


def _catalog(code: Path, head: str) -> str:
    fingerprint = compute_git_blob_set_fingerprint(code, [SOURCE], tree=head)
    return _filled(
        "repo-entity-catalog.md",
        {
            "<repository name>": "Adoption evidence fixture",
            "<YYYY-MM-DDThh:mm UTC>": "2026-11-06T00:00 UTC",
            "<draft or active>": "active",
            "<Explain the real entities this selective catalog covers and the naming or tracing "
            "confusion it resolves.>": "Distinguish an onboarding card from its source file.",
            "<Canonical Entity Name>": ENTITY,
            "<digest>": fingerprint.removeprefix("sha256:"),
            "<repository-relative defining path>`; `<repository-relative mapping path>": SOURCE,
            "<the kind of real object or stable system concept>": "Onboarding document",
            "<what this entity stands for>": "An onboarding Markdown document read at adoption",
            "<the entity's meaning and relevant boundaries>": "A card describes source; it is not the source file itself.",
            "<the defining layer, model or authoritative document>": "The adoption scanner",
            "<current synonyms or overloaded labels>": "Card and source file are distinct objects.",
            "<identifiers and their scope>": "Memory-relative Markdown path",
            "<containment and ownership>": "Lives under the memory repository's onboarding tree",
            "<nearby entities and the distinction>": "The paired code file supplies evidence.",
            "<actual defining code or document references>": f"`{SOURCE}:find_legacy_cards`",
            "<active naming or representation constraints, when applicable>": "This catalog describes cards inspected by the adoption scan.",
            "<relevant layer>": "Adoption scan",
            "<how the same entity appears here>": "A memory-relative path in the scan result",
            "<Describe catalog-level boundaries or naming caveats. Omit when empty.>": "The inventory is selective and makes no claim to enumerate all source objects.",
        },
    )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _world(root: Path) -> tuple[Path, Path, str]:
    code = code_repository(root / "code").root
    files: dict[str, str | None] = {path: "# code\n" for path in CODE_PATHS}
    files.update({path: data.decode("utf-8") for path, data in fixture_code_contents().items()})
    files[SOURCE] = SOURCE_INPUT.read_text(encoding="utf-8")
    head = commit_files(code, files, "Pinned validator world and actual scanner evidence")
    memory = root / "memory"
    init_repository(memory)
    write_tree(memory, fixture_tree_files())
    _write(memory / "onboarding/src/pkg/overview.md", "# Package\n\n## Purpose\n\nCard adoption.\n")
    base = commit_files(memory, {}, "Unchanged validator family, records and sidecars")
    for name, text in zip(
        DOCUMENTS, (_file_card(), _repo_overview(), _route_overview()), strict=True
    ):
        _write(memory / "onboarding" / name, text)
    _write(memory / "onboarding/entities.md", _catalog(code, head))
    return code, memory, base


def _fix(code: Path, memory: Path, *, dry_run: bool = False) -> dict:
    return _converted_citation_fix(
        memory, code, CitationOperationScope(documents=DOCUMENTS), dry_run=dry_run
    )


def _bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(root).parts
    }


def _judge_candidate(code: Path, memory: Path, base: str, candidate: str) -> str | None:
    return GitKnowledgeValidation().leaf_refusal(
        memory_repository=memory,
        publication=LeafPublication(candidate, (base,)),
        code_repository=code,
        code_commit=git(code, "rev-parse", "HEAD"),
    )


def _publication_refusal(code: Path, memory: Path, base: str) -> tuple[str, str | None]:
    git(memory, "add", "-A")
    candidate = git(memory, "write-tree")
    return candidate, _judge_candidate(code, memory, base, candidate)


def _publish(code: Path, memory: Path, base: str) -> None:
    candidate, refusal = _publication_refusal(code, memory, base)
    assert refusal is None, refusal
    git(memory, "commit", "-q", "--allow-empty", "-m", "Publish the validated layout candidate")
    assert git(memory, "rev-parse", "HEAD^{tree}") == candidate


def test_filled_layouts_author_real_references_and_publish(tmp_path: Path) -> None:
    code, memory, base = _world(tmp_path)
    before = _bytes(memory)
    preview = _fix(code, memory, dry_run=True)
    assert preview["ok"] is True
    assert preview["authoring"]["authoredReferences"] == len(DOCUMENTS)
    assert _bytes(memory) == before
    result = _fix(code, memory)
    assert result["ok"] is True and result["authoring"]["refused"] == []
    assert result["authoring"]["authoredReferences"] == len(DOCUMENTS)
    assert result["authoring"]["unresolvedTargets"] == [] and result["stale"] == []
    assert set(result["authoring"]["createdSidecars"]) == {
        card_sidecar_path(f"onboarding/{name}") for name in DOCUMENTS
    }
    markerless = memory / "onboarding/src/pkg/overview.md"
    assert not (memory / card_sidecar_path("onboarding/src/pkg/overview.md")).exists()
    assert "[1]" not in markerless.read_text(encoding="utf-8")
    for name in DOCUMENTS:
        card = f"onboarding/{name}"
        sidecar = json.loads((memory / card_sidecar_path(card)).read_text(encoding="utf-8"))
        assert set(sidecar["references"]) == {"1"}
        assert sidecar["references"]["1"]["targets"]
        assert f"- {FINDING} [1]" in (memory / card).read_text(encoding="utf-8")
    assert find_legacy_cards(memory) == []
    _publish(code, memory, base)


@pytest.mark.parametrize("name", (ROOT_OVERVIEW, ROUTE_OVERVIEW))
@pytest.mark.parametrize("bad_order", ("before-later-section", "prose-after-citation"))
def test_filled_overviews_refuse_bad_citation_order(
    tmp_path: Path,
    name: str,
    bad_order: str,
) -> None:
    code, memory, base = _world(tmp_path)
    assert _fix(code, memory)["ok"] is True
    _publish(code, memory, base)
    card = memory / "onboarding" / name
    good = card.read_text(encoding="utf-8")
    citation = f"- {FINDING} [1]"
    assert good.count(citation) == 1
    if bad_order == "before-later-section":
        bad = good.replace(citation, "").replace("## Evidence", f"{citation}\n\n## Evidence")
    else:
        assert bad_order == "prose-after-citation"
        bad = good + "Ordinary prose cannot follow the final citation.\n"
    card.write_text(bad, encoding="utf-8")
    _, refusal = _publication_refusal(code, memory, base)
    assert refusal is not None
    lines = refusal.splitlines()[1:]
    assert lines and all(": [R80.7-citations-at-end] " in line for line in lines)
    assert all(line.startswith(f"onboarding/{name}: ") for line in lines)
    card.write_text(good, encoding="utf-8")
    _publish(code, memory, base)


def _publish_successful_pair(code: Path, memory: Path, base: str) -> None:
    pair = (f"onboarding/{FILE_CARD}", card_sidecar_path(f"onboarding/{FILE_CARD}"))
    git(memory, "add", "--", *pair)
    candidate = git(memory, "write-tree")
    assert _judge_candidate(code, memory, base, candidate) is None
    git(memory, "commit", "-q", "-m", "Publish only the successful card pair")
    assert git(memory, "rev-parse", "HEAD^{tree}") == candidate
    assert set(git(memory, "ls-tree", "-r", "--name-only", "HEAD").splitlines()) == {
        *git(memory, "ls-tree", "-r", "--name-only", base).splitlines(),
        *pair,
    }
    for path in pair:
        assert git(memory, "rev-parse", f"HEAD:{path}") == git(memory, "hash-object", "--", path)


def test_named_batch_publishes_the_valid_pair_and_retains_two_refused_cards(tmp_path: Path) -> None:
    code, memory, base = _world(tmp_path)
    root_card = memory / f"onboarding/{ROOT_OVERVIEW}"
    root_sidecar = memory / card_sidecar_path(f"onboarding/{ROOT_OVERVIEW}")
    _write(root_sidecar, "{ not json\n")
    route_card = memory / f"onboarding/{ROUTE_OVERVIEW}"
    table = route_card.read_text(encoding="utf-8").split("## Evidence\n\n", 1)[1]
    route_card.write_text(route_card.read_text(encoding="utf-8") + table, encoding="utf-8")
    route_sidecar = memory / card_sidecar_path(f"onboarding/{ROUTE_OVERVIEW}")
    held = (root_card.read_bytes(), root_sidecar.read_bytes(), route_card.read_bytes())
    untouched = memory / "onboarding/src/pkg/overview.md"
    untouched_bytes = untouched.read_bytes()
    result = _fix(code, memory)
    assert result["ok"] is False
    refused = {item["card"]: item["reason"] for item in result["authoring"]["refused"]}
    assert set(refused) == {f"onboarding/{ROOT_OVERVIEW}", f"onboarding/{ROUTE_OVERVIEW}"}
    assert "does not parse" in refused[f"onboarding/{ROOT_OVERVIEW}"]
    assert "two tables with no blank line" in refused[f"onboarding/{ROUTE_OVERVIEW}"]
    assert (root_card.read_bytes(), root_sidecar.read_bytes(), route_card.read_bytes()) == held
    assert not route_sidecar.exists()
    assert untouched.read_bytes() == untouched_bytes
    assert result["authoring"]["authoredCards"] == [f"onboarding/{FILE_CARD}"]
    assert result["authoring"]["authoredReferences"] == 1
    valid = memory / f"onboarding/{FILE_CARD}"
    assert f"- {FINDING} [1]" in valid.read_text(encoding="utf-8")
    valid_sidecar = card_sidecar_path(f"onboarding/{FILE_CARD}")
    assert (memory / valid_sidecar).is_file()
    _publish_successful_pair(code, memory, base)
    assert (root_card.read_bytes(), root_sidecar.read_bytes(), route_card.read_bytes()) == held
    assert not route_sidecar.exists() and untouched.read_bytes() == untouched_bytes


def test_filled_entity_catalog_has_one_supported_inventory_fingerprint_pair(tmp_path: Path) -> None:
    code, memory, _ = _world(tmp_path)
    onboarding = memory / "onboarding"
    context = SimpleNamespace(onboarding_root=onboarding, code_repository_root=code)
    plan = entity_fingerprint_refresh_plan_for_context(context, [SOURCE], base_evidence={})
    assert plan["unsupported"] == [] and len(plan["required"]) == 1
    assert (
        entity_fingerprint_plan_problems(context, plan, code_tree=git(code, "rev-parse", "HEAD"))
        == []
    )
    text = (onboarding / "entities.md").read_text(encoding="utf-8")
    result = entity_catalog_alignment.check_onboarding_root(onboarding)
    assert result["ok"] is True and result["filesChecked"] == 1
    rows = parse_entity_census_rows(text)
    assert len(rows) == 1 and rows[0].key == ENTITY
    assert rows[0].evidence_paths == (SOURCE,)


@pytest.mark.parametrize(
    "mutation, expected",
    (
        (
            "orphan",
            ("entity_fingerprint_without_inventory", "entity_inventory_without_fingerprint"),
        ),
        ("missing-fingerprint", ("entity_inventory_without_fingerprint",)),
        ("duplicate-fingerprint", ("entity_fingerprint_duplicate",)),
        (
            "missing-inventory-heading",
            (
                "entity_inventory_section_missing",
                "entity_fingerprint_without_inventory",
            ),
        ),
        (
            "missing-fingerprint-heading",
            (
                "entity_fingerprint_section_missing",
                "entity_inventory_without_fingerprint",
            ),
        ),
    ),
)
def test_filled_entity_catalog_refuses_misaligned_inventory(
    tmp_path: Path,
    mutation: str,
    expected: tuple[str, ...],
) -> None:
    _, memory, _ = _world(tmp_path)
    onboarding = memory / "onboarding"
    catalog = onboarding / "entities.md"
    good = catalog.read_text(encoding="utf-8")
    row = next(line for line in good.splitlines() if line.startswith(f"| {ENTITY} |"))
    if mutation == "orphan":
        bad = good.replace(f"### {ENTITY}", "### Unrelated document", 1)
    elif mutation == "missing-fingerprint":
        bad = good.replace(row + "\n", "", 1)
    elif mutation == "duplicate-fingerprint":
        bad = good.replace(row, row + "\n" + row, 1)
    elif mutation == "missing-inventory-heading":
        bad = good.replace("## Entity Inventory", "## Other objects", 1)
    else:
        assert mutation == "missing-fingerprint-heading"
        bad = good.replace("## Entity Fingerprints", "## Other fingerprints", 1)
    catalog.write_text(bad, encoding="utf-8")
    result = entity_catalog_alignment.check_onboarding_root(onboarding)
    assert result["ok"] is False
    assert set(expected) == {item["code"] for item in result["findings"]}
    assert {item["path"] for item in result["findings"]} == {"entities.md"}
    with pytest.raises(ValueError):
        parse_entity_census_rows(bad)
    catalog.write_text(good, encoding="utf-8")
    assert entity_catalog_alignment.check_onboarding_root(onboarding)["ok"] is True
    assert len(parse_entity_census_rows(good)) == 1


@pytest.mark.parametrize(
    "name, heading",
    (
        (ROOT_OVERVIEW, "What This Repo Is"),
        (ROUTE_OVERVIEW, "What This Area Is"),
    ),
)
def test_filled_overview_refuses_a_nonpurpose_first_section(
    tmp_path: Path,
    name: str,
    heading: str,
) -> None:
    code, memory, base = _world(tmp_path)
    assert _fix(code, memory)["ok"] is True
    card = memory / "onboarding" / name
    good = card.read_text(encoding="utf-8")
    card.write_text(good.replace(f"## {heading}", "## Implementation", 1), encoding="utf-8")
    _, refusal = _publication_refusal(code, memory, base)
    assert refusal is not None
    lines = refusal.splitlines()[1:]
    assert lines and all(": [R80.1-purpose-first] " in line for line in lines)
    assert all(line.startswith(f"onboarding/{name}: ") for line in lines)
    card.write_text(good, encoding="utf-8")
    _publish(code, memory, base)


@pytest.mark.parametrize(
    "old_format",
    (
        "\n| doc_type | file-level-onboarding |\n",
        "\n## Update History\n",
    ),
)
def test_filled_card_has_no_old_format_marks_until_the_negative_mutation(
    tmp_path: Path,
    old_format: str,
) -> None:
    _, memory, _ = _world(tmp_path)
    card = memory / f"onboarding/{FILE_CARD}"
    good = card.read_text(encoding="utf-8")
    assert find_legacy_cards(memory) == []
    card.write_text(good + old_format, encoding="utf-8")
    assert find_legacy_cards(memory) == [f"onboarding/{FILE_CARD}"]
    card.write_text(good, encoding="utf-8")
    assert find_legacy_cards(memory) == []


def test_filled_entity_catalog_duplicate_inventory_uses_the_exact_census_reader(
    tmp_path: Path,
) -> None:
    _, memory, _ = _world(tmp_path)
    catalog = memory / "onboarding/entities.md"
    good = catalog.read_text(encoding="utf-8")
    bad = good.replace(f"### {ENTITY}", f"### {ENTITY}\n\n### {ENTITY}", 1)
    catalog.write_text(bad, encoding="utf-8")
    # Alignment's historical reader deduplicates inventory names; the exact census owns this refusal.
    assert entity_catalog_alignment.check_onboarding_root(catalog.parent)["ok"] is True
    with pytest.raises(ValueError, match="duplicate entity inventory key"):
        parse_entity_census_rows(bad)
    assert len(parse_entity_census_rows(good)) == 1


@pytest.mark.parametrize(
    "mutation, expected",
    (
        ("algorithm", "unsupported algorithm 'unsupported-v1'"),
        ("evidence", "cites evidence paths the code candidate does not hold: src/pkg/absent.py"),
    ),
)
def test_filled_catalog_uses_the_supported_algorithm_and_candidate_evidence(
    tmp_path: Path,
    mutation: str,
    expected: str,
) -> None:
    code, memory, _ = _world(tmp_path)
    catalog = memory / "onboarding/entities.md"
    good = catalog.read_text(encoding="utf-8")
    bad = (
        good.replace("`git-blob-set-v1`", "`unsupported-v1`", 1)
        if mutation == "algorithm"
        else good.replace(f"`{SOURCE}` |", "`src/pkg/absent.py` |", 1)
    )
    assert bad != good
    catalog.write_text(bad, encoding="utf-8")
    context = SimpleNamespace(onboarding_root=catalog.parent, code_repository_root=code)
    plan = entity_fingerprint_refresh_plan_for_context(context, [SOURCE], base_evidence={})
    problems = entity_fingerprint_plan_problems(
        context, plan, code_tree=git(code, "rev-parse", "HEAD")
    )
    assert len(problems) == 1 and expected in problems[0]
    assert ENTITY in problems[0] and catalog.as_posix() in problems[0]
    catalog.write_text(good, encoding="utf-8")
    plan = entity_fingerprint_refresh_plan_for_context(context, [SOURCE], base_evidence={})
    assert (
        entity_fingerprint_plan_problems(context, plan, code_tree=git(code, "rev-parse", "HEAD"))
        == []
    )
