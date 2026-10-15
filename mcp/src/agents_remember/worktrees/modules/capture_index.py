"""Existing private-index location and trust-flag handling for worktree capture."""

from __future__ import annotations

from pathlib import Path

from agents_remember.kernel.git_command import copy_git_index, run_git, run_git_with_index
from agents_remember.kernel.memory_ledger import LEDGER_RELATIVE_PATH, MEMORY_CACHE_EXCLUDE


class _IndexCopyUnusable(Exception):
    """The capture from a copy of the worktree's index cannot answer; the full capture does."""


def _real_index(repo: Path) -> tuple[Path, Path]:
    """The worktree's own index file and its top-level directory.

    The index file is only ever read, as bytes, to be copied: no Git command of the capture reads
    it, so none can refresh it or leave a lock beside it.

    The locations are derived from the worktree's ``.git`` pointer without asking Git whenever
    the files prove the layout (MIK-R81 rule 7, F17): a main repository keeps its index at
    ``.git/index`` under its root, and a linked worktree keeps its at the ``index`` beside the
    administrative directory its ``.git`` file names, with the passed repository itself as the
    top level. A derived pair is used only when the index file exists and the top level is a
    directory; any other layout falls back to asking Git, so an exotic setup answers exactly as
    before, with one child, instead of answering from a guess.
    """

    derived = _derived_index(repo)
    if derived is not None:
        return derived
    located = run_git(
        repo, ["rev-parse", "--path-format=absolute", "--git-path", "index", "--show-toplevel"]
    )
    lines = located.stdout.splitlines()
    if located.returncode != 0 or len(lines) != 2 or not all(lines):
        raise _IndexCopyUnusable(f"the worktree's index cannot be located: {located.stderr}")
    return Path(lines[0]), Path(lines[1])


def _derived_index(repo: Path) -> tuple[Path, Path] | None:
    """The index locations from the ``.git`` pointer, or ``None`` when unprovable without Git."""

    try:
        dotgit = repo / ".git"
        if dotgit.is_dir():
            index, top = dotgit / "index", repo
        else:
            text = dotgit.read_text(encoding="utf-8").strip()
            if not text.startswith("gitdir: "):
                return None
            gitdir = Path(text[len("gitdir: ") :].strip())
            if not gitdir.is_absolute():
                return None
            index, top = gitdir / "index", repo
        if not index.is_file() or not top.is_dir():
            return None
        return index, top
    except OSError:
        return None


def _index_step(repo: Path, index: Path, args: list[str], *, paths: str | None = None) -> str:
    """One Git command against the private copy; a failure hands the capture to the full one."""

    result = run_git_with_index(repo, args, index, input_text=paths)
    if result.returncode != 0:
        raise _IndexCopyUnusable(f"git {args[0]} failed on the index copy: {result.stderr.strip()}")
    return result.stdout


def _index_copy_has_trust_flags(index: Path) -> bool | None:
    """Whether the private index copy carries ``assume-unchanged`` or ``skip-worktree``.

    Read in-process from the copy's own bytes, so a clean copy skips the ``ls-files`` listing
    entirely (MIK-R81 rule 7, F17). ``None`` means the bytes could not be walked (an index
    version other than 2, a truncation, an unreadable file): the caller falls back to the
    listing, exactly as without the scan. Only a fully walked version-2 index with no flag set
    reports ``False``.
    """

    try:
        data = index.read_bytes()
    except OSError:
        return None
    if len(data) < 12 or data[:4] != b"DIRC":
        return None
    if int.from_bytes(data[4:8], "big") != 2:
        return None
    count = int.from_bytes(data[8:12], "big")
    offset = 12
    for _ in range(count):
        flagged = _index_entry_trust_flag(data, offset)
        if flagged is None:
            return None
        found, offset = flagged
        if found:
            return True
    return False


def _index_entry_trust_flag(data: bytes, offset: int) -> tuple[bool, int] | None:
    """One index entry's trust flag and the next entry's offset, else ``None``.

    ``None`` is any shape this scan does not understand (a truncation, a missing name
    terminator): the caller falls back to the listing rather than trusting the walk.
    """

    if offset + 62 > len(data):
        return None
    flags = int.from_bytes(data[offset + 60 : offset + 62], "big")
    if flags & 0x8000:  # assume-unchanged
        return True, offset
    cursor = offset + 62
    if flags & 0x4000:  # extended flags present
        if cursor + 2 > len(data):
            return None
        if int.from_bytes(data[cursor : cursor + 2], "big") & 0x4000:  # skip-worktree
            return True, offset
        cursor += 2
    end = data.find(b"\x00", cursor)
    if end < 0:
        return None
    return False, (end + 1 - offset + 7) // 8 * 8 + offset


def _clear_trust_flags(top: Path, index: Path) -> None:
    """Clear ``assume-unchanged`` and ``skip-worktree`` on every entry of the private copy.

    It runs in the worktree's top-level directory: ``ls-files`` lists only the entries under the
    directory it runs in, and ``git add -A`` updates the whole worktree wherever it runs.
    """

    if _index_copy_has_trust_flags(index) is False:
        # The copy carries no trust flag: no entry can keep a blob over its file, so the listing
        # and the clears are skipped (MIK-R81 rule 7, F17).
        return
    listing = _index_step(top, index, ["ls-files", "-v", "-z"])
    flagged = [
        row[2:]
        for row in listing.split("\0")
        if len(row) > 2 and (row[0].islower() or row[0] == "S")
    ]
    if not flagged:
        return
    paths = "".join(f"{path}\0" for path in flagged)
    # One flag per command: ``update-index`` applies only the last of several such options.
    for option in ("--no-assume-unchanged", "--no-skip-worktree"):
        _index_step(top, index, ["update-index", "-z", option, "--stdin"], paths=paths)


def _excluded_pathspec(path: str) -> str:
    return MEMORY_CACHE_EXCLUDE if path == LEDGER_RELATIVE_PATH else f":(top,exclude){path}"


def _tree_from_index_copy(repo: Path, index: Path, exclude_paths: tuple[str, ...]) -> str:
    """Capture through a private copy of the worktree's own index, trusting Git's own evidence.

    The copy is brought to exactly the state the full capture starts from -- ``HEAD``'s entries and
    nothing else -- before any file is added, so both captures add the same files to the same
    entries and return the same tree for every worktree state:

    * **The copy keeps the index file's time** (:func:`copy_git_index`). Git re-checks by content
      every entry recorded in the second the index was written, and it recognises those entries by
      the index file's own time; a plain copy would lose that and keep the old blob of a same-size
      rewrite made in that second.
    * **A one-way merge of ``HEAD`` into the copy** (``read-tree -m -i HEAD``) leaves ``HEAD``'s
      entries: an entry whose path and content equal ``HEAD``'s keeps the file times the index
      recorded, and every other entry -- a staged change, a staged addition, a removal -- becomes
      ``HEAD``'s entry with no file times, or is dropped. A staged path that is ignored and not in
      ``HEAD`` is therefore not in the tree, exactly as in the full capture. ``-i`` keeps the merge
      from consulting the working tree, which the capture reads only through ``git add``.
    * **No entry is trusted over its file.** ``assume-unchanged`` and ``skip-worktree`` tell
      ``git add`` to keep the index's blob whatever the file holds; a kept entry carries them over
      from the real index, so both are cleared on the copy. The real index keeps them.

    Whatever is not proven unchanged by the recorded file times is hashed by ``git add``, as in the
    full capture. An index whose file times are stale costs what the full capture costs.

    **What trusting Git's evidence leaves out.** A file whose recorded times still match is not
    read, so a conversion rule that changed after the file was recorded -- a ``.gitattributes``
    line, ``core.autocrlf``, a filter -- is not applied to it again. That is what ``git add -A``
    does on the worktree's own index, and therefore what a commit made from that index records;
    the full capture converted such a file again and so named a tree the index does not stage.
    Once the file itself is touched, both captures read and convert it.
    """

    real_index, top = _real_index(repo)
    try:
        copy_git_index(real_index, index)
    except OSError as error:
        raise _IndexCopyUnusable(f"the worktree's index cannot be copied: {error}") from error
    _index_step(repo, index, ["read-tree", "-m", "-i", "HEAD"])
    _clear_trust_flags(top, index)
    if exclude_paths:
        _index_step(repo, index, ["update-index", "--force-remove", "--", *exclude_paths])
    _index_step(repo, index, _add_all_args(exclude_paths))
    tree = _index_step(repo, index, ["write-tree"]).strip()
    return tree


def _add_all_args(exclude_paths: tuple[str, ...]) -> list[str]:
    args = ["add", "-A"]
    if exclude_paths:
        args.extend(["--", ".", *map(_excluded_pathspec, exclude_paths)])
    return args
