"""Where a complete census artifact is written, addressed, bounded and reclaimed (MIK-R198 rules 1-2).

A whole-memory census is tens of megabytes, so its delivery has three obligations that are not the
census's own: the artifact an answer advertises must keep resolving to the bytes and digest it named;
its metadata must name the trees that were actually measured rather than the directories a caller
typed; and the store it lands in must be bounded by an owner that reclaims it. This module is that
owner, kept out of the tool entry points so the entry points stay about operations.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from agents_remember.kernel.authority import require_within_coordination
from agents_remember.kernel.file_lock import exclusive_file_lock
from agents_remember.kernel.git_command import run_git, run_git_with_index
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig

#: How many default census artifacts the coordination root keeps before reclaiming the oldest.
#: A census of the whole memory tree is tens of megabytes; twenty of them outlive any one leaf's
#: repair work, and a longer life would let one measurement fill the disk it sits on.
CENSUS_ARTIFACT_KEEP: Final = 20

#: The store's own ownership ledger. Reclamation may only reclaim what this store wrote as a default
#: artifact, so a caller-named file that happens to sit in the same folder -- even one whose name
#: looks like a default's -- is never deleted by a policy it never agreed to (L191-A3-04).
OWNED_ARTIFACTS: Final = ".default-artifacts.json"


@dataclass(frozen=True)
class CensusInputs:
    """The captured inputs consumed by the census and diagnostic live endpoint equality."""

    identity: dict[str, Any]
    stable: bool


def write_census_artifact(
    config: McpRuntimeConfig,
    full: dict[str, Any],
    artifact_path: str | None,
    *,
    repo_id: str,
    inputs: CensusInputs,
) -> dict[str, Any]:
    """Write the complete census and answer its path, digest, pair identity and retention (rules 1-2).

    The artifact is the whole population, not the bounded answer: every target occurrence and the
    sidecar map. Three properties are the reason it is stored the way it is:

    * **Addressable, never replaced.** A default artifact's name carries the digest of its own bytes,
      so two populations measured in the same second land at two names and neither can overwrite the
      other: an address the answer advertised keeps resolving to the bytes it named. An identical
      re-measurement resolves to the artifact already on disk instead of writing a twin.
    * **Bound to its measured pair.** The metadata carries the tree identity actually read on each
      side -- the Git commit plus the tree object of the working tree, built through a throwaway
      index so neither worktree nor index is touched -- not the directory names the caller typed.
    * **Bounded store.** ``CENSUS_ARTIFACT_KEEP`` newest default artifacts are retained; older ones are
      reclaimed as newer ones land, and the answer names the policy, the folder and what it pruned. A
      caller-named ``artifact_path`` transfers the address to the caller, including an address that
      was formerly a default. A later default reuse of that address does not take ownership back.
    """

    text = json.dumps(full, indent=1, sort_keys=True, default=str) + "\n"
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    folder = config.coordination_root / "temp" / "census"
    path = (
        require_within_coordination(config, artifact_path, "artifact_path")
        if artifact_path is not None
        else None
    )
    if path is not None and path.parent.resolve() != folder.resolve():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        retention = _caller_retention()
    else:
        # Ownership transfer, writing, and retirement are one transaction across threads/processes.
        with exclusive_file_lock(folder / OWNED_ARTIFACTS, "census artifact store"):
            path, retention = _write_in_store(folder, path, text, digest, repo_id=repo_id)
    return {
        "artifactPath": path.as_posix(),
        "artifactSha256": digest,
        "artifactTargets": len(full["targets"]),
        # These identities address the captured bytes consumed by the parser and resolver.
        # Endpoint equality is diagnostic; a reverted live edit need not change it.
        "artifactInputIdentities": inputs.identity,
        "artifactPairStableDuringRead": inputs.stable,
        "artifactRetention": retention,
    }


def _caller_retention() -> dict[str, Any]:
    return {"store": None, "keep": None, "lifetime": "owned by the caller", "pruned": []}


def _write_in_store(
    folder: Path, path: Path | None, text: str, digest: str, *, repo_id: str
) -> tuple[Path, dict[str, Any]]:
    """Write under the store lock; defaults never replace or reclaim caller-owned bytes."""

    if path is not None:
        path.write_text(text, encoding="utf-8")
        _set_ownership(path, owned=False)
        return path, _caller_retention()
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    path = folder / f"{stamp}-{repo_id}-census-{digest[:12]}.json"
    existing = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    managed = existing is None or path.name in _owned_ledger(folder)
    if not managed and existing == digest:
        return path, _caller_retention()
    if not managed:
        # This address was explicitly transferred and replaced. Reserve a new default address rather
        # than overwrite the caller's still-valid receipt, even for a same-second re-measurement.
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=folder,
            prefix=f"{path.stem}-",
            suffix=".json",
            delete=False,
        ) as output:
            output.write(text)
            path = Path(output.name)
    elif existing != digest:
        path.write_text(text, encoding="utf-8")
    _set_ownership(path, owned=True)
    return path, {
        "store": folder.as_posix(),
        "keep": CENSUS_ARTIFACT_KEEP,
        "pruned": reclaim_census_artifacts(folder.parent.parent, keep=path),
        "lifetime": (
            f"kept until {CENSUS_ARTIFACT_KEEP} newer default censuses land in this coordination "
            "root, then reclaimed oldest-first"
        ),
    }


def _owned_ledger(folder: Path) -> list[str]:
    """The names this store wrote as default artifacts; anything else in the folder is not ours."""

    ledger = folder / OWNED_ARTIFACTS
    if not ledger.is_file():
        return []
    try:
        names = json.loads(ledger.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    return [name for name in names if isinstance(name, str)] if isinstance(names, list) else []


def _set_ownership(path: Path, *, owned: bool) -> None:
    """Record or relinquish the store's ownership; caller naming retires the default lifetime."""

    ledger = path.parent / OWNED_ARTIFACTS
    names = set(_owned_ledger(path.parent))
    if (path.name in names) == owned:
        return
    if owned:
        names.add(path.name)
    else:
        names.discard(path.name)
    ledger.write_text(json.dumps(sorted(names), indent=1) + "\n", encoding="utf-8")


def reclaim_census_artifacts(coordination_root: Path, *, keep: Path) -> list[str]:
    """Keep the newest ``CENSUS_ARTIFACT_KEEP`` owned artifacts and reclaim the rest, never ``keep``.

    Ownership is read from the store's own ledger, not from the folder's contents: a caller-named
    artifact that happens to live under ``temp/census`` is not reclaimed by a retention policy it was
    never subject to, and the answer's stated lifetime for it stays true (L191-A3-04).
    """

    folder = coordination_root / "temp" / "census"
    if not folder.is_dir():
        return []
    owned = [folder / name for name in _owned_ledger(folder)]
    stored = sorted(
        (path for path in owned if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    reclaimed: list[str] = []
    for path in stored[CENSUS_ARTIFACT_KEEP:]:
        if path.resolve() == keep.resolve():
            continue
        path.unlink(missing_ok=True)
        reclaimed.append(path.name)
    if reclaimed:
        remaining = [name for name in _owned_ledger(folder) if name not in set(reclaimed)]
        (folder / OWNED_ARTIFACTS).write_text(
            json.dumps(remaining, indent=1) + "\n", encoding="utf-8"
        )
    return reclaimed


def pair_identities(memory_root: Path, code_root: Path) -> dict[str, Any]:
    """The exact tree identity read on each side: commit plus working-tree tree object."""

    return {"code": tree_identity(code_root), "memory": tree_identity(memory_root)}


def tree_identity(root: Path) -> dict[str, Any]:
    """``{commit, worktreeTree}`` for one Git worktree, or a content digest for a plain directory.

    The tree object is built through a throwaway index (``GIT_INDEX_FILE``), so measuring a tree
    writes no index entry into the worktree it measures and stages nothing.
    """

    # Resolve first: a relative root reaches Git as a path it cannot enter, which would silently
    # degrade a measured Git identity to a content digest. An identity that depends on the caller's
    # working directory is not an identity.
    root = root.resolve()
    if not root.is_dir():
        return {"kind": "absent"}
    try:
        commit = run_git(root, ["rev-parse", "--verify", "HEAD"]).stdout.strip()
    except OSError:  # no git on PATH, or a root the runner cannot enter
        return {"kind": "unreadable"}
    if not commit:
        return {"kind": "directory", "contentSha256": directory_digest(directory_files(root))}
    with tempfile.TemporaryDirectory() as scratch:
        index = Path(scratch) / "index"
        captured = run_git_with_index(root, ["add", "-A", "--", "."], index)
        written = (
            run_git_with_index(root, ["write-tree"], index) if captured.returncode == 0 else None
        )
        tree = written.stdout.strip() if written is not None and written.returncode == 0 else ""
    if not tree:
        return {"kind": "git", "commit": commit, "worktreeTree": None}
    return {"kind": "git", "commit": commit, "worktreeTree": tree}


def directory_files(root: Path) -> dict[str, bytes]:
    """Capture a plain directory once; hashing and census resolution share these bytes."""

    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def directory_digest(files: Mapping[str, bytes]) -> str:
    """A reproducible content digest of the captured plain-directory files."""

    digest = hashlib.sha256()
    for path, data in sorted(files.items()):
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(data).digest())
    return digest.hexdigest()
