"""Investigation reads at a comparison's exact trees (MIK-R102 rules 3, 5, 7, 8, 12; MIK-R211 v4).

The operand is the comparison's own tree, never something the caller can choose:

* a side resolves through the comparison record; a memory base with a conversion applied resolves to
  the **effective converted tree**, with the stored tree named beside it (rules 1, 3, 5);
* a symbolic revision, a branch, a URL, an absolute path, a ``..`` segment or Git pathspec magic is
  refused by name rather than repaired (rule 3);
* the object is read whole through ``git cat-file --batch`` and the read answers in full: no byte
  window, page or scan bound shapes it (MIK-R211 v4 rule 3). A line range is an optional offset and
  length into that whole object, and the receipt states the object's true size and line count;
* a range that runs past the object's end ends at its last line and is complete, and a start line
  past the end is a measured range absence with the real line count (rules 3, 12);
* a symlink inside the tree is inside the tree, so it is followed -- through chains and through a
  linked directory, guarded only by the set of paths already visited -- and the entry it ends at
  is read exactly as a regular one; only a link whose target lies outside the tree is refused, as
  a path outside the tree naming the link and its target (ruling K3);
* relationships are read through the landed owners' own schemas -- family members, record links and
  supersession, sidecar realizations and proofs, history rows -- with their direction, revision,
  origin leaf and attempt, and a lookup that failed is not a measured absence (rule 5);
* a claim basis is resolved against the packet that cites it, so a recorded authority rests on an
  object the packet actually holds (rules 7, 8).

The refusals that stay are about the request, never its size: a path outside the tree, an
unreadable entry and a wrong-request operand; a binary object is stated as binary.

Nothing here writes, starts a process of the compared repository, runs a test or launches a role.
"""

from __future__ import annotations

import contextlib
import posixpath
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.application.review_evidence_packet import comparison_reference
from agents_remember.kernel.git_command import git_environment, run_git
from agents_remember.models.knowledge.review_evidence_read import (
    EVIDENCE_SIDES,
    EvidenceReadReceipt,
)
from agents_remember.models.knowledge.review_trees import ReviewTreeComparisonRecord

__all__ = [
    "SideOperand",
    "read_bound_source",
    "read_whole_source",
    "side_operand",
]

# How much of the blob stream one pipe read takes. It is the stream's granularity, not a bound: a
# read takes as many of these as the object holds.
_CHUNK = 64 * 1024

_SUBMODULE_MODE = "160000"
_SYMLINK_MODE = "120000"
_TREE_MODE = "040000"
# How far into an object a NUL byte marks it binary (Git's own heuristic).
_BINARY_SNIFF_BYTES = 8000
_RECORD_FILE = re.compile(r"^(?P<id>[A-Z]{2,4}-[A-Z0-9]{4,})-[^/]*\.json$")
_PATH_ADDRESS = re.compile(
    r"^(?P<side>code-base|code-candidate|memory-base|memory-candidate):"
    r"(?P<path>[^#]+?)(?:#L(?P<start>[1-9][0-9]*)(?:-L?(?P<end>[1-9][0-9]*))?)?$"
)
_URL_PREFIXES = ("http://", "https://", "file://", "ssh://", "git://")


@dataclass(frozen=True)
class SideOperand:
    """The repository and *effective* tree one side names, and the stored tree beside it (rule 1)."""

    side: str
    repository: Path
    tree: str
    stored_tree: str | None = None

    @property
    def converted(self) -> bool:
        """Whether this operand is a converted base, whose stored tree is a different object."""

        return self.stored_tree is not None and self.stored_tree != self.tree


@dataclass(frozen=True)
class EvidenceAddress:
    """One claim reference resolved to the operands the same read takes (rule 8)."""

    side: str
    path: str
    start_line: int | None = None
    end_line: int | None = None


def side_operand(
    record: ReviewTreeComparisonRecord, side: str
) -> SideOperand | EvidenceReadReceipt:
    """The effective operand one side names, or the refusal that side earns (rules 1, 3, 5)."""

    if side not in EVIDENCE_SIDES:
        return _refused(
            record,
            side,
            "unknown-side",
            (
                f"the requested side {side!r} is not one of this comparison's four operands "
                f"({', '.join(EVIDENCE_SIDES)}); a branch, a working checkout and another "
                "comparison are not alternate operands"
            ),
        )
    chosen = {
        "code-base": record.code_base,
        "code-candidate": record.code_candidate,
        "memory-base": record.memory_base,
        "memory-candidate": record.memory_candidate,
    }[side]
    if side == "memory-base" and record.converted_base is not None:
        # The comparison compared the converted tree (rule 1); the stored tree stays named.
        return SideOperand(
            side=side,
            repository=Path(chosen.repository),
            tree=record.converted_base.tree,
            stored_tree=chosen.tree,
        )
    return SideOperand(side=side, repository=Path(chosen.repository), tree=chosen.tree)


# --- one read -----------------------------------------------------------------------------------


def read_bound_source(
    record: ReviewTreeComparisonRecord,
    side: str,
    path: str,
    *,
    start_line: int | None = None,
    end_line: int | None = None,
) -> EvidenceReadReceipt:
    """Read one path at one bound side, in full, from ``start_line`` to ``end_line`` (rules 3, 12).

    No ``end_line`` reads to the object's end; no ``start_line`` starts at its first line. The whole
    object is examined either way, so the receipt states the object's true line count beside the
    lines it returns.
    """

    found = _locate(record, side, path, start_line=start_line, end_line=end_line)
    if isinstance(found, EvidenceReadReceipt):
        return found
    return _through(
        found.through, _entry_state(found) or _range_receipt(found, start_line or 1, end_line)
    )


def read_whole_source(
    record: ReviewTreeComparisonRecord, side: str, path: str
) -> EvidenceReadReceipt:
    """Read one path at one bound side to its end, whole (rules 3, 12; MIK-R211 v4 rule 3).

    The operand, the refusal of an unreadable path, the links followed, the entry's own kind and the
    binary and empty-object states are exactly :func:`read_bound_source`'s. What differs is the answer: its
    ``text`` is the object's every byte, trailing newline included, so a collection whose rows are
    the population of one file -- a changed history file's judgments -- counts them all.
    """

    found = _locate(record, side, path, start_line=None, end_line=None)
    if isinstance(found, EvidenceReadReceipt):
        return found
    return _through(found.through, _entry_state(found) or _whole_receipt(found))


@dataclass(frozen=True)
class _Object:
    """One located entry and every byte of its object, with the facts every receipt states."""

    entry: _Entry
    payload: bytes
    base: dict[str, Any]
    # The ``link → target`` hops the read went through, when the requested path was a link.
    through: str | None = None

    @property
    def text(self) -> str | None:
        """The object as lossless UTF-8 text, or ``None`` when it is binary."""

        if b"\x00" in self.payload[:_BINARY_SNIFF_BYTES]:
            return None
        try:
            return self.payload.decode("utf-8")
        except UnicodeDecodeError:
            return None


def _locate(
    record: ReviewTreeComparisonRecord,
    side: str,
    path: str,
    *,
    start_line: int | None,
    end_line: int | None,
) -> _Object | EvidenceReadReceipt:
    """The entry one read names and its whole object, or the receipt that ends the read."""

    operand = side_operand(record, side)
    if isinstance(operand, EvidenceReadReceipt):
        return operand
    refusal = _path_refusal(record, side, path, start_line=start_line, end_line=end_line)
    if refusal is not None:
        return refusal
    followed = _follow(operand.repository, operand.tree, path)
    if isinstance(followed, _Unfollowed):
        return _unfollowed(record, operand, path, followed)
    found = followed.entry
    if found.mode in {_TREE_MODE, _SUBMODULE_MODE} or found.kind in {"tree", "commit"}:
        return _through(followed.through, _not_a_file(record, operand, path, found))
    payload = _fetch_blob(operand.repository, found.object_id)
    if isinstance(payload, str):
        return _unavailable(record, operand, path, payload)
    return _Object(
        entry=found,
        payload=payload,
        base=_base_fields(record, operand, path, found.object_id, len(payload)),
        through=followed.through,
    )


def _entry_state(found: _Object) -> EvidenceReadReceipt | None:
    """The receipt of an entry that holds no text lines -- an empty or a binary object."""

    if not found.payload:
        return _measured_empty(found.base)
    if found.text is None:
        return _binary(found.base, len(found.payload))
    return None


def _range_receipt(found: _Object, start: int, end_line: int | None) -> EvidenceReadReceipt:
    """The requested lines of a whole text object, with its true line count (rules 3, 12)."""

    text = found.text or ""
    lines = text.split("\n")
    if text.endswith("\n"):
        lines.pop()
    total = len(lines)
    if start > total:
        return EvidenceReadReceipt(
            state="absent",
            requested_range=(start, end_line or start),
            examined_bytes=len(found.payload),
            object_lines=total,
            detail=(
                f"the object holds {total} line(s), so line {start} lies past its end: a measured "
                "range absence, not an empty file"
            ),
            **found.base,
        )
    last = total if end_line is None else min(end_line, total)
    returned = "\n".join(lines[start - 1 : last])
    start_byte = sum(len(line.encode()) + 1 for line in lines[: start - 1])
    returned_bytes = len(returned.encode())
    ended = last < total or text.endswith("\n")
    detail = (
        f"requested {start}-{end_line or 'end of object'}; returned {start}-{last} of the object's "
        f"{total} line(s) and {len(found.payload)} byte(s)"
    )
    return EvidenceReadReceipt(
        state="present",
        requested_range=(start, end_line or total),
        returned_range=(start, last),
        start_byte=start_byte,
        next_byte=start_byte + returned_bytes + (1 if ended else 0),
        examined_bytes=len(found.payload),
        returned_bytes=returned_bytes,
        remaining_state="known",
        remaining_lines=0,
        object_lines=total,
        text=returned,
        detail=(
            detail
            + f"; the object's last line is line {total} and the requested range runs past it"
            if end_line is not None and end_line > total
            else detail + "; the requested source is complete"
        ),
        **found.base,
    )


def _whole_receipt(found: _Object) -> EvidenceReadReceipt:
    """Every byte of a whole text object, with its own size and line count beside them."""

    text = found.text or ""
    total = text.count("\n") + (0 if text.endswith("\n") else 1)
    return EvidenceReadReceipt(
        state="present",
        examined_bytes=len(found.payload),
        returned_bytes=len(found.payload),
        next_byte=len(found.payload),
        object_lines=total,
        text=text,
        detail=f"the whole object was read: {len(found.payload)} byte(s) in {total} line(s)",
        **found.base,
    )


def _through(through: str | None, receipt: EvidenceReadReceipt) -> EvidenceReadReceipt:
    """The receipt, saying the read reached its entry through the link chain when it did."""

    if through is None:
        return receipt
    return receipt.model_copy(
        update={"detail": f"read through the link {through}: {receipt.detail}"}
    )


# --- symlinks inside the tree (ruling K3) -------------------------------------------------------


@dataclass(frozen=True)
class _Followed:
    """The entry a path names once every link inside the tree is followed, and the hops taken."""

    entry: _Entry
    chain: tuple[str, ...]

    @property
    def through(self) -> str | None:
        """The ``link → target`` hops, or ``None`` when the path named its entry directly."""

        return " → ".join(self.chain) if len(self.chain) > 1 else None


@dataclass(frozen=True)
class _Unfollowed:
    """Why a path names no readable entry: ``unavailable``, ``absent`` or ``outside`` the tree."""

    state: str
    detail: str


def _follow(repository: Path, tree: str, path: str) -> _Followed | _Unfollowed:
    """Follow ``path`` through every link inside the tree; the visited set is the only guard."""

    chain = [path]
    visited = {path}
    while True:
        step = _step(repository, tree, chain)
        if isinstance(step, _Followed):
            return step
        if isinstance(step, _Unfollowed):
            if len(chain) == 1:
                return step
            return _Unfollowed(step.state, f"through the links {' → '.join(chain)}: {step.detail}")
        if step in visited:
            return _Unfollowed(
                "unavailable",
                f"{' → '.join([*chain, step])} returns to {step}: a symlink loop, so no entry "
                "ends the chain and no bytes were read",
            )
        visited.add(step)
        chain.append(step)


def _step(repository: Path, tree: str, chain: list[str]) -> _Followed | _Unfollowed | str:
    """One hop: the entry the last path names, the path a link there leads to, or why neither."""

    current = chain[-1]
    if current == ".":
        return _Followed(_Entry(mode=_TREE_MODE, kind="tree", object_id=tree), tuple(chain))
    found = _tree_entry(repository, tree, current)
    if isinstance(found, str):
        return _Unfollowed("unavailable", found)
    if found is None:
        return _linked_directory(repository, tree, current)
    if found.mode == _SYMLINK_MODE:
        return _link_target(repository, current, found.object_id, "")
    return _Followed(found, tuple(chain))


def _linked_directory(repository: Path, tree: str, path: str) -> _Unfollowed | str:
    """A path the tree does not hold continues through its leading directory when that is a link."""

    parts = path.split("/")
    for depth in range(1, len(parts)):
        prefix = "/".join(parts[:depth])
        found = _tree_entry(repository, tree, prefix)
        if isinstance(found, str):
            return _Unfollowed("unavailable", found)
        if found is not None and found.mode == _SYMLINK_MODE:
            return _link_target(repository, prefix, found.object_id, "/".join(parts[depth:]))
        if found is None or found.kind != "tree":
            break
    return _Unfollowed("absent", _NO_ENTRY)


def _link_target(repository: Path, link: str, object_id: str, rest: str) -> _Unfollowed | str:
    """The tree path one link leads to, resolved against the link's own directory (ruling K3)."""

    payload = _fetch_blob(repository, object_id)
    if isinstance(payload, str):
        return _Unfollowed("unavailable", payload)
    target = payload.decode("utf-8", "replace")
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(link), target, rest))
    if posixpath.isabs(target) or resolved == ".." or resolved.startswith("../"):
        return _Unfollowed(
            "outside",
            f"{link} is a symlink to {target}, a path outside the tree: a link that leaves the "
            "comparison's tree is refused rather than followed",
        )
    return resolved


def _unfollowed(
    record: ReviewTreeComparisonRecord, operand: SideOperand, path: str, stop: _Unfollowed
) -> EvidenceReadReceipt:
    """An outside link is refused as a path outside the tree; the rest is absent or unavailable."""

    if stop.state == "outside":
        return _refused(record, operand.side, "path-escape", stop.detail)
    if stop.state == "absent":
        return _absent(record, operand, path, stop.detail)
    return _unavailable(record, operand, path, stop.detail)


# --- the blob stream ----------------------------------------------------------------------------


def _fetch_blob(repository: Path, object_id: str) -> bytes | str:
    """One blob's every byte through ``git cat-file --batch``, or why it could not be read."""

    try:
        process = subprocess.Popen(
            ["git", "cat-file", "--batch"],
            cwd=repository,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=git_environment(),
        )
    except OSError as failure:
        return f"Git could not be run for object {object_id} ({failure})"
    try:
        return _stream_blob(process, object_id)
    except OSError as failure:
        return f"the blob stream failed ({failure})"
    finally:
        _dispose(process)


def _stream_blob(process: subprocess.Popen[bytes], object_id: str) -> bytes | str:
    """Ask the stream for one object and take its bytes in ``_CHUNK`` steps until its end."""

    assert process.stdin is not None and process.stdout is not None
    process.stdin.write(f"{object_id}\n".encode())
    process.stdin.flush()
    header = process.stdout.readline()
    fields = header.split()
    if len(fields) != 3 or fields[1] != b"blob":
        return (
            f"the object {object_id} is not a blob this repository can read "
            f"({header.decode('utf-8', 'replace').strip() or 'no header'})"
        )
    try:
        size = int(fields[2])
    except ValueError:
        return f"the object {object_id} reported no readable size"
    chunks: list[bytes] = []
    taken = 0
    while taken < size:
        chunk = process.stdout.read(min(_CHUNK, size - taken))
        if not chunk:
            return f"the blob stream for {object_id} ended after {taken} of its {size} byte(s)"
        chunks.append(chunk)
        taken += len(chunk)
    return b"".join(chunks)


def _dispose(process: subprocess.Popen[bytes]) -> None:
    """Stop one ``cat-file`` child and close the pipes this read opened."""

    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:  # pragma: no cover - defensive
        process.kill()
        process.wait(timeout=5)
    for stream in (process.stdin, process.stdout):
        if stream is not None:
            with contextlib.suppress(OSError):  # a pipe the child already closed
                stream.close()


# --- entry states -------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Entry:
    """One ``ls-tree`` record: Git's mode and type for the path, and the object it names."""

    mode: str
    kind: str
    object_id: str


def _base_fields(
    record: ReviewTreeComparisonRecord,
    operand: SideOperand,
    path: str,
    object_id: str,
    size: int,
) -> dict[str, Any]:
    return {
        "side": operand.side,
        "comparison_number": record.number,
        "comparison_reference": comparison_reference(record),
        "repository": str(operand.repository),
        "tree": operand.tree,
        "stored_tree": operand.stored_tree,
        "path": path,
        "object_id": object_id,
        "object_size": size,
    }


def _measured_empty(base: dict[str, Any]) -> EvidenceReadReceipt:
    return EvidenceReadReceipt(
        state="present",
        text="",
        detail=(
            "measured empty: the tree holds an entry at this path and its object is zero bytes; "
            "this is not a missing path and no other tree was read"
        ),
        **base,
    )


def _binary(base: dict[str, Any], size: int) -> EvidenceReadReceipt:
    return EvidenceReadReceipt(
        state="binary",
        detail=(
            f"this endpoint holds {size} byte(s) that are not lossless UTF-8 text, so no text form "
            "of the content exists here; the exact object identity and size are stated instead"
        ),
        **base,
    )


def _not_a_file(
    record: ReviewTreeComparisonRecord, operand: SideOperand, path: str, entry: _Entry
) -> EvidenceReadReceipt:
    """A tree is not a file at this endpoint; a submodule is a pointer with no bytes of its own."""

    if entry.mode == _TREE_MODE or entry.kind == "tree":
        return _unavailable(
            record, operand, path, "this path is a tree at this endpoint rather than a file"
        )
    object_id = entry.object_id
    return EvidenceReadReceipt(
        state="submodule",
        side=operand.side,
        comparison_number=record.number,
        comparison_reference=comparison_reference(record),
        repository=str(operand.repository),
        tree=operand.tree,
        stored_tree=operand.stored_tree,
        path=path,
        object_id=object_id,
        detail=(
            f"this endpoint's entry is a submodule pointer to {object_id}: the entry has no file "
            "bytes of its own and its working tree is not read"
        ),
    )


_NO_ENTRY = (
    "the requested tree holds no entry at this path: a measured fact about this side, not an empty "
    "file and not a failed read"
)


def _absent(
    record: ReviewTreeComparisonRecord, operand: SideOperand, path: str, detail: str
) -> EvidenceReadReceipt:
    """A lookup that answered with no entry: a measured absence at this side."""

    return EvidenceReadReceipt(
        state="absent",
        side=operand.side,
        comparison_number=record.number,
        comparison_reference=comparison_reference(record),
        repository=str(operand.repository),
        tree=operand.tree,
        stored_tree=operand.stored_tree,
        path=path,
        detail=detail,
    )


def _unavailable(
    record: ReviewTreeComparisonRecord,
    operand: SideOperand,
    path: str,
    detail: str,
) -> EvidenceReadReceipt:
    return EvidenceReadReceipt(
        state="unavailable",
        side=operand.side,
        comparison_number=record.number,
        comparison_reference=comparison_reference(record),
        repository=str(operand.repository),
        tree=operand.tree,
        stored_tree=operand.stored_tree,
        path=path,
        detail=detail,
    )


def _refused(
    record: ReviewTreeComparisonRecord, side: str, code: str, detail: str
) -> EvidenceReadReceipt:
    return EvidenceReadReceipt(
        state="refused",
        side=side,
        comparison_number=record.number,
        comparison_reference=comparison_reference(record),
        detail=detail,
        refusal_code=code,
    )


# --- the requested operand ----------------------------------------------------------------------


def _path_refusal(
    record: ReviewTreeComparisonRecord,
    side: str,
    path: str,
    *,
    start_line: int | None,
    end_line: int | None,
) -> EvidenceReadReceipt | None:
    """Why this read's operand or range is inadmissible, or ``None`` when it is admissible."""

    reason = _path_problem(path)
    if reason is not None:
        return _refused(record, side, reason[0], reason[1])
    for bound in (start_line, end_line):
        if bound is not None and bound < 1:
            return _refused(
                record, side, "range-invalid", "a line range is one-based and addresses no line 0"
            )
    if start_line is not None and end_line is not None and end_line < start_line:
        return _refused(record, side, "range-invalid", "the requested range ends before it starts")
    return None


def _path_problem(path: str) -> tuple[str, str] | None:
    """The refusal code and sentence one path spelling earns, or ``None`` when it is an address."""

    if not path or "\x00" in path:
        return ("path-escape", "the requested path is empty or holds a NUL byte")
    if path.lower().startswith(_URL_PREFIXES):
        return (
            "arbitrary-url",
            "the requested operand is a URL: this surface reads the comparison's own Git trees and "
            "fetches no external material",
        )
    if path.startswith("/") or path.startswith(":") or ".." in path.split("/"):
        return (
            "path-escape",
            (
                f"the requested path {path!r} is not confined to the tree: an absolute path, a '..' "
                "segment and a leading ':' (Git pathspec magic) are all refused"
            ),
        )
    if re.match(r"^[A-Za-z][\w./-]*:", path):
        return (
            "symbolic-revision",
            (
                f"the requested operand {path!r} is a revision:path spelling; a symbolic HEAD, a "
                "branch name and a working checkout are not alternate operands"
            ),
        )
    return None


def _tree_entry(repository: Path, tree: str, path: str) -> _Entry | str | None:
    """One path's entry in one tree, the sentence saying why it could not be read, or ``None``."""

    try:
        result = run_git(repository, ["ls-tree", "-z", tree, "--", f":(literal){path}"])
    except OSError as failure:
        return (
            f"the entry at this path could not be looked up in tree {tree} because Git could not be "
            f"run ({failure}), so this side's content is unknown rather than absent"
        )
    if result.returncode != 0:
        return (
            f"the entry at this path could not be looked up in tree {tree} (git ls-tree exited "
            f"{result.returncode}): the tree may be gone, and no other tree was substituted"
        )
    record = result.stdout.split("\0", 1)[0]
    if not record:
        return None
    header, _separator, named = record.partition("\t")
    fields = header.split()
    if len(fields) < 3 or named != path:
        return (
            "the tree lookup answered with a record this surface does not read as the requested "
            "path's entry, so this side's content is unknown rather than another entry's bytes"
        )
    return _Entry(mode=fields[0], kind=fields[1], object_id=fields[2])
