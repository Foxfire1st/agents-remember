"""The binding coverage gate: a floor on the lines this change touched.

An aggregate coverage pin does not work here and the numbers say why. This tree has
88k lines of tests against 44,697 measured statements; a large share of the package is
executed simply by being imported, so the aggregate starts high and barely moves. On
2026-07-31 the whole suite reported 87.16% (90.17% statements, 76.52% branches) over
434 files. One newly added, entirely untested 20-line function moves that figure by
0.04 percentage points -- below the resolution of any threshold anyone would set, and
far below the run-to-run wobble of a suite that starts subprocesses. An aggregate pin
is therefore satisfied by import-time execution and cannot see a regression in a single
change, which is the only thing a pre-merge gate is for.

So the gate is on the diff. ``git diff`` against the merge base gives the lines this
branch introduced; the coverage JSON the wrapper already produces says which of them
ran. No second coverage run, no second measurement: the units are Coverage.py's own --
statements plus branch arcs -- so this floor, the aggregate, and CRAP are all reading
the same number.

Why the floor is 100%, i.e. zero uncovered changed lines
--------------------------------------------------------
A floor below 100% is not a standard, it is a per-change budget for untested code, and
the budget grows with the change. At floor ``X`` a change of ``N`` units may carry
``floor((1 - X) * N)`` uncovered ones, so the bigger the change the more untested code
it buys -- exactly backwards, and exactly the shape of the ratchets this repository has
already deleted.

Measured on this repository rather than assumed. Of the last 40 commits on ``main``, 31
touch ``mcp/src/agents_remember``: p25 46 added source lines, median 234, p75 532, max
10,549. Against the median change of 234 units the budget is 23 uncovered units at a
90% floor, 11 at 95%, 4 at 98%. The median function in this package is about 9
statements (44,697 statements across 4,672 scored functions), so a 90% floor lets an
entire untested function land inside an average change without the gate noticing, and
95% lets one land inside any change past 180 units -- above the observed median. Only
100% means the same thing for a 3-line change and a 300-line one.

The lower bound argument agrees and rules out the popular numbers outright. A floor at
or below the tree's own aggregate (87.16%) passes any change that is merely average, so
the tree can never improve and drifts down as the code grows. That disqualifies 80 and
85 without needing any argument about budgets at all.

What arming it costs, stated rather than hidden. Measured against this branch's merge
base on 2026-07-31: 5,302 changed measurable units, 4,899 covered -- 92.40% (94.20% on
statements alone), leaving 403 uncovered units across 71 files. The floor is not set to
93% to make that green. Of those 403, 172 are lines whose exact text also appears among
the diff's removed lines -- code this branch relocated during its parameter-object and
complexity refactors, and the whole-tree ``ruff format`` in 00e8379 -- and 231 are new
content. The gate does not subtract the relocated ones: a carve-out for "lines that
moved" is an exemption list with a different name, and a change that moves uncovered
code into a new home owns it on arrival.

Reported states, none of which pass silently
--------------------------------------------
Every run prints the base it used and how it was chosen, then one of:

``measured``
    Changed Python lines exist inside the measured packages. The floor applies and
    every uncovered line and untaken branch is named, not counted.
``no-changed-lines``
    The diff against the base is empty. Nothing to certify; states so.
``no-python-changes``
    The diff touches files, none of them Python. States so.
``no-measurable-changes``
    Python changed, but no changed line is inside a measured package -- tests, scripts,
    provider images. The floor cannot speak for those; the report names the files and
    their line counts so the hole is visible on every run rather than inferred.

A first commit with no merge base is not a fourth state: the base becomes git's empty
tree, so every tracked line is a changed line and the floor applies to all of it. That
is the honest reading of "nothing to compare against", and it is the strict one.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from agents_remember.code_quality import crap_calculator
from agents_remember.kernel import git_command

# Zero uncovered changed lines. See the module docstring for the derivation; the short
# form is that any value below 100 is a budget for untested code that grows with the
# size of the change, and that a value at or below the tree's own aggregate (87.16% on
# 2026-07-31) passes a change that is merely average.
DEFAULT_DIFF_COVERAGE_FLOOR = 100.0

# git's empty tree. Diffing against it yields the whole tree, which is what "no merge
# base" honestly means: nothing has been established yet, so nothing is grandfathered.
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

HUNK = re.compile(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@")


class DiffScopeError(RuntimeError):
    """The gate could not work out which lines this change touched."""


@dataclass(frozen=True)
class BaseResolution:
    """Which revision the diff is taken against, and how that was decided.

    ``origin`` is printed on every run. A gate that silently picks its own comparison
    point can be made to certify nothing by picking the wrong one, so the choice is
    always visible in the output next to the verdict it produced.
    """

    revision: str
    origin: str


@dataclass(frozen=True)
class DiffCoverage:
    """One run's verdict."""

    base: BaseResolution
    state: str
    covered_units: int
    total_units: int
    uncovered_lines: tuple[str, ...]
    untaken_branches: tuple[str, ...]
    unmeasured_files: tuple[tuple[str, int], ...]

    @property
    def ratio(self) -> float:
        if not self.total_units:
            return 1.0
        return self.covered_units / self.total_units

    @property
    def percent(self) -> float:
        return self.ratio * 100.0


def _git(project_root: Path, arguments: list[str]) -> subprocess.CompletedProcess[str]:
    """This gate's git calls, on the package's one runner.

    ``core.quotePath=false`` is this gate's own requirement and stays: without it git
    octal-escapes non-ASCII paths in ``diff`` output and the changed-line parser stops
    recognising the files it is supposed to score.

    Routing through the shared runner is not cosmetic here. The full tier runs from the
    ``pre-push`` hook, and git exports ``GIT_DIR`` to its hooks -- so of every git call
    site in this package, this gate's were the ones most certain to meet the very
    variables they did not strip, and would then have certified the coverage of a
    different repository than the one being pushed.

    The conversion to :class:`DiffScopeError` lives here rather than in one of the three
    callers, so every caller gets it. ``run_git`` used to own it alone while
    ``revision_exists`` and ``merge_base`` called this function bare, and the shared runner
    made that difference load-bearing: it raises ``TimeoutExpired`` where the old inline
    call had no timeout at all, and ``FileNotFoundError`` for a ``project_root`` that does
    not exist, where the old ``git -C <path>`` with no ``cwd=`` merely exited non-zero and
    those two answered ``False`` / ``None``. Three siblings on one runner must not disagree
    about which failures are this gate's own error.
    """

    try:
        return git_command.run_git(project_root, ["-c", "core.quotePath=false", *arguments])
    except (OSError, subprocess.SubprocessError) as error:
        raise DiffScopeError(f"git command failed (git {' '.join(arguments)}): {error}") from error


def run_git(project_root: Path, arguments: list[str]) -> str:
    completed = _git(project_root, arguments)
    if completed.returncode != 0:
        raise DiffScopeError(
            f"git command failed (git {' '.join(arguments)}): "
            f"exit {completed.returncode}: {completed.stderr.strip()}"
        )
    return completed.stdout


def revision_exists(project_root: Path, revision: str) -> bool:
    completed = _git(project_root, ["rev-parse", "--verify", "--quiet", f"{revision}^{{commit}}"])
    return completed.returncode == 0


def merge_base(project_root: Path, revision: str) -> str | None:
    completed = _git(project_root, ["merge-base", "HEAD", revision])
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def candidate_sources(environment: Mapping[str, str]) -> list[tuple[str, str]]:
    """``(revision, origin)`` pairs to try, in order, most explicit first.

    The order is deliberate and each entry earns its place:

    ``AR_GATE_DIFF_BASE``
        The one way to say it outright. A leaf worktree branched from a series branch
        is the case git cannot infer -- ``main`` is not its source and it has no
        upstream -- so the hooks and the closeout path pass this.
    ``GITHUB_BASE_REF``
        A pull request states its own base. This is what CI uses.
    ``@{upstream}``
        Git's own record of where a branch came from, when one is configured.
    ``origin/HEAD`` then ``main``
        The default branch, which is the source branch for anything cut from it.
    """
    explicit = environment.get("AR_GATE_DIFF_BASE", "").strip()
    candidates: list[tuple[str, str]] = []
    if explicit:
        candidates.append((explicit, "AR_GATE_DIFF_BASE"))
    base_ref = environment.get("GITHUB_BASE_REF", "").strip()
    if base_ref:
        candidates.append((f"origin/{base_ref}", "GITHUB_BASE_REF (pull request base)"))
        candidates.append((base_ref, "GITHUB_BASE_REF (pull request base)"))
    candidates.append(("@{upstream}", "the branch's configured upstream"))
    candidates.append(("origin/HEAD", "the remote's default branch"))
    candidates.append(("main", "the local default branch"))
    return candidates


def resolve_base(
    project_root: Path,
    *,
    explicit_base: str | None = None,
    environment: Mapping[str, str] | None = None,
) -> BaseResolution:
    """The revision to diff against.

    ``explicit_base`` is the ``--diff-base`` flag and wins over everything, including
    the environment. When nothing resolves -- an orphan branch, a first commit, a clone
    with no remote and no ``main`` -- the base is the empty tree rather than a skip.
    """
    env = os.environ if environment is None else environment
    if explicit_base:
        if not revision_exists(project_root, explicit_base):
            raise DiffScopeError(
                f"--diff-base {explicit_base!r} is not a commit in this repository"
            )
        return BaseResolution(revision=explicit_base, origin="--diff-base")
    for revision, origin in candidate_sources(env):
        if not revision_exists(project_root, revision):
            continue
        base = merge_base(project_root, revision)
        if base is not None:
            return BaseResolution(revision=base, origin=f"merge base with {revision} ({origin})")
    return BaseResolution(
        revision=EMPTY_TREE,
        origin="no merge base is resolvable; comparing against the empty tree",
    )


def changed_python_lines(project_root: Path, base: str) -> dict[str, set[int]]:
    """Line numbers this change added or modified, keyed by repo-relative path.

    The comparison is base-to-working-tree, with no second revision, because the
    working tree is what the suite just imported and measured. The numbers therefore
    index the same file content the coverage report describes.
    """
    return parse_unified_diff(
        run_git(
            project_root,
            [
                "diff",
                "--unified=0",
                "--no-color",
                "--no-ext-diff",
                "--diff-filter=ACMR",
                base,
                "--",
                "*.py",
            ],
        )
    )


def parse_unified_diff(diff: str) -> dict[str, set[int]]:
    """Post-image line numbers per file, from ``diff --unified=0`` output.

    A separate function from the git call because its two guards are only reachable
    from malformed input: a ``+++`` header that is not ``+++ b/<path>`` (which
    ``--diff-filter=ACMR`` never produces, since a deletion is filtered out before it
    can write ``+++ /dev/null``), and a ``@@`` line the hunk pattern does not match.
    Both drop the content rather than guess at it, and both are exercised directly by
    ``mcp/tests/test_diff_coverage.py`` -- a guard no test can reach is a guard nobody
    can show is right.
    """
    changed: dict[str, set[int]] = {}
    current: str | None = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else None
            if current is not None:
                changed.setdefault(current, set())
            continue
        if current is None or not line.startswith("@@"):
            continue
        match = HUNK.match(line)
        if match is None:
            continue
        start = int(match.group(1))
        count = 1 if match.group(2) is None else int(match.group(2))
        changed[current].update(range(start, start + count))
    return {path: lines for path, lines in changed.items() if lines}


def coverage_by_relative_path(
    coverage_json: Path, project_root: Path
) -> dict[str, crap_calculator.FileCoverage]:
    """The coverage report re-keyed on repo-relative posix paths.

    ``crap_calculator`` already normalises every spelling Coverage.py might emit; this
    keeps the single repo-relative key, which is the one ``git diff`` produces.
    """
    by_key = crap_calculator.load_coverage_by_path(coverage_json, project_root)
    relative: dict[str, crap_calculator.FileCoverage] = {}
    for key, coverage in by_key.items():
        candidate = Path(key)
        if candidate.is_absolute():
            continue
        relative.setdefault(candidate.as_posix(), coverage)
    return relative


@dataclass
class UnitTally:
    """Running totals while walking the diff, and the findings behind them."""

    covered: int = 0
    total: int = 0
    uncovered_lines: list[str] = field(default_factory=list)
    untaken_branches: list[str] = field(default_factory=list)


def tally_file(
    tally: UnitTally,
    path: str,
    lines: set[int],
    coverage: crap_calculator.FileCoverage,
) -> None:
    """Fold one changed file's measurable units into ``tally``.

    A unit is a measurable statement or a branch arc leaving one of the changed lines,
    which is Coverage.py's own accounting and the same one ``crap_calculator`` uses. A
    changed line that carries no statement -- a comment, a blank, a continuation --
    contributes nothing rather than counting as covered.
    """
    executed = coverage.executed_lines & lines
    missing = coverage.missing_lines & lines
    taken = {arc for arc in coverage.executed_branches if arc[0] in lines}
    untaken = {arc for arc in coverage.missing_branches if arc[0] in lines}
    tally.covered += len(executed) + len(taken)
    tally.total += len(executed) + len(missing) + len(taken) + len(untaken)
    tally.uncovered_lines.extend(f"{path}:{number}" for number in sorted(missing))
    tally.untaken_branches.extend(
        f"{path}:{source} -> {describe_destination(destination)}"
        for source, destination in sorted(untaken)
    )


def describe_destination(destination: int) -> str:
    """Coverage.py writes a function exit as a negative destination line."""
    return "exit" if destination < 0 else str(destination)


def measure(
    project_root: Path,
    coverage_json: Path,
    base: BaseResolution,
) -> DiffCoverage:
    """Score the changed lines, or report why there is nothing to score."""
    changed = changed_python_lines(project_root, base.revision)
    if not changed:
        return empty_result(base, state_for_empty_diff(project_root, base.revision))
    coverage = coverage_by_relative_path(coverage_json, project_root)
    tally = UnitTally()
    unmeasured: list[tuple[str, int]] = []
    for path in sorted(changed):
        file_coverage = coverage.get(path)
        if file_coverage is None:
            unmeasured.append((path, len(changed[path])))
            continue
        tally_file(tally, path, changed[path], file_coverage)
    state = "measured" if tally.total else "no-measurable-changes"
    return DiffCoverage(
        base=base,
        state=state,
        covered_units=tally.covered,
        total_units=tally.total,
        uncovered_lines=tuple(tally.uncovered_lines),
        untaken_branches=tuple(tally.untaken_branches),
        unmeasured_files=tuple(unmeasured),
    )


def state_for_empty_diff(project_root: Path, base: str) -> str:
    """Tell "nothing changed" apart from "nothing *Python* changed".

    Both pass, and they are different facts about the change: one says the branch is
    empty, the other says it edited documentation or a workflow. Collapsing them into a
    single "skipped" is how a gate stops being readable.
    """
    diff = run_git(project_root, ["diff", "--name-only", "--diff-filter=ACMR", base])
    return "no-python-changes" if diff.strip() else "no-changed-lines"


def empty_result(base: BaseResolution, state: str) -> DiffCoverage:
    return DiffCoverage(
        base=base,
        state=state,
        covered_units=0,
        total_units=0,
        uncovered_lines=(),
        untaken_branches=(),
        unmeasured_files=(),
    )


def render(result: DiffCoverage, floor: float) -> list[str]:
    """The report, findings and all.

    Every uncovered line is named. The percentage alone trains people to add any test
    until the number moves; the list says which line has never run, which is the only
    form of the finding someone can act on directly.
    """
    lines = [
        f"base: {result.base.revision} -- {result.base.origin}",
        f"floor: {floor:.1f}% of changed statements and branches",
        f"state: {result.state}",
    ]
    lines.extend(STATE_NOTES.get(result.state, ()))
    if result.unmeasured_files:
        lines.append(unmeasured_header(result))
        lines.extend(
            f"  {count:>5} changed line(s)  {path}" for path, count in result.unmeasured_files
        )
    if result.state != "measured":
        return lines
    lines.append(
        f"changed coverage: {result.covered_units}/{result.total_units} units "
        f"= {result.percent:.2f}%"
    )
    if result.percent >= floor:
        return lines
    lines.append(
        f"\n{len(result.uncovered_lines)} changed line(s) and "
        f"{len(result.untaken_branches)} changed branch(es) never ran. There is no "
        "exemption list: each is cleared by a test that reaches it, or by deleting the "
        "code."
    )
    lines.extend(f"  uncovered line    {entry}" for entry in result.uncovered_lines)
    lines.extend(f"  untaken branch    {entry}" for entry in result.untaken_branches)
    return lines


STATE_NOTES: dict[str, tuple[str, ...]] = {
    "no-changed-lines": (
        "nothing changed against the base, so there is nothing for a coverage floor to certify.",
    ),
    "no-python-changes": (
        "files changed against the base, none of them Python; the floor has nothing to measure.",
    ),
    "no-measurable-changes": (
        "Python changed, but no changed line sits inside a package the coverage run measures.",
    ),
}


def unmeasured_header(result: DiffCoverage) -> str:
    """Name the changed Python the coverage run does not measure.

    ``--cov`` is aimed at the tracked top-level packages, which is the shipped code; the
    suite itself, ``scripts/`` and the provider images are outside it. Their changed
    lines cannot be scored, so they are listed on every run instead of being dropped --
    an unscored file that nobody prints is indistinguishable from a covered one.
    """
    total = sum(count for _path, count in result.unmeasured_files)
    return (
        f"\n{len(result.unmeasured_files)} changed Python file(s) carrying {total} changed "
        "line(s) are outside the packages coverage measures, so this floor cannot speak "
        "for them:"
    )
