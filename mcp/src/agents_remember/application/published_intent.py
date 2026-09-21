"""The repository's published intent, selected and read before a task exists (ICR-R19@v1).

The ordinary planning read is a *paired* read -- source beside its onboarding -- and what it could
not answer is "what has this repository already intended here?". :mod:`application.knowledge_read`
has served a taskless read at one exact snapshot since KS-R07; nothing in the ordinary route ever
selected a dataset, so a fresh planner read an empty scratch database instead of the repository's
own published knowledge. This module is that selection.

Three decisions, and this module owns exactly these:

* **Which dataset.** One repository-scoped location, resolved from the coordination context the
  ordinary read already carries: the memory layer's knowledge dataset at
  ``<memory_root>/knowledge.sqlite`` (:data:`PUBLISHED_DATASET_NAME`). **This route declares the
  location it reads, and the ordinary write side publishes there.** That wiring was the obligation
  this declaration recorded until ICR-R20@v1 landed it: no shipped owner computed or defaulted a
  publication destination before then -- ``IngestPublication.destination_path`` was whatever the
  caller's ``--publish-to`` named, and a run that named none committed without publishing -- so the
  ingest command now selects this one location with ``--publish``, resolving it through this module's
  :func:`published_dataset_path` and reading the published identity back through
  :func:`resolve_published_intent`. A run that names no destination and passes no ``--publish`` still
  commits without publishing, which is why the destination stays a selection rather than a default.
  The two-consecutive-task journey that proves task A's publication lands where task B's planner looks
  is still ICR-R25@v1's. Declaring it here is what makes it one shared spelling instead of two
  conventions that happen to agree. It is a *selection*, not a search: no other repository's dataset is read, no
  working directory is guessed, and naming it needs no leaf, no enclosure and no task. *Which* memory
  root that is follows the resolved context: the canonical external memory root when no enclosure is
  in scope -- the taskless planner this route exists for -- and the memory **worktree** (that task's
  own memory line) when one is. There is deliberately no fallback between the two: a publication that
  is not on the line being read is reported ``not-recorded``, never substituted from the other.
* **Which source identity.** The read context requires the source-resolution pair together or not
  at all, so the pair is resolved here from the same context -- the code repository root and the
  tree id of its current commit -- and a pair that cannot be resolved is left *unrequested* rather
  than completed with a fabrication. A recorded anchor is then observed against a real tree, and a
  blob that moved is reported as the observation it is.
* **What a caller reads.** The seeds: a source seed (``PathSeed``) for each path the ordinary read
  already asked about, and the identity seeds a caller names when it already holds a record id. The
  page itself is built by the shipped selective read (``open_read_context`` +
  ``read_knowledge_scope``) at the dataset's own snapshot. This module selects and shapes; it never
  selects rows itself, and it holds no second store, no cache and no durable state.

Every way the selection can fail is a *named* state rather than an empty success. A location holding
no file system entry at all is ``not-recorded``: a repository whose knowledge begins later is not a
repository whose history was measured as empty, and nothing about it is invented -- the source and
onboarding halves of the ordinary read continue exactly as they did. A location that holds something
other than a dataset file, a dataset that cannot be read as a dataset of this code, or one bound to
another repository's authority home is ``unusable``, and it carries the shipped refusal code for the
exact input (``selected_input_unavailable`` when there is no file to open, ``snapshot_unavailable``
when there are bytes that are not the expected dataset), naming the failed binding. A path no
recorded anchor could carry is refused as a seed instead of being answered with an absence this read
never observed, and a record identity the snapshot does not hold is the read's own
``selector_absent`` -- the named absence of a generation this snapshot does not carry. Today's
database is never substituted for a historical generation.

One limitation travels with a bounded page rather than being left for a caller to discover: the
``continuation`` a page mints is a ``read_knowledge_scope`` cursor (``continuationOperation``), and
the mounted ``knowledge_read`` tool continues *views*, so it refuses that token. A caller that needs
more than the page carries reads on by identity -- the exact ``invariant_id``/``revision_id`` the
page returned -- rather than by paging this cursor through a tool that does not accept it.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import apsw
from pydantic import ValidationError

from agents_remember.application.knowledge_before_half import read_dataset_identity
from agents_remember.application.knowledge_read import open_read_context, read_knowledge_scope
from agents_remember.kernel.coordination_context.models import CoordinationContext
from agents_remember.kernel.git_command import run_git
from agents_remember.memory.knowledge.connection import open_read_only_database
from agents_remember.memory.knowledge.logical import bound_repository
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.models.knowledge.read import (
    KNOWLEDGE_READ_POLICY_VERSION,
    KnowledgeReadBudget,
    KnowledgeReadContext,
    KnowledgeReadPage,
    KnowledgeReadRequest,
    KnowledgeReadResult,
    KnowledgeReadSeed,
    PathSeed,
    ReadItem,
)
from agents_remember.models.knowledge.result import KnowledgeRefusal

__all__ = [
    "PUBLISHED_DATASET_NAME",
    "PUBLISHED_INTENT_MAX_ITEMS",
    "PUBLISHED_INTENT_MAX_UTF8_BYTES",
    "PublishedIntentSelection",
    "PublishedIntentSourcePair",
    "PublishedIntentUnavailable",
    "published_dataset_path",
    "published_intent_block",
    "read_published_intent",
    "resolve_published_intent",
]

# The one file name a repository's published knowledge dataset occupies inside its memory layer.
# THIS ROUTE DECLARES THE LOCATION IT READS, AND THE ORDINARY WRITE SIDE PUBLISHES THERE: the ingest
# command selects it with ``--publish``, which resolves this same path through
# ``published_dataset_path`` and reads the published identity back through
# ``resolve_published_intent``. A run that names no destination and passes no ``--publish`` still
# commits without publishing, which is why the destination is a selection rather than a default.
# Before ICR-R20@v1 no shipped owner computed or defaulted a destination at all --
# ``IngestPublication.destination_path`` was whatever the caller's ``--publish-to`` named -- and the
# two-consecutive-task journey that has to prove task A's publication lands where task B's planner
# looks is still ICR-R25@v1's. Declaring it here is what gives the read side and the write side one
# shared spelling instead of two conventions that agree today.
PUBLISHED_DATASET_NAME = "knowledge.sqlite"

# One bounded page per seed. A path seed selects the revisions realized at that path plus the
# families directly containing them, so the bound is a page size rather than a narrowing: a page
# that leaves items behind reports ``hasMore`` and hands back the continuation that reaches them.
PUBLISHED_INTENT_MAX_ITEMS = 8
PUBLISHED_INTENT_MAX_UTF8_BYTES = 8192

# The two spellings a Git object identity of a tree can have. Nothing is invented for a value that
# matches neither: the pair is then left unrequested, which the read reports as "no source
# resolution was requested" rather than as a resolution that silently failed.
_TREE_ID_PATTERN = re.compile(r"^[0-9a-f]{40}$|^[0-9a-f]{64}$")

# The failure classes this route models. Each is a fact about the selected publication rather than
# a defect of the caller, and the ordinary read must survive every one of them: a paired read that
# aborted because a repository's knowledge file was unreadable or unparseable would trade one gap
# for a worse one. ``ValidationError`` belongs here for that same reason -- a dataset whose stored
# namespace row this build cannot decode is an input the route was handed, not a caller mistake.
_PUBLICATION_FAILURES = (KnowledgeStorageError, apsw.Error, OSError, ValidationError)


@dataclass(frozen=True)
class PublishedIntentSourcePair:
    """The source-resolution pair recorded anchors are observed against, resolved together.

    Both halves travel as one value because the read context refuses either alone: a tree id
    without the repository that holds it is not resolvable, and a root without a tree is an
    incomplete request rather than a narrower one.
    """

    repository_root: Path
    code_tree_id: str


@dataclass(frozen=True)
class PublishedIntentSelection:
    """The one published snapshot a repository-context resolution selected, named exactly.

    ``repository_id``, ``schema_version`` and ``logical_digest`` are read from the dataset itself
    rather than from the caller, so a caller cannot hand-write the snapshot the read is verified
    against; the read compares this resolution again against the file it opens.
    """

    database_path: Path
    repository_id: str
    schema_version: str
    logical_digest: str
    source_pair: PublishedIntentSourcePair | None


@dataclass(frozen=True)
class PublishedIntentUnavailable:
    """Why the repository's intended publication could not be read, named by its binding.

    ``state`` separates the two facts a caller acts on differently: ``not-recorded`` is "this
    location holds no publication at all" (nothing was measured, so nothing is claimed about
    history, and source/onboarding research continues), and ``unusable`` is "something is there and
    is not a dataset this read can answer from" -- a non-file entry, bytes that are not a dataset of
    this code, or another repository's dataset. ``code`` is the shipped refusal vocabulary for the
    same fact (``selected_input_unavailable`` when there is no file to open,
    ``snapshot_unavailable`` when there are bytes that are not the expected dataset), so a caller
    branches on a code rather than on this module's prose.
    """

    state: Literal["not-recorded", "unusable"]
    code: str
    detail: str
    dataset_path: Path


@dataclass(frozen=True)
class _UnseedablePath:
    """One requested path that no recorded anchor could carry, so no seed can select it.

    It is a *refusal to seed* rather than an absence: answering it with ``registration_absent``
    would publish a negative answer this read never observed, which is the one thing a path rule
    must never do.
    """

    path: str
    detail: str


def published_dataset_path(context: CoordinationContext) -> Path:
    """The one location this repository's published knowledge dataset is selected at.

    ``context.memory_root`` is the memory layer the repository's own coordination declaration
    resolves, so the selection is repository-scoped by construction: there is no argument a caller
    can aim at another repository's dataset, and a repository that declares no memory layer has no
    published intent to read rather than a neighbour's to borrow.

    *Which* root that is follows the resolved scope, and the difference is load-bearing: inside a
    leaf enclosure the coordination context's memory root is the contract's memory **worktree** (that
    task's own memory line), and with no enclosure in scope it is the canonical external memory root
    (``kernel/coordination_context/resolver.py``, ``_effective_memory_root``). This route never
    substitutes one for the other, so a publication that is not on the line being read is reported
    ``not-recorded``.
    """

    return context.memory_root / PUBLISHED_DATASET_NAME


def resolve_published_intent(
    context: CoordinationContext,
) -> PublishedIntentSelection | PublishedIntentUnavailable:
    """Resolve the repository's intended published dataset, or name why it cannot be read."""

    database_path = published_dataset_path(context)
    absent = _absence_state(database_path, context.code_repository_name)
    if absent is not None:
        return absent
    try:
        identity = read_dataset_identity(database_path)
    except _PUBLICATION_FAILURES as error:
        return _unusable(database_path, f"the published dataset could not be read ({error})")
    if isinstance(identity, str):
        return _unusable(database_path, identity)
    mismatch = _authority_mismatch(database_path, context.code_repository_name)
    if mismatch is not None:
        return _unusable(database_path, mismatch)
    return PublishedIntentSelection(
        database_path=database_path,
        repository_id=identity.repository_id,
        schema_version=identity.schema_version,
        logical_digest=identity.logical_digest,
        source_pair=_source_pair(context),
    )


def _absence_state(database_path: Path, repository_name: str) -> PublishedIntentUnavailable | None:
    """The named state of a location this route cannot read a dataset from, or ``None``.

    Absent and present-but-not-a-regular-file are different facts, and one answer cannot state both:
    a directory (or a dangling link, or a device) sitting where the publication belongs is something
    that was put there, and reporting it as "nothing is recorded here" would hide it and send the
    caller away satisfied. Only a location with no file system entry at all is ``not-recorded``.
    """

    if database_path.is_file():
        return None
    if not database_path.exists() and not database_path.is_symlink():
        return PublishedIntentUnavailable(
            state="not-recorded",
            code="selected_input_unavailable",
            detail=(
                f"no published knowledge dataset is recorded for {repository_name} at "
                f"{database_path}, so no recorded intent was read and nothing is claimed about this "
                "repository's history; source and onboarding research continue unchanged"
            ),
            dataset_path=database_path,
        )
    held = "a directory" if database_path.is_dir() else "no regular file"
    return _unusable(
        database_path,
        (
            f"the expected publication location {database_path} holds {held} rather than the "
            "repository's published dataset, so nothing could be read from it"
        ),
        code="selected_input_unavailable",
    )


def published_intent_block(
    context: CoordinationContext, source_paths: Sequence[str]
) -> dict[str, Any]:
    """The published-intent block one ordinary paired read attaches for its requested paths.

    This is the route's whole public surface for the ordinary read: resolve the repository's
    publication, seed it with the paths the caller already asked about, and read one bounded page
    per path at the dataset's own snapshot. Every failure is returned as a named state, so a caller
    that asked for source bytes still receives them.
    """

    resolved = resolve_published_intent(context)
    if isinstance(resolved, PublishedIntentUnavailable):
        return _unavailable_block(resolved)
    seeds: list[KnowledgeReadSeed | _UnseedablePath] = [_source_seed(path) for path in source_paths]
    return read_published_intent(resolved, seeds)


def read_published_intent(
    selection: PublishedIntentSelection,
    seeds: Sequence[KnowledgeReadSeed | _UnseedablePath],
    *,
    max_items: int = PUBLISHED_INTENT_MAX_ITEMS,
) -> dict[str, Any]:
    """Read one bounded page per seed at the selection's own snapshot, through the shipped read.

    The context is opened by ``open_read_context``, which resolves the snapshot the dataset at that
    path actually holds, and each page is built by ``read_knowledge_scope`` -- the same taskless,
    exact-snapshot operation every other caller of the selective read uses. ``task_ref`` is left
    unset because planning has to be able to read recorded knowledge before a leaf exists.
    """

    context = _open_context(selection)
    if isinstance(context, PublishedIntentUnavailable):
        return _unavailable_block(context)
    blocks = [_seed_block(selection, context, seed, max_items) for seed in seeds]
    return _recorded_block(selection, blocks)


def _source_pair(context: CoordinationContext) -> PublishedIntentSourcePair | None:
    """The source-resolution pair this repository's current commit defines, or ``None``.

    ``None`` is the honest answer for a code root that is not a Git repository, a Git that cannot
    answer, or an answer that is not an object id: the pair is then *unrequested*, which the read
    reports as exactly that. HEAD is used here as the tree a planner is planning against -- not as
    a substitute for a recorded endpoint, which is the review's own binding and not this route's.
    """

    root = context.code_repository_root
    if root is None:
        return None
    try:
        completed = run_git(root, ["rev-parse", "HEAD^{tree}"])
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    tree_id = completed.stdout.strip()
    if not _TREE_ID_PATTERN.match(tree_id):
        return None
    return PublishedIntentSourcePair(repository_root=root, code_tree_id=tree_id)


def _authority_mismatch(database_path: Path, repository_name: str) -> str | None:
    """The reason this publication belongs to another repository, or ``None`` when it does not.

    A namespace is an id, so it cannot say which repository a dataset belongs to; the authority
    home the dataset records is the row that can. Checking it is what makes "never silently select
    another repository" a fact this route verifies instead of a claim it makes.
    """

    try:
        authority_home = _bound_authority_home(database_path)
    except _PUBLICATION_FAILURES as error:
        return f"the published dataset could not be read ({error})"
    if authority_home == repository_name:
        return None
    return (
        f"the dataset at {database_path} is bound to the authority home {authority_home!r}, not "
        f"to {repository_name!r}; this route selects no other repository's publication"
    )


def _bound_authority_home(database_path: Path) -> str | None:
    """The authority home the dataset itself records, read through a read-only handle."""

    connection = open_read_only_database(database_path)
    try:
        repository = bound_repository(connection)
    finally:
        connection.close()
    return None if repository is None else repository.authority_home


def _open_context(
    selection: PublishedIntentSelection,
) -> KnowledgeReadContext | PublishedIntentUnavailable:
    pair = selection.source_pair
    try:
        return open_read_context(
            selection.database_path,
            selection.repository_id,
            repository_root=None if pair is None else pair.repository_root,
            code_tree_id=None if pair is None else pair.code_tree_id,
        )
    except _PUBLICATION_FAILURES as error:
        return _unusable(
            selection.database_path, f"the resolved snapshot could not be opened ({error})"
        )


def _source_seed(path: str) -> KnowledgeReadSeed | _UnseedablePath:
    """The path seed one requested path spells, or the refusal that it spells none."""

    try:
        return PathSeed(path=path)
    except ValidationError as error:
        first = error.errors()[0] if error.errors() else {}
        reason = str(first.get("msg", error))
        return _UnseedablePath(
            path=path,
            detail=(
                f"the requested path {path!r} is not a spelling a recorded source anchor can "
                f"carry, so no seed selects it and no absence is claimed for it ({reason})"
            ),
        )


def _seed_block(
    selection: PublishedIntentSelection,
    context: KnowledgeReadContext,
    seed: KnowledgeReadSeed | _UnseedablePath,
    max_items: int,
) -> dict[str, Any]:
    if isinstance(seed, _UnseedablePath):
        return _refused_block(
            {"kind": "path", "path": seed.path}, "invalid_payload", seed.detail, None
        )
    seed_json = _seed_json(seed)
    if seed_json is None:
        return _unaddressable_seed_block(seed)
    try:
        result = read_knowledge_scope(
            selection.database_path,
            context,
            KnowledgeReadRequest(
                seed=seed,
                budget=KnowledgeReadBudget(
                    max_items=max_items, max_utf8_bytes=PUBLISHED_INTENT_MAX_UTF8_BYTES
                ),
            ),
        )
    except _PUBLICATION_FAILURES as error:
        return _refused_block(
            seed_json,
            "snapshot_unavailable",
            f"the published snapshot could not be read for this seed ({error})",
            None,
        )
    return _result_block(seed_json, result)


def _seed_json(seed: object) -> dict[str, Any] | None:
    """The seed's own recorded spelling, or ``None`` when the value is not a seed model at all.

    ``read_published_intent`` is an exported application function, so it can be reached with a value
    its annotation describes but the runtime never checked. Asking the value for its own dump and
    answering ``None`` when it has none is what keeps that mistake a named refusal instead of an
    ``AttributeError`` raised from inside this read.
    """

    dumper = getattr(seed, "model_dump", None)
    if not callable(dumper):
        return None
    dumped = dumper(mode="json")
    return dumped if isinstance(dumped, dict) else None


def _unaddressable_seed_block(seed: object) -> dict[str, Any]:
    """The refusal for a value that is not one of the two typed seeds this route addresses.

    A caller's mistake is stated as a refusal with the offending value's Python type (and a bounded
    repr for a scalar) rather than as a defect of this read: the same discipline every other input on
    this route follows, and the reason a seed that selected nothing is never reported as an absence.
    """

    described: dict[str, Any] = {"kind": "unaddressable", "python_type": type(seed).__name__}
    if isinstance(seed, (str, bytes, int, float, bool)):
        described["value"] = repr(seed)[:120]
    return _refused_block(
        described,
        "invalid_payload",
        (
            "the requested seed is not one of the typed seeds this route addresses (a path seed or "
            "an invariant/family identity or revision seed), so no recorded scope was selected for "
            "it and no absence is claimed"
        ),
        None,
    )


def _result_block(seed_json: dict[str, Any], result: KnowledgeReadResult) -> dict[str, Any]:
    """One read result as the exact identities and authored words it carries, or its refusal."""

    if result.state == "refused" or result.refusal is not None:
        return _refusal_block(seed_json, result.refusal)
    if result.page is None:  # pragma: no cover - the result model carries one outcome
        return _no_outcome_block(seed_json)
    return _page_block(seed_json, result, result.page)


def _refusal_block(seed_json: dict[str, Any], refusal: KnowledgeRefusal | None) -> dict[str, Any]:
    """The block for a refused read, carrying the read's own code, detail and next action."""

    if refusal is None:  # pragma: no cover - the result model carries one outcome
        return _no_outcome_block(seed_json)
    return _refused_block(seed_json, refusal.code, refusal.detail, refusal.next_action)


def _no_outcome_block(seed_json: dict[str, Any]) -> dict[str, Any]:
    """The block for a result that carried neither outcome: a refusal, never a fabricated page."""

    return _refused_block(
        seed_json, "snapshot_unavailable", "the read returned neither a page nor a refusal", None
    )


def _page_block(
    seed_json: dict[str, Any], result: KnowledgeReadResult, page: KnowledgeReadPage
) -> dict[str, Any]:
    """One bounded page: its exact snapshot, its items and the continuation that reaches the rest.

    ``continuationOperation`` names which operation continues the token the page mints. It is stated
    because the answer is not the obvious one: this cursor continues ``read_knowledge_scope``, while
    the mounted ``knowledge_read`` tool continues *views* and therefore refuses it. A caller that
    needs more than this page carries reads on by the identity the page returned instead.
    """

    return {
        "seed": seed_json,
        "state": "page",
        "snapshot": None if result.snapshot is None else result.snapshot.logical_digest,
        "seedDigest": result.seed_digest,
        "manifestDigest": result.manifest_digest,
        "items": [_item_json(item) for item in page.items],
        "counts": page.counts.model_dump(mode="json"),
        "hasMore": page.has_more,
        "enumerationComplete": page.enumeration_complete,
        "continuation": page.continuation,
        "continuationOperation": "read_knowledge_scope",
    }


def _item_json(item: ReadItem) -> dict[str, Any]:
    """One selected item exactly as the read selected it: its identities, words and anchor.

    The item is dumped by its own model rather than re-spelled field by field, so a field the read
    adds later travels without this module deciding whether it matters. ``exclude_none`` keeps the
    page readable without ever dropping a value the read recorded.
    """

    return item.model_dump(mode="json", exclude_none=True)


def _refused_block(
    seed_json: dict[str, Any], code: str, detail: str, next_action: str | None
) -> dict[str, Any]:
    block: dict[str, Any] = {
        "seed": seed_json,
        "state": "refused",
        "refusalCode": code,
        "refusalDetail": detail,
    }
    if next_action:
        block["nextAction"] = next_action
    return block


def _recorded_block(
    selection: PublishedIntentSelection, seeds: list[dict[str, Any]]
) -> dict[str, Any]:
    pair = selection.source_pair
    return {
        "state": "recorded",
        "datasetPath": str(selection.database_path),
        "repositoryId": selection.repository_id,
        "schemaVersion": selection.schema_version,
        "snapshot": selection.logical_digest,
        "policyVersion": KNOWLEDGE_READ_POLICY_VERSION,
        "sourceResolution": (
            None
            if pair is None
            else {
                "repositoryRoot": str(pair.repository_root),
                "codeTreeId": pair.code_tree_id,
            }
        ),
        "seeds": seeds,
    }


def _unavailable_block(unavailable: PublishedIntentUnavailable) -> dict[str, Any]:
    """The block for a publication that could not be read, with the failed binding named."""

    return {
        "state": unavailable.state,
        "datasetPath": str(unavailable.dataset_path),
        "refusalCode": unavailable.code,
        "refusalDetail": unavailable.detail,
        "seeds": [],
    }


def _unusable(
    database_path: Path, detail: str, *, code: str = "snapshot_unavailable"
) -> PublishedIntentUnavailable:
    """One ``unusable`` publication, carrying the shipped code for the exact input it was handed."""

    return PublishedIntentUnavailable(
        state="unusable",
        code=code,
        detail=detail,
        dataset_path=database_path,
    )
