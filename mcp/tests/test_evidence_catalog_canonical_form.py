"""The two test catalogs keep one canonical form, one command writes it, and Git merges them.

Every case builds a small synthetic repository (a Git repository, because the dependency oracle
reads tracked files) with both catalogs in it, then drives the real loaders, the real command
(``evidence_lifecycle --write``) or real Git.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import cast

import conftest
import pytest
from _evidence_catalog_fixture import write_synthetic_evidence_catalog
from agents_remember_test_support.code_quality.dependency_ownership import DependencyOwnershipGraph
from agents_remember_test_support.testing.dependency_facts import RepositoryDependencyFacts
from agents_remember_test_support.testing.evidence_lifecycle import (
    EvidenceLifecycleError,
    load_evidence_inventory,
    main,
)
from agents_remember_test_support.testing.lane_manifest import LaneManifestError, load_lane_manifest

REPOSITORY_ROOT = Path(__file__).parents[2]
CATALOG = "mcp/tests/evidence-lifecycle.toml"
LANES = "mcp/tests/test-evidence-lanes.toml"
ANCHOR = "mcp/tests/_catalog_anchor.py"
LANE_NAMES = (
    "unit-regression",
    "public-contract",
    "integration",
    "provider-conformance",
    "stress-durability",
    "architecture-fitness",
    "migration",
)
COMMAND = "evidence_lifecycle --project-root . --write"
SYNTHETIC_CONTRACT = "synthetic-test-evidence"
ROW = """
[[artifact]]
path = "{path}"
kind = "shared-support"
authority = "internal-canonical"
owner = "synthetic-test-evidence"
category = "unit-regression"
fidelity = "in-process"
cadence = "affected"
source_version_or_generator = "repository test builder"
introduced_by = "synthetic-test"
lifetime = "permanent"
permanence_rationale = "Required input to the synthetic contract."
replacement_contract = "contract:synthetic-test-evidence"
consumer_scope = "exact"
consumers = [
  "{consumer}",
]
"""


def module_path(name: str) -> str:
    return f"mcp/tests/test_{name}.py"


def git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    command = ["git", "-c", "user.name=t", "-c", "user.email=t@example.test", *args]
    return subprocess.run(
        command, cwd=root, text=True, capture_output=True, check=False, timeout=30
    )


def add_test_module(root: Path, name: str, *artifacts: str) -> None:
    """Write a test module that reads each of ``artifacts``, the way the fixture builder does."""

    source = "def test_plain():\n    assert True\n"
    if artifacts:
        source += f"_AR_EVIDENCE_INPUTS = {tuple(sorted(artifacts))!r}\n"
    (root / module_path(name)).write_text(source, encoding="utf-8")


def write_lanes(root: Path, members: dict[str, list[str]]) -> None:
    lines = ['schema_version = "ar-test-evidence-lanes/v1"', "", "[files]"]
    for lane in LANE_NAMES:
        lines.append(f"{lane} = [")
        lines.extend(f'  "{path}",' for path in members.get(lane, []))
        lines.append("]")
    (root / LANES).write_text("\n".join(lines) + "\n", encoding="utf-8")


def insert_line(root: Path, relative: str, key: str, value: str, *, anchor: str = "]") -> None:
    """Add one list line before the closing bracket of ``key``, the edit a leaf makes by hand."""

    path = root / relative
    lines = path.read_text(encoding="utf-8").split("\n")
    start = next(i for i, line in enumerate(lines) if line.startswith(f"{key} = ["))
    close = next(i for i in range(start, len(lines)) if lines[i] == anchor)
    lines.insert(close, f'  "{value}",')
    path.write_text("\n".join(lines), encoding="utf-8")


def synthetic_repo(root: Path, idle: tuple[str, ...] = ()) -> Path:
    """Build a canonical, valid, committed repository whose artifact is read by alpha and beta.

    ``idle`` names further test modules that are in the lane but read no governed artifact yet.
    """

    names = ("alpha", "beta")
    (root / "mcp/tests").mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["mcp/tests"]\n', encoding="utf-8"
    )
    (root / ANCHOR).write_text("VALUE = 1\n", encoding="utf-8")
    for name in (*names, *idle):
        add_test_module(root, name)
    write_lanes(root, {"unit-regression": [module_path(name) for name in (*names, *idle)]})
    # The lane manifest is itself governed evidence, so the catalog describes it too.
    write_synthetic_evidence_catalog(
        root,
        {ANCHOR: [module_path(name) for name in names], LANES: [module_path(names[0])]},
    )
    attributes = [
        line
        for line in (REPOSITORY_ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
        if "merge=union" in line
    ]
    (root / ".gitattributes").write_text("\n".join(attributes) + "\n", encoding="utf-8")
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    assert main(["--project-root", str(root)]) == 0
    return root


def write_command(root: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    capsys.readouterr()
    code = main(["--project-root", str(root), "--write"])
    return code, capsys.readouterr().out


def validator(root: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    capsys.readouterr()
    code = main(["--project-root", str(root)])
    return code, capsys.readouterr().out


def test_the_repository_catalogs_are_in_canonical_form() -> None:
    """Both real catalogs load, which includes the canonical-form refusal of each loader."""

    assert load_evidence_inventory(REPOSITORY_ROOT)
    assert load_lane_manifest(REPOSITORY_ROOT)


def test_both_catalogs_merge_by_union() -> None:
    result = git(REPOSITORY_ROOT, "check-attr", "merge", "--", CATALOG, LANES)
    assert sorted(result.stdout.splitlines()) == sorted(
        [f"{CATALOG}: merge: union", f"{LANES}: merge: union"]
    )


def test_the_validator_also_refuses_a_lane_manifest_that_is_not_canonical(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = synthetic_repo(tmp_path / "repo")
    write_lanes(root, {"unit-regression": [module_path("beta"), module_path("alpha")]})
    code, printed = validator(root, capsys)
    assert code == 1 and "unit-regression: lane list is out of order" in printed
    assert write_command(root, capsys)[0] == 0
    assert validator(root, capsys)[0] == 0


def test_a_consumer_list_out_of_order_is_refused_then_repaired(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = synthetic_repo(tmp_path / "repo")
    path = root / CATALOG
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            f'  "{module_path("alpha")}",\n  "{module_path("beta")}",\n',
            f'  "{module_path("beta")}",\n  "{module_path("alpha")}",\n',
        ),
        encoding="utf-8",
    )
    with pytest.raises(EvidenceLifecycleError) as caught:
        load_evidence_inventory(root)
    assert f"{ANCHOR}: consumers is out of order" in str(caught.value)
    assert COMMAND in str(caught.value)
    code, printed = write_command(root, capsys)
    assert code == 0
    assert f"{ANCHOR}: reordered" in printed
    assert validator(root, capsys)[0] == 0


def test_a_duplicate_consumer_is_refused_and_removed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = synthetic_repo(tmp_path / "repo")
    insert_line(root, CATALOG, "consumers", module_path("beta"))
    with pytest.raises(EvidenceLifecycleError, match="consumers holds duplicates"):
        load_evidence_inventory(root)
    code, printed = write_command(root, capsys)
    assert code == 0
    assert "removed 1 duplicate line(s)" in printed
    assert validator(root, capsys)[0] == 0


def test_the_command_derives_a_missing_and_drops_an_unsupported_consumer(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = synthetic_repo(tmp_path / "repo")
    add_test_module(root, "gamma", ANCHOR)
    insert_line(root, LANES, "unit-regression", module_path("gamma"))
    git(root, "add", "-A")
    path = root / CATALOG
    text = path.read_text(encoding="utf-8").replace(f'  "{module_path("beta")}",\n', "")
    text = text.replace("\n]\n", f'\n  "{module_path("ghost")}",\n]\n')
    path.write_text(text, encoding="utf-8")
    code, printed = write_command(root, capsys)
    assert code == 0
    assert module_path("gamma") in printed and module_path("ghost") in printed
    listed = path.read_text(encoding="utf-8")
    assert f'"{module_path("gamma")}"' in listed and f'"{module_path("ghost")}"' not in listed
    assert validator(root, capsys)[0] == 0


def test_the_second_run_changes_nothing_and_comments_survive(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = synthetic_repo(tmp_path / "repo")
    lanes, catalog = root / LANES, root / CATALOG
    lanes.write_text(
        lanes.read_text(encoding="utf-8").replace("[files]", "# lanes are authored\n[files]"),
        encoding="utf-8",
    )
    catalog.write_text(
        catalog.read_text(encoding="utf-8").replace("[[artifact]]", "# why this row\n[[artifact]]"),
        encoding="utf-8",
    )
    insert_line(root, LANES, "unit-regression", module_path("alpha"))
    code, printed = write_command(root, capsys)
    assert code == 0 and "unit-regression: removed 1 duplicate line(s)" in printed
    once = catalog.read_bytes(), lanes.read_bytes()
    code, printed = write_command(root, capsys)
    assert code == 0 and "already canonical" in printed
    assert (catalog.read_bytes(), lanes.read_bytes()) == once
    assert "# lanes are authored\n[files]" in lanes.read_text(encoding="utf-8")
    assert "# why this row\n[[artifact]]" in catalog.read_text(encoding="utf-8")


def test_rows_are_ordered_by_key_and_none_is_added_or_removed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = synthetic_repo(tmp_path / "repo")
    add_test_module(root, "gamma", "mcp/tests/_b_anchor.py")
    (root / "mcp/tests/_b_anchor.py").write_text("VALUE = 2\n", encoding="utf-8")
    path = root / CATALOG
    path.write_text(
        path.read_text(encoding="utf-8")
        + ROW.format(path="mcp/tests/_b_anchor.py", consumer=module_path("gamma")),
        encoding="utf-8",
    )
    add_test_module(root, "beta", ANCHOR, "mcp/tests/_b_anchor.py")
    insert_line(root, LANES, "unit-regression", module_path("gamma"))
    git(root, "add", "-A")
    with pytest.raises(EvidenceLifecycleError, match="artifact row is out of order"):
        load_evidence_inventory(root)
    code, printed = write_command(root, capsys)
    assert code == 0 and "artifact rows reordered" in printed
    paths = [line for line in path.read_text(encoding="utf-8").splitlines() if "path = " in line]
    assert paths == sorted(paths) and len(paths) == 3
    assert validator(root, capsys)[0] == 0


def test_the_command_refuses_an_unparseable_catalog_without_writing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = synthetic_repo(tmp_path / "repo")
    path = root / CATALOG
    path.write_text(
        path.read_text(encoding="utf-8") + 'consumers = [ path = "x"\n', encoding="utf-8"
    )
    before = path.read_bytes(), (root / LANES).read_bytes()
    code, printed = write_command(root, capsys)
    assert code == 1 and "nothing written" in printed
    assert "interleaved by the merge" in printed
    assert (path.read_bytes(), (root / LANES).read_bytes()) == before


def test_the_command_refuses_incomplete_dependency_facts_without_writing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = synthetic_repo(tmp_path / "repo")
    (root / module_path("broken")).write_text("def broken(:\n", encoding="utf-8")
    git(root, "add", "-A")
    insert_line(root, LANES, "unit-regression", module_path("alpha"))
    before = (root / CATALOG).read_bytes(), (root / LANES).read_bytes()
    code, printed = write_command(root, capsys)
    assert code == 1 and "nothing written" in printed and "incomplete" in printed
    assert ((root / CATALOG).read_bytes(), (root / LANES).read_bytes()) == before


def test_the_lane_loader_names_an_unordered_lane_and_a_duplicated_path(tmp_path: Path) -> None:
    root = synthetic_repo(tmp_path / "repo")
    write_lanes(root, {"unit-regression": [module_path("beta"), module_path("alpha")]})
    with pytest.raises(LaneManifestError) as caught:
        load_lane_manifest(root)
    assert "unit-regression: lane list is out of order" in str(caught.value)
    assert COMMAND in str(caught.value)
    write_lanes(
        root, {"unit-regression": [module_path("alpha"), module_path("alpha"), module_path("beta")]}
    )
    with pytest.raises(LaneManifestError) as caught:
        load_lane_manifest(root)
    assert "unit-regression: lane list holds duplicates" in str(caught.value)
    assert "conflicting file lanes" not in str(caught.value)


@pytest.mark.parametrize("catalog", [CATALOG, LANES])
def test_both_loaders_explain_an_interleaved_file(tmp_path: Path, catalog: str) -> None:
    root = synthetic_repo(tmp_path / "repo")
    path = root / catalog
    path.write_text(
        path.read_text(encoding="utf-8") + 'consumers = [ path = "x"\n', encoding="utf-8"
    )
    loader = load_evidence_inventory if catalog == CATALOG else load_lane_manifest
    with pytest.raises(
        (EvidenceLifecycleError, LaneManifestError), match="interleaved by the merge"
    ):
        loader(root)


def start_sync(root: Path, tmp_path: Path) -> Path:
    """Return a second worktree of the base that a leaf edits, as the sync's parked work."""

    leaf = tmp_path / "leaf"
    git(root, "worktree", "add", "-q", "-b", "leaf", str(leaf), "HEAD")
    return leaf


def land(root: Path, mutate: Callable[[Path], None]) -> None:
    """Commit one landed change on ``main``; ``mutate`` receives the repository root."""

    mutate(root)
    git(root, "add", "-A")
    assert git(root, "commit", "-q", "-m", "landed").returncode == 0


def reapply(leaf: Path, *, true_merge: bool = False) -> subprocess.CompletedProcess[str]:
    """What worktree_sync does: park the work, bring in the landed commit, apply the park.

    The landed commit arrives by fast-forward when the leaf has no commit of its own, and by a
    merge commit when it has (``true_merge`` asserts that).
    """

    assert git(leaf, "stash", "push", "-q", "--include-untracked").returncode == 0
    assert git(leaf, "merge", "-q", "--no-edit", "main").returncode == 0
    parents = git(leaf, "rev-list", "--parents", "-n", "1", "HEAD").stdout.split()
    assert (len(parents) == 3) is true_merge
    return git(leaf, "stash", "apply", "--quiet")


def unmerged(leaf: Path) -> list[str]:
    return git(leaf, "diff", "--name-only", "--diff-filter=U").stdout.split()


def add_member(root: Path, name: str) -> None:
    add_test_module(root, name, ANCHOR)
    insert_line(root, CATALOG, "consumers", module_path(name))
    insert_line(root, LANES, "unit-regression", module_path(name))


def test_two_changes_that_add_a_test_file_at_the_same_place_sync_without_a_stop(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = synthetic_repo(tmp_path / "repo")
    leaf = start_sync(root, tmp_path)
    add_member(leaf, "gamma")
    land(root, lambda repository: add_member(repository, "zeta"))
    result = reapply(leaf)
    assert result.returncode == 0, result.stderr
    assert unmerged(leaf) == []
    for name in ("gamma", "zeta"):
        assert module_path(name) in (leaf / CATALOG).read_text(encoding="utf-8")
        assert module_path(name) in (leaf / LANES).read_text(encoding="utf-8")
    code, _ = write_command(leaf, capsys)
    assert code == 0 and validator(leaf, capsys)[0] == 0


def test_the_same_line_on_both_sides_is_refused_with_the_command_then_removed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two changes both make the existing test module ``delta`` read the anchor, and each adds a file.

    The lines around the shared one differ, so Git cannot see one shared hunk and keeps both copies.
    """

    root = synthetic_repo(tmp_path / "repo", idle=("delta",))
    delta = module_path("delta")
    reads = f"_AR_EVIDENCE_INPUTS = ({ANCHOR!r},)\n"

    def make_delta_read_the_anchor(repository: Path, *, first: bool) -> None:
        source = (repository / delta).read_text(encoding="utf-8")
        (repository / delta).write_text(
            reads + source if first else source + reads, encoding="utf-8"
        )
        if first:  # the leaf appended its own file, then the shared one
            add_member(repository, "gamma")
        insert_line(repository, CATALOG, "consumers", delta)
        if not first:  # the landed change was written in canonical order
            add_member(repository, "zeta")

    leaf = start_sync(root, tmp_path)
    make_delta_read_the_anchor(leaf, first=True)
    land(root, lambda repository: make_delta_read_the_anchor(repository, first=False))
    result = reapply(leaf)
    assert result.returncode == 0 and unmerged(leaf) == []
    assert (leaf / CATALOG).read_text(encoding="utf-8").count(f'"{delta}"') == 2
    code, printed = validator(leaf, capsys)
    assert code == 1 and "holds duplicates" in printed and COMMAND in printed
    assert write_command(leaf, capsys)[0] == 0
    assert validator(leaf, capsys)[0] == 0


def new_anchor_row(root: Path, anchor: str, name: str) -> None:
    (root / anchor).write_text("VALUE = 3\n", encoding="utf-8")
    add_test_module(root, name, anchor)
    path = root / CATALOG
    path.write_text(
        path.read_text(encoding="utf-8") + ROW.format(path=anchor, consumer=module_path(name)),
        encoding="utf-8",
    )
    insert_line(root, LANES, "unit-regression", module_path(name))


def test_two_whole_rows_at_the_same_place_end_in_the_refusal_that_says_what_to_do(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = synthetic_repo(tmp_path / "repo")
    leaf = start_sync(root, tmp_path)
    new_anchor_row(leaf, "mcp/tests/_leaf_anchor.py", "gamma")
    land(root, lambda repository: new_anchor_row(repository, "mcp/tests/_landed_anchor.py", "zeta"))
    reapply(leaf)
    with pytest.raises(EvidenceLifecycleError) as caught:
        load_evidence_inventory(leaf)
    assert "interleaved by the merge" in str(caught.value)
    assert "restore the file from the landed commit and add this leaf's row again" in str(
        caught.value
    )
    # The documented recovery: restore the landed file, add this leaf's row again, run the command.
    assert git(leaf, "checkout", "HEAD", "--", CATALOG).returncode == 0
    path = leaf / CATALOG
    path.write_text(
        path.read_text(encoding="utf-8")
        + ROW.format(path="mcp/tests/_leaf_anchor.py", consumer=module_path("gamma")),
        encoding="utf-8",
    )
    assert write_command(leaf, capsys)[0] == 0
    assert validator(leaf, capsys)[0] == 0


def test_two_branches_that_both_changed_the_lists_merge_with_a_merge_commit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = synthetic_repo(tmp_path / "repo")
    leaf = start_sync(root, tmp_path)
    add_member(leaf, "gamma")
    git(leaf, "add", "-A")
    assert git(leaf, "commit", "-q", "-m", "leaf").returncode == 0
    land(root, lambda repository: add_member(repository, "zeta"))
    merged = git(leaf, "merge", "--no-edit", "main")
    assert merged.returncode == 0, merged.stdout
    assert len(git(leaf, "rev-list", "--parents", "-n", "1", "HEAD").stdout.split()) == 3
    assert unmerged(leaf) == []
    for name in ("gamma", "zeta"):
        assert module_path(name) in (leaf / CATALOG).read_text(encoding="utf-8")
        assert module_path(name) in (leaf / LANES).read_text(encoding="utf-8")
    assert write_command(leaf, capsys)[0] == 0 and validator(leaf, capsys)[0] == 0


def test_parked_work_is_reapplied_over_a_merge_commit_without_a_stop(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The leaf has a commit and uncommitted work; the sync parks, merges, and applies the park."""

    root = synthetic_repo(tmp_path / "repo")
    leaf = start_sync(root, tmp_path)
    add_member(leaf, "gamma")
    git(leaf, "add", "-A")
    assert git(leaf, "commit", "-q", "-m", "leaf").returncode == 0
    add_member(leaf, "theta")
    land(root, lambda repository: add_member(repository, "zeta"))
    result = reapply(leaf, true_merge=True)
    assert result.returncode == 0, result.stderr
    assert unmerged(leaf) == []
    for name in ("gamma", "theta", "zeta"):
        assert module_path(name) in (leaf / CATALOG).read_text(encoding="utf-8")
    assert write_command(leaf, capsys)[0] == 0 and validator(leaf, capsys)[0] == 0


CONTRACT = """
[[contract]]
id = "aaa-contract"
owner = "mcp/tests/_catalog_anchor.py"
evidence_node = "{node}"
"""


def test_contract_rows_are_ordered_by_id(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = synthetic_repo(tmp_path / "repo")
    path = root / CATALOG
    text = path.read_text(encoding="utf-8")
    node = f"{module_path('alpha')}::test_plain"
    text = text.replace("\n[[artifact]]", CONTRACT.format(node=node) + "\n[[artifact]]", 1)
    head, _, last = text.rpartition("synthetic-test-evidence")
    path.write_text(head + "aaa-contract" + last, encoding="utf-8")
    with pytest.raises(EvidenceLifecycleError) as caught:
        load_evidence_inventory(root)
    assert "aaa-contract: contract row is out of order" in str(caught.value)
    assert COMMAND in str(caught.value)
    code, printed = write_command(root, capsys)
    assert code == 0 and "contract rows reordered" in printed
    ids = [
        line for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("id =")
    ]
    assert ids == ['id = "aaa-contract"', 'id = "synthetic-test-evidence"']
    assert validator(root, capsys)[0] == 0


def test_the_command_derives_an_exact_source_consumer_list(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = synthetic_repo(tmp_path / "repo")
    (root / "mcp/tests/_src_anchor.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "tools").mkdir()
    (root / "tools/user.py").write_text('PATH = "mcp/tests/_src_anchor.py"\n', encoding="utf-8")
    (root / "tools/other.py").write_text("VALUE = 2\n", encoding="utf-8")
    row = ROW.format(path="mcp/tests/_src_anchor.py", consumer="tools/other.py")
    path = root / CATALOG
    path.write_text(
        path.read_text(encoding="utf-8") + row.replace('"exact"', '"exact-source"'),
        encoding="utf-8",
    )
    git(root, "add", "-A")
    code, printed = write_command(root, capsys)
    assert code == 0 and "added ['tools/user.py'], removed ['tools/other.py']" in printed
    assert validator(root, capsys)[0] == 0


def test_the_command_refuses_a_directory_that_is_not_a_git_repository(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = synthetic_repo(tmp_path / "repo")
    shutil.rmtree(root / ".git")
    before = (root / CATALOG).read_bytes(), (root / LANES).read_bytes()
    code, printed = write_command(root, capsys)
    assert code == 1 and "nothing written" in printed
    assert ((root / CATALOG).read_bytes(), (root / LANES).read_bytes()) == before


@pytest.mark.parametrize(
    ("kind", "finding"),
    [("artifact", "duplicate catalog entry"), ("contract", "duplicate contract identity")],
)
def test_the_command_refuses_a_row_listed_twice_and_names_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], kind: str, finding: str
) -> None:
    root = synthetic_repo(tmp_path / "repo")
    path = root / CATALOG
    contract = CONTRACT.format(node=f"{module_path('alpha')}::test_plain")
    twice, name = {
        "artifact": (ROW.format(path=ANCHOR, consumer=module_path("alpha")), ANCHOR),
        "contract": (contract.replace("aaa-contract", SYNTHETIC_CONTRACT), SYNTHETIC_CONTRACT),
    }[kind]
    path.write_text(path.read_text(encoding="utf-8") + twice, encoding="utf-8")
    with pytest.raises(EvidenceLifecycleError, match=finding):
        load_evidence_inventory(root)
    before = path.read_bytes()
    code, printed = write_command(root, capsys)
    assert code == 1 and f"the {kind} row {name!r} is listed twice" in printed
    assert "restore the file from the landed commit and add that side's own lines again" in printed
    assert path.read_bytes() == before


def test_a_whitespace_only_change_is_reported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = synthetic_repo(tmp_path / "repo")
    path = root / LANES
    path.write_text(path.read_text(encoding="utf-8").replace('",\n', '",  \n'), encoding="utf-8")
    code, printed = write_command(root, capsys)
    assert code == 0 and "whitespace normalised" in printed
    assert "already canonical" not in printed
    assert "  \n" not in path.read_text(encoding="utf-8")


def test_a_catalog_with_crlf_line_endings_is_refused_and_left_alone(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = synthetic_repo(tmp_path / "repo")
    path = root / LANES
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    insert_line(root, CATALOG, "consumers", module_path("beta"))
    before = path.read_bytes(), (root / CATALOG).read_bytes()
    code, printed = write_command(root, capsys)
    assert code == 1 and "CRLF" in printed and "nothing written" in printed
    assert (path.read_bytes(), (root / CATALOG).read_bytes()) == before


def test_a_table_header_followed_by_a_comment_is_read_and_keeps_its_comment(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A row under ``[[artifact]] # note`` is ordered and derived like every other row."""

    root = synthetic_repo(tmp_path / "repo")
    path = root / CATALOG
    alpha, beta = module_path("alpha"), module_path("beta")
    text = path.read_text(encoding="utf-8").replace("[[artifact]]", "[[artifact]] # why this row")
    text = text.replace(f'  "{alpha}",\n  "{beta}",\n', f'  "{beta}",\n  "{alpha}",\n')
    head, first, second = text.split("\n[[artifact]] # why this row\n")
    header = "\n[[artifact]] # why this row\n"
    path.write_text(f"{head}{header}{second}{header}{first}", encoding="utf-8")
    code, printed = write_command(root, capsys)
    assert code == 0 and f"{ANCHOR}: reordered" in printed and "artifact rows reordered" in printed
    assert path.read_text(encoding="utf-8").count("[[artifact]] # why this row") == 2
    assert validator(root, capsys)[0] == 0


@pytest.mark.parametrize(
    ("catalog", "rewrite", "named"),
    [
        (CATALOG, ("[[artifact]]", "  [[artifact]]"), "a [[artifact]] table header"),
        (CATALOG, ("consumers = [", "consumers=["), "the list consumers is written"),
        (LANES, ("unit-regression = [", "unit-regression=["), "list unit-regression is written"),
        (LANES, ('",\n]\npublic', '"]\npublic'), "list unit-regression is not closed"),
        (LANES, ("[files]", "  [files]"), "a [files] table header"),
    ],
    ids=["indented-header", "consumers-key", "lane-key", "bracket-not-on-its-own-line", "files"],
)
def test_a_form_the_command_does_not_read_is_refused_and_nothing_is_written(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    catalog: str,
    rewrite: tuple[str, str],
    named: str,
) -> None:
    """Valid TOML that the line rewrite would skip or damage is refused; nothing is written."""

    root = synthetic_repo(tmp_path / "repo")
    path = root / catalog
    path.write_text(path.read_text(encoding="utf-8").replace(*rewrite, 1), encoding="utf-8")
    code, printed = validator(root, capsys)
    # For a header the loaders give the command's own message; for a list they name its form.
    assert code == 1 and (named if "table header" in named else "one path per line") in printed
    before = (root / CATALOG).read_bytes(), (root / LANES).read_bytes()
    code, printed = write_command(root, capsys)
    assert code == 1 and "nothing written" in printed and named in printed
    assert ((root / CATALOG).read_bytes(), (root / LANES).read_bytes()) == before


def test_the_validator_derives_the_source_graph_once_for_both_catalogs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both loaders check against one dependency graph, and the lane loader still checks with it."""

    root = synthetic_repo(tmp_path / "repo")
    build = RepositoryDependencyFacts.build
    built: list[Path] = []

    def counting_build(project_root: Path) -> RepositoryDependencyFacts:
        built.append(project_root)
        return build(project_root)

    monkeypatch.setattr(RepositoryDependencyFacts, "build", counting_build)
    assert validator(root, capsys)[0] == 0
    assert len(built) == 1
    add_test_module(root, "gamma")  # a test file that no lane lists
    code, printed = validator(root, capsys)
    assert code == 1 and "test files without an explicit lane" in printed
    assert len(built) == 2


def row_fields(text: str) -> dict[str, list[str]]:
    """Each row's lines other than its ``consumers`` list, keyed by the row's ``id`` or ``path`` line.

    A comment directly above a table header belongs to the row below it; the lines before the first
    row are kept under the empty key.
    """

    blocks: list[list[str]] = [[]]
    for line in text.splitlines():
        if line.startswith("[["):
            above: list[str] = []
            while blocks[-1] and blocks[-1][-1].startswith("#"):
                above.insert(0, blocks[-1].pop())
            blocks.append(above)
        blocks[-1].append(line)
    fields: dict[str, list[str]] = {}
    for block in blocks:
        kept: list[str] = []
        inside = False
        for line in block:
            inside = inside or line.startswith("consumers = [")
            if not inside and line.strip():
                kept.append(line)
            inside = inside and line != "]"
        key = next((line for line in kept if line.startswith(("id = ", "path = "))), "")
        fields[key] = kept
    return fields


def test_the_command_changes_no_field_of_a_row_but_its_consumers(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Rows move and consumer lists are rewritten; every other line of every row keeps its bytes."""

    root = synthetic_repo(tmp_path / "repo")
    path = root / CATALOG
    alpha, beta = module_path("alpha"), module_path("beta")
    text = path.read_text(encoding="utf-8")
    # Descriptive fields written by hand, in forms a rewrite from parsed data would not reproduce.
    text = text.replace(
        'owner = "synthetic-test-evidence"\ncategory',
        "# a note between two fields\nowner = 'synthetic-test-evidence'   \ncategory",
        1,
    )
    text = text.replace(
        'introduced_by = "synthetic-test"',
        'introduced_by   =   "synthetic \\"test\\""  # as typed',
        1,
    )
    # A list out of order, with a duplicate and a consumer the source tree does not support.
    text = text.replace(
        f'  "{alpha}",\n  "{beta}",\n',
        f'  "{beta}",\n  "{alpha}",\n  "{beta}",\n  "tools/ghost.py",\n',
    )
    # Both kinds of row out of order: a second contract after the first, the artifact rows swapped.
    contract = CONTRACT.format(node=f"{alpha}::test_plain")
    head, first, second = text.replace("\n[[artifact]]", contract + "\n[[artifact]]", 1).split(
        "\n[[artifact]]\n"
    )
    scrambled = f"{head}\n# why the lanes row\n[[artifact]]\n{second}\n[[artifact]]\n{first}"
    head, _, last = scrambled.rpartition(f'"contract:{SYNTHETIC_CONTRACT}"')
    path.write_text(f'{head}"contract:aaa-contract"{last}', encoding="utf-8")
    before = path.read_text(encoding="utf-8")
    code, printed = write_command(root, capsys)
    assert code == 0
    for change in (
        "contract rows reordered",
        "artifact rows reordered",
        "removed ['tools/ghost.py']",
    ):
        assert change in printed
    after = path.read_text(encoding="utf-8")
    # The lines above the first row, two contracts and two artifacts.
    assert len(row_fields(before)) == 5
    assert row_fields(after) == row_fields(before)
    assert validator(root, capsys)[0] == 0


def retire_member(root: Path, name: str) -> None:
    """Delete a test file with its lane line and its consumer line."""

    (root / module_path(name)).unlink()
    for catalog in (CATALOG, LANES):
        path = root / catalog
        listed = path.read_text(encoding="utf-8").replace(f'  "{module_path(name)}",\n', "")
        path.write_text(listed, encoding="utf-8")


def test_a_line_the_union_brings_back_for_a_deleted_file_is_refused_then_removed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The landed change deleted a test file and its lines; the leaf added lines next to them."""

    root = synthetic_repo(tmp_path / "repo")
    leaf = start_sync(root, tmp_path)
    add_member(leaf, "gamma")
    land(root, lambda repository: retire_member(repository, "beta"))
    beta = module_path("beta")
    assert reapply(leaf).returncode == 0 and unmerged(leaf) == [] and not (leaf / beta).exists()
    for catalog in (CATALOG, LANES):
        assert f'"{beta}"' in (leaf / catalog).read_text(encoding="utf-8")
    code, printed = validator(leaf, capsys)
    assert code == 1 and f"missing consumers ['{beta}']; run" in printed and COMMAND in printed
    with pytest.raises(LaneManifestError, match="the file does not exist; run") as caught:
        load_lane_manifest(leaf)
    assert f"{beta}: lane member is not" in str(caught.value) and COMMAND in str(caught.value)
    code, printed = write_command(leaf, capsys)
    assert code == 0 and f"{ANCHOR}: derived from source (added [], removed ['{beta}'])" in printed
    assert f"unit-regression: removed {beta}: the file does not exist" in printed
    assert validator(leaf, capsys)[0] == 0


def test_a_row_whose_artifact_file_is_gone_is_named_and_never_removed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = synthetic_repo(tmp_path / "repo")
    (root / ANCHOR).unlink()
    code, printed = write_command(root, capsys)
    assert code == 0 and f"{ANCHOR}: the artifact file does not exist; the command" in printed
    assert f'path = "{ANCHOR}"' in (root / CATALOG).read_text(encoding="utf-8")
    code, printed = validator(root, capsys)
    assert code == 1 and f"{ANCHOR}: cataloged artifact does not exist; the command" in printed


def test_a_row_without_a_derived_consumer_keeps_every_consumer_that_exists(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Retiring such a row is a decision: it is named, and only the line of a missing file goes."""

    root = synthetic_repo(tmp_path / "repo")
    alpha, gone = module_path("alpha"), module_path("gone")
    path = root / CATALOG
    idle = ROW.format(path="mcp/tests/_idle.py", consumer=f'{alpha}",\n  "{gone}')
    lost = ROW.format(path="mcp/tests/_lost.py", consumer=gone)
    path.write_text(path.read_text(encoding="utf-8") + idle + lost, encoding="utf-8")
    for name in ("_idle", "_lost"):
        (root / f"mcp/tests/{name}.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / gone).mkdir()  # gone means "not a file": a directory of that name does not count
    code, printed = write_command(root, capsys)
    assert code == 0 and f"mcp/tests/_idle.py: removed {gone}: the file does not exist" in printed
    assert printed.count("the source tree shows no consumer; consumers kept as written") == 2
    listed = path.read_text(encoding="utf-8")
    # The row that lists only the missing file keeps it: the command never leaves a row empty.
    # The other row keeps its existing consumer, as the synthetic lane-manifest row does.
    assert listed.count(f'"{gone}"') == 1 and listed.count(f'  "{alpha}",\n]') == 2
    said = f"_lost.py: missing consumers ['{gone}']; the command does not repair this row"
    assert said in validator(root, capsys)[1]


def test_the_command_refuses_ambiguous_modules_without_writing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = synthetic_repo(tmp_path / "repo")
    (root / "lib/shared").mkdir(parents=True)
    (root / "lib/shared/__init__.py").write_text("", encoding="utf-8")
    (root / "mcp/tests/shared.py").write_text("VALUE = 1\n", encoding="utf-8")
    insert_line(root, CATALOG, "consumers", module_path("beta"))
    before = (root / CATALOG).read_bytes(), (root / LANES).read_bytes()
    code, printed = write_command(root, capsys)
    assert code == 1 and "nothing written" in printed and "ambiguous modules: ['shared']" in printed
    assert ((root / CATALOG).read_bytes(), (root / LANES).read_bytes()) == before


def test_a_refusal_for_the_lane_manifest_leaves_a_repairable_lifecycle_catalog_alone(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A comment inside a list cannot be kept, so the command refuses; neither file is written."""

    root = synthetic_repo(tmp_path / "repo")
    insert_line(root, CATALOG, "consumers", module_path("beta"))
    lanes = root / LANES
    listed = lanes.read_text(encoding="utf-8").replace(
        "regression = [\n", "regression = [\n  # x\n"
    )
    lanes.write_text(listed, encoding="utf-8")
    before = (root / CATALOG).read_bytes(), lanes.read_bytes()
    code, printed = write_command(root, capsys)
    assert code == 1 and "nothing written" in printed and "holds a comment" in printed
    assert ((root / CATALOG).read_bytes(), lanes.read_bytes()) == before


@pytest.mark.parametrize(
    ("catalog", "named"),
    [(CATALOG, f"{ANCHOR}: consumers"), (LANES, "unit-regression: lane list")],
    ids=["consumers", "lane"],
)
def test_a_list_with_two_paths_on_one_line_is_refused_then_rewritten(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], catalog: str, named: str
) -> None:
    root = synthetic_repo(tmp_path / "repo")
    one_per_line = f'  "{module_path("alpha")}",\n  "{module_path("beta")}",\n'
    path = root / catalog
    text = path.read_text(encoding="utf-8")
    joined = one_per_line.replace(",\n  ", ", ")
    path.write_text(text.replace(one_per_line, joined), encoding="utf-8")
    code, printed = validator(root, capsys)
    assert code == 1 and COMMAND in printed
    assert f"{named} is not written with one path per line, in double quotes" in printed
    code, printed = write_command(root, capsys)
    assert code == 0 and "whitespace normalised" in printed
    assert path.read_text(encoding="utf-8") == text and validator(root, capsys)[0] == 0


def test_pytest_start_up_explains_a_lane_manifest_that_does_not_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first read of a test run is in ``conftest.pytest_configure``, before any loader."""

    manifest = tmp_path / LANES
    manifest.parent.mkdir(parents=True)
    manifest.write_text('[files]\nintegration = [ path = "x"\n', encoding="utf-8")
    monkeypatch.setattr(conftest, "REPOSITORY_ROOT", tmp_path)
    with pytest.raises(pytest.UsageError) as caught:
        conftest.pytest_configure(cast(pytest.Config, object()))
    assert f"cannot read evidence lane manifest {manifest}" in str(caught.value)
    assert "interleaved by the merge" in str(caught.value)


def test_a_lane_manifest_without_a_files_table_is_refused_by_name_at_every_entry_point(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = synthetic_repo(tmp_path / "repo")
    (root / LANES).write_text('schema_version = "ar-test-evidence-lanes/v1"\n', encoding="utf-8")
    said = f"{LANES} has no [files] table"
    code, printed = validator(root, capsys)
    assert code == 1 and said in printed
    code, printed = write_command(root, capsys)
    assert code == 1 and "nothing written" in printed and said in printed
    monkeypatch.setattr(conftest, "REPOSITORY_ROOT", root)
    with pytest.raises(pytest.UsageError) as caught:
        conftest.pytest_configure(cast(pytest.Config, object()))
    assert said in str(caught.value)
    # A table without the lanes the start-up reads is refused by name too, not with a bare KeyError.
    (root / LANES).write_text("[files]\n", encoding="utf-8")
    with pytest.raises(pytest.UsageError, match="must hold the lanes integration"):
        conftest.pytest_configure(cast(pytest.Config, object()))


def test_a_list_with_other_quotes_or_commas_is_refused_and_the_command_says_what_it_rewrote(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = synthetic_repo(tmp_path / "repo")
    path = root / LANES
    text = path.read_text(encoding="utf-8")
    beta = module_path("beta")
    path.write_text(text.replace(f'  "{beta}",\n', f"  '{beta}'\n"), encoding="utf-8")
    code, printed = validator(root, capsys)
    assert code == 1 and "in double quotes, with a comma after each path" in printed
    code, printed = write_command(root, capsys)
    assert code == 0 and "unit-regression: rewritten with one path per line" in printed
    assert "whitespace normalised" not in printed and path.read_text(encoding="utf-8") == text


def test_test_selection_names_a_base_catalog_that_does_not_parse(tmp_path: Path) -> None:
    """Selection compares with the catalog of the base revision, which a merge can have broken."""

    root = synthetic_repo(tmp_path / "repo")
    path = root / CATALOG
    healthy = path.read_text(encoding="utf-8")
    path.write_text(healthy + 'consumers = [ path = "x"\n', encoding="utf-8")
    land(root, lambda repository: None)
    path.write_text(healthy, encoding="utf-8")
    impact = DependencyOwnershipGraph(root).resolve([Path(CATALOG)], base_revision="HEAD")
    [unresolved] = impact.unresolved_inputs
    assert f"cannot read evidence catalog {CATALOG} at the base revision" in unresolved.detail
    assert "interleaved by the merge" in unresolved.detail


@pytest.mark.parametrize(
    "workers", [("-n0",), ("-n=4", "--dist", "loadfile")], ids=["no-workers", "default-workers"]
)
@pytest.mark.parametrize("defect", ["no-lane-line", "out-of-order"])
def test_a_test_run_prints_the_lane_refusal_once_with_the_command(
    tmp_path: Path, workers: tuple[str, ...], defect: str
) -> None:
    """Only a real pytest controller can show the worker's collection refusal in its output."""

    root = synthetic_repo(tmp_path / "repo")
    plugin = "agents_remember_test_support.testing.evidence_lanes"
    (root / "mcp/tests/conftest.py").write_text(f"pytest_plugins = ({plugin!r},)\n", "utf-8")
    if defect == "no-lane-line":
        add_test_module(root, "gamma")
        finding = f"test files without an explicit lane: ['{module_path('gamma')}']"
    else:
        write_lanes(root, {"unit-regression": [module_path("beta"), module_path("alpha")]})
        finding = "unit-regression: lane list is out of order"
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("PYTEST_")
    }
    environment["PYTHONPATH"] = os.pathsep.join(
        str(REPOSITORY_ROOT / source) for source in ("mcp/test_support", "mcp/src")
    )
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    command = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *workers]
    run = subprocess.run(
        command, cwd=root, env=environment, text=True, capture_output=True, check=False, timeout=30
    )
    output = run.stdout + run.stderr
    assert run.returncode == pytest.ExitCode.USAGE_ERROR, output
    assert output.count(finding) == 1 and COMMAND in output, output
    assert "INTERNALERROR" not in output and "Traceback" not in output, output
