"""The rule-6 read-boundary oracle: snapshots, findings and the classifying entry point.

Split whole from test_knowledge_read_boundary.py under the Owner line-bound
disposition: every case, assertion and negative control is preserved; only the address
changes. Tests import the five names they touch; nothing here runs product code.
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class _GitState:
    refs: str
    head: str


def _git_run_in(path: Path, *args: str, git_dir: Path | None = None) -> str | None:
    """One read-only Git probe, or None when it fails or Git is unreachable."""

    if git_dir is not None:
        argv = ["git", "--git-dir", str(git_dir), *args]
    else:
        argv = ["git", "-C", str(path), *args]
    try:
        result = subprocess.run(argv, check=False, capture_output=True, text=True)
    except OSError:
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _git_state(path: Path) -> _GitState | None:
    """Refs and HEAD of one worktree, or None when it is not a live checkout."""

    refs = _git_run_in(path, "for-each-ref", "--format=%(refname) %(objectname)")
    head = _git_run_in(path, "rev-parse", "HEAD")
    if refs is None or head is None:
        return None
    return _GitState(refs=refs, head=head)


def _git_admin_dirs(path: Path) -> tuple[Path | None, Path | None]:
    """A checkout's actual git and common directories (linked worktrees resolve out).

    The common directory resolves through ``--path-format=absolute
    --git-common-dir``: no absolute single flag exists (a literal
    ``--absolute-git-common-dir`` is echoed back verbatim by real Git and must
    never be mistaken for a path). Worktree status/index files and the object store
    are inventoried from files, so only ref/HEAD facts are taken per checkout; the
    store and locks ride the file walk.
    """

    git_dir = _git_run_in(path, "rev-parse", "--absolute-git-dir")
    common_dir = _git_run_in(path, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return (Path(git_dir) if git_dir else None, Path(common_dir) if common_dir else None)


def _discover_repos(root: Path) -> list[tuple[str, Path]]:
    """Every Git checkout beneath a snapshot root: standard dirs and linked worktrees.

    Returns ``(key suffix, checkout path)`` with ``""`` for the root itself. A linked
    worktree carries a ``.git`` file rather than a directory; it is a checkout all the
    same, with its own HEAD. Repositories are discovered, never assumed.
    """

    found: list[tuple[str, Path]] = []
    if (root / ".git").is_dir() or (root / ".git").is_file():
        found.append(("", root))
    for path in sorted(root.rglob("*")):
        if path.name != ".git" or not (path.is_dir() or path.is_file()):
            continue
        parent = path.parent
        if parent == root or any(parent == existing for _, existing in found):
            continue
        if any(str(parent).startswith(str(existing) + "/.git/") for _, existing in found):
            continue
        found.append((parent.relative_to(root).as_posix(), parent))
    return found


@dataclass(frozen=True)
class _WorldState:
    git: dict[str, _GitState]
    files: dict[str, bytes | None]
    dirs: set[str]
    roots: dict[str, str] = dataclasses.field(default_factory=dict)
    repo_paths: dict[str, tuple[str, ...]] = dataclasses.field(default_factory=dict)


def _walk_files(root: Path, tag: str, files: dict, dirs: set) -> None:
    """Inventory every file and directory beneath a root under one snapshot tag."""

    for path in sorted(root.rglob("*")):
        relative = f"{tag}/{path.relative_to(root).as_posix()}"
        if path.is_symlink() or path.is_file():
            try:
                files[relative] = path.read_bytes()
            except OSError:
                files[relative] = None
        elif path.is_dir():
            # Git administrative directories are inventoried too: an empty
            # `.git/refs/...` or `.git/objects/...` directory a read leaves
            # behind is a change no file finding would otherwise name.
            dirs.add(relative)


@dataclass
class _SnapAcc:
    """The mutable accumulators one snapshot walk fills."""

    git: dict[str, _GitState] = dataclasses.field(default_factory=dict)
    files: dict[str, bytes | None] = dataclasses.field(default_factory=dict)
    dirs: set[str] = dataclasses.field(default_factory=set)
    repo_paths: dict[str, list[Path]] = dataclasses.field(default_factory=dict)
    external: int = 0
    alternate_visited: set[str] = dataclasses.field(default_factory=set)


def _alternate_targets(gitdir: Path) -> list[Path]:
    """Object stores one git directory borrows through its alternates pointer file.

    The pointer file is ``objects/info/alternates`` under a git directory and
    ``info/alternates`` under a standalone object store, whatever that store
    directory is named: a borrowed store sitting inside a snapshot root need not
    be called ``objects``. Every readable pointer location is followed. Relative
    entries resolve against the objects directory, matching Git's own resolution.
    A missing or unreadable pointer file means no borrowed store.
    """

    if gitdir.name == "objects" or not (gitdir / "objects").is_dir():
        objects_candidates = [gitdir]
    else:
        objects_candidates = [gitdir / "objects"]
    if (gitdir / "info").is_dir() and gitdir / "info" not in [
        candidate / "info" for candidate in objects_candidates
    ]:
        objects_candidates.append(gitdir)
    targets = []
    for objects in objects_candidates:
        pointer = objects / "info" / "alternates"
        try:
            text = pointer.read_text(encoding="utf-8")
        except OSError:
            continue
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            candidate = Path(line)
            if not candidate.is_absolute():
                candidate = objects / line
            targets.append(candidate)
    return targets


def _snapshot_alternates(name: str, root: Path, gitdir: Path, acc: _SnapAcc) -> None:
    """Inventory borrowed object stores, following chains of alternates.

    The alternates pointer file itself rides the ordinary file walk wherever its
    git directory is inventoried. Every outside store is walked under its own
    `@alternate` namespace, and every visited store's own pointer file is followed
    before containment can skip it, so a chain inside the root ending at an outside
    lender (C borrows B borrows A with B already inventoried) still inventories A.
    Only genuinely uncovered stores are walked; the visited set keeps alternate
    cycles (A borrows B borrows A) finite. Every walked store resolves through the
    same tree attribution as any captured object, so a borrowed object no review
    touched still fails wherever it sits.
    """

    known = acc.repo_paths.setdefault(name, [])
    visited = acc.alternate_visited
    worklist = _alternate_targets(gitdir)
    while worklist:
        target = worklist.pop()
        objects = target if target.name == "objects" else target / "objects"
        store = objects if objects.is_dir() else target
        if not store.is_dir():
            continue
        key = str(store.resolve())
        if key in visited:
            continue
        visited.add(key)
        worklist.extend(_alternate_targets(store))
        if any(
            str(store) == str(other) or str(store).startswith(str(other) + "/")
            for other in [root, *known]
        ):
            continue
        acc.external += 1
        _walk_files(store, f"{name}@alternate{acc.external}", acc.files, acc.dirs)
        known.append(store)
        owner = store.parent if store.name == "objects" else store
        if (
            owner != store
            and (owner / "HEAD").is_file()
            and (owner / "objects").is_dir()
            and owner not in known
        ):
            known.append(owner)


def _snapshot_external_admin(name: str, root: Path, checkout: Path, acc: _SnapAcc) -> None:
    """Inventory a checkout's admin dirs when they escape every known path.

    A linked worktree's real git/common directory usually sits inside the snapshot
    root already (the main `.git/worktrees/...`); only a truly external one earns its
    own `@gitdir` namespace, where its object store rides the same attribution.
    """

    known = acc.repo_paths.setdefault(name, [])
    if checkout not in known:
        known.append(checkout)
    for admin in _git_admin_dirs(checkout):
        if admin is None:
            continue
        _snapshot_alternates(name, root, admin, acc)
        if admin == checkout / ".git":
            continue
        if any(str(admin) == str(other) or str(admin).startswith(str(other) + "/") for other in [root, *known]):
            continue
        acc.external += 1
        _walk_files(admin, f"{name}@gitdir{acc.external}", acc.files, acc.dirs)
        acc.repo_paths[name].append(admin)
        _snapshot_alternates(name, root, admin, acc)


def _snapshot(roots: dict[str, Path]) -> _WorldState:
    acc = _SnapAcc()
    for name, root in roots.items():
        _walk_files(root, name, acc.files, acc.dirs)
        for suffix, checkout in _discover_repos(root):
            key = name if not suffix else f"{name}/{suffix}"
            state = _git_state(checkout)
            if state is not None:
                acc.git[key] = state
            _snapshot_external_admin(name, root, checkout, acc)
    root_paths = {name: root.as_posix() for name, root in roots.items()}
    frozen_paths = {
        name: tuple(path.as_posix() for path in paths) for name, paths in acc.repo_paths.items()
    }
    return _WorldState(git=acc.git, files=acc.files, dirs=acc.dirs, roots=root_paths, repo_paths=frozen_paths)


def _parse_refs(raw: str) -> dict[str, str]:
    """Map ref names to objects: one row per ref, so shared objects never collapse."""

    refs = {}
    for line in raw.splitlines():
        if not line.strip():
            continue
        name, _, obj = line.partition(" ")
        refs[name.strip()] = obj.strip()
    return refs


def _ref_findings(
    name: str,
    old_refs: dict[str, str],
    new_refs: dict[str, str],
    *,
    record_refs: set[str],
    attributed_trees: set[str],
) -> list[str]:
    """Added, removed and moved refs by name; an added pin needs attestation.

    An added review-namespace pin is allowed only naming a pin ref a genuine record of
    this run attests AND pointing at an object inside an attributed compared tree.
    Either half alone -- a record-named pin to a foreign object, or an attributed
    object under an unrecorded name -- is a finding.
    """

    findings = []
    for ref in sorted(set(old_refs) | set(new_refs)):
        if ref not in old_refs:
            if (
                ref.startswith("refs/ar/review/")
                and ref in record_refs
                and new_refs[ref] in attributed_trees
            ):
                continue
            findings.append(f"{name}: ref appeared: {ref}")
        elif ref not in new_refs:
            findings.append(f"{name}: ref removed: {ref}")
        elif new_refs[ref] != old_refs[ref]:
            findings.append(f"{name}: ref moved to another object: {ref}")
    return findings


def _git_findings(
    before: _WorldState,
    after: _WorldState,
    *,
    record_refs: set[str],
    attributed_trees: set[str],
) -> list[str]:
    """Refs and HEAD per discovered checkout: what names a branch, a pin or a HEAD.

    Worktree status and object counts are deliberately not compared: status dirt is
    legitimate beside allowed (c) writes and is already inventoried per file, and loose
    objects and packs are inventoried per file with strictly stronger attribution.
    A pin ref added under ``refs/ar/review/`` is allowed only attested by both halves
    (record ref and attributed tree); a removed or moved pin, and any other ref change,
    is a finding.
    """

    findings: list[str] = []
    for name, state in after.git.items():
        old = before.git.get(name)
        if old is None:
            findings.append(f"a repository appeared: {name}")
            continue
        if state.head != old.head:
            findings.append(f"{name}: HEAD moved {old.head[:12]} -> {state.head[:12]}")
        old_refs = _parse_refs(old.refs)
        new_refs = _parse_refs(state.refs)
        findings += _ref_findings(
            name, old_refs, new_refs, record_refs=record_refs, attributed_trees=attributed_trees
        )
    return findings


def _is_hex_id(value: object) -> bool:
    """Whether a value is a 40-hex Git object id worth attributing through."""

    return (
        isinstance(value, str)
        and len(value) == 40
        and all(char in "0123456789abcdef" for char in value)
    )


def _record_side_attestation(record: dict) -> tuple[set[str], set[str]]:
    """The tree ids and pin refs one parsed comparison record vouches for."""

    trees: set[str] = set()
    refs: set[str] = set()
    for side in ("code_base", "code_candidate", "memory_base", "memory_candidate"):
        entry = record.get(side)
        if not isinstance(entry, dict):
            continue
        for field in ("tree", "commit"):
            if _is_hex_id(entry.get(field)):
                trees.add(entry[field])
        ref = entry.get("ref")
        if isinstance(ref, str) and ref:
            refs.add(ref)
    return trees, refs


def _record_attestation(before_files: dict, after_files: dict) -> tuple[bool, set[str], set[str]]:
    """What the run's comparison records attest: presence, tree ids and pin refs.

    Presence (a new or rewritten record) is the (b) placement allowance. Trees and refs
    come only from records that parse as ``ar-review-tree-comparison/v1`` with 40-hex
    sides: an empty or malformed record attests nothing, so objects beside it still fail.
    """

    marker = "notes/reports/review-comparisons/"
    recorded = False
    trees: set[str] = set()
    refs: set[str] = set()
    for key, content in after_files.items():
        if marker not in key:
            continue
        if key in before_files and before_files[key] == content:
            continue
        recorded = True
        if not isinstance(content, (bytes, bytearray)):
            continue
        try:
            record = json.loads(bytes(content).decode("utf-8"))
        except (ValueError, UnicodeError):
            continue
        if not isinstance(record, dict) or record.get("schema") != "ar-review-tree-comparison/v1":
            continue
        side_trees, side_refs = _record_side_attestation(record)
        trees.update(side_trees)
        refs.update(side_refs)
    return recorded, trees, refs


def _is_object_store_file(relative: str) -> bool:
    """Whether a snapshot key names a file inside a Git object store.

    Both the in-tree ``.git/objects/`` and an external admin inventory (``@gitdir``,
    taken when a checkout's real git or common directory escapes the snapshot roots).
    """

    if "/.git/objects/" in relative:
        return True
    tag, _, rest = relative.partition("@gitdir")
    if tag and "/objects/" in rest:
        return True
    tag, _, rest = relative.partition("@alternate")
    return bool(tag) and bool(rest)


def _loose_object_sha(relative: str) -> str | None:
    """The object id of a new loose object file, or None for pack/index/info files."""

    if not _is_object_store_file(relative):
        return None
    for marker in ("/.git/objects/", "/objects/"):
        _, sep, tail = relative.partition(marker)
        if not sep:
            continue
        fanout, slash, rest = tail.partition("/")
        if (
            slash
            and len(fanout) == 2
            and len(rest) == 38
            and all(char in "0123456789abcdef" for char in fanout + rest)
        ):
            return fanout + rest
    tail = relative.rsplit("/", 2)
    if (
        len(tail) == 3
        and len(tail[1]) == 2
        and len(tail[2]) == 38
        and all(char in "0123456789abcdef" for char in tail[1] + tail[2])
    ):
        return tail[1] + tail[2]
    return None


def _git_run(repo: Path, *args: str) -> str | None:
    """One read-only Git probe against a checkout, or None when it fails."""

    return _git_run_in(repo, *args)


def _tree_members(repo: Path, tree: str) -> set[str] | None:
    """Every object a review's compared tree holds: the tree, its entries, its commits.

    ``None`` when the tree resolves nowhere here: the oracle cannot attribute through
    a tree this repository does not hold, so nothing is allowed through it. Bare
    administrative directories probe through ``--git-dir`` instead of ``-C``.
    """

    if (repo / ".git").exists():
        run = lambda *args: _git_run(repo, *args)  # noqa: E731
    elif (repo / "HEAD").is_file() and (repo / "objects").is_dir():
        run = lambda *args: _git_run_in(repo, *args, git_dir=repo)  # noqa: E731
    else:
        return None
    if run("cat-file", "-e", tree) is None:
        return None
    members = {tree}
    listed = run("ls-tree", "-r", "-t", "-z", tree)
    if listed is not None:
        for row in listed.split("\0"):
            if not row.strip():
                continue
            meta, _, _ = row.rpartition("\t")
            parts = meta.split()
            if len(parts) >= 3:
                members.add(parts[2])
    typed = run("cat-file", "-t", tree)
    if typed is not None and typed.strip() == "commit":
        history = run("rev-list", "--objects", tree)
        if history is not None:
            members.update(row.split()[0] for row in history.splitlines() if row.strip())
    return members


def _object_attributed(
    repo_paths: dict[str, tuple[str, ...]],
    name: str,
    sha: str,
    trees: set[str],
) -> bool:
    """Whether a new loose object belongs to an attributed compared tree.

    Trees come from genuine comparison records of this run plus the trees the test read
    off the review answer it actually received (synthetic seats record nothing). Every
    new object must resolve inside one of them, in any repository of the same snapshot
    root. Anything else -- however valid an object it is -- is an object change no
    review touched.
    """

    if not trees:
        return False
    for candidate in repo_paths.get(name, ()):
        repo = Path(candidate)
        for tree in trees:
            members = _tree_members(repo, tree)
            if members is not None and sha in members:
                return True
    return False


def _classify_object_file(
    relative: str,
    *,
    repo_paths: dict[str, tuple[str, ...]],
    attributed_trees: set[str],
) -> str | None:
    """Classify one new file under a reviewed object store prefix.

    Returns a finding, or None when the object resolves inside an attributed compared
    tree. Pack, index and info files name no object and are never allowed through trees.
    Mere placement beside any changed record path is not custody.
    """

    name = relative.split("/", 1)[0].split("@", 1)[0]
    sha = _loose_object_sha(relative)
    if sha is not None and _object_attributed(repo_paths, name, sha, attributed_trees):
        return None
    return f"object changed with no attributed comparison: {relative}"


def _file_findings(
    before: _WorldState,
    after: _WorldState,
    *,
    coordination_roots: tuple[str, ...],
    reviewed_prefixes: tuple[str, ...],
    reviewed_trees: dict[str, tuple[str, ...]] | None = None,
) -> list[str]:
    """Files and directories: only (a) cache, (b) records/objects/pins and (c) previews.

    Captured objects under a reviewed object store are allowed only resolving inside a
    compared tree this run attests: trees from genuine comparison records it wrote, plus
    trees the test read off the review answer (synthetic seats record nothing). Mere
    placement beside any changed record path is not custody. A new directory is allowed
    only under an allowlisted root holding a new allowed file -- an empty directory,
    here or under ``runtime/``, is a finding.
    """

    findings: list[str] = []
    before_files, after_files = before.files, after.files
    runtime_roots = tuple(f"{root}/runtime/" for root in coordination_roots)
    _, record_trees, _record_refs = _record_attestation(before_files, after_files)
    declared = reviewed_trees or {}
    attributed = {
        name: set(record_trees) | set(declared.get(name, ()))
        for name in {relative.split("/", 1)[0].split("@", 1)[0] for relative in after_files}
    }
    changed = sorted(
        set(after_files) ^ set(before_files)
        | {
            key
            for key in after_files
            if key in before_files and after_files[key] != before_files[key]
        }
    )
    allowed_files: set[str] = set()
    for relative in changed:
        if relative.startswith(runtime_roots):
            allowed_files.add(relative)
            continue
        if "notes/reports/review-comparisons/" in relative:
            allowed_files.add(relative)
            continue
        if _is_object_store_file(relative) and relative.startswith(reviewed_prefixes):
            name = relative.split("/", 1)[0].split("@", 1)[0]
            finding = _classify_object_file(
                relative,
                repo_paths=after.repo_paths,
                attributed_trees=attributed.get(name, set()),
            )
            if finding is None:
                allowed_files.add(relative)
            else:
                findings.append(finding)
            continue
        findings.append(f"file changed outside (a)/(b)/(c): {relative}")
    allowed_parents: set[str] = set()
    for relative in allowed_files:
        parts = relative.split("/")[:-1]
        for depth in range(1, len(parts) + 1):
            allowed_parents.add("/".join(parts[:depth]))
    record_marker = "notes/reports/review-comparisons"
    for relative in sorted(set(after.dirs) - set(before.dirs)):
        if relative in allowed_parents and (
            "/.git/" in relative
            or "@gitdir" in relative
            or "@alternate" in relative
            or relative.startswith(runtime_roots)
            or any(relative == f"{root}/runtime" for root in coordination_roots)
            or record_marker in relative
        ):
            continue
        findings.append(f"a read created a directory: {relative}")
    return findings


def _classify(
    before: _WorldState,
    after: _WorldState,
    *,
    coordination_roots: tuple[str, ...],
    reviewed_prefixes: tuple[str, ...] = (),
    reviewed_trees: dict[str, tuple[str, ...]] | None = None,
) -> list[str]:
    """Every difference outside (a), (b) and (c), as human-readable findings.

    The review's captured objects (b) land in the object store of a repository the review ran
    against: new files under such a store are allowed only resolving inside a compared tree
    this run attests -- trees from genuine comparison records it wrote, plus trees the test
    read off the review answer it actually received (``reviewed_trees`` maps a snapshot root
    name to those tree ids; synthetic seats record nothing). An added review-namespace pin
    is allowed only naming a record-attested ref AND pointing at an attributed tree. A live
    seat additionally leaves the comparison's record; the synthetic seat cannot adopt, so no
    record is expected here -- but an object change anywhere no review touched still fails.
    """

    _, record_trees, record_refs = _record_attestation(before.files, after.files)
    declared = reviewed_trees or {}
    attributed_trees = set(record_trees)
    for trees in declared.values():
        attributed_trees.update(trees)
    return [
        *_git_findings(
            before, after, record_refs=record_refs, attributed_trees=attributed_trees
        ),
        *_file_findings(
            before,
            after,
            coordination_roots=coordination_roots,
            reviewed_prefixes=reviewed_prefixes,
            reviewed_trees=reviewed_trees,
        ),
    ]


def _bare_repo(name: str = "repo") -> dict[str, _GitState]:
    """One repository whose Git facts never move, so only files and dirs speak."""

    state = _GitState(refs="", head="0" * 40)
    return {name: state}
