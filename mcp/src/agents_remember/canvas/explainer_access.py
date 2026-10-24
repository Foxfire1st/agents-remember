"""What the explainer may read, and the refusals that bound it (MIK-R186 rule 3).

The seat's read authority is three surfaces and no others:

* ``ExplainerReads`` -- ``list``, ``search`` and ``read`` over the bound snapshot, the leaf's
  retained page revisions and ``notes/reference/<leaf>/``;
* ``ExplainerGit`` -- six read-only subcommands (``log``, ``show``, ``diff``, ``ls-tree``,
  ``cat-file``, ``rev-parse``) on the code and memory repositories. The argv is composed here, so
  an external diff program, ``--textconv``, ``--filters``, a ``-c`` override, a pager,
  ``--output``, ``diff --no-index`` and a worktree path have no spelling that reaches Git; the
  kernel's runner adds ``GIT_OPTIONAL_LOCKS=0``, so nothing writes an index, a ref, a worktree file
  or a config. History read beyond the bound trees informs and is not evidence;
* ``ExplainerKnowledge`` -- ``knowledge_read``'s views at the bound memory tree, through the tree
  reader that opens a Git tree by id (``memory/knowledge_index/cache.py``).

Every answer names its binding: ``#<n>``, or ``planned`` with its two tip commits. A path outside
the admitted surfaces, a symlinked component on the way, a surface this module does not serve and
a Git option or object name it will not pass are refused *by name*, answered as data
(``state: "refused"`` with a typed ``refusal``), never as a crash. That shape, the binding face,
and the seat and binding-claim checks live here too, because every surface answers through them.
"""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.application.review_frame_store import frames_area
from agents_remember.canvas.explainer_answers import answered, refusal  # re-exported: same objects
from agents_remember.canvas.explainer_binding import require_current_seat
from agents_remember.canvas.explainer_state import (
    ExplainerBinding,
    ExplainerRefused,
    ExplainerStateError,
    binding_digest,
    read_binding,
    same_binding,
)
from agents_remember.canvas.paths import guard_path, path_segment
from agents_remember.canvas.presenter_store import (
    PresenterRefused,
    digest_bytes,
    read_bounded,
)
from agents_remember.kernel.git_command import DIFF_PREFIX_OPTIONS, run_git
from agents_remember.memory.knowledge_index.cache import (
    KnowledgeIndexCache,
    default_cache_directory,
)
from agents_remember.memory.knowledge_index.query import KnowledgeIndex
from agents_remember.models.knowledge.review_frame import FrameBinding

__all__ = [
    "ExplainerGit",
    "ExplainerKnowledge",
    "ExplainerReads",
    "KnowledgeQuery",
    "answered",
    "binding_claim",
    "binding_face",
    "object_name",
    "refusal",
    "relative_parts",
    "require_binding",
    "require_claim",
    "require_seat",
    "tree_path",
]

MAX_READ_BYTES = 1024 * 1024
READ_CHUNK_CHARACTERS = 12_000
MAX_LIST_ENTRIES = 400
MAX_LIST_DEPTH = 4
#: A search walks deeper than a listing shows, but never unbounded.
MAX_WALK_DEPTH = 24
MAX_SEARCH_FILES = 200
MAX_SEARCH_MATCHES = 50
MAX_SEARCH_BYTES = 4 * 1024 * 1024
MAX_QUERY_CHARACTERS = 200
MAX_GIT_CHARACTERS = 200_000
MAX_GIT_LIMIT = 200
MAX_KNOWLEDGE_ROWS = 200
REFERENCE_DIRECTORY = ("notes", "reference")
SNAPSHOT_SURFACES = ("code", "memory")
TASK_SURFACES = ("pages", "reference")

_SNAPSHOT_NAME = re.compile(r"^snapshot-[0-9]+$")
_OBJECT_NAME = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._/^~@{}-]*$")
_DRIVE = re.compile(r"^[A-Za-z]:")
#: Options that run another program, read a worktree path or write an output elsewhere.
_FOREIGN_OPTION = re.compile(
    r"^(?:--(?:textconv|filters|ext-diff|no-index|paginate|open-files-in-pager|config-env(?:=|$)"
    r"|output(?:=|$)|exec-path|git-dir|work-tree|namespace|super-prefix|show-toplevel|show-cdup"
    r"|show-prefix|git-common-dir|absolute-git-dir|git-path|path-format)|-c$|-C$)"
)


# MIK-R204 @ v1 rule 1: the answer boundary lives in a light module now. The names below are the *same
# objects* imported from there, so an existing caller's import and a declaration owner's import cannot
# diverge; `tests/test_explainer_answers.py` asserts that identity rather than the spelling.


def binding_claim(binding: ExplainerBinding) -> str:
    """The full comparison-identity token of one binding, including planned tips (MIK-R186 rule 3)."""

    frame = binding.binding
    if binding.planned is not None:
        return (
            f"planned@{binding.planned.code_tip}:{binding.planned.memory_tip}:"
            f"{binding.planned.code_tree}:{binding.planned.memory_tree}"
        )
    return (
        f"{frame.comparison_owner}#{frame.comparison_number}@"
        f"{frame.code_base_tree}:{frame.code_candidate_tree}:"
        f"{frame.compared_memory_base_tree}:{frame.memory_candidate_tree}"
    )


def binding_face(binding: ExplainerBinding) -> dict[str, Any]:
    """How one answer names its binding: ``#<n>``, or ``planned`` with its two tips (rule 3)."""

    frame = binding.binding
    face: dict[str, Any] = {
        "label": "planned" if binding.planned is not None else f"#{frame.comparison_number}",
        "owner": frame.comparison_owner,
        "number": frame.comparison_number,
        "reference": frame.comparison_reference,
        "digest": binding_digest(binding),
        "claim": binding_claim(binding),
        "snapshot": binding.snapshot,
        "codeCandidateTree": frame.code_candidate_tree,
        "memoryCandidateTree": frame.memory_candidate_tree,
    }
    if binding.planned is not None:
        face["planned"] = {
            "codeTip": binding.planned.code_tip,
            "memoryTip": binding.planned.memory_tip,
            "codeTree": binding.planned.code_tree,
            "memoryTree": binding.planned.memory_tree,
        }
    return face


def require_binding(workspace: Path) -> ExplainerBinding:
    """The environment's binding, or a named refusal: nothing is read without one."""

    try:
        binding = read_binding(workspace)
    except ExplainerStateError as error:
        raise ExplainerRefused("binding-unreadable", str(error)) from error
    if binding is None:
        raise ExplainerRefused(
            "explainer-missing",
            f"the leaf's environment at {workspace} has no binding.json; only a build writes one",
        )
    return binding


def require_seat(workspace: Path, seat: str | None) -> str:
    """The caller's identity, read at construction, must be this leaf's explainer (rule 5).

    The comparison itself lives in :func:`agents_remember.canvas.explainer_binding.require_current_seat`,
    so the seat a read is gated on at dispatch and the seat a grade or a report re-checks under the
    lock are one decision, not two spellings of it.
    """

    return require_current_seat(workspace, seat)


def _exact_claims(binding: ExplainerBinding) -> set[str]:
    """All exact full-identity claim spellings that uniquely identify ``binding``."""

    frame = binding.binding
    claims = {binding_digest(binding), binding_claim(binding)}
    if binding.planned is not None:
        tips = f"{binding.planned.code_tip}:{binding.planned.memory_tip}"
        full = f"{tips}:{binding.planned.code_tree}:{binding.planned.memory_tree}"
        claims.update(
            {
                f"planned@{tips}",
                f"planned:{tips}",
                f"planned:{full}",
                f"{frame.comparison_reference}@{tips}",
                f"{frame.comparison_reference}@{full}",
            }
        )
    else:
        trees = (
            f"{frame.code_base_tree}:{frame.code_candidate_tree}:"
            f"{frame.compared_memory_base_tree}:{frame.memory_candidate_tree}"
        )
        claims.add(f"{frame.comparison_reference}@{trees}")
    return claims


def _matches_model_claim(given: dict[str, Any], binding: ExplainerBinding) -> bool | None:
    frame = binding.binding
    try:
        parsed_env = ExplainerBinding.model_validate(given)
        return same_binding(parsed_env.binding, frame) and parsed_env.planned == binding.planned
    except ValueError:
        pass
    try:
        parsed_frame = FrameBinding.model_validate(given)
    except ValueError:
        return None
    if not same_binding(parsed_frame, frame):
        return False
    if binding.planned is None:
        return True
    return (
        parsed_frame.code_candidate_commit == binding.planned.code_tip
        and parsed_frame.memory_candidate_commit == binding.planned.memory_tip
    )


def _matches_face_claim(given: dict[str, Any], binding: ExplainerBinding) -> bool:
    frame = binding.binding
    base_matches = (
        given.get("owner") == frame.comparison_owner
        and given.get("number") == frame.comparison_number
        and given.get("reference") == frame.comparison_reference
        and given.get("codeCandidateTree") == frame.code_candidate_tree
        and given.get("memoryCandidateTree") == frame.memory_candidate_tree
    )
    if not base_matches:
        return False
    if binding.planned is None:
        return "planned" not in given
    planned = given.get("planned")
    return (
        isinstance(planned, dict)
        and planned.get("codeTip") == binding.planned.code_tip
        and planned.get("memoryTip") == binding.planned.memory_tip
        and planned.get("codeTree", binding.planned.code_tree) == binding.planned.code_tree
        and planned.get("memoryTree", binding.planned.memory_tree) == binding.planned.memory_tree
    )


def _matches_json_claim(stated: str, binding: ExplainerBinding) -> bool | None:
    """``True`` if JSON claim matches ``binding``, ``False`` if another binding, ``None`` if not a binding JSON."""

    if not stated.startswith("{"):
        return None
    try:
        given = json.loads(stated)
    except ValueError:
        return None
    if not isinstance(given, dict):
        return None
    if given.get("digest") == binding_digest(binding) or given.get("claim") in _exact_claims(
        binding
    ):
        return True
    model_match = _matches_model_claim(given, binding)
    if model_match is not None:
        return model_match
    face_keys = ("owner", "number", "reference", "planned", "digest", "comparison_number")
    return _matches_face_claim(given, binding) if any(k in given for k in face_keys) else None


def _looks_like_binding(stated: str) -> bool:
    return (
        stated == "planned"
        or stated.startswith(("planned@", "planned:", "#", "sha256:", "review-comparison:"))
        or stated.isdigit()
        or "#" in stated
    )


def require_claim(
    claim: str, binding: ExplainerBinding, *, prior: ExplainerBinding | None = None
) -> None:
    """Refuse a call made at a binding the environment has left (``binding-moved``)."""

    frame = binding.binding
    label = "planned" if binding.planned is not None else f"#{frame.comparison_number}"
    stated = claim.strip()
    if stated in _exact_claims(binding):
        return
    json_match = _matches_json_claim(stated, binding)
    if json_match is True:
        return
    if json_match is False:
        raise ExplainerRefused(
            "binding-moved",
            f"the environment is bound to {label} ({binding_claim(binding)}); {claim!r} is "
            "another binding, so nothing was written at it",
        )
    if binding.planned is None and frame.comparison_number > 0:
        same_prior = (
            prior is None
            or prior.binding.comparison_number != frame.comparison_number
            or same_binding(prior.binding, frame)
        )
        if same_prior and stated in {
            label,
            str(frame.comparison_number),
            frame.comparison_reference,
            f"{frame.comparison_owner}#{frame.comparison_number}",
        }:
            return
    if _looks_like_binding(stated):
        raise ExplainerRefused(
            "binding-moved",
            f"the environment is bound to {label} ({binding_claim(binding)}); {claim!r} is "
            "another binding, so nothing was written at it",
        )
    raise ExplainerRefused(
        "binding-unknown",
        f"{claim!r} names no binding of this environment: give {binding_claim(binding)!r} or its "
        "binding digest",
    )


def relative_parts(path: str) -> tuple[str, ...]:
    """One relative path as parts, refusing traversal, an absolute path and a separator lie."""

    if len(path) > 1024:
        raise ExplainerRefused("path-refused", "a path of more than 1024 characters is not read")
    if not path:
        return ()
    parts = tuple(path.split("/"))
    for part in parts:
        if part in {"", ".", ".."} or "\\" in part or ":" in part or "\x00" in part:
            raise ExplainerRefused(
                "path-refused",
                f"{path!r} is not a plain relative path: '..', an empty segment, a drive letter "
                "or a backslash is refused",
            )
    return parts


def object_name(value: str) -> str:
    """One commit, tree, blob or ref name, refusing a path, an option or a range."""

    if not value or len(value) > 200 or value.startswith(("-", "/")) or "\\" in value:
        raise ExplainerRefused("revision-refused", "an object name is 1 to 200 characters")
    if ".." in value or _DRIVE.match(value) or not _OBJECT_NAME.fullmatch(value):
        raise ExplainerRefused(
            "revision-refused",
            f"{value!r} is not one object name: a range, a worktree path and an option are refused",
        )
    return value


def tree_path(value: str) -> str:
    """One path inside a tree (a ``show``/``diff``/``ls-tree`` filter), never a worktree path."""

    if not value or value.startswith("-") or len(value) > 1024:
        raise ExplainerRefused("path-refused", f"{value!r} is not one path inside a tree")
    relative_parts(value)
    return value


@dataclass(frozen=True)
class ExplainerReads:
    """``list``, ``search`` and ``read`` over the four admitted surfaces (MIK-R186 rule 3).

    ``code`` and ``memory`` are the bound snapshot's trees; ``snapshot-<n>/code`` names an older
    one, which a turn that spanned an Update may still be reading. ``pages`` is the leaf's retained
    page revisions and ``reference`` is ``notes/reference/<leaf>/`` -- read as they are now, and
    not part of the binding.
    """

    workspace: Path
    task_root: Path | None
    leaf: str

    def binding(self) -> ExplainerBinding:
        return require_binding(self.workspace)

    def root(self, surface: str) -> Path:
        """The one folder a surface's paths are relative to, admitted physically."""

        if surface in SNAPSHOT_SURFACES or surface.startswith("snapshot-"):
            return self._snapshot(surface)
        if surface in TASK_SURFACES:
            return self._records(surface)
        raise ExplainerRefused(
            "surface-unknown",
            f"{surface!r} is not a surface: 'code', 'memory', 'snapshot-<n>/code', "
            "'snapshot-<n>/memory', 'pages' or 'reference'",
        )

    def _snapshot(self, surface: str) -> Path:
        folder, _, side = surface.partition("/")
        if not side:
            side, folder = folder, self.binding().snapshot
        if not _SNAPSHOT_NAME.fullmatch(folder) or side not in SNAPSHOT_SURFACES:
            raise ExplainerRefused("surface-unknown", f"{surface!r} is not a snapshot surface")
        root = self.workspace / folder / side
        if not root.is_dir():
            raise ExplainerRefused(
                "snapshot-unknown", f"this environment holds no {folder}/{side} tree"
            )
        guard_path(self.workspace, root, label="snapshot")
        return root

    def _records(self, surface: str) -> Path:
        if self.task_root is None:
            raise ExplainerRefused(
                "surface-unavailable",
                f"{surface} lies with the task's records, and this server was built without the "
                "task folder",
            )
        if surface == "pages":
            root = frames_area(self.task_root, self.leaf)
        else:
            root = self.task_root.joinpath(
                *REFERENCE_DIRECTORY, path_segment(self.leaf, name="leaf")
            )
        guard_path(self.task_root, root, label=f"{surface} folder")
        return root

    def entry(self, surface: str, path: str) -> Path:
        """One admitted target: inside the surface's root, with no symlink on the way."""

        root = self.root(surface)
        target = root.joinpath(*relative_parts(path))
        guard_path(root, target, label=f"{surface} path")
        return target

    def listing(self, surface: str, path: str = "", depth: int = 1) -> dict[str, Any]:
        """Name the entries under one admitted path, bounded in count and in depth."""

        binding = self.binding()
        target = self.entry(surface, path)
        if not target.exists():
            raise ExplainerRefused("path-missing", f"{surface}:{path or '.'} does not exist")
        if target.is_file():
            rows, truncated = [_entry_row(target, target.parent)], False
        else:
            walked, truncated = _walk(target, max(1, min(depth, MAX_LIST_DEPTH)))
            rows = [_entry_row(item, target) for item in walked]
        return {
            "state": "listed",
            "surface": surface,
            "path": path,
            "entries": rows,
            "truncated": truncated,
            "partOfBinding": surface not in TASK_SURFACES,
            "binding": binding_face(binding),
        }

    def searching(
        self, surface: str, query: str, path: str = "", limit: int = 30
    ) -> dict[str, Any]:
        """Find a literal string in the admitted files, bounded in files, bytes and matches."""

        binding = self.binding()
        needle = _needle(query)
        capped = max(1, min(limit, MAX_SEARCH_MATCHES))
        target = self.entry(surface, path)
        if not target.exists():
            raise ExplainerRefused("path-missing", f"{surface}:{path or '.'} does not exist")
        matches, scanned, files, truncated = _scan_for(target, needle, capped)
        return {
            "state": "searched",
            "surface": surface,
            "path": path,
            "query": query,
            "matches": matches,
            "filesRead": files,
            "bytesRead": scanned,
            "truncated": truncated,
            "partOfBinding": surface not in TASK_SURFACES,
            "binding": binding_face(binding),
        }

    def reading(self, surface: str, path: str, offset: int = 0) -> dict[str, Any]:
        """Read one admitted file, a bounded chunk at a time, naming its whole-file digest."""

        binding = self.binding()
        target = self.entry(surface, path)
        if not target.is_file():
            raise ExplainerRefused("not-a-file", f"{surface}:{path} is not a regular file")
        body = read_bounded(
            target, MAX_READ_BYTES, reason="read-too-large", detail=f"{surface}:{path}"
        )
        try:
            content, encoding = body.decode("utf-8"), "utf-8"
        except UnicodeDecodeError:
            content, encoding = base64.b64encode(body).decode("ascii"), "base64"
        if not 0 <= offset <= len(content):
            raise ExplainerRefused("offset-refused", "the offset is outside the file's characters")
        end = offset + READ_CHUNK_CHARACTERS
        return {
            "state": "read",
            "surface": surface,
            "path": path,
            "bytes": len(body),
            "sha256": "sha256:" + digest_bytes(body),
            "encoding": encoding,
            "offset": offset,
            "content": content[offset:end],
            "nextOffset": end if end < len(content) else None,
            "partOfBinding": surface not in TASK_SURFACES,
            "binding": binding_face(binding),
        }


def _entry_row(item: Path, base: Path) -> dict[str, Any]:
    """One listing row: its relative path, its kind and, for a file, its size."""

    kind = "directory" if item.is_dir() else "file"
    return {
        "path": item.relative_to(base).as_posix(),
        "kind": kind,
        "bytes": item.stat().st_size if kind == "file" else 0,
    }


def _walk(start: Path, depth: int) -> tuple[list[Path], bool]:
    """Every entry at or below ``start`` to ``depth``; a symlink is held, never followed."""

    found: list[Path] = []
    frontier: list[tuple[Path, int]] = [(start, 0)]
    while frontier:
        directory, level = frontier.pop(0)
        try:
            children = sorted(directory.iterdir(), key=lambda item: item.name)
        except OSError as error:
            raise ExplainerRefused(
                "io-refused", f"{directory} cannot be listed: {error}"
            ) from error
        for child in children:
            if child.is_symlink():
                raise ExplainerRefused(
                    "path-held", f"{child} is a symlink; it is held, not followed"
                )
            if len(found) >= MAX_LIST_ENTRIES:
                return found, True
            found.append(child)
            if child.is_dir() and level + 1 < depth:
                frontier.append((child, level + 1))
    return found, False


def _needle(query: str) -> str:
    """The literal string a search looks for, refused when absent or oversized."""

    if not query or len(query) > MAX_QUERY_CHARACTERS:
        raise ExplainerRefused("search-query", f"a query is 1 to {MAX_QUERY_CHARACTERS} characters")
    return query.casefold()


def _scan_for(start: Path, needle: str, capped: int) -> tuple[list[dict], int, int, bool]:
    """Match ``needle`` in the files at or below ``start``; answer rows and what was read."""

    candidates = [start] if start.is_file() else _walk(start, MAX_WALK_DEPTH)[0]
    matches: list[dict] = []
    scanned = 0
    files = 0
    truncated = False
    for candidate in candidates:
        if len(matches) >= capped or files >= MAX_SEARCH_FILES or scanned >= MAX_SEARCH_BYTES:
            truncated = True
            break
        if not candidate.is_file():
            continue
        try:
            body = read_bounded(candidate, MAX_READ_BYTES, reason="read-too-large", detail="scan")
        except PresenterRefused:
            continue
        files += 1
        scanned += len(body)
        matches.extend(_matched_rows(body, candidate, start, needle, capped - len(matches)))
    return matches, scanned, files, truncated


def _matched_rows(body: bytes, candidate: Path, start: Path, needle: str, room: int) -> list[dict]:
    """The rows one file contributes: its line number and the line, bounded."""

    base = start if start.is_dir() else start.parent
    relative = candidate.relative_to(base).as_posix()
    rows: list[dict] = []
    for number, line in enumerate(body.decode("utf-8", "replace").splitlines(), start=1):
        if needle not in line.casefold():
            continue
        rows.append({"path": relative, "line": number, "text": line.strip()[:240]})
        if len(rows) >= room:
            break
    return rows


@dataclass(frozen=True)
class ExplainerGit:
    """Six read-only Git subcommands, composed here so no foreign option has a spelling.

    The caller names a side and object names; this module builds the argv, so an external diff
    program, ``--textconv``, ``--filters``, a ``-c`` override, a pager, ``--output``,
    ``diff --no-index`` and a worktree path cannot be asked for. The kernel's runner strips the
    repository selectors and sets ``GIT_OPTIONAL_LOCKS=0``, so nothing writes an index, a ref, a
    worktree file or a config.
    """

    repositories: Mapping[str, Path]
    workspace: Path

    def log(self, repository: str, ref: str = "HEAD", limit: int = 20, path: str = "") -> dict:
        """Commits of one repository, newest first; a bounded count and an optional tree path."""

        args = [
            "log",
            "--no-color",
            "--no-decorate",
            f"--max-count={max(1, min(limit, MAX_GIT_LIMIT))}",
            "--format=%H%x09%aI%x09%an%x09%s",
            object_name(ref),
        ]
        return self._run(repository, _with_path(args, path))

    def show(self, repository: str, target: str, path: str = "") -> dict:
        """One commit or tree as text, addressed by its own object name, never today's checkout."""

        args = ["show", "--no-color", "--no-ext-diff", "--no-textconv", object_name(target)]
        return self._run(repository, _with_path(args, path))

    def diff(self, repository: str, base: str, candidate: str, path: str = "") -> dict:
        """The diff between two named trees or commits; never between worktree paths."""

        args = [
            "diff",
            "--no-color",
            "--no-ext-diff",
            "--no-textconv",
            *DIFF_PREFIX_OPTIONS,
            object_name(base),
            object_name(candidate),
        ]
        return self._run(repository, _with_path(args, path))

    def ls_tree(self, repository: str, tree: str, recursive: bool = False, path: str = "") -> dict:
        """The entries of one tree, with sizes; ``recursive`` walks the whole tree."""

        args = ["ls-tree", *(["-r"] if recursive else ()), "--long", object_name(tree)]
        return self._run(repository, _with_path(args, path))

    def cat_file(self, repository: str, target: str, mode: str = "pretty") -> dict:
        """One object's content (``pretty``), type (``type``) or size (``size``)."""

        flag = {"pretty": "-p", "type": "-t", "size": "-s"}.get(mode)
        if flag is None:
            raise ExplainerRefused("cat-file-mode", "a cat-file read is 'pretty', 'type' or 'size'")
        return self._run(repository, ["cat-file", flag, object_name(target)])

    def rev_parse(self, repository: str, expression: str = "HEAD") -> dict:
        """Resolve one object name. No ``--show-toplevel``: a worktree path is not an answer."""

        return self._run(repository, ["rev-parse", object_name(expression)])

    def _run(self, repository: str, args: Sequence[str]) -> dict[str, Any]:
        root = self.repositories.get(repository)
        if root is None or not root.is_dir():
            raise ExplainerRefused(
                "repository-unknown",
                f"{repository!r} is not a bound repository; the two are 'code' and 'memory'",
            )
        foreign = [token for token in args if _FOREIGN_OPTION.match(token)]
        if foreign:
            raise ExplainerRefused(
                "git-option-refused",
                f"{foreign[0]!r} would run another program, read a worktree path or write an "
                "output; this server runs none of those",
            )
        completed = run_git(root, list(args))
        if completed.returncode:
            detail = completed.stderr.strip() or f"git {args[0]} answered nothing"
            raise ExplainerRefused("git-refused", detail[:2000])
        text = completed.stdout
        truncated = len(text) > MAX_GIT_CHARACTERS
        return {
            "state": "git",
            "binding": binding_face(require_binding(self.workspace)),
            "repository": repository,
            "repositoryPath": root.as_posix(),
            "subcommand": args[0],
            "args": list(args[1:]),
            "text": text[:MAX_GIT_CHARACTERS],
            "truncated": truncated,
            "history": True,
            "evidence": False,
        }


def _with_path(args: list[str], path: str) -> list[str]:
    """Append one tree-path filter, which Git only reads after its own ``--``."""

    return args if not path else [*args, "--", tree_path(path)]


@dataclass(frozen=True)
class KnowledgeQuery:
    """One knowledge read's arguments: the product's, addressed by the binding's tree."""

    view: str
    record_id: str = ""
    source_path: str = ""
    limit: int = 32
    continuation: str | None = None


@dataclass(frozen=True)
class ExplainerKnowledge:
    """``knowledge_read``'s views at the bound memory tree, through the tree reader."""

    workspace: Path
    memory_repository: Path | None
    coordination_root: Path

    def read(self, query: KnowledgeQuery) -> dict[str, Any]:
        """Name the records one view holds at the bound memory tree; rows never carry text."""

        binding = require_binding(self.workspace)
        tree = binding.binding.memory_candidate_tree
        repository = self.memory_repository or Path(binding.binding.memory_candidate_repository)
        offset = _continuation(query.continuation)
        page = max(1, min(query.limit, MAX_KNOWLEDGE_ROWS))
        with self._index(repository, tree) as index:
            rows = _view_rows(index, query)
            state = {
                "key": index.state.key,
                "state": index.state.state,
                "converted": index.state.converted,
                "problems": len(index.state.problems),
            }
        end = offset + page
        return {
            "state": "named",
            "view": query.view,
            "memoryTree": tree,
            "rows": rows[offset:end],
            "totalRows": len(rows),
            "continuation": str(end) if end < len(rows) else None,
            "index": state,
            "partOfBinding": True,
            "binding": binding_face(binding),
        }

    def _index(self, repository: Path, tree: str) -> KnowledgeIndex:
        if not repository.is_dir():
            raise ExplainerRefused(
                "knowledge-tree-unreadable",
                f"the memory repository {repository} is not on this host",
            )
        cache = KnowledgeIndexCache(default_cache_directory(self.coordination_root))
        try:
            return cache.for_git_tree(repository, tree)
        except Exception as error:  # the tree reader's own fault is a named refusal
            raise ExplainerRefused(
                "knowledge-tree-unreadable", f"the memory tree {tree} cannot be read: {error}"
            ) from error


def _continuation(value: str | None) -> int:
    """The offset a continuation names, refused when it is not one."""

    if value in (None, ""):
        return 0
    if not value.isdigit():
        raise ExplainerRefused("continuation-refused", f"{value!r} is not a row offset")
    return int(value)


def _view_rows(index: KnowledgeIndex, query: KnowledgeQuery) -> list[dict[str, Any]]:
    """One view's rows, or a named refusal for a view this reader does not serve."""

    view = _VIEWS.get(query.view)
    if view is None:
        served = ", ".join(sorted(_VIEWS))
        raise ExplainerRefused(
            "view-unknown", f"{query.view!r} is not a view; the ones served here are {served}"
        )
    return view(index, query)


def _record_view(index: KnowledgeIndex, query: KnowledgeQuery) -> list[dict[str, Any]]:
    record = index.record(_required(query.record_id, query.view)).value
    if record is None:
        raise ExplainerRefused("record-unknown", f"the tree holds no record {query.record_id!r}")
    return [_record_row(record)]


def _source_view(index: KnowledgeIndex, query: KnowledgeQuery) -> list[dict[str, Any]]:
    path = _required(query.source_path, query.view)
    entries = index.entries_at_path(path).value
    families = index.families_governing(path).value
    return [
        *(_entry_row_of(one) for one in (*entries.realizations, *entries.proofs)),
        *({"kind": "family", "id": family, "path": route} for family, route in families),
    ]


def _invariant_view(index: KnowledgeIndex, query: KnowledgeQuery) -> list[dict[str, Any]]:
    found = index.invariant(_required(query.record_id, query.view)).value
    if found.record is None and not found.realizations and not found.proofs:
        raise ExplainerRefused("record-unknown", f"the tree holds no invariant {query.record_id!r}")
    rows = [] if found.record is None else [_record_row(found.record)]
    return [
        *rows,
        *(_entry_row_of(one) for one in (*found.realizations, *found.proofs)),
        *({"kind": "family", "id": family} for family in found.families),
        *(_link_row(one) for one in found.linked_from),
    ]


def _family_view(index: KnowledgeIndex, query: KnowledgeQuery) -> list[dict[str, Any]]:
    found = index.family(_required(query.record_id, query.view)).value
    if found.record is None and not found.members:
        raise ExplainerRefused("record-unknown", f"the tree holds no family {query.record_id!r}")
    rows = [] if found.record is None else [_record_row(found.record)]
    return [
        *rows,
        *({"kind": "member", "id": member} for member in found.members),
        *({"kind": "route", "path": route} for route in found.routes),
    ]


def _dependent_view(index: KnowledgeIndex, query: KnowledgeQuery) -> list[dict[str, Any]]:
    links = index.incoming_links(_required(query.record_id, query.view)).value
    return [_link_row(one) for one in links]


def _weakness_view(index: KnowledgeIndex, query: KnowledgeQuery) -> list[dict[str, Any]]:
    records = index.records_of_kind("weakness").value
    if query.source_path:
        records = tuple(one for one in records if one.path.startswith(query.source_path))
    return [_record_row(one) for one in records]


_VIEWS: Mapping[str, Callable[[KnowledgeIndex, KnowledgeQuery], list[dict[str, Any]]]] = {
    "record": _record_view,
    "source_context": _source_view,
    "invariant": _invariant_view,
    "family": _family_view,
    "dependent": _dependent_view,
    "weakness": _weakness_view,
}


def _required(value: str, view: str) -> str:
    """One argument a view cannot answer without."""

    if not value or len(value) > 400:
        raise ExplainerRefused(
            "argument-required", f"the {view} view names one record id or one source path"
        )
    return value


def _record_row(record: Any) -> dict[str, Any]:
    return {
        "kind": record.kind,
        "id": record.id,
        "path": record.path,
        "revision": record.revision,
        "status": record.status,
    }


def _entry_row_of(entry: Any) -> dict[str, Any]:
    return {
        "kind": entry.kind,
        "id": entry.id,
        "invariant": entry.invariant,
        "path": entry.path,
        "sidecar": entry.sidecar,
    }


def _link_row(link: Any) -> dict[str, Any]:
    return {
        "kind": "link",
        "id": link.source,
        "source": link.source,
        "relation": link.relation,
        "target": link.target,
        "origin": link.origin_path,
    }
