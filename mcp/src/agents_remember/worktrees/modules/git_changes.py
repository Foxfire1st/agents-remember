"""Native NUL-delimited file statuses and counts for one exact Git range."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from agents_remember.kernel import filesystem
from agents_remember.worktrees.modules.git import _nul_git as _native_nul_git
from agents_remember.worktrees.modules.git import _nul_records


def _nul_git(repo: Path, args: list[str]) -> str:
    # Change-set reads preserve the real index generation even for identical-byte replacements.
    return _native_nul_git(repo, ["-c", "diff.autoRefreshIndex=false", *args])


def _name_status_letters(
    repo: Path, rng: list[str], observe: Callable[[list[str]], None] | None = None
) -> dict[str, str]:
    """Each changed path's status letter, from one NUL-delimited ``--name-status`` read.

    ``-z`` is what makes the pairing unambiguous: a record is the status and then its path, and a
    rename or copy status is followed by **two** path fields -- the source and the destination. The
    line-oriented form cannot be split on tabs without also splitting a name that contains one, and
    reading it as lines is how Git's quoting of such a name reached a caller as a different path.
    The reported path is the destination, which is where the change lands. A record whose fields do
    not fit its own status is a refusal rather than a path dropped from the inventory in silence.
    """

    records = _nul_records(_nul_git(repo, ["diff", "--name-status", "-z", "--find-renames", *rng]))
    letters: dict[str, str] = {}
    paths: list[str] = []
    cursor = 0
    while cursor < len(records):
        letter = records[cursor][:1]
        cursor += 1
        if letter in {"R", "C"}:
            if cursor + 1 >= len(records):
                raise RuntimeError(
                    "git reported a rename or copy without both of its paths, so no path's status "
                    "could be paired with it"
                )
            letters[records[cursor + 1]] = letter
            paths.extend(records[cursor : cursor + 2])
            cursor += 2
            continue
        if cursor >= len(records):
            raise RuntimeError(
                "git reported a status without the path it belongs to, so the change inventory "
                "would have silently omitted that path"
            )
        letters[records[cursor]] = letter
        paths.append(records[cursor])
        cursor += 1
    if observe is not None:
        observe(paths)
    return letters


def _numstat_rows(repo: Path, rng: list[str]) -> list[tuple[str, int | None, int | None]]:
    """``(path, insertions, deletions)`` per file, from one NUL-delimited ``--numstat`` read.

    A record is ``insertions<TAB>deletions<TAB>path``; for a rename or a copy the path field is
    **empty** and the source and the destination follow as two further records, so the pairing is
    positional. That is why a name containing a tab needs no re-splitting here and no ``a => b``
    reconstruction, and why the reported path is the destination. Binary files report ``-`` for
    both counts and keep it as ``None`` rather than as a measured zero.
    """

    records = _nul_records(_nul_git(repo, ["diff", "--numstat", "-z", "--find-renames", *rng]))
    rows: list[tuple[str, int | None, int | None]] = []
    cursor = 0
    while cursor < len(records):
        fields = records[cursor].split("\t", 2)
        cursor += 1
        if len(fields) != 3:
            raise RuntimeError(
                f"git's numstat record {fields!r} is not insertions, deletions and one path"
            )
        insertions, deletions, path = fields
        if not path:
            if cursor + 1 >= len(records):
                raise RuntimeError(
                    "git reported a rename or copy without both of its paths, so its counts could "
                    "not be paired with a path"
                )
            path = records[cursor + 1]
            cursor += 2
        rows.append(
            (
                path,
                None if insertions == "-" else int(insertions),
                None if deletions == "-" else int(deletions),
            )
        )
    return rows


def changed_files_with_counts(
    repo: Path,
    base: str,
    head: str | None = None,
    *,
    observe: Callable[[list[str]], None] | None = None,
) -> list[dict[str, Any]]:
    """Per-file change-set ``base``->``head`` (``head=None`` -> the working tree).

    Each entry is ``{path, insertions, deletions, status}``. Unlike
    :func:`changed_worktree_paths` / :func:`committed_changed_paths` this KEEPS
    deletions (status ``D``) and reports per-file insertion/deletion counts (``None``
    for binary files, whose numstat shows ``-``); in working-tree mode untracked files
    are reported as additions (status ``A``). ``status`` is the git letter
    (``A``/``M``/``D``/``R``/``C``).

    Both reads are NUL-delimited and nothing is rewritten afterwards, so ``path`` is the address
    of the file it names: a name holding a tab, a newline or a backslash is reported as itself
    rather than as a quoted or separator-substituted variant that resolves to nothing. Git's
    ``core.quotePath`` quoting never reaches a caller here, because ``-z`` is precisely the
    interface that turns it off. Records are sorted by path.
    """
    rng = [base] if head is None else [base, head]
    letters = _name_status_letters(repo, rng, observe)
    out: list[dict[str, Any]] = [
        {
            "path": path,
            "insertions": insertions,
            "deletions": deletions,
            "status": letters.get(path, "M"),
        }
        for path, insertions, deletions in _numstat_rows(repo, rng)
    ]
    if head is None:
        for rel in _nul_records(
            _nul_git(repo, ["ls-files", "--others", "--exclude-standard", "-z"])
        ):
            if filesystem.is_file(repo / rel):
                line_count = len((repo / rel).read_text(errors="replace").splitlines())
                out.append({"path": rel, "insertions": line_count, "deletions": 0, "status": "A"})
    return sorted(out, key=lambda entry: str(entry["path"]))
