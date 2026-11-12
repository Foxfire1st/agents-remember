"""What a landing cuts from a card and what it adds beside the knowledge (MIK-R120).

The quality check of a leaf's memory compares each card and overview under
``onboarding/`` at the candidate with the memory base of the leaf, and lists
every path the candidate adds outside the knowledge folders. Both are
report-only: they refuse no check, no gate and no closeout, and they do not
count as actionable.

The memory base of the leaf is its admitted base (the contract's memory base
commit), not the validator's operation base: cuts and additions the leaf
already committed stay reported. Without an admitted base (a run with no
leaf contract) the run's comparison base stands in.

The measure (ruling ``eb467b1e``) lives in one place here: a card is reported
when its Markdown removes at least :data:`CUT_MIN_REMOVED_LINES` lines of the
base and more than :data:`CUT_REMOVED_TO_ADDED_FACTOR` times what it adds --
including a card removed outright. The deleted-source exemption applies only
to a source actually deleted by the leaf (present in the base code, absent in
the candidate): a measured card whose deletion is unproven stays visible,
even when the card itself is gone and its source is absent on both known
code sides. Only where no code tree exists on either side does a vanished
card count as a deleted source and stay silent. A card renamed with its
source is compared with the card at the old path when the code change names
the rename through ``git -M``; anything the code change does not name is
judged by the same two rules, never by blob guesswork.
"""

from __future__ import annotations

import os
import stat
import subprocess
from collections import Counter
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from agents_remember.kernel.git_command import (
    GIT_METADATA_TIMEOUT_SECONDS,
    GitRunnerOptions,
    run_git,
)
from agents_remember.memory_quality.knowledge_validator.trees import (
    CodeDirectory,
    KnowledgeTree,
    KnowledgeTreeReadError,
    code_tree_from_git,
    knowledge_tree_from_git,
)
from agents_remember.models.knowledge_files.canonical import parse_json
from agents_remember.models.knowledge_files.documents import ONBOARDING_ROOT

#: A card is reported when it removes at least this many lines of the base
#: (MIK-R120 rule 1; the measure of ruling ``eb467b1e``).
CUT_MIN_REMOVED_LINES: Final = 8
#: A card is reported when what it removes is more than this many times what
#: it adds (MIK-R120 rule 1; the measure of ruling ``eb467b1e``).
CUT_REMOVED_TO_ADDED_FACTOR: Final = 2

CUT_CARDS_CHECK: Final = "knowledge.landing.cut_cards"
ADDED_FILES_CHECK: Final = "knowledge.landing.added_files"
BASE_UNREADABLE_CODE: Final = "landing-base-unreadable"

#: Tops of the memory tree a landing may never grow: a path the candidate
#: adds under one of these is knowledge, not a file beside it (MIK-R120
#: rule 2).
KNOWLEDGE_TOPS: Final = ("onboarding/", "knowledge/", "system/")

_FILE_SIDECAR_SCHEMA: Final = "ar-onboarding-file/v1"
_OVERVIEW_BASENAME: Final = "overview.md"


def multiset_line_delta(
    base_lines: Sequence[str], candidate_lines: Sequence[str]
) -> tuple[int, int]:
    """Lines gone from the base multiset and lines the candidate adds.

    Order-insensitive: moving lines within a card neither removes nor adds
    them. Each line is hashed once, so the work is linear in the card size
    no matter how often lines repeat.
    """

    base_counts = Counter(base_lines)
    candidate_counts = Counter(candidate_lines)
    removed = sum((base_counts - candidate_counts).values())
    added = sum((candidate_counts - base_counts).values())
    return removed, added


def count_line_delta(base_text: str, candidate_text: str) -> tuple[int, int]:
    """Lines of ``base_text`` removed and lines ``candidate_text`` adds.

    Both counts come from one line-multiset difference, so a rewritten line
    counts once on each side. A trailing newline is not a line.
    """

    return multiset_line_delta(base_text.splitlines(), candidate_text.splitlines())


def is_cut_card(removed: int, added: int) -> bool:
    """Whether ``removed``/``added`` line counts meet the cut measure."""

    return removed >= CUT_MIN_REMOVED_LINES and removed > CUT_REMOVED_TO_ADDED_FACTOR * added


def is_landing_card(path: str) -> bool:
    """Whether ``path`` is a card or overview the landing report compares."""

    return path.startswith(f"{ONBOARDING_ROOT}/") and path.endswith(".md")


def is_overview(path: str) -> bool:
    """Whether ``path`` is a route overview (compared, never a deleted source)."""

    return path.startswith(f"{ONBOARDING_ROOT}/") and (
        path == f"{ONBOARDING_ROOT}/{_OVERVIEW_BASENAME}" or path.endswith(f"/{_OVERVIEW_BASENAME}")
    )


def card_source_path(card: str, files: Mapping[str, bytes]) -> str | None:
    """The source file ``card`` documents, or ``None`` for an overview.

    The sidecar beside the card names it (``path`` of an
    ``ar-onboarding-file/v1`` sidecar); without a readable sidecar the path
    convention (``onboarding/<source>.md``) stands in.
    """

    if is_overview(card):
        return None
    sidecar = files.get(f"{card[:-3]}.json")
    if sidecar is not None:
        try:
            document = parse_json(sidecar.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            document = None
        if (
            isinstance(document, dict)
            and document.get("schema") == _FILE_SIDECAR_SCHEMA
            and isinstance(document.get("path"), str)
            and document["path"]
        ):
            return str(document["path"])
    prefix = f"{ONBOARDING_ROOT}/"
    if card.startswith(prefix) and card.endswith(".md"):
        return card[len(prefix) : -len(".md")]
    return None


def _cut_message(path: str, removed: int, added: int, renamed_from: str | None) -> str:
    compared = f", compared with {renamed_from}" if renamed_from else ""
    return (
        f"card {path} removes {removed} lines of the memory base"
        f" and adds {added}{compared}; the handover names the change"
        " that made the removed text untrue"
    )


def _cut_row(path: str, removed: int, added: int, renamed_from: str | None) -> dict[str, Any]:
    return {
        "check": CUT_CARDS_CHECK,
        "path": path,
        "code": "landing-cut-card",
        "linesRemoved": removed,
        "linesAdded": added,
        "message": _cut_message(path, removed, added, renamed_from),
    }


def _deleted_by_leaf(
    source: str | None,
    code_has_file: Callable[[str], bool] | None,
    base_code_has_file: Callable[[str], bool] | None,
) -> bool:
    """Whether ``source`` is proven deleted by the leaf.

    Only a source the base code holds and the candidate code no longer holds
    counts as deleted. Anything else -- a source held on both sides, held on
    neither, or untellable for lack of a base code tree -- is not a deletion
    the report may assume.
    """

    return (
        source is not None
        and code_has_file is not None
        and not code_has_file(source)
        and base_code_has_file is not None
        and base_code_has_file(source)
    )


def _compared_cut_rows(
    candidate_cards: Mapping[str, bytes],
    base_cards: Mapping[str, bytes],
    code_has_file: Callable[[str], bool] | None,
    base_code_has_file: Callable[[str], bool] | None,
    renamed: Mapping[str, str],
) -> list[dict[str, Any]]:
    """Cards at the candidate, each against its base text (or its rename's)."""

    rows: list[dict[str, Any]] = []
    for path in sorted(candidate_cards):
        if not is_landing_card(path):
            continue  # sidecars and records ride along for source lookup only
        base_path = renamed.get(path, path)
        base_data = base_cards.get(base_path)
        if base_data is None:
            continue  # a new card has no base text to cut
        data = candidate_cards[path]
        if data == base_data:
            continue
        removed, added = count_line_delta(
            base_data.decode("utf-8", errors="replace"),
            data.decode("utf-8", errors="replace"),
        )
        if not is_cut_card(removed, added):
            continue
        source = card_source_path(path, candidate_cards)
        if _deleted_by_leaf(source, code_has_file, base_code_has_file):
            continue  # the card of a source file the leaf deletes
        rows.append(_cut_row(path, removed, added, renamed.get(path) if path in renamed else None))
    return rows


def _removed_cut_rows(
    candidate_cards: Mapping[str, bytes],
    base_cards: Mapping[str, bytes],
    code_has_file: Callable[[str], bool] | None,
    renamed_old: Collection[str],
    base_code_has_file: Callable[[str], bool] | None = None,
) -> list[dict[str, Any]]:
    """Base cards the candidate no longer holds: a full cut, unless deleted.

    A complete removal answers the same one shared measure as a shortening.
    Only a source the leaf provably deleted is exempt; a vanished card whose
    source is absent on both known code sides is reported, never assumed
    deleted. Only where no code tree exists on either side does the vanished
    card count as a deleted source and stay silent.
    """

    rows: list[dict[str, Any]] = []
    for path in sorted(base_cards):
        if not is_landing_card(path) or path in candidate_cards or path in renamed_old:
            continue
        source = card_source_path(path, base_cards)
        if source is not None and (
            _deleted_by_leaf(source, code_has_file, base_code_has_file)
            or (code_has_file is None and base_code_has_file is None)
        ):
            # Proven deleted, or no code tree on either side to place it.
            continue
        base_lines = base_cards[path].decode("utf-8", errors="replace").splitlines()
        if not is_cut_card(len(base_lines), 0):
            continue
        rows.append(_cut_row(path, len(base_lines), 0, None))
    return rows


def cut_card_rows(
    candidate_cards: Mapping[str, bytes],
    base_cards: Mapping[str, bytes],
    code_has_file: Callable[[str], bool] | None,
    base_code_has_file: Callable[[str], bool] | None = None,
    renames: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Every reported cut, ordered by card path.

    ``code_has_file`` answers the candidate code tree (``None`` when the
    caller has no code tree). ``base_code_has_file`` answers the base code
    tree when the caller knows it; a source absent from the candidate is
    excluded as deleted only when the base still holds it. ``renames`` maps
    a candidate card to the base card it continues after a rename the code
    change names.
    """

    renamed: Mapping[str, str] = dict(renames or {})
    rows = _compared_cut_rows(
        candidate_cards, base_cards, code_has_file, base_code_has_file, renamed
    )
    rows.extend(
        _removed_cut_rows(
            candidate_cards,
            base_cards,
            code_has_file,
            set(renamed.values()),
            base_code_has_file,
        )
    )
    return sorted(rows, key=lambda row: str(row["path"]))


def added_file_rows(
    candidate_listing: Mapping[str, int], base_paths: Collection[str]
) -> tuple[list[dict[str, Any]], int]:
    """Every path the candidate adds outside the knowledge tops, with sizes.

    Returns the rows ordered by path, and the summed size in bytes.
    """

    base = set(base_paths)
    rows = [
        {
            "check": ADDED_FILES_CHECK,
            "path": path,
            "code": "landing-added-file",
            "sizeBytes": candidate_listing[path],
            "message": (
                f"file {path} ({candidate_listing[path]} bytes) is added"
                " outside onboarding/, knowledge/ and system/;"
                " evidence and working files belong to the task record"
                " or the scratch root"
            ),
        }
        for path in sorted(candidate_listing)
        if not path.startswith(KNOWLEDGE_TOPS) and path not in base
    ]
    return rows, sum(int(row["sizeBytes"]) for row in rows)


@dataclass(frozen=True)
class LandingReportInputs:
    """Everything one landing report compares: cards, code trees, listings.

    ``base_cards`` is ``None`` when the memory base cannot be read: the
    report then names that and reports no cut as none. ``code_has_file``
    answers the candidate code tree (``None`` without one);
    ``base_code_has_file`` answers the base code tree when the caller knows
    it. A vanished card is exempt only for a proven deletion, or where no
    code tree exists on either side. ``renames`` maps a candidate card to
    the base card it continues after a rename the code change names.
    ``candidate_listing`` with ``base_paths`` enables the added-file section.
    """

    candidate_cards: Mapping[str, bytes]
    base_cards: Mapping[str, bytes] | None
    base_problem: str | None = None
    code_has_file: Callable[[str], bool] | None = None
    base_code_has_file: Callable[[str], bool] | None = None
    renames: Mapping[str, str] | None = None
    candidate_listing: Mapping[str, int] | None = None
    base_paths: Collection[str] | None = None


@dataclass(frozen=True)
class GitCodePair:
    """The code refs a git landing report resolves beside the memory refs."""

    repository: Path
    candidate_ref: str
    base_ref: str


def check_landing_report(inputs: LandingReportInputs) -> dict[str, Any]:
    """The landing cut-card and added-file report as one check-shaped result.

    Report-only throughout: ``ok`` is always ``True``, ``findingCount`` is
    always zero, and every row lands in ``reportOnlyFindings``. When the base
    cannot be read the result names that and reports no cut as none
    (MIK-R120 failure behavior).
    """

    report_only: list[dict[str, Any]] = []
    if inputs.base_cards is None:
        report_only.append(
            {
                "check": CUT_CARDS_CHECK,
                "path": f"{ONBOARDING_ROOT}",
                "code": BASE_UNREADABLE_CODE,
                "message": (
                    "the memory base of the leaf cannot be read"
                    f"{(': ' + inputs.base_problem) if inputs.base_problem else ''};"
                    " no cut is reported as none"
                ),
            }
        )
        cuts = []
    else:
        cuts = cut_card_rows(
            inputs.candidate_cards,
            inputs.base_cards,
            inputs.code_has_file,
            base_code_has_file=inputs.base_code_has_file,
            renames=inputs.renames,
        )
    report_only.extend(cuts)
    added: list[dict[str, Any]] = []
    added_bytes = 0
    if inputs.candidate_listing is not None and inputs.base_paths is not None:
        added, added_bytes = added_file_rows(inputs.candidate_listing, inputs.base_paths)
        report_only.extend(added)
    return {
        "ok": True,
        "findingCount": 0,
        "findings": [],
        "reportOnlyFindings": report_only,
        "cutCardCount": len(cuts),
        "cutCards": cuts,
        "addedFileCount": len(added),
        "addedFileBytes": added_bytes,
        "addedFiles": added,
    }


def walk_candidate_listing(memory_root: Path) -> dict[str, int]:
    """Every file entry of the candidate working tree, by repo-relative path.

    Entries are inventoried without following symlink targets, so a dangling
    link is still an addition; its size is its own lstat size, the same
    measure the Git tree reports for a stored link. Git administrative
    metadata (any ``.git`` path part) is not candidate content.
    """

    listing: dict[str, int] = {}
    for directory, directories, names in os.walk(memory_root, followlinks=False):
        directories[:] = sorted(name for name in directories if name != ".git")
        for name in sorted(directories + names):
            full = Path(directory) / name
            try:
                relative = full.relative_to(memory_root)
            except ValueError:
                continue
            if ".git" in relative.parts:
                continue
            try:
                entry_stat = full.lstat()
            except OSError:
                continue
            if not (stat.S_ISREG(entry_stat.st_mode) or stat.S_ISLNK(entry_stat.st_mode)):
                continue
            listing[relative.as_posix()] = entry_stat.st_size
    return listing


def _resolve_git_tree(repository: Path, treeish: str) -> str:
    """The tree object ``treeish`` names in ``repository``."""

    result = run_git(
        repository,
        ["rev-parse", "--verify", "--quiet", f"{treeish}^{{tree}}"],
        GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
    )
    tree = result.stdout.strip()
    if result.returncode != 0 or not tree:
        raise KnowledgeTreeReadError(f"{treeish!r} does not name a tree in {repository}")
    return tree


def git_tree_entries(repository: Path, treeish: str) -> dict[str, tuple[str, int]]:
    """Every stored file entry of ``treeish``: path to (mode, size in bytes).

    Regular files and symlinks alike (modes ``100644``/``100755``/``120000``);
    submodule commits and anything else a tree can hold are not candidate
    content. A symlink's size is its stored target length, the same measure
    the working-tree inventory reports without following the link.
    """

    tree = _resolve_git_tree(repository, treeish)
    result = run_git(
        repository,
        ["ls-tree", "-r", "-l", "-z", tree],
        GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
    )
    if result.returncode != 0:
        raise KnowledgeTreeReadError(
            f"tree {treeish!r} cannot be listed in {repository}: {result.stderr.strip()}"
        )
    entries: dict[str, tuple[str, int]] = {}
    for row in result.stdout.split("\0"):
        if not row or "\t" not in row:
            continue
        metadata, path = row.split("\t", 1)
        parts = metadata.split()
        if len(parts) != 4:
            continue
        mode, kind, _blob, size = parts
        if kind != "blob" or mode not in ("100644", "100755", "120000"):
            continue
        try:
            entries[path] = (mode, int(size))
        except ValueError:
            continue
    return entries


def git_candidate_listing(repository: Path, treeish: str) -> dict[str, int]:
    """Every file of ``treeish``, by path, with its size in bytes."""

    return {
        path: size for path, (_mode, size) in sorted(git_tree_entries(repository, treeish).items())
    }


def _git_name_status_renames(
    name_status: str,
    base_cards: Collection[str],
    candidate_cards: Collection[str],
) -> dict[str, str]:
    """Card renames from a ``git diff --name-status -M`` listing.

    Only similarity renames (``R`` lines) name a rename; copies, additions
    and deletions never do. Both mapped card paths must exist on their side.
    """

    renames: dict[str, str] = {}
    for line in name_status.splitlines():
        status, _, rest = line.partition("\t")
        if not status.startswith("R") or "\t" not in rest:
            continue
        old, new = rest.split("\t", 1)
        new_card = f"{ONBOARDING_ROOT}/{new}.md"
        old_card = f"{ONBOARDING_ROOT}/{old}.md"
        if new_card in candidate_cards and old_card in base_cards:
            renames[new_card] = old_card
    return renames


def _run_git_text(repository: Path, args: list[str]) -> str | None:
    """The stdout of a read-only Git command, or ``None`` when it fails."""

    try:
        result = run_git(repository, args, GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS))
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def git_named_renames_refs(
    code_repository: Path,
    base_ref: str,
    candidate_ref: str,
    base_cards: Collection[str],
    candidate_cards: Collection[str],
) -> dict[str, str]:
    """Renames the code change names between two code refs (``git -M``)."""

    output = _run_git_text(
        code_repository,
        ["diff", "--no-color", "--no-ext-diff", "--name-status", "-M", base_ref, candidate_ref],
    )
    if output is None:
        return {}
    return _git_name_status_renames(output, base_cards, candidate_cards)


def git_named_renames_worktree(
    code_root: Path,
    base_ref: str,
    base_cards: Collection[str],
    candidate_cards: Collection[str],
) -> dict[str, str]:
    """Renames the code change names between a base ref and the worktree."""

    output = _run_git_text(
        code_root, ["diff", "--no-color", "--no-ext-diff", "--name-status", "-M", base_ref]
    )
    if output is None:
        return {}
    return _git_name_status_renames(output, base_cards, candidate_cards)


def git_base_code_paths(code_root: Path, base_ref: str) -> frozenset[str] | None:
    """The file paths of the base code tree, or ``None`` when unreadable."""

    output = _run_git_text(code_root, ["ls-tree", "-r", "--name-only", "-z", base_ref])
    if output is None:
        return None
    return frozenset(path for path in output.split("\0") if path)


def code_renames(
    code_base_blobs: Mapping[str, str],
    code_candidate_blobs: Mapping[str, str],
    base_cards: Collection[str],
    candidate_cards: Collection[str],
) -> dict[str, str]:
    """Cards renamed with their source: candidate card to base card.

    A blob names a move only when it leaves exactly one base path for
    exactly one candidate path: copies held on both sides, unchanged
    duplicate-blob files, and edited sources (whose blob changed) never
    qualify. Both mapped card paths must exist on their side
    (``onboarding/<source>.md``). Prefer the ``git -M`` entry points, which
    read what the code change names, over this blob heuristic.
    """

    left: dict[str, set[str]] = {}
    for path, blob in code_base_blobs.items():
        if path not in code_candidate_blobs or code_candidate_blobs[path] != blob:
            left.setdefault(blob, set()).add(path)
    arrived: dict[str, set[str]] = {}
    for path, blob in code_candidate_blobs.items():
        if path not in code_base_blobs or code_base_blobs[path] != blob:
            arrived.setdefault(blob, set()).add(path)
    renames: dict[str, str] = {}
    for blob, old_paths in left.items():
        new_paths = arrived.get(blob, set())
        if len(old_paths) != 1 or len(new_paths) != 1:
            continue
        (old,) = old_paths
        (new,) = new_paths
        new_card = f"{ONBOARDING_ROOT}/{new}.md"
        old_card = f"{ONBOARDING_ROOT}/{old}.md"
        if new_card in candidate_cards and old_card in base_cards:
            renames[new_card] = old_card
    return renames


@dataclass(frozen=True)
class LeafBaseRequest:
    """The leaf's admitted memory base, as a quality run binds it.

    ``memory_repository`` holds ``base_commit`` (the contract's memory base
    commit); ``code_repository`` with ``code_base_commit`` names the paired
    code base when the contract knows it. ``cache_directory`` serves the
    converted-base cache when the base needs conversion.
    """

    memory_repository: Path
    base_commit: str
    code_repository: Path | None = None
    code_base_commit: str | None = None
    cache_directory: Path | None = None


@dataclass(frozen=True)
class ResolvedLeafBase:
    """A bound leaf base, resolved against one candidate.

    ``cards`` is ``None`` with ``problem`` naming why when the base cannot be
    read or converted; ``paths`` is still the base tree's file listing when
    Git can list it. ``code_paths`` names the base code tree's files (``None``
    when unknown); ``renames`` maps candidate cards to the base cards the
    code change names through ``git -M``.
    """

    cards: KnowledgeTree | None
    problem: str | None
    paths: frozenset[str] = frozenset()
    code_paths: frozenset[str] | None = None
    renames: Mapping[str, str] = field(default_factory=dict)


LandingBasePort = Callable[[KnowledgeTree], ResolvedLeafBase]
"""Resolve the bound leaf base against one candidate tree."""


def landing_report_from_git(
    memory_repository: Path,
    candidate_ref: str,
    base_ref: str,
    *,
    code: GitCodePair | None = None,
) -> dict[str, Any]:
    """The landing report of one memory candidate commit against its base.

    Both refs name commits (or trees) in ``memory_repository``. With a code
    pair, only proven-deleted sources are exempt and renames the code change
    names are compared across the move; without one every measured cut is
    reported as measured.
    """

    candidate = knowledge_tree_from_git(
        memory_repository, candidate_ref, label=f"landing candidate {candidate_ref}"
    )
    base = knowledge_tree_from_git(memory_repository, base_ref, label=f"landing base {base_ref}")
    candidate_cards = {
        path: data for path, data in candidate.files.items() if is_landing_card(path)
    }
    base_cards = {path: data for path, data in base.files.items() if is_landing_card(path)}
    candidate_listing = git_candidate_listing(memory_repository, candidate_ref)
    try:
        base_paths: Collection[str] | None = set(git_tree_entries(memory_repository, base_ref))
    except (KnowledgeTreeReadError, ValueError, OSError, subprocess.SubprocessError):
        base_paths = None
    code_tree = None
    base_code_paths: frozenset[str] | None = None
    renames: dict[str, str] = {}
    if code is not None:
        code_tree = code_tree_from_git(
            code.repository, code.candidate_ref, label=f"landing code {code.candidate_ref}"
        )
        base_code_paths = git_base_code_paths(code.repository, code.base_ref)
        renames = git_named_renames_refs(
            code.repository,
            code.base_ref,
            code.candidate_ref,
            set(base_cards),
            set(candidate_cards),
        )
    return check_landing_report(
        LandingReportInputs(
            candidate_cards=candidate_cards,
            base_cards=base_cards,
            code_has_file=(code_tree.has_file if code_tree is not None else None),
            base_code_has_file=(None if base_code_paths is None else base_code_paths.__contains__),
            renames=renames,
            candidate_listing=candidate_listing,
            base_paths=base_paths,
        )
    )


@dataclass(frozen=True)
class CheckLandingScope:
    """Everything the landing report needs inside one quality run."""

    memory_root: Path
    candidate: KnowledgeTree
    bases: tuple[KnowledgeTree, ...]
    base_problem: str | None
    code_root: Path
    leaf_base: ResolvedLeafBase | None = None


def landing_report_for_check(scope: CheckLandingScope) -> dict[str, Any]:
    """The landing report inside a quality run: the working tree vs the base.

    With a resolved leaf base the comparison and the added-file census bind
    the leaf's admitted base commit, so cuts and additions the leaf already
    committed stay reported; the base code listing proves deletions and the
    code change's own rename record compares moves. Without one the run's
    comparison base (the memory worktree's ``HEAD``, converted when the
    candidate needs it) stands in, with its tree as the added-file listing.
    Anything Git cannot read names that beside an empty section instead of
    failing the run.
    """

    candidate = scope.candidate
    memory_root = scope.memory_root
    code_root = scope.code_root
    leaf_base = scope.leaf_base
    candidate_cards = {
        path: data for path, data in candidate.files.items() if is_landing_card(path)
    }
    candidate_listing = walk_candidate_listing(memory_root)
    code_has_file = CodeDirectory(label="landing code", root=code_root).has_file
    if leaf_base is not None:
        if leaf_base.cards is None:
            return check_landing_report(
                LandingReportInputs(
                    candidate_cards=candidate_cards,
                    base_cards=None,
                    base_problem=leaf_base.problem
                    or "the leaf's admitted memory base cannot be read",
                    code_has_file=code_has_file,
                    candidate_listing=candidate_listing,
                    base_paths=leaf_base.paths or None,
                )
            )
        base_cards = {
            path: data for path, data in leaf_base.cards.files.items() if is_landing_card(path)
        }
        return check_landing_report(
            LandingReportInputs(
                candidate_cards=candidate_cards,
                base_cards=base_cards,
                code_has_file=code_has_file,
                base_code_has_file=(
                    None if leaf_base.code_paths is None else leaf_base.code_paths.__contains__
                ),
                renames=leaf_base.renames,
                candidate_listing=candidate_listing,
                base_paths=leaf_base.paths,
            )
        )
    base = scope.bases[0] if scope.bases else None
    if base is None:
        return check_landing_report(
            LandingReportInputs(
                candidate_cards={},
                base_cards=None,
                base_problem=scope.base_problem
                or "the memory worktree's HEAD cannot be read as a comparison base",
            )
        )
    base_cards = {path: data for path, data in base.files.items() if is_landing_card(path)}
    try:
        head_paths: Collection[str] | None = set(git_tree_entries(memory_root, "HEAD"))
    except (
        KnowledgeTreeReadError,
        ValueError,
        OSError,
        subprocess.SubprocessError,
    ):
        # A listing Git cannot read is a named gap, never a failure.
        head_paths = None
    report = check_landing_report(
        LandingReportInputs(
            candidate_cards=candidate_cards,
            base_cards=base_cards,
            code_has_file=code_has_file,
            candidate_listing=candidate_listing,
            base_paths=head_paths,
        )
    )
    if head_paths is None:
        report["reportOnlyFindings"].append(
            {
                "check": ADDED_FILES_CHECK,
                "path": ".",
                "code": BASE_UNREADABLE_CODE,
                "message": (
                    "the added-file base listing cannot be read from the memory"
                    " worktree's HEAD; no added file is reported as none"
                ),
            }
        )
    return report
