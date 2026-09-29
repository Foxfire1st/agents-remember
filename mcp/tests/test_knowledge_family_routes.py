"""MIK-R04: family routes -- the validator's route rules, the reported states, and the mechanical
suggestion, over the converted Doc14 fixture tree (``knowledge_validator_test_support``).

The fixture's family is Doc14 §4.2's "attribution-and-landing-pairing" with its three routes; its
realizations sit in the sidecars of ``direct_landing.py``, ``integrate.py``, ``ledger_projection.py``
(``worktrees``), ``source.py`` and ``authorship.py`` (``models/knowledge``) and
``curator_source_manifest.py`` (``application``). A test changes one thing and checks that exactly the
owning rule answers, naming the family, the route and the uncovered path.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from agents_remember.cli.__main__ import main as cli_main
from agents_remember.memory_quality.knowledge_validator import (
    CodeDirectory,
    CodePathSet,
    ValidationReport,
    validate_tree,
    writer_reported_rule_ids,
)
from agents_remember.memory_quality.knowledge_validator.family_routes import (
    MECHANICAL,
    family_route_state,
    realization_locations,
    suggest_family_routes,
    suggest_routes,
)
from agents_remember.memory_quality.knowledge_validator.parsed import parse_tree
from agents_remember.memory_quality.knowledge_validator.rules_routes import ROUTE_RULES
from agents_remember.models.knowledge_files.records import FamilyRecord
from knowledge_validator_test_support import (
    CODE_PATHS,
    FAMILY,
    LEGACY_COUNT,
    code,
    edit_json,
    encode,
    fixture_tree_files,
    realization_sidecar,
    tree,
    write_tree,
)
from pydantic import ValidationError

AGENTS = "mcp/src/agents_remember"
WORKTREES = f"{AGENTS}/worktrees"
MODELS_KNOWLEDGE = f"{AGENTS}/models/knowledge"
APPLICATION = f"{AGENTS}/application"
MANIFEST = f"{APPLICATION}/curator_source_manifest.py"
KERNEL_FILE = f"{AGENTS}/kernel/route_index.py"
NEW_FAMILY = "knowledge/families/FAM-N3WFAM-new.json"
ADMITTED = {"criteria": ["joint_guarantee"], "justification": "One guarantee spans the members."}


def _validate(files: dict[str, bytes], **kwargs: Any) -> ValidationReport:
    kwargs.setdefault("code", code())
    return validate_tree(tree(files), **kwargs)


def _rules(report: ValidationReport, *, refusing: bool = True) -> set[str]:
    selected = report.refusals if refusing else report.reports
    return {violation.rule for violation in selected}


def _family(files: dict[str, bytes], path: str = FAMILY) -> FamilyRecord:
    return FamilyRecord.model_validate(json.loads(files[path]))


def _with_family(files: dict[str, bytes], **changes: Any) -> dict[str, bytes]:
    return edit_json(files, FAMILY, lambda document: document.update(changes))


def _new_family(files: dict[str, bytes], **changes: Any) -> dict[str, bytes]:
    document = json.loads(files[FAMILY])
    document.update(id="FAM-N3WFAM", title="new", members=["INV-R8M2TD"], **changes)
    return {**files, NEW_FAMILY: encode(document)}


def _add_realization(files: dict[str, bytes], path: str, entry: str) -> dict[str, bytes]:
    sidecar = realization_sidecar(path, entry, "helper", "0" * 40)
    return {
        **files,
        f"onboarding/{path}.json": encode(sidecar),
        f"onboarding/{path}.md": f"# {path}\n".encode(),
    }


# --------------------------------------------------------------------------------------------------
# The Doc14 family example (conforming)
# --------------------------------------------------------------------------------------------------


def test_the_doc14_family_example_validates_with_every_route_holding_realizations() -> None:
    files = fixture_tree_files()

    report = _validate(files)
    state = family_route_state(
        _family(files), realization_locations(parse_tree(tree(files)).sidecars)
    )

    assert report.ok and _rules(report, refusing=False) == {LEGACY_COUNT}, report.render()
    assert state.uncovered == () and state.emptied == ()
    under = {
        route: sorted(
            location.path.rsplit("/", 1)[1]
            for location in state.locations
            if location.path.startswith(f"{route}/")
        )
        for route in state.family.routes
    }
    assert under == {
        WORKTREES: ["direct_landing.py", "integrate.py", "ledger_projection.py"],
        MODELS_KNOWLEDGE: ["authorship.py", "source.py"],
        APPLICATION: ["curator_source_manifest.py"],
    }
    # The proof entry in mcp/tests/test_direct_landing.py lies under no route and is not a
    # realization, so Coverage does not ask for it (rule 2: "Proof entries do not count").
    assert {location.entry for location in state.locations}.isdisjoint({"PRF-T3ST0K"})


# --------------------------------------------------------------------------------------------------
# Rule 2: Coverage and Non-empty
# --------------------------------------------------------------------------------------------------


def test_a_realization_outside_every_route_violates_coverage_naming_family_and_path() -> None:
    files = _add_realization(fixture_tree_files(), KERNEL_FILE, "RLZ-KERN31")

    report = _validate(files, code=code(CODE_PATHS | {KERNEL_FILE}))

    assert _rules(report) == {"R04.2-coverage"}, report.render()
    [line] = [violation.render() for violation in report.refusals]
    assert line.startswith(f"{FAMILY}: routes: [R04.2-coverage] family FAM-SEQNTS6C:")
    assert f"RLZ-KERN31 (INV-7K3F9Q) at {KERNEL_FILE} lies under no route" in line


def test_a_route_that_no_longer_contains_a_realization_violates_non_empty() -> None:
    files = fixture_tree_files()
    del files[f"onboarding/{MANIFEST}.json"]

    report = _validate(files)

    assert _rules(report) == {"R04.2-non-empty"}, report.render()
    [line] = [violation.render() for violation in report.refusals]
    assert line.startswith(f"{FAMILY}: routes.2: [R04.2-non-empty] family FAM-SEQNTS6C:")
    assert f"route {APPLICATION} contains none" in line


def test_a_route_holding_only_a_proof_entry_violates_non_empty() -> None:
    """Rule 2: "Proof entries do not count, because tests live in their own directories"."""

    files = _with_family(
        fixture_tree_files(), routes=[WORKTREES, MODELS_KNOWLEDGE, APPLICATION, "mcp/tests"]
    )

    report = _validate(files)

    assert _rules(report) == {"R04.2-non-empty"}, report.render()
    [line] = [violation.render() for violation in report.refusals]
    assert line.startswith(f"{FAMILY}: routes.3: [R04.2-non-empty]")
    assert "route mcp/tests contains none of its members' realization entries" in line


def test_one_broad_route_satisfies_the_rules_and_the_suggestion_offers_the_local_ones() -> None:
    """Packet non-conforming example: ``mcp/`` over everything is a placement the curator corrects;
    the validator cannot tell depth from breadth, the mechanical suggestion shows the local routes."""

    files = _with_family(fixture_tree_files(), routes=["mcp"])
    parsed = parse_tree(tree(files))

    suggestion = suggest_family_routes(
        _family(files), realization_locations(parsed.sidecars), CODE_PATHS | {KERNEL_FILE}
    )

    assert _validate(files).ok
    assert suggestion.routes == (APPLICATION, MODELS_KNOWLEDGE, WORKTREES)
    assert suggestion.label == MECHANICAL


# --------------------------------------------------------------------------------------------------
# Rule 1: routes are directories of the code tree
# --------------------------------------------------------------------------------------------------


def test_an_added_route_must_be_a_directory_of_the_code_tree() -> None:
    base = fixture_tree_files()
    absent = f"{AGENTS}/nowhere"
    files = _add_realization(base, f"{absent}/x.py", "RLZ-N0WHR3")
    files = _with_family(files, routes=[WORKTREES, MODELS_KNOWLEDGE, APPLICATION, absent])

    report = _validate(files, bases=[tree(base, "K_B")])

    assert _rules(report) == {"R04.1-route-directory", "R22.6-anchor-path"}, report.render()
    [line] = [v.render() for v in report.refusals if v.rule == "R04.1-route-directory"]
    assert line.startswith(f"{FAMILY}: routes.3: [R04.1-route-directory] family FAM-SEQNTS6C:")
    assert f"route {absent} is not a directory of the paired code tree code" in line


def test_a_carried_route_whose_directory_is_gone_is_reported_not_refused() -> None:
    files = fixture_tree_files()
    without_application = frozenset(p for p in CODE_PATHS if not p.startswith(APPLICATION))

    report = _validate(files, bases=[tree(files, "K_B")], code=code(without_application))

    assert report.ok, report.render()
    assert _rules(report, refusing=False) == {
        "R22.6-carried-stale",
        "R04.1-carried-route-absent",
        LEGACY_COUNT,
    }
    [line] = [v.render() for v in report.reports if v.rule == "R04.1-carried-route-absent"]
    assert f"carried route {APPLICATION} is absent from code: route_path_absent" in line
    # With no base the same route is added by the tree, and refused.
    assert "R04.1-route-directory" in _rules(_validate(files, code=code(without_application)))


def test_at_a_merge_a_route_carried_by_either_parent_is_carried() -> None:
    files = fixture_tree_files()
    without_route = _with_family(files, routes=[WORKTREES, MODELS_KNOWLEDGE])
    without_application = frozenset(p for p in CODE_PATHS if not p.startswith(APPLICATION))
    absent_code = code(without_application)

    carried = _validate(
        files, bases=[tree(files, "left"), tree(without_route, "right")], code=absent_code
    )
    added = _validate(
        files, bases=[tree(without_route, "left"), tree(without_route, "right")], code=absent_code
    )

    assert carried.ok, carried.render()
    assert "R04.1-carried-route-absent" in _rules(carried, refusing=False)
    assert "R04.1-route-directory" in _rules(added), added.render()
    assert "R04.1-carried-route-absent" not in _rules(added, refusing=False)


def test_the_refusing_route_rules_are_reported_inside_the_writer() -> None:
    """Rule 6: the registry marks the rules a writer reports; every commit route refuses them."""

    assert writer_reported_rule_ids() >= {
        "R04.1-route-directory",
        "R04.2-coverage",
        "R04.2-non-empty",
    }
    assert {rule.id for rule in ROUTE_RULES if rule.writer_reports} == {
        "R04.1-route-directory",
        "R04.2-coverage",
        "R04.2-non-empty",
    }
    files = fixture_tree_files()
    del files[f"onboarding/{MANIFEST}.json"]
    report = _validate(files)
    assert not report.ok
    assert {violation.rule for violation in report.refusals} <= writer_reported_rule_ids()


def test_a_standalone_conversion_checks_no_route_directory() -> None:
    report = _validate(fixture_tree_files(), code=None, conversion=True)

    assert report.ok and _rules(report, refusing=False) == {LEGACY_COUNT}, report.render()


def test_code_trees_answer_directory_existence(tmp_path: Path) -> None:
    paths = CodePathSet(label="paths", paths=frozenset({"a/b/c.py", "d.py"}))
    (tmp_path / "a" / "b").mkdir(parents=True)

    assert paths.has_directory("a") and paths.has_directory("a/b")
    assert not paths.has_directory("a/b/c.py") and not paths.has_directory("d")
    assert not paths.has_directory("") and not paths.has_directory("a/b/c")
    assert paths.has_directory(".")  # the repository root route
    directory = CodeDirectory(label="dir", root=tmp_path)
    assert directory.has_directory("a/b") and not directory.has_directory("a/x")
    assert directory.has_directory(".")


@pytest.mark.parametrize("route", ["", " .", "./", "./mcp", "..", "/", "mcp/./src", "mcp/"])
def test_a_family_route_is_a_repository_path_or_the_root_route(route: str) -> None:
    document = json.loads(fixture_tree_files()[FAMILY])

    assert FamilyRecord.model_validate({**document, "routes": ["."]}).routes == (".",)
    with pytest.raises(ValidationError):
        FamilyRecord.model_validate({**document, "routes": [route]})


def test_the_root_route_covers_a_realization_at_the_repository_root() -> None:
    files = _add_realization(fixture_tree_files(), "setup.py", "RLZ-R00TF1")
    covered = _with_family(files, routes=[WORKTREES, MODELS_KNOWLEDGE, APPLICATION, "."])

    uncovered = _validate(files, code=code(CODE_PATHS | {"setup.py"}))
    report = _validate(covered, bases=[tree(files, "K_B")], code=code(CODE_PATHS | {"setup.py"}))

    assert _rules(uncovered) == {"R04.2-coverage"}, uncovered.render()
    assert "RLZ-R00TF1 (INV-7K3F9Q) at setup.py lies under no route" in uncovered.render()
    assert report.ok and _rules(report, refusing=False) == {LEGACY_COUNT}, report.render()


def test_the_root_route_alone_is_non_empty_whenever_the_family_is_realized() -> None:
    files = _with_family(fixture_tree_files(), routes=["."])

    assert [violation.rule for violation in _validate(files).violations] == [LEGACY_COUNT]


# --------------------------------------------------------------------------------------------------
# Rule 4: states that are reported, never refused
# --------------------------------------------------------------------------------------------------


def test_an_unrealized_family_is_reported_and_non_empty_is_waived() -> None:
    files = _new_family(fixture_tree_files(), routes=[WORKTREES], admission=ADMITTED)

    report = _validate(files)

    assert report.ok, report.render()
    assert _rules(report, refusing=False) == {"R04.4-unrealized-family", LEGACY_COUNT}
    (unrealized,) = (one for one in report.reports if one.rule == "R04.4-unrealized-family")
    assert unrealized.path == NEW_FAMILY


def test_an_unrealized_family_still_needs_a_route_unless_it_is_an_unassessed_export() -> None:
    files = _new_family(fixture_tree_files(), routes=[], admission=ADMITTED)

    report = _validate(files)

    assert _rules(report) == {"R04.2-coverage"}, report.render()
    assert "lists no route and is not legacy-unassessed" in report.refusals[0].message


def test_an_exported_family_without_routes_is_route_unassigned_and_coverage_is_waived() -> None:
    files = _with_family(fixture_tree_files(), routes=[])

    report = _validate(files)

    assert report.ok, report.render()
    assert _rules(report, refusing=False) == {"R04.4-route-unassigned", LEGACY_COUNT}
    (unassigned,) = (one for one in report.reports if one.rule == "R04.4-route-unassigned")
    assert "FAM-SEQNTS6C is route_unassigned" in unassigned.message


def test_a_family_without_routes_that_is_not_legacy_unassessed_violates_coverage() -> None:
    files = _with_family(fixture_tree_files(), routes=[], admission=ADMITTED)

    report = _validate(files)

    assert _rules(report) == {"R04.2-coverage"}, report.render()
    message = report.refusals[0].message
    assert "lists no route and is not legacy-unassessed" in message
    assert f"realization paths: {MANIFEST}, " in message and "direct_landing.py" in message


def test_a_retired_family_is_exempt_from_every_route_rule() -> None:
    absent = f"{AGENTS}/nowhere"
    files = _add_realization(fixture_tree_files(), KERNEL_FILE, "RLZ-KERN31")
    files = _with_family(files, status="retired", routes=[absent, APPLICATION])

    report = _validate(files, code=code(CODE_PATHS | {KERNEL_FILE}))

    assert report.ok and report.violations == (), report.render()


# --------------------------------------------------------------------------------------------------
# Rule 3: the mechanical suggestion
# --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("realized", "code_files", "expected"),
    [
        pytest.param(
            ["p/a/b/x.py", "p/a/c/y.py"],
            ["p/a/b/x.py", "p/a/c/y.py", "p/z.py"],
            ("p/a",),
            id="siblings-collapse-to-a-parent-of-only-family-code",
        ),
        pytest.param(
            ["p/a/b/x.py", "p/a/c/y.py"],
            ["p/a/b/x.py", "p/a/c/y.py", "p/a/other.py"],
            ("p/a/b", "p/a/c"),
            id="no-collapse-when-the-parent-holds-other-code",
        ),
        pytest.param(
            ["p/a/b/x.py", "p/a/b/d/z.py"],
            ["p/a/b/x.py", "p/a/b/d/z.py", "p/other.py"],
            ("p/a/b",),
            id="a-directory-under-another-is-covered-by-it",
        ),
        pytest.param(
            ["p/a/b/x.py", "p/a/c/y.py", "p/d/w.py"],
            ["p/a/b/x.py", "p/a/c/y.py", "p/d/w.py"],
            ("p",),
            id="collapse-repeats-until-nothing-collapses",
        ),
        pytest.param(
            ["a/x.py", "b/y.py"],
            ["a/x.py", "b/y.py"],
            ("a", "b"),
            id="never-the-repository-root",
        ),
    ],
)
def test_the_mechanical_suggestion(
    realized: list[str], code_files: list[str], expected: tuple[str, ...]
) -> None:
    suggestion = suggest_routes("FAM-N3WFAM", realized, code_files)

    assert suggestion.routes == expected
    assert suggestion.label == MECHANICAL


def test_the_root_route_is_suggested_only_for_a_realization_at_the_repository_root() -> None:
    suggestion = suggest_routes("FAM-N3WFAM", ["setup.py", "a/x.py"], ["setup.py", "a/x.py"])

    assert suggestion.routes == (".", "a")
    assert suggestion.at_repository_root == ("setup.py",)
    assert suggest_routes("FAM-N3WFAM", [], ["a/x.py"]).routes == ()


def test_the_routes_command_offers_the_suggestion_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    memory = tmp_path / "memory"
    files = _with_family(fixture_tree_files(), routes=[])
    write_tree(memory, files)
    code_root = tmp_path / "code"
    write_tree(code_root, {path: b"# code\n" for path in CODE_PATHS | {KERNEL_FILE}})
    subprocess.run(["git", "init", "-q"], cwd=code_root, check=True)

    status = cli_main(["knowledge-routes", str(memory), "--code", str(code_root), "--json"])
    [document] = json.loads(capsys.readouterr().out)

    assert status == 0
    assert document["state"]["routeUnassigned"] is True
    assert document["suggestion"] == {
        "family": "FAM-SEQNTS6C",
        "label": "mechanical",
        "routes": [APPLICATION, MODELS_KNOWLEDGE, WORKTREES],
        "atRepositoryRoot": [],
    }
    assert all((memory / path).read_bytes() == data for path, data in files.items())
    assert (
        cli_main(
            ["knowledge-routes", str(memory), "--code", str(code_root), "--family", "FAM-UNKN0W"]
        )
        == 0
    )
    assert "unknown family: FAM-UNKN0W" in capsys.readouterr().out
    assert cli_main(["knowledge-routes", str(memory), "--code", str(tmp_path / "none")]) == 2
    assert "suggestion (mechanical)" not in capsys.readouterr().out
