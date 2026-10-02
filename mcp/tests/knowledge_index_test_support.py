"""Fixture memory trees for the derived knowledge index (MIK-R23).

Two kinds of fixture live here:

* :func:`write_review_tree` -- a converted memory tree written directly in the MIK-R21/R07 file
  formats: the packet's conforming example (a family whose members are realized across four files,
  one of them ``dashboard/src/data/review.ts``), with a test proof, a decision and an incident that
  link to an invariant, a route sidecar citing it, and a closed history file with rows about it.
* :func:`convert_dataset` -- a *fixture converter*: it reads a knowledge database (the legacy store)
  and writes the head revision of every invariant and family, and every realization claim on a head
  revision, as record files and file sidecars. It is not the MIK-R24 conversion (which owns the
  real export, anchors' content identities, onboarding and the layout version); it exists so the
  parity tests and the timing measurement can run before L24, as the packet allows. Its anchors
  carry the recorded blob and a content identity derived from the recorded locator, not from code.
"""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import apsw
from agents_remember.models.knowledge_files.canonical import canonical_text
from agents_remember.models.knowledge_files.documents import (
    LAYOUT_MARKER_PATH,
    file_sidecar_path,
    record_path,
)
from agents_remember.models.knowledge_files.ids import derived_realization_id, derived_record_id

BLOB = "d8baa15ca43be010159ae15f6b0e744949e6d33a"
OTHER_BLOB = "6aa80fe43345f17ee2533b3d1f54b1c4b4e7a940"

REVIEW_PATH = "dashboard/src/data/review.ts"
SIBLING_PATHS = (
    "dashboard/src/views/family.ts",
    "mcp/src/agents_remember/application/review_family_context.py",
    "mcp/src/agents_remember/models/knowledge/review_family_source.py",
)
TEST_PATH = "mcp/tests/test_review_family_context.py"

REVIEW_INVARIANT = "INV-RVW001"
SIBLING_INVARIANT = "INV-SBX002"
OUTSIDER_INVARIANT = "INV-0TS003"
FAMILY = "FAM-CMPBND"
DECISION = "DEC-D12RTE"
INCIDENT = "INC-1NC1DT"
LEAF = "260928-MIK-L23"
OTHER_LEAF = "260928-MIK-L07"


def content_of(text: str) -> str:
    """A ``sha256:`` content identity for fixture anchors."""

    import hashlib  # noqa: PLC0415 - local to keep the fixture surface small

    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def anchor(name: str, *, blob: str = BLOB, path: str | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {
        "locator": {"kind": "symbol", "name": name},
        "blob": blob,
        "content": content_of(name),
    }
    if path is not None:
        value["path"] = path
    return value


def write_document(root: Path, path: str, document: Mapping[str, Any]) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(canonical_text(dict(document)), encoding="utf-8")


def git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def rewrite_in_the_second_of_the_index_write(repository: Path, target: Path, text: str) -> None:
    """Record ``target`` in the repository's index, then rewrite it in place with ``text``: same
    size, same clock second. Git recognises such a file only by its index file's own time.

    Returns in a later second, as a capture is normally taken: a copy of the index made within
    the second of the write would carry that second by accident and hide the difference.
    """

    recorded = target.read_text("utf-8")
    assert text != recorded and len(text.encode()) == len(recorded.encode())
    index = Path(git(repository, "rev-parse", "--path-format=absolute", "--git-path", "index"))
    for _ in range(40):
        while not 0.10 < time.time() % 1 < 0.35:  # well inside one second of the coarse clock
            time.sleep(0.005)
        target.write_text(recorded, "utf-8")
        git(repository, "add", "--all")
        target.write_text(text, "utf-8")
        if int(index.stat().st_mtime) == int(target.stat().st_mtime):
            time.sleep(1.05 - time.time() % 1)
            return
    raise AssertionError("the index write and the rewrite could not be placed in one second")


def init_repository(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "user.name", "fixture")
    git(root, "config", "commit.gpgsign", "false")


def commit_all(root: Path, message: str = "fixture") -> str:
    git(root, "add", "-A")
    git(root, "commit", "-q", "--allow-empty", "-m", message)
    return git(root, "rev-parse", "HEAD")


def _origin(**extra: str) -> dict[str, str]:
    return {"task": "260928-MIK", "leaf": LEAF, **extra}


def _invariant(identifier: str, statement: str, *, revision: int = 1) -> dict[str, Any]:
    return {
        "schema": "ar-invariant/v1",
        "id": identifier,
        "origin": _origin(),
        "revision": revision,
        "status": "accepted",
        "statement": statement,
        "applicability": "Every review comparison.",
        "conditions": [],
        "exclusions": [],
        "supersedes": [],
        "admission": "legacy-unassessed",
    }


def _file_sidecar(path: str, realizes: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {
        "schema": "ar-onboarding-file/v1",
        "path": path,
        "references": {},
        "realizes": realizes,
        **extra,
    }


def _realization(identifier: str, invariant: str, name: str) -> dict[str, Any]:
    return {
        "id": identifier,
        "invariant": invariant,
        "anchor": anchor(name),
        "role": "enforcement",
        "rationale": f"{name} enforces {invariant}.",
    }


def write_review_tree(root: Path) -> None:
    """Write the conforming-example tree (uncommitted) under ``root``."""

    write_document(
        root, LAYOUT_MARKER_PATH, {"schema": "ar-memory-layout/v2", "conversion": "fixture"}
    )
    write_document(
        root,
        record_path("invariant", REVIEW_INVARIANT, "unchanged-realization-context"),
        _invariant(
            REVIEW_INVARIANT, "A comparison shows unchanged realizations beside changed ones."
        ),
    )
    write_document(
        root,
        record_path("invariant", SIBLING_INVARIANT, "family-context-source"),
        _invariant(
            SIBLING_INVARIANT, "Family context is read from the comparison's own endpoints."
        ),
    )
    write_document(
        root,
        record_path("invariant", OUTSIDER_INVARIANT, "outsider"),
        _invariant(OUTSIDER_INVARIANT, "An unrelated invariant.", revision=2),
    )
    write_document(
        root,
        record_path("family", FAMILY, "comparison-bound-context"),
        {
            "schema": "ar-family/v1",
            "id": FAMILY,
            "origin": _origin(),
            "revision": 1,
            "status": "accepted",
            "title": "Comparison-bound unchanged realization context",
            "guarantee": "Every realization of the family is shown at the comparison's endpoints.",
            "members": [REVIEW_INVARIANT, SIBLING_INVARIANT],
            "routes": ["dashboard/src", "mcp/src/agents_remember/application"],
            "admission": "legacy-unassessed",
        },
    )
    write_document(
        root,
        record_path("decision", DECISION, "local-family-routes"),
        {
            "schema": "ar-decision/v1",
            "id": DECISION,
            "origin": {"task": "260928-MIK"},
            "revision": 1,
            "status": "active",
            "context": "Families span subtrees.",
            "alternatives": [
                {"option": "Local routes", "status": "chosen", "reason": "Accurate."},
                {
                    "option": "One route",
                    "status": "rejected",
                    "reason": "Collapses to the root.",
                    "reconsider_when": "Families stop spanning subtrees.",
                },
            ],
            "consequences": ["Coverage runs per route set."],
            "decider": "developer",
            "supersedes": [],
            "admission": "legacy-unassessed",
            "links": [
                {"target": REVIEW_INVARIANT, "relation": "constrains"},
                {"target": REVIEW_INVARIANT, "relation": "reconsider_on", "alternative": 1},
                {"target": "route:dashboard/src", "relation": "constrains"},
            ],
        },
    )
    write_document(
        root,
        record_path("incident", INCIDENT, "stale-context"),
        {
            "schema": "ar-incident/v1",
            "id": INCIDENT,
            "origin": {"task": "260921-ICR"},
            "revision": 1,
            "status": "accepted",
            "occurrence": "The review showed stale context.",
            "observed_at": "260921-ICR-L40",
            "observed_effect": "A reviewer saw the wrong realization.",
            "detection": "Manual review.",
            "cause": "The context was read from the working tree.",
            "cause_uncertainty": "None.",
            "applicability": "unresolved",
            "links": [
                {"target": REVIEW_INVARIANT, "relation": "violated"},
                {"target": anchor("loadReview", path=REVIEW_PATH), "relation": "occurred_at"},
            ],
        },
    )
    write_document(
        root,
        file_sidecar_path(REVIEW_PATH),
        _file_sidecar(
            REVIEW_PATH,
            [
                _realization("RLZ-RVW001", REVIEW_INVARIANT, "loadReview"),
                _realization("RLZ-RVW002", SIBLING_INVARIANT, "familyContext"),
            ],
        ),
    )
    for number, path in enumerate(SIBLING_PATHS, start=1):
        write_document(
            root,
            file_sidecar_path(path),
            _file_sidecar(path, [_realization(f"RLZ-SBX00{number}", SIBLING_INVARIANT, "render")]),
        )
    write_document(
        root,
        file_sidecar_path(TEST_PATH),
        _file_sidecar(
            TEST_PATH,
            [],
            proves=[
                {
                    "id": "PRF-T3ST0K",
                    "invariant": REVIEW_INVARIANT,
                    "anchor": anchor("test_unchanged_context", blob=OTHER_BLOB),
                    "facet": "unchanged realizations are shown",
                }
            ],
        ),
    )
    write_document(
        root,
        "onboarding/dashboard/src/overview.json",
        {
            "schema": "ar-onboarding-route/v1",
            "path": "dashboard/src",
            "references": {
                "1": {"targets": [{"kind": "invariant", "id": REVIEW_INVARIANT}]},
                "2": {
                    "targets": [{"kind": "code", "anchor": anchor("loadReview", path=REVIEW_PATH)}]
                },
            },
        },
    )
    write_document(
        root,
        f"knowledge/history/{OTHER_LEAF}.json",
        {
            "schema": "ar-history/v1",
            "leaf": OTHER_LEAF,
            "closed": True,
            "rows": [
                {
                    "id": "ROW-AAAAA1",
                    "subject": REVIEW_INVARIANT,
                    "disposition": "no_impact",
                    "reason": "Only formatting changed near loadReview.",
                    "items": [],
                    "covers": [],
                    "revision": 1,
                },
                {
                    "id": "ROW-AAAAA2",
                    "subject": FAMILY,
                    "disposition": "no_impact",
                    "reason": "Members unchanged.",
                    "items": [],
                    "examined": [
                        {"id": REVIEW_INVARIANT, "revision": 1},
                        {"id": SIBLING_INVARIANT, "revision": 1},
                    ],
                },
            ],
        },
    )
    write_document(
        root,
        f"knowledge/history/{LEAF}.json",
        {
            "schema": "ar-history/v1",
            "leaf": LEAF,
            "closed": False,
            "rows": [
                {
                    "id": "ROW-BBBBB1",
                    "subject": REVIEW_INVARIANT,
                    "disposition": "no_impact",
                    "reason": "The index leaf reads, never writes, this invariant's code.",
                    "items": [],
                    "covers": [],
                    "revision": 1,
                    "because": [DECISION],
                }
            ],
        },
    )
    (root / "onboarding" / "dashboard" / "src" / "overview.md").write_text(
        "# dashboard/src\n", encoding="utf-8"
    )
    (root / "notes.md").write_text("prose outside the index\n", encoding="utf-8")


# --------------------------------------------------------------------------------------------------
# The fixture converter: a legacy knowledge database -> record files and file sidecars
# --------------------------------------------------------------------------------------------------


def _heads(
    connection: apsw.Connection, table: str, predecessor: str, identity: str
) -> dict[str, dict[str, Any]]:
    columns = [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
    rows = connection.execute(
        f"SELECT * FROM {table} r WHERE NOT EXISTS (SELECT 1 FROM {predecessor} p "
        "WHERE p.parent_revision_id = r.revision_id)"
    )
    heads: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = dict(zip(columns, row, strict=True))
        if value[identity] in heads:
            raise ValueError(f"{value[identity]} has more than one head revision")
        heads[value[identity]] = value
    return heads


def _slug(text: str) -> str:
    cleaned = "".join(character if character.isalnum() else "-" for character in text.lower())
    return "-".join(part for part in cleaned.split("-") if part)[:48] or "record"


def _new_locator(legacy: Mapping[str, Any]) -> dict[str, Any]:
    if legacy["kind"] == "symbol":
        return {"kind": "symbol", "name": legacy["qualified_name"]}
    if legacy["kind"] == "line_range":
        return {"kind": "line_range", "start": legacy["start_line"], "end": legacy["end_line"]}
    return {"kind": "file"}


def convert_dataset(database_path: Path, destination: Path) -> dict[str, str]:
    """Write the head knowledge of ``database_path`` under ``destination``; return legacy -> new IDs."""

    connection = apsw.Connection(str(database_path), flags=apsw.SQLITE_OPEN_READONLY)
    try:
        return _convert(connection, destination)
    finally:
        connection.close()


def _convert(connection: apsw.Connection, destination: Path) -> dict[str, str]:
    invariants = _heads(connection, "invariant_revision", "invariant_predecessor", "invariant_id")
    families = _heads(connection, "family_revision", "family_predecessor", "family_id")
    labels = dict(connection.execute("SELECT family_id, display_label FROM family"))
    new_ids: dict[str, str] = {}
    revision_identity = dict(
        connection.execute("SELECT revision_id, invariant_id FROM invariant_revision")
    )
    write_document(
        destination, LAYOUT_MARKER_PATH, {"schema": "ar-memory-layout/v2", "conversion": "fixture"}
    )
    for legacy_id, row in invariants.items():
        identifier = derived_record_id("invariant", legacy_id)
        new_ids[legacy_id] = identifier
        conditions = json.loads(row["conditions"])
        exclusions = json.loads(row["exclusions"])
        write_document(
            destination,
            record_path("invariant", identifier, _slug(row["statement"])),
            {
                "schema": "ar-invariant/v1",
                "id": identifier,
                "origin": {"task": "fixture-conversion", "legacyId": legacy_id},
                "revision": 1,
                "status": "accepted" if row["state_at_origin"] == "accepted" else "proposed",
                "statement": row["statement"],
                "applicability": row["applicability"],
                "conditions": conditions,
                "exclusions": exclusions,
                "supersedes": [],
                "admission": "legacy-unassessed",
            },
        )
    for legacy_id, row in families.items():
        identifier = derived_record_id("family", legacy_id)
        new_ids[legacy_id] = identifier
        members = sorted(
            {
                new_ids[revision_identity[str(member[0])]]
                for member in connection.execute(
                    "SELECT invariant_revision_id FROM family_member WHERE family_revision_id = ?",
                    (row["revision_id"],),
                )
            }
        )
        write_document(
            destination,
            record_path("family", identifier, _slug(str(labels[legacy_id]))),
            {
                "schema": "ar-family/v1",
                "id": identifier,
                "origin": {"task": "fixture-conversion", "legacyId": legacy_id},
                "revision": 1,
                "status": "accepted" if row["state_at_origin"] == "accepted" else "proposed",
                "title": str(labels[legacy_id]),
                "guarantee": row["joint_guarantee"],
                "members": members,
                "routes": [],
                "admission": "legacy-unassessed",
            },
        )
    _convert_realizations(connection, destination, invariants, new_ids)
    return new_ids


def _convert_realizations(
    connection: apsw.Connection,
    destination: Path,
    invariants: Mapping[str, Mapping[str, Any]],
    new_ids: Mapping[str, str],
) -> None:
    head_revisions = {row["revision_id"]: legacy for legacy, row in invariants.items()}
    sidecars: dict[str, list[dict[str, Any]]] = {}
    rows = connection.execute(
        "SELECT c.invariant_revision_id, c.role, c.rationale, a.path, a.source_identity, a.locator "
        "FROM realization_claim c JOIN source_anchor a ON a.anchor_id = c.anchor_id "
        "ORDER BY c.claim_id"
    )
    for revision_id, role, rationale, path, identity, locator_text in rows:
        legacy_invariant = head_revisions.get(str(revision_id))
        if legacy_invariant is None:
            continue
        locator = _new_locator(json.loads(locator_text))
        entry_id = derived_realization_id(legacy_invariant, str(path), locator)
        sidecars.setdefault(str(path), []).append(
            {
                "id": entry_id,
                "invariant": new_ids[legacy_invariant],
                "anchor": {
                    "locator": locator,
                    "blob": json.loads(identity)["object_id"],
                    "content": content_of(json.dumps(locator, sort_keys=True)),
                },
                "role": "unclassified" if role == "incidental" else str(role),
                "rationale": str(rationale),
            }
        )
    for path, realizes in sidecars.items():
        unique = {entry["id"]: entry for entry in realizes}
        write_document(
            destination, file_sidecar_path(path), _file_sidecar(path, list(unique.values()))
        )


# --------------------------------------------------------------------------------------------------
# The parity dataset: one revision per identity, so a converted tree is its exact equivalent
# --------------------------------------------------------------------------------------------------

PARITY_PATHS = (
    "src/integration.py",
    "src/synchronization.py",
    "src/batch.py",
    "src/resolution.py",
    "src/anchors.py",
)


def build_parity_dataset(directory: Path) -> tuple[Path, str]:
    """Author ``P -> I``, ``F -> {I, J}``, ``G -> {J, K}``, ``H -> {I, L}`` through the store.

    Every identity has one revision, so the fixture converter's tree holds exactly the same graph
    and the reused selection must return the same set over both.
    """

    from datetime import UTC, datetime  # noqa: PLC0415
    from uuid import UUID, uuid4  # noqa: PLC0415

    from agents_remember.memory.knowledge import (  # noqa: PLC0415
        families,
        memberships,
        realizations,
    )
    from agents_remember.memory.knowledge.store import open_knowledge_store  # noqa: PLC0415
    from agents_remember.models.knowledge.authorship import Authorship  # noqa: PLC0415
    from agents_remember.models.knowledge.family import FamilyRevisionDraft  # noqa: PLC0415
    from agents_remember.models.knowledge.graph import (  # noqa: PLC0415
        FamilyMemberDraft,
        RealizationClaimDraft,
    )
    from agents_remember.models.knowledge.repository import RepositoryIdentity  # noqa: PLC0415
    from agents_remember.models.knowledge.result import (  # noqa: PLC0415
        FamilyMemberRequest,
        FamilyRequest,
        FamilyRevisionRequest,
        InvariantRequest,
        NewAnchor,
        RealizationClaimRequest,
        RevisionDraft,
        RevisionRequest,
    )
    from agents_remember.models.knowledge.source import (  # noqa: PLC0415
        FileLocator,
        GitBlobIdentity,
        LineRangeLocator,
        SourceAnchorDraft,
        SymbolLocator,
    )

    def require(result: Any) -> None:
        assert result.state == "created", result.refusal

    directory.mkdir(parents=True, exist_ok=True)
    database = directory / "knowledge.sqlite"
    repository_id = str(uuid4())
    authorship = Authorship(
        actor_ref="agent:parity-fixture",
        authorization_ref="MIK-R23 parity",
        operation_id=uuid4(),
        recorded_at=datetime.now(UTC).isoformat(),
    )
    store = open_knowledge_store(database, repository_id)
    try:
        require(
            store.create_repository(
                RepositoryIdentity(repository_id=repository_id, authority_home="agents-remember")
            )
        )
        revisions: dict[str, str] = {}
        for name in ("I", "J", "K", "L"):
            invariant_id, revision_id = str(uuid4()), str(uuid4())
            revisions[name] = revision_id
            require(
                store.create_invariant(
                    InvariantRequest(
                        repository_id=repository_id,
                        invariant_id=invariant_id,
                        display_label=f"parity-{name}",
                        provenance=authorship,
                    )
                )
            )
            require(
                store.create_revision(
                    RevisionRequest(
                        repository_id=repository_id,
                        revision=RevisionDraft(
                            revision_id=revision_id,
                            invariant_id=invariant_id,
                            display_version="v1",
                            statement=f"Invariant {name} holds.",
                            applicability="Every parity case.",
                            conditions=(f"{name} is admitted.",),
                            exclusions=(),
                            predecessors=(),
                            provenance=authorship,
                        ),
                    )
                )
            )
        for name, members in (("F", ("I", "J")), ("G", ("J", "K")), ("H", ("I", "L"))):
            family_id, family_revision = str(uuid4()), str(uuid4())
            require(
                families.create_family(
                    store,
                    FamilyRequest(
                        repository_id=repository_id,
                        family_id=family_id,
                        display_label=f"family-{name}",
                        provenance=authorship,
                    ),
                )
            )
            require(
                families.create_family_revision(
                    store,
                    FamilyRevisionRequest(
                        repository_id=repository_id,
                        revision=FamilyRevisionDraft(
                            family_id=family_id,
                            revision_id=family_revision,
                            display_version="v1",
                            joint_guarantee=f"Family {name} holds jointly.",
                            provenance=authorship,
                        ),
                    ),
                )
            )
            for member in members:
                require(
                    memberships.create_family_member(
                        store,
                        FamilyMemberRequest(
                            repository_id=repository_id,
                            member=FamilyMemberDraft(
                                member_id=str(uuid4()),
                                family_revision_id=family_revision,
                                invariant_revision_id=revisions[member],
                                provenance=authorship,
                            ),
                        ),
                    )
                )
        claims = (
            ("I", PARITY_PATHS[0], FileLocator(), "enforcement"),
            (
                "I",
                PARITY_PATHS[1],
                LineRangeLocator(start_line=3, end_line=7),
                "propagation-persistence",
            ),
            ("J", PARITY_PATHS[2], FileLocator(), "enforcement"),
            (
                "J",
                PARITY_PATHS[2],
                SymbolLocator(language="python", qualified_name="apply_batch"),
                "support",
            ),
            ("K", PARITY_PATHS[3], FileLocator(), "presentation"),
            ("L", PARITY_PATHS[4], FileLocator(), "support"),
        )
        for name, path, locator, role in claims:
            require(
                realizations.create_realization_claim(
                    store,
                    RealizationClaimRequest(
                        repository_id=repository_id,
                        claim=RealizationClaimDraft(
                            claim_id=str(uuid4()),
                            invariant_revision_id=revisions[name],
                            role=role,  # type: ignore[arg-type]
                            rationale=f"{name} is realized at {path}.",
                        ),
                        anchor=NewAnchor(
                            anchor=SourceAnchorDraft(
                                anchor_id=UUID(str(uuid4())),
                                path=path,
                                source_identity=GitBlobIdentity(object_id=BLOB),
                                locator=locator,
                            )
                        ),
                        provenance=authorship,
                    ),
                )
            )
    finally:
        store.close()
    return database, repository_id


def add_parity_claim(database: Path, repository_id: str, name: str, path: str) -> None:
    """Author one more whole-file realization of parity invariant ``name`` at ``path``."""

    from datetime import UTC, datetime  # noqa: PLC0415
    from uuid import UUID, uuid4  # noqa: PLC0415

    from agents_remember.memory.knowledge import realizations  # noqa: PLC0415
    from agents_remember.memory.knowledge.store import open_knowledge_store  # noqa: PLC0415
    from agents_remember.models.knowledge.authorship import Authorship  # noqa: PLC0415
    from agents_remember.models.knowledge.graph import RealizationClaimDraft  # noqa: PLC0415
    from agents_remember.models.knowledge.result import (  # noqa: PLC0415
        NewAnchor,
        RealizationClaimRequest,
    )
    from agents_remember.models.knowledge.source import (  # noqa: PLC0415
        FileLocator,
        GitBlobIdentity,
        SourceAnchorDraft,
    )

    store = open_knowledge_store(database, repository_id)
    try:
        revision = next(
            iter(
                store.connection.execute(
                    "SELECT r.revision_id FROM invariant_revision r JOIN invariant i "
                    "ON i.invariant_id = r.invariant_id WHERE i.display_label = ?",
                    (f"parity-{name}",),
                )
            )
        )[0]
        created = realizations.create_realization_claim(
            store,
            RealizationClaimRequest(
                repository_id=repository_id,
                claim=RealizationClaimDraft(
                    claim_id=str(uuid4()),
                    invariant_revision_id=str(revision),
                    role="support",
                    rationale=f"{name} is realized at {path}.",
                ),
                anchor=NewAnchor(
                    anchor=SourceAnchorDraft(
                        anchor_id=UUID(str(uuid4())),
                        path=path,
                        source_identity=GitBlobIdentity(object_id=OTHER_BLOB),
                        locator=FileLocator(),
                    )
                ),
                provenance=Authorship(
                    actor_ref="agent:parity-fixture",
                    authorization_ref="MIK-R23 parity",
                    operation_id=uuid4(),
                    recorded_at=datetime.now(UTC).isoformat(),
                ),
            ),
        )
        assert created.state == "created", created.refusal
    finally:
        store.close()
