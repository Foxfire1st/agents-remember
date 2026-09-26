"""The taskless knowledge bootstrap: real authority, the one write plane, and recovery.

``knowledge-ingest`` needs a leaf enclosure contract, so before this leaf the only way a repository's
*knowledge* could be written was by a task that already had a worktree -- and the documented answer
for a repository whose first knowledge is being written was to fabricate one. The bootstrap handover
refuses that, and a second write path is refused by the preservation boundaries, so what these cases
measure is the third option: a second **real** admission, derived from the MCP settings document and
the ordinary read route's own resolution, driving the *same* operation the leaf path drives.

Every case below is one user operation on a real coordination world -- a real code checkout, a real
external memory repository, a real MCP settings document -- driven through the shipped command line
(``agents-remember knowledge-bootstrap``), and every comparison is made against something other than
the run's own prose: the location is derived by the read route's own owner, the identity is read back
through that owner, the stored statements come from the mounted read surface, and the remaining-work
manifest is read off disk.

Nineteen cases, as the user operations and the refusals that guard them:

* **a repository with no leaf bootstraps and publishes where ordinary readers look** -- and the world
  contains no contract file at all, which is what makes "no enclosure was fabricated" a measurement
  rather than a claim;
* **an exact retry reuses operation and record identity** without a duplicate row;
* **existing knowledge is not overwritten** and a deliberate update gets its own revision while the
  earlier one survives inside the successor dataset;
* **an interrupted bootstrap resumes from retained identity-bound progress**, keeping the identity the
  earlier run was allocated;
* **a planning run writes no batch, no publication and no progress record**;
* **cleanup cannot destroy unpublised work**, and removes staging once the work is published -- the
  packet's own non-conforming example, driven both ways -- and it also removes a staging that holds
  no authored row at all, which is a measured zero rather than an assumption;
* **a moved source revision is an explicit re-observation condition** that leaves the previously
  published dataset exactly as it was, and refreshes the retained record with what that run actually
  did -- the record is written by any run given the commit word, and its own fields say a batch was
  not attempted and nothing was published;
* **a refused publication is not success**: the batch committed, the publication was refused, and the
  entry is named remaining against a location measured to hold no dataset;
* **a partial run names the refused entry as remaining** and publishes exactly what committed;
* **owed work is carried across runs**: a narrowed resume keeps the refused entry named on disk, a
  run that commits nothing still refreshes the manifest, and a carried entry the repository has since
  received is re-derived to ``stored`` and is no longer owed;
* **a retained record for another operation is refused, not inherited** -- one staging directory
  belongs to exactly one bootstrap operation, and the refusal names both scopes;
* **the destination is derived, not accepted**: no argument on the surface can aim it elsewhere, and
  the location equals the one the read route's own owner computes; the staging root is the
  bootstrap's own bounded temp area and never a leaf's review candidate root;
* and the refusals that guard all of it: a repository the settings do not declare, and a destination
  that cannot be read as a dataset, are named rather than worked around, and the memory initializer
  reports the knowledge foundation's location and its measured state at both moments.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from agents_remember.application.knowledge_bootstrap_admission import (
    AdmittedKnowledgeBootstrap,
    admit_bootstrap_context,
)
from agents_remember.application.knowledge_bootstrap_staging import (
    BOOTSTRAP_PROGRESS_NAME,
    staged_candidate_directory,
)
from agents_remember.application.knowledge_review import REVIEW_CANDIDATE_RELATIVE_ROOT
from agents_remember.application.memory_tools import memory_init_tool
from agents_remember.application.published_intent import (
    PublishedIntentUnavailable,
    published_dataset_path,
    resolve_published_intent,
)
from agents_remember.cli.__main__ import main
from agents_remember.cli.knowledge_bootstrap import add_arguments
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.mcp.tools.knowledge import ReadToolRequest, knowledge_read_payload
from agents_remember.models.knowledge.snapshot import candidate_database_path

pytestmark = pytest.mark.evidence_unit

REPO_ID = "bootstrap-repo"
AUTHORIZATION = "authorization:test-l29-bootstrap"
CODE_FILE = "pkg/module.py"
CODE_SYMBOL = "resolve_budget"
SECOND_FILE = "pkg/retired.py"
SECOND_SYMBOL = "retired_budget"
MEMORY_CARD = "onboarding/pkg/module.md"

_CODE_TEXT = (
    '"""A governed module."""\n\n\ndef resolve_budget(attempt: int) -> int:\n    return attempt\n'
)
_SECOND_FILE_TEXT = (
    '"""A second governed module."""\n'
    "\n"
    "\n"
    "def retired_budget(attempt: int) -> int:\n"
    "    return attempt\n"
)


@dataclass(frozen=True)
class World:
    """One real coordination world: a code checkout, an external memory repo, and the settings."""

    root: Path
    code_root: Path
    coordination_root: Path
    memory_root: Path
    settings_path: Path
    code_commit: str
    memory_commit: str

    @property
    def destination(self) -> Path:
        """The published location the ordinary read route selects, derived by that route's owner."""

        return published_dataset_path(self.admitted().context)

    def admitted(self) -> AdmittedKnowledgeBootstrap:
        """The bootstrap context this world admits, or the fixture defect that stopped it."""

        admitted = admit_bootstrap_context(load_config(self.settings_path), REPO_ID)
        assert isinstance(admitted, AdmittedKnowledgeBootstrap), admitted
        return admitted


def _git(root: Path, args: Sequence[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(root), "GIT_CONFIG_NOSYSTEM": "1"},
    )
    if result.returncode != 0:
        raise AssertionError(f"fixture git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _write(root: Path, files: dict[str, str]) -> None:
    for relative, text in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


def _commit(root: Path, message: str) -> str:
    _git(root, ["config", "user.email", "fixture@example.invalid"])
    _git(root, ["config", "user.name", "bootstrap fixture"])
    _git(root, ["add", "-A"])
    _git(root, ["commit", "-q", "-m", message])
    return _git(root, ["rev-parse", "HEAD"])


def world(root: Path) -> World:
    """A code repository at ``workspace_root / repo_id``, its external memory repo, and settings.

    The layout is the one the resolver and the settings reader already agree on: the code checkout
    where the workspace search finds it, the memory repository at
    ``<coordination_root>/memory-repos/ar-<repo_id>``, and an MCP settings document outside the
    coordination root declaring both. No leaf, no worktree group and no contract exists anywhere in
    it -- which is the point, and case one asserts it.
    """

    coordination_root = root / "coordination"
    code_root = root / REPO_ID
    memory_root = coordination_root / "memory-repos" / f"ar-{REPO_ID}"
    _write(code_root, {CODE_FILE: _CODE_TEXT, SECOND_FILE: _SECOND_FILE_TEXT})
    _write(memory_root, {MEMORY_CARD: "# pkg module\n\nThe card for the module.\n"})
    _git(code_root, ["init", "-q", "--initial-branch=main"])
    _git(memory_root, ["init", "-q", "--initial-branch=main"])
    code_commit = _commit(code_root, "the code tree")
    memory_commit = _commit(memory_root, "the memory tree")
    settings_path = root / "settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "version": 1,
                "coordinationRoot": coordination_root.as_posix(),
                "workspaceRoot": root.as_posix(),
                "repositories": {REPO_ID: {}},
            }
        ),
        encoding="utf-8",
    )
    return World(
        root=root,
        code_root=code_root,
        coordination_root=coordination_root,
        memory_root=memory_root,
        settings_path=settings_path,
        code_commit=code_commit,
        memory_commit=memory_commit,
    )


def entry(
    entry_id: str, *, symbol_name: str | None = None, path: str | None = None
) -> dict[str, Any]:
    """One hand-off entry: an obligation, citing a construct or naming nothing at all."""

    targets: list[dict[str, Any]] = []
    if symbol_name is not None:
        targets.append(
            {
                "path": path or CODE_FILE,
                "locator": {"kind": "symbol", "value": symbol_name},
                "governing_route": "pkg",
            }
        )
    return {
        "id": entry_id,
        "statement": f"The obligation {entry_id} records.",
        "scope": {
            "applicability": "Calls to the cited constructs in this repository's pkg module.",
            "conditions": [],
            "exclusions": [],
        },
        "kind": "clause",
        "target": targets,
        "found_at": [],
        "disposition": "satisfied",
        "disposition_source": None,
        "evidence": f"{entry_id} terminal report",
        "resolution": None,
        "validated_at": None,
        "record_action": None,
        "supersedes": None,
        "authority": {"governing_route": "pkg", "task_document": "bootstrap_case"},
    }


def hand_off(root: Path, name: str, entries: list[dict[str, Any]]) -> Path:
    path = root / f"{name}.json"
    path.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    return path


def argv(world_one: World, listed: Path, *extra: str) -> list[str]:
    return [
        "knowledge-bootstrap",
        "--repo",
        REPO_ID,
        "--config",
        str(world_one.settings_path),
        "--list",
        str(listed),
        "--authorization-ref",
        AUTHORIZATION,
        *extra,
    ]


def cli(argv_list: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, Any]]:
    """Drive the shipped command line and read its JSON report back."""

    code = main(argv_list)
    captured = capsys.readouterr()
    return code, json.loads(captured.out)


def statements(dataset: Path, repository_id: str) -> list[str]:
    """The statements the MOUNTED read surface answers with, at the dataset's own snapshot."""

    view = knowledge_read_payload(
        ReadToolRequest(database_path=str(dataset), repository_id=repository_id, view="invariant")
    )
    assert view["state"] == "view", view
    return [
        str(row["statement"]) for row in view["payload"]["rows"] if row["fact_kind"] == "statement"
    ]


def revisions(dataset: Path, repository_id: str) -> set[str]:
    """The exact revision identities the dataset holds, read through the view's own subject field."""

    view = knowledge_read_payload(
        ReadToolRequest(database_path=str(dataset), repository_id=repository_id, view="invariant")
    )
    assert view["state"] == "view", view
    return {
        str(row["subject"]["revision_id"])
        for row in view["payload"]["rows"]
        if row["subject"].get("revision_id")
    }


def progress_record(world_one: World) -> dict[str, Any]:
    """The retained remaining-work manifest, read off disk rather than from the run's report."""

    admitted = world_one.admitted()
    path = admitted.staging_root / BOOTSTRAP_PROGRESS_NAME
    assert path.is_file(), f"no progress record was retained at {path}"
    return json.loads(path.read_text(encoding="utf-8"))


def test_a_repository_with_no_leaf_bootstraps_and_publishes_where_readers_look(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The whole operation: no enclosure anywhere, real authority, and a dataset readers select.

    Six facts are measured in the order the run establishes them: nothing in this world is a
    contract, the run's admission names the settings document as its authority, the destination is
    the location the READ route's own owner derives, the publication owner reported ``published``,
    the route's independent read-back confirms that identity, and the mounted read surface answers
    with the authored statement -- so "the repository now holds this knowledge" is read rather than
    inferred from a file appearing.
    """

    one = world(tmp_path / "world")
    assert not list(one.root.rglob("series-contract.md")), "the fixture must contain no enclosure"
    listed = hand_off(one.root, "first", [entry("B-one", symbol_name=CODE_SYMBOL)])

    code, report = cli(argv(one, listed, "--commit", "--json"), capsys)

    assert code == 0, report
    assert report["admissionProvenance"]["kind"] == "repository-bootstrap", report[
        "admissionProvenance"
    ]
    assert report["authoritySource"] == f"{one.settings_path}#repositories.{REPO_ID}", report
    assert report["sourceRevisions"]["codeBaseCommit"] == one.code_commit, report["sourceRevisions"]
    assert report["sourceRevisions"]["memoryBaseCommit"] == one.memory_commit, report[
        "sourceRevisions"
    ]
    assert report["destinationPath"] == str(one.destination), report["destinationPath"]
    assert report["destinationBefore"]["state"] == "not-recorded", report["destinationBefore"]

    assert report["run"]["batchState"] == "changed", report["run"]
    assert report["run"]["committed"] == ["B-one"], report["run"]
    assert report["publication"]["state"] == "published", report["publication"]
    assert report["publication"]["destinationRef"] == str(one.destination), report["publication"]

    identity = report["publication"]["identity"]
    assert identity is not None, report["publication"]
    read_back = report["publishedIdentity"]
    assert read_back["state"] == "confirmed", read_back
    assert read_back["datasetPath"] == str(one.destination), read_back

    assert report["destinationContents"]["state"] == "complete", report["destinationContents"]
    assert report["destinationContents"]["revisions"] == 1, report["destinationContents"]
    assert report["destinationContents"]["publishedByThisRun"] is True, report[
        "destinationContents"
    ]
    assert statements(one.destination, identity["repositoryId"]) == [
        "The obligation B-one records."
    ]
    assert report["remaining"] == [], report["remaining"]
    assert report["progressRecordWritten"] is True, report


def test_an_exact_retry_reuses_operation_and_record_identity(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The same list again: the same identities, no duplicate row, and the same dataset contents.

    Both halves are measured separately, because "the run reported a replay" and "the store holds one
    revision" are different claims: the revision set is read out of both datasets, and the identities
    the candidate's journal holds are compared between the two runs.
    """

    one = world(tmp_path / "world")
    listed = hand_off(one.root, "first", [entry("B-one", symbol_name=CODE_SYMBOL)])

    first_code, first = cli(argv(one, listed, "--commit", "--json"), capsys)
    assert first_code == 0, first
    first_identity = first["publication"]["identity"]
    first_revisions = revisions(one.destination, first_identity["repositoryId"])
    first_held = {
        one_row["entryId"]: one_row["revisionId"] for one_row in first["run"]["heldOperations"]
    }
    assert first_held == {"B-one": sorted(first_revisions)[0]}, first["run"]["heldOperations"]

    second_code, second = cli(argv(one, listed, "--commit", "--json"), capsys)

    assert second_code == 0, second
    second_held = {
        one_row["entryId"]: one_row["revisionId"] for one_row in second["run"]["heldOperations"]
    }
    assert second_held == first_held, (first_held, second_held)
    assert revisions(one.destination, first_identity["repositoryId"]) == first_revisions
    assert second["publishedIdentity"]["state"] == "confirmed", second["publishedIdentity"]
    assert second["run"]["refused"] == [], second["run"]
    assert second["run"]["committed"] == ["B-one"], second["run"]


def test_existing_knowledge_is_not_overwritten_and_a_semantic_update_gets_its_own_revision(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Reinitialization keeps what is there; a deliberate update adds a revision beside it.

    The second run's list carries the first obligation again (an exact replay) plus one new
    obligation. The stored statement of the first must be byte-identical afterwards, the dataset must
    hold two revisions rather than one or three, and the dataset identity must have moved -- which is
    the explicit update of exactly the identity the run admitted, not an overwrite behind it.
    """

    one = world(tmp_path / "world")
    first_list = hand_off(one.root, "first", [entry("B-one", symbol_name=CODE_SYMBOL)])
    first_code, first = cli(argv(one, first_list, "--commit", "--json"), capsys)
    assert first_code == 0, first
    namespace = first["publication"]["identity"]["repositoryId"]
    before = revisions(one.destination, namespace)
    assert len(before) == 1, before
    assert statements(one.destination, namespace) == ["The obligation B-one records."]

    grown = hand_off(
        one.root,
        "grown",
        [
            entry("B-one", symbol_name=CODE_SYMBOL),
            entry("B-two", symbol_name=SECOND_SYMBOL, path=SECOND_FILE),
        ],
    )
    second_code, second = cli(argv(one, grown, "--commit", "--json"), capsys)

    assert second_code == 0, second
    assert second["destinationBefore"]["state"] == "recorded", second["destinationBefore"]
    assert (
        second["destinationBefore"]["identity"]["logicalDigest"]
        == first["publication"]["identity"]["logicalDigest"]
    ), second["destinationBefore"]
    after = revisions(one.destination, namespace)
    assert before < after, (before, after)
    assert len(after) == 2, after
    stored = statements(one.destination, namespace)
    assert stored.count("The obligation B-one records.") == 1, stored
    assert "The obligation B-two records." in stored, stored
    assert second["publishedIdentity"]["state"] == "confirmed", second["publishedIdentity"]
    assert second["remaining"] == [], second


def test_an_interrupted_bootstrap_resumes_from_retained_identity_bound_progress(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A first batch is interrupted after publishing; the resume keeps its identity and adds the rest.

    Interruption is modelled at the only boundary the operation has: the first run carries one entry
    and finishes, the second carries the whole list. What a resume has to reuse is the identity the
    earlier run was ALLOCATED, so that is what is compared -- and the retained manifest read off disk
    must name the remaining work of the first run and then of the second.
    """

    one = world(tmp_path / "world")
    partial = hand_off(one.root, "partial", [entry("B-one", symbol_name=CODE_SYMBOL)])
    first_code, first = cli(argv(one, partial, "--commit", "--json"), capsys)
    assert first_code == 0, first
    namespace = first["publication"]["identity"]["repositoryId"]
    first_revision = sorted(revisions(one.destination, namespace))
    assert len(first_revision) == 1, first_revision
    retained = progress_record(one)
    assert retained["remaining"] == [], retained["remaining"]
    assert (
        retained["destinationIdentity"]["logicalDigest"]
        == first["publication"]["identity"]["logicalDigest"]
    ), retained["destinationIdentity"]

    whole = hand_off(
        one.root,
        "whole",
        [
            entry("B-one", symbol_name=CODE_SYMBOL),
            entry("B-two", symbol_name=SECOND_SYMBOL, path=SECOND_FILE),
        ],
    )
    second_code, second = cli(argv(one, whole, "--commit", "--json"), capsys)

    assert second_code == 0, second
    held = {
        one_row["entryId"]: one_row["revisionId"] for one_row in second["run"]["heldOperations"]
    }
    assert held["B-one"] == first_revision[0], (held, first_revision)
    after = revisions(one.destination, namespace)
    assert first_revision[0] in after, (first_revision, after)
    assert len(after) == 2, after
    resumed = progress_record(one)
    assert resumed["remaining"] == [], resumed["remaining"]
    assert resumed["unmeasured"] == [], resumed
    assert resumed["destinationReadBack"] == "confirmed", resumed["destinationReadBack"]
    assert (
        resumed["destinationIdentity"]["logicalDigest"]
        == second["publication"]["identity"]["logicalDigest"]
    ), resumed["destinationIdentity"]
    assert second["staging"]["retainedBefore"] == "retained", second["staging"]


def test_a_planning_run_writes_no_batch_no_publication_and_no_progress_record(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without the commit word: a report, and nothing else anywhere on disk.

    The progress record is the one artifact a reader might assume exists merely because a run
    happened, so its absence is asserted directly -- a manifest written by a planning run would be a
    claim about a publication that never occurred.
    """

    one = world(tmp_path / "world")
    listed = hand_off(one.root, "first", [entry("B-one", symbol_name=CODE_SYMBOL)])

    code, report = cli(argv(one, listed, "--json"), capsys)

    assert code == 0, report
    assert report["run"]["dryRun"] is True, report["run"]
    assert report["publication"]["state"] == "not-selected", report["publication"]
    assert report["publishedIdentity"] is None, report["publishedIdentity"]
    assert report["destinationPath"] == str(one.destination)
    assert not one.destination.exists(), "a planning run published a dataset"
    assert report["progressRecordWritten"] is False, report
    admitted = one.admitted()
    assert not (admitted.staging_root / BOOTSTRAP_PROGRESS_NAME).exists()
    assert not candidate_database_path(staged_candidate_directory(admitted.staging_root)).exists()


def test_cleanup_cannot_destroy_unpublised_work_and_removes_published_staging(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The packet's non-conforming example, driven both ways.

    First: the published dataset is taken away, so the staged candidate holds the only copy of the
    authored rows. Cleanup must refuse by name and leave every byte -- which is asserted by re-reading
    the staged candidate's own revision set afterwards. Second: the run is repeated, the publication
    lands again, and cleanup then removes the staging root while the published dataset is untouched.
    """

    one = world(tmp_path / "world")
    listed = hand_off(one.root, "first", [entry("B-one", symbol_name=CODE_SYMBOL)])
    code, first = cli(argv(one, listed, "--commit", "--json"), capsys)
    assert code == 0, first
    admitted = one.admitted()
    staged = candidate_database_path(staged_candidate_directory(admitted.staging_root))
    staged_revisions = revisions(staged, first["publication"]["identity"]["repositoryId"])
    assert len(staged_revisions) == 1, staged_revisions

    one.destination.unlink()
    refused_code, refused = cli(
        [
            "knowledge-bootstrap",
            "--repo",
            REPO_ID,
            "--config",
            str(one.settings_path),
            "--discard-staging",
            "--json",
        ],
        capsys,
    )

    assert refused_code == 2, refused
    assert refused["code"] == "destination_does_not_hold_the_staged_dataset", refused
    assert admitted.staging_root.exists(), (
        "the staging root was removed while it held the only copy"
    )
    assert revisions(staged, first["publication"]["identity"]["repositoryId"]) == staged_revisions

    republished_code, republished = cli(argv(one, listed, "--commit", "--json"), capsys)
    assert republished_code == 0, republished
    assert republished["publishedIdentity"]["state"] == "confirmed", republished[
        "publishedIdentity"
    ]

    discarded_code, discarded = cli(
        [
            "knowledge-bootstrap",
            "--repo",
            REPO_ID,
            "--config",
            str(one.settings_path),
            "--discard-staging",
            "--json",
        ],
        capsys,
    )

    assert discarded_code == 0, discarded
    assert discarded["state"] == "cleanup", discarded
    assert discarded["code"] == "staging_discarded", discarded
    assert not admitted.staging_root.exists(), discarded
    assert one.destination.is_file(), discarded


def test_a_moved_source_revision_is_an_explicit_re_observation_condition(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A new commit on the code line stops the resume and leaves the published dataset alone.

    The admission binds the candidate to the exact code revision it observed, so the code line moving
    under a staged candidate is not a detail to absorb: the run must refuse to continue, and the
    dataset a reader selects must be byte-identical afterwards. That is the "previous valid dataset
    survives a failed run" half of the packet, measured by comparing the file's own dataset identity
    before and after.
    """

    one = world(tmp_path / "world")
    listed = hand_off(one.root, "first", [entry("B-one", symbol_name=CODE_SYMBOL)])
    code, first = cli(argv(one, listed, "--commit", "--json"), capsys)
    assert code == 0, first
    published = first["publication"]["identity"]
    before = one.destination.read_bytes()

    _write(one.code_root, {"pkg/added.py": "# the line moved on\n"})
    _commit(one.code_root, "the code line moves on")

    second_code, second = cli(argv(one, listed, "--commit", "--json"), capsys)

    assert second_code == 0, second
    assert second["sourceRevisions"]["codeBaseCommit"] != first["sourceRevisions"]["codeBaseCommit"]
    assert second["run"]["batchState"] == "not_attempted", second["run"]
    assert second["publication"]["state"] == "not-selected", second["publication"]
    refused = {one_row["entryId"]: one_row for one_row in second["entries"]}
    assert refused["B-one"]["outcome"] == "refused", refused
    assert "candidate_binding_changed" in refused["B-one"]["detail"], refused["B-one"]
    # The run's outcome and the store's answer are recorded side by side, and here they genuinely
    # differ: this run wrote nothing, while the knowledge the list carries is already published.
    assert refused["B-one"]["storeState"] == "stored", refused["B-one"]
    assert second["remaining"] == [], second["remaining"]
    # A committed-word run now refreshes the record even when its batch wrote nothing, and every
    # field of what it writes says what that run actually did -- so the manifest cannot go stale
    # exactly when the candidate binding moved.
    assert second["progressRecordWritten"] is True, second
    retained = progress_record(one)
    assert retained["batchState"] == "not_attempted", retained["batchState"]
    assert retained["publicationState"] == "not-selected", retained["publicationState"]
    assert retained["destinationReadBack"] == "not-published", retained["destinationReadBack"]
    assert retained["destinationState"] == "recorded", retained["destinationState"]
    assert one.destination.read_bytes() == before, "a failed run replaced the published dataset"
    after = resolve_published_intent(one.admitted().context)
    assert not isinstance(after, PublishedIntentUnavailable), after
    assert after.logical_digest == published["logicalDigest"], after


def test_a_repository_the_settings_do_not_declare_is_refused_by_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Authority is the settings document, not the caller: an undeclared repository has no context."""

    one = world(tmp_path / "world")
    listed = hand_off(one.root, "first", [entry("B-one", symbol_name=CODE_SYMBOL)])

    code, report = cli(
        [
            "knowledge-bootstrap",
            "--repo",
            "ghost-repo",
            "--config",
            str(one.settings_path),
            "--list",
            str(listed),
            "--authorization-ref",
            AUTHORIZATION,
            "--commit",
            "--json",
        ],
        capsys,
    )

    assert code == 2, report
    assert report["code"] == "repository_not_allowed", report
    assert REPO_ID in report["detail"], report
    assert not one.destination.exists(), report


def test_the_destination_is_derived_and_no_argument_can_aim_it_elsewhere(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The taskless route has no destination argument, and its location is the read route's own.

    A caller-supplied destination would be the "second convention that agrees today" this route
    exists to remove, so the surface is asserted rather than trusted: the admitted destination equals
    what :func:`published_dataset_path` derives for the same context, and the subcommand's own
    argument list carries nothing that names a dataset path.
    """

    one = world(tmp_path / "world")
    admitted = one.admitted()
    assert admitted.destination_path == published_dataset_path(admitted.context)
    assert admitted.destination_path.name == "knowledge.sqlite"

    parser = argparse.ArgumentParser()
    add_arguments(parser)
    options = {action.dest for action in parser._actions}
    assert "publish_to" not in options, options
    assert "destination" not in options, options
    assert "expected_destination" not in options, options


def test_a_repository_without_a_leaf_writes_its_only_candidate_under_the_staging_root(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Nothing the bootstrap writes lands in a leaf's review candidate directory.

    The leaf route's canonical candidate root is derived from a contract's worktree group; a taskless
    bootstrap has neither, so its working candidate must be inside its own bounded staging root and
    the leaf-shaped directory must not be created anywhere in this world.
    """

    one = world(tmp_path / "world")
    listed = hand_off(one.root, "first", [entry("B-one", symbol_name=CODE_SYMBOL)])
    code, report = cli(argv(one, listed, "--commit", "--json"), capsys)
    assert code == 0, report
    assert not list(one.root.rglob(REVIEW_CANDIDATE_RELATIVE_ROOT.as_posix())), (
        "a leaf candidate root appeared"
    )
    assert report["staging"]["root"].startswith(str(one.coordination_root / "temp")), report[
        "staging"
    ]
    assert Path(report["staging"]["root"]).is_dir(), report["staging"]


def test_memory_init_names_the_knowledge_foundation_and_what_is_there_now(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The kernel memory setup reports the knowledge location, and never invents its contents.

    Two moments are measured. Before any bootstrap the initializer names the location the ordinary
    read route selects and reports ``not-recorded`` -- so the gap is visible rather than silent. After
    a bootstrap the same call reports ``recorded`` with the dataset's own identity, which is the
    "a new project's knowledge is included in the legitimate first memory baseline" half: the
    foundation is populated by the curator's authored run and simply observed by the initializer.
    """

    one = world(tmp_path / "world")
    config = load_config(one.settings_path)

    before = memory_init_tool(config, repo_id=REPO_ID, dry_run=True, initial_branch="main")

    assert before["knowledge"]["state"] == "not-recorded", before["knowledge"]
    assert before["knowledge"]["datasetPath"] == str(one.destination), before["knowledge"]
    assert "knowledge-bootstrap" in before["knowledge"]["nextAction"], before["knowledge"]

    listed = hand_off(one.root, "first", [entry("B-one", symbol_name=CODE_SYMBOL)])
    code, report = cli(argv(one, listed, "--commit", "--json"), capsys)
    assert code == 0, report

    after = memory_init_tool(config, repo_id=REPO_ID, dry_run=True, initial_branch="main")

    assert after["knowledge"]["state"] == "recorded", after["knowledge"]
    assert after["knowledge"]["datasetPath"] == str(one.destination), after["knowledge"]
    assert (
        after["knowledge"]["detail"].find(report["publication"]["identity"]["logicalDigest"]) >= 0
    ), after["knowledge"]


def test_an_unusable_destination_refuses_instead_of_publishing_over_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Something that is not a dataset at the declared location is refused, not replaced.

    The bytes are put there by the case itself, and the assertion is that they are byte-identical
    afterwards: a bootstrap may not turn "the location holds something I cannot read" into "the
    location holds my new knowledge".
    """

    one = world(tmp_path / "world")
    one.destination.parent.mkdir(parents=True, exist_ok=True)
    one.destination.write_text("this is not a dataset\n", encoding="utf-8")
    listed = hand_off(one.root, "first", [entry("B-one", symbol_name=CODE_SYMBOL)])

    code, report = cli(argv(one, listed, "--commit", "--json"), capsys)

    assert code == 2, report
    assert report["code"] == "destination_unusable", report
    assert one.destination.read_text(encoding="utf-8") == "this is not a dataset\n", report


def test_a_partial_run_names_the_refused_entry_as_remaining_and_publishes_only_what_committed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """One list, one committed entry and one refused: each named, and only the committed one stored.

    The remaining-work manifest is worthless if it counts a refused entry as work still to come
    without saying why, so both halves are read: the report names the refusal per entry, the dataset
    holds exactly the committed revision, and the manifest retained on disk names the refused entry as
    remaining with the store state that makes it so -- while the published entry is not called
    remaining merely because a later read might have been unsure.
    """

    one = world(tmp_path / "world")
    listed = hand_off(
        one.root,
        "partial",
        [
            entry("B-good", symbol_name=CODE_SYMBOL),
            entry("B-bad", symbol_name="no_such_construct"),
        ],
    )

    code, report = cli(argv(one, listed, "--commit", "--json"), capsys)

    assert code == 0, report
    assert report["run"]["committed"] == ["B-good"], report["run"]
    assert report["run"]["refused"] == ["B-bad"], report["run"]
    assert report["publication"]["state"] == "published", report["publication"]
    namespace = report["publication"]["identity"]["repositoryId"]
    assert len(revisions(one.destination, namespace)) == 1, revisions(one.destination, namespace)
    assert report["remaining"] == ["B-bad"], report["remaining"]
    assert report["unmeasured"] == [], report["unmeasured"]
    by_id = {one_row["entryId"]: one_row for one_row in report["entries"]}
    assert by_id["B-good"]["storeState"] == "stored", by_id
    assert by_id["B-bad"]["storeState"] == "not-attempted", by_id
    retained = progress_record(one)
    assert retained["remaining"] == ["B-bad"], retained["remaining"]
    assert retained["unmeasured"] == [], retained["unmeasured"]
    assert "B-good" not in retained["remaining"], retained["remaining"]


def test_cleanup_removes_a_staging_that_holds_no_authored_row(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A run that committed nothing still has a staging root, and cleanup may remove it.

    The batch is all-or-nothing, so a candidate holding no invariant revision holds no committed
    authored row -- which is a measurement of zero, not an assumption, and it is what lets the cleanup
    owner stay reachable after a run that never got as far as publishing. The case asserts the
    measured fact as well: the staged candidate really is empty, and the published location really
    holds nothing.
    """

    one = world(tmp_path / "world")
    listed = hand_off(one.root, "refused-only", [entry("B-bad", symbol_name="no_such_construct")])
    code, report = cli(argv(one, listed, "--commit", "--json"), capsys)

    assert code == 0, report
    assert report["run"]["committed"] == [], report["run"]
    assert report["run"]["refused"] == ["B-bad"], report["run"]
    assert report["publication"]["state"] == "not-selected", report["publication"]
    assert not one.destination.exists(), "a run that committed nothing published a dataset"

    admitted = one.admitted()
    staged = candidate_database_path(staged_candidate_directory(admitted.staging_root))
    assert staged.is_file(), "the run created no candidate to stage"
    assert report["destinationContents"]["state"] == "absent", report["destinationContents"]
    assert report["destinationContents"]["publishedByThisRun"] is False, report[
        "destinationContents"
    ]

    discarded_code, discarded = cli(
        [
            "knowledge-bootstrap",
            "--repo",
            REPO_ID,
            "--config",
            str(one.settings_path),
            "--discard-staging",
            "--json",
        ],
        capsys,
    )

    assert discarded_code == 0, discarded
    assert discarded["code"] == "staging_discarded", discarded
    assert "no invariant revision" in discarded["detail"], discarded
    assert not admitted.staging_root.exists(), discarded


def test_a_refused_publication_is_not_success_and_names_the_work_as_remaining(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The batch commits, the publication is refused, and the report does not call that success.

    The memory line is made unwritable for the length of the run, which is the one way a publication
    can fail while the batch it publishes has already committed. Four facts are then measured: the
    batch really did commit, the publication owner refused, no dataset exists at the declared
    location, and the entry is named as **remaining** rather than quietly dropped -- because the
    location holding no dataset is a measured absence, not an unknown. The staged candidate keeps the
    authored work, which is what makes resuming possible at all.
    """

    one = world(tmp_path / "world")
    listed = hand_off(one.root, "first", [entry("B-one", symbol_name=CODE_SYMBOL)])
    one.memory_root.chmod(0o500)
    try:
        code, report = cli(argv(one, listed, "--commit", "--json"), capsys)
    finally:
        one.memory_root.chmod(0o700)

    assert code == 0, report
    assert report["run"]["committed"] == ["B-one"], report["run"]
    assert report["publication"]["state"] == "refused", report["publication"]
    assert report["publication"]["refusalCode"] is not None, report["publication"]
    assert report["publication"]["destinationRef"] is None, report["publication"]
    assert report["publishedIdentity"] is None, report["publishedIdentity"]
    assert not one.destination.exists(), "a refused publication installed a dataset"
    assert report["remaining"] == ["B-one"], report["remaining"]
    assert report["unmeasured"] == [], report["unmeasured"]
    assert report["entries"][0]["storeState"] == "absent", report["entries"][0]
    contents = report["destinationContents"]
    assert contents["publishedByThisRun"] is False, contents
    assert "this run published nothing" in contents["detail"], contents
    retained = progress_record(one)
    assert retained["remaining"] == ["B-one"], retained["remaining"]
    assert retained["publicationState"] == "refused", retained["publicationState"]
    assert retained["destinationReadBack"] == "not-published", retained["destinationReadBack"]
    assert retained["destinationState"] == "not-recorded", retained["destinationState"]
    assert retained["destinationIdentity"] is None, retained["destinationIdentity"]

    one.memory_root.chmod(0o700)
    republished_code, republished = cli(argv(one, listed, "--commit", "--json"), capsys)

    assert republished_code == 0, republished
    assert republished["publication"]["state"] == "published", republished["publication"]
    assert republished["remaining"] == [], republished["remaining"]
    assert one.destination.is_file(), republished


def test_a_retained_record_for_another_operation_refuses_before_anything_is_written(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """One staging directory belongs to exactly one bootstrap operation, and it says so by name.

    The retained record is rewritten to name another scope, which is the state a second operation's
    progress in the same directory would produce. The run must refuse **before** it reads a list or
    touches the destination, and the destination's bytes must be identical afterwards -- a refusal
    that had already published would be a different failure, not this guard.
    """

    one = world(tmp_path / "world")
    listed = hand_off(one.root, "first", [entry("B-one", symbol_name=CODE_SYMBOL)])
    first_code, first = cli(argv(one, listed, "--commit", "--json"), capsys)
    assert first_code == 0, first
    assert first["publication"]["state"] == "published", first["publication"]
    before = one.destination.read_bytes()
    admitted = one.admitted()
    record_path = admitted.staging_root / BOOTSTRAP_PROGRESS_NAME
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["scope"] = "knowledge-bootstrap:some-other-repo"
    record_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    second_code, second = cli(argv(one, listed, "--commit", "--json"), capsys)

    assert second_code == 2, second
    assert second["code"] == "staging_belongs_to_another_operation", second
    assert "knowledge-bootstrap:some-other-repo" in second["detail"], second
    assert f"knowledge-bootstrap:{REPO_ID}" in second["detail"], second
    assert one.destination.read_bytes() == before, "a refused run touched the destination"


def test_a_narrowed_resume_keeps_the_owed_entry_named_on_disk(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Handing over only the part that succeeded must not delete the rest of the debt.

    Run one carries a committed entry and a refused one, so the record names the refused entry as
    owed. Run two hands over **only** the committed entry: it replays, and the record it writes must
    still name the refused entry -- carried by operation identity and re-derived against run two's own
    store read, not copied -- or a curator narrowing a resumed list would silently lose owed work.
    """

    one = world(tmp_path / "world")
    first_list = hand_off(
        one.root,
        "first",
        [entry("E-OK", symbol_name=CODE_SYMBOL), entry("E-BAD", symbol_name="no_such_construct")],
    )
    first_code, first = cli(argv(one, first_list, "--commit", "--json"), capsys)
    assert first_code == 0, first
    assert first["remaining"] == ["E-BAD"], first["remaining"]
    retained = progress_record(one)
    assert retained["remaining"] == ["E-BAD"], retained["remaining"]

    narrowed = hand_off(one.root, "narrowed", [entry("E-OK", symbol_name=CODE_SYMBOL)])
    second_code, second = cli(argv(one, narrowed, "--commit", "--json"), capsys)

    assert second_code == 0, second
    assert second["run"]["committed"] == ["E-OK"], second["run"]
    assert second["remaining"] == ["E-BAD"], second["remaining"]
    assert second["carried"] == ["E-BAD"], second["carried"]
    retained = progress_record(one)
    assert retained["remaining"] == ["E-BAD"], retained["remaining"]
    assert retained["carried"] == ["E-BAD"], retained["carried"]
    row = {one_row["entryId"]: one_row for one_row in retained["entries"]}["E-BAD"]
    assert row["outcome"] == "carried", row
    assert row["storeState"] == "not-attempted", row


def test_a_run_that_commits_nothing_still_refreshes_the_manifest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A committed-word run that wrote nothing still has a true observation to retain.

    Run one publishes and leaves no owed work. Run two hands over a single entry that cannot resolve:
    the batch commits nothing and nothing is published, so the *manifest* is the only place the owed
    entry can be named. It must be refreshed -- a stale record reading ``remaining: []`` while the run
    it just made names an owed entry is the manifest contradicting the run that owns it -- and the
    destination must be untouched by both.
    """

    one = world(tmp_path / "world")
    first_list = hand_off(one.root, "first", [entry("E-OK", symbol_name=CODE_SYMBOL)])
    first_code, first = cli(argv(one, first_list, "--commit", "--json"), capsys)
    assert first_code == 0, first
    assert first["remaining"] == [], first["remaining"]
    observed = progress_record(one)["observedAt"]
    before = one.destination.read_bytes()

    failing = hand_off(one.root, "failing", [entry("E-BAD", symbol_name="no_such_construct")])
    second_code, second = cli(argv(one, failing, "--commit", "--json"), capsys)

    assert second_code == 0, second
    assert second["run"]["committed"] == [], second["run"]
    assert second["publication"]["state"] == "not-selected", second["publication"]
    assert second["remaining"] == ["E-BAD"], second["remaining"]
    retained = progress_record(one)
    assert retained["observedAt"] != observed, "a run that wrote nothing left the manifest stale"
    assert retained["remaining"] == ["E-BAD"], retained["remaining"]
    assert retained["runMode"] == "committed", retained["runMode"]
    assert retained["publicationState"] == "not-selected", retained["publicationState"]
    assert one.destination.read_bytes() == before, (
        "a run that committed nothing touched the dataset"
    )


def test_a_carried_entry_the_repository_has_since_received_is_no_longer_owed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Carried owed work is re-derived against the current store, not copied from the record.

    Run one commits its entry and fails to publish, so the record owes it with ``storeState absent``.
    Run two carries a *different* entry and publishes successfully -- and because the candidate is the
    one the first run built, the publication installs the first run's revision too. The carried entry
    is therefore no longer owed, and a manifest that copied the retained row would name work that the
    repository already holds. Both halves are read: the record's carried row says ``stored``, and the
    reopened dataset holds both revisions.
    """

    one = world(tmp_path / "world")
    first_list = hand_off(one.root, "first", [entry("E-OK", symbol_name=CODE_SYMBOL)])
    one.memory_root.chmod(0o500)
    try:
        first_code, first = cli(argv(one, first_list, "--commit", "--json"), capsys)
    finally:
        one.memory_root.chmod(0o700)
    assert first_code == 0, first
    assert first["run"]["committed"] == ["E-OK"], first["run"]
    assert first["publication"]["state"] == "refused", first["publication"]
    owed = progress_record(one)
    assert owed["remaining"] == ["E-OK"], owed["remaining"]
    owed_revision = {one_row["entryId"]: one_row for one_row in owed["entries"]}["E-OK"][
        "allocatedRevisionId"
    ]

    second_list = hand_off(
        one.root, "second", [entry("E-NEW", symbol_name=SECOND_SYMBOL, path=SECOND_FILE)]
    )
    second_code, second = cli(argv(one, second_list, "--commit", "--json"), capsys)

    assert second_code == 0, second
    assert second["publication"]["state"] == "published", second["publication"]
    namespace = second["publication"]["identity"]["repositoryId"]
    after = revisions(one.destination, namespace)
    assert len(after) == 2, after
    assert owed_revision in after, (owed_revision, after)
    assert second["carried"] == ["E-OK"], second["carried"]
    assert second["remaining"] == [], second["remaining"]
    retained = progress_record(one)
    carried_row = {one_row["entryId"]: one_row for one_row in retained["entries"]}["E-OK"]
    assert carried_row["outcome"] == "carried", carried_row
    assert carried_row["storeState"] == "stored", carried_row
    assert owed_revision in carried_row["storeDetail"], carried_row
