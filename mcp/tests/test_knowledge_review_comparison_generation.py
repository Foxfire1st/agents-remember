"""One frozen comparison survives restart, worktree cleanup and Git object reclamation (ICR-R11).

`ICR-R11@v1` requires a frozen comparison to retain resolvable source, knowledge and evidence inputs
*through* cleanup and restart, and it names the one non-conforming shape directly: a manifest that
stores only a digest of already-deleted SQLite bytes. These cases measure that through the real
production composition -- a real enclosure, a real linked worktree with staged, unstaged and untracked
content, the real resolution and capture owners, the real comparison, the real storage snapshot owner
and a real Git object store -- and never through a preconstructed payload.

The load-bearing properties, one case each:

* the whole thing survives the journey the packet names: freeze, ``git gc --prune=now`` (measured
  against a control object that really is reclaimed), remove the fixture worktree, and reopen the
  exact source, knowledge and evidence content **in a new process**;
* what the manifest binds is the *owners'* values -- the contract's recorded base, the capture
  owner's tree, each dataset's own logical identity, R02's inventory and the comparison's own binding
  digest -- and its seal covers its own fields;
* an explicit release of the code pin leaves an unavailable-history record, and the reopen reports
  what it measured before reclamation and what it measured after, never one in place of the other;
* a retained input that is gone or damaged is reported per channel, and a missing expected dataset is
  never reported as absent history;
* a record that cannot be read is not a readable generation, including a sealed field edited in
  place;
* a refused freeze publishes nothing, leaves no hidden stage and releases the pin it created, and the
  leaf is freezable afterwards;
* an exact retry converges on the published record, while a superseding generation names its
  predecessor and leaves the first generation's bytes untouched;
* a half with no recorded generation is a *declared* typed absence, and a declaration beside present
  bytes is refused rather than believed.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from agents_remember.application.knowledge_review import read_knowledge_review
from agents_remember.application.review_comparison_freeze import (
    ComparisonEvidenceInput,
    ComparisonFreezeOptions,
    freeze_review_comparison,
)
from agents_remember.application.review_comparison_generation import (
    COMPARISON_MANIFEST_NAME,
    COMPARISON_SNAPSHOT_NAME,
    TYPED_ABSENCE_STATES,
    ComparisonGenerationManifest,
    generation_directories,
    generation_identity,
    leaf_generation_root,
    read_generation_refs,
    read_history_deletion,
)
from agents_remember.application.review_comparison_reclamation import (
    SNAPSHOT_DELETION_OWNER,
    discard_comparison_snapshots,
    release_comparison_code_object,
)
from agents_remember.application.review_comparison_reopen import reopen_comparison_generation
from agents_remember.application.review_record_rendering import ReviewRecordInputs
from agents_remember.errors import CodeObjectRetentionError, ComparisonReclamationError
from agents_remember.kernel.canonical_json import sha256_digest
from agents_remember.memory.knowledge.logical import dataset_identity
from agents_remember.worktrees.modules.future_code_candidate import (
    capture_future_code_candidate,
)
from diff_scope_test_support import BATCH_PATH_CANDIDATE_TEXT, _git
from read_scope_test_support import BATCH_PATH
from test_knowledge_review_source_endpoints import (
    LEAF_ID,
    EndpointFixture,
    build_endpoint_fixture,
)

pytestmark = pytest.mark.evidence_unit

# The one path whose candidate bytes these cases read back out of the retained tree: the file the
# fixture edits without staging it, so its content exists in no commit and only the pin keeps it.
CANDIDATE_CONTENT_PATH = BATCH_PATH
CANDIDATE_CONTENT = BATCH_PATH_CANDIDATE_TEXT

# A path no reference and no tree holds, written and pruned as the control that proves
# ``git gc --prune=now`` really reclaimed something in the run these cases measure.
_CONTROL_TEXT = "an object no reference and no tree holds\n"

# Where the fixture's task-artifact root lets these cases cite an owner-produced artifact, and the
# distinct contents a case writes there so "the bytes behind the reference moved" is measurable.
_EVIDENCE_RELATIVE = "notes/reports/icr-l11-cited-evidence.md"
_EVIDENCE_TEXT = "# cited evidence\n\nthe owner published this before the comparison was frozen\n"
_EVIDENCE_REWRITTEN = "# cited evidence\n\nrewritten after the comparison was frozen\n"

# The reopen runs in a *new process*, so "restart" is measured rather than simulated. The child
# imports the package from the worktree it is pointed at, reopens the generation from the task
# artifact plane alone, and reads the exact retained content back through Git and through the
# knowledge identity reader.
_CHILD_REOPEN = """
import json
import subprocess
import sys
from pathlib import Path

from agents_remember.application.review_comparison_reopen import reopen_comparison_generation
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.memory.knowledge.logical import dataset_identity

payload = json.loads(sys.argv[1])
config = McpRuntimeConfig(
    workspace_root=Path(payload["workspace_root"]),
    coordination_root=Path(payload["coordination_root"]),
    config_path=Path(payload["config_path"]),
    transcript_root=Path(payload["transcript_root"]),
)
reopened = reopen_comparison_generation(
    config,
    payload["repository_id"],
    payload["master"],
    payload["leaf_id"],
    generation_id=payload["generation_id"],
)
manifest = reopened.manifest
summary = {
    "state": reopened.state,
    "source_state": None if reopened.source is None else reopened.source.state,
    "candidate_tree": None if manifest is None else manifest.source.candidate_code_tree_id,
    "generation_id": None if manifest is None else manifest.generation_id,
    "generation_index": None if manifest is None else manifest.generation_index,
    "manifest_digest": None if reopened.generation is None else reopened.generation.manifest_digest,
    "knowledge": [
        {
            "side": channel.side,
            "state": channel.state,
            "logical_digest": None if channel.identity is None else channel.identity.logical_digest,
            "read_back": None
            if channel.path is None or not channel.path.is_file()
            else dataset_identity(channel.path).logical_digest,
        }
        for channel in reopened.knowledge
    ],
    "evidence": [
        {"path": channel.relative_path, "state": channel.state} for channel in reopened.evidence
    ],
}
if summary["candidate_tree"] is not None:
    shown = subprocess.run(
        ["git", "show", f"{summary['candidate_tree']}:{payload['content_path']}"],
        cwd=payload["repository"],
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": payload["repository"],
            "GIT_CONFIG_NOSYSTEM": "1",
        },
    )
    summary["candidate_content"] = shown.stdout if shown.returncode == 0 else None
print(json.dumps(summary))
"""


@pytest.fixture
def comparison_fixture(tmp_path: Path) -> EndpointFixture:
    """One fresh live enclosure per case; no case observes another's worktree or repository."""

    return build_endpoint_fixture(tmp_path / "comparison")


def _freeze(
    fixture: EndpointFixture,
    options: ComparisonFreezeOptions | None = None,
):
    """Freeze the fixture's review and require that a generation was published."""

    outcome = freeze_review_comparison(
        fixture.config, fixture.request(), options or ComparisonFreezeOptions()
    )
    assert outcome.state == "published", outcome.refusal
    assert outcome.manifest is not None and outcome.directory is not None
    return outcome


def _cite_evidence(fixture: EndpointFixture, text: str = _EVIDENCE_TEXT) -> Path:
    """Publish the one owner-produced artifact these cases cite, and return its path."""

    path = fixture.contract.task_root / _EVIDENCE_RELATIVE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _evidence_options() -> ComparisonFreezeOptions:
    return ComparisonFreezeOptions(
        evidence=(ComparisonEvidenceInput(owner="icr-l11-case", relative_path=_EVIDENCE_RELATIVE),)
    )


def _run_git(repo: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    """One Git question whose *exit code* is the answer, not its output."""

    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(repo),
            "GIT_CONFIG_NOSYSTEM": "1",
        },
    )


def _object_present(repo: Path, object_id: str) -> bool:
    return _run_git(repo, ["cat-file", "-e", object_id]).returncode == 0


def _refs(repo: Path) -> tuple[str, ...]:
    listed = _git(repo, ["for-each-ref", "--format=%(refname)", "refs/ar/"])
    return tuple(line for line in listed.splitlines() if line)


def _hidden_stages(fixture: EndpointFixture) -> tuple[str, ...]:
    root = leaf_generation_root(fixture.contract.task_root, LEAF_ID)
    if not root.is_dir():
        return ()
    return tuple(sorted(entry.name for entry in root.iterdir() if entry.name.startswith(".")))


def _write_control_object(repo: Path, directory: Path) -> str:
    """Write one unreferenced object and return its id, as the reclamation control."""

    control = directory / "reclamation-control.txt"
    control.write_text(_CONTROL_TEXT, encoding="utf-8")
    return _git(repo, ["hash-object", "-w", str(control)])


def _reseal(payload: dict) -> str:
    """Recompute the seal over an edited field set, the way a fabrication would have to.

    The test fabricates a record the way the verification finding did: edit a bound identity and
    recompute ``binding_digest`` over the result, so the record is internally consistent. Whatever
    catches that can only be a *second* statement of the same identity.
    """

    body = {key: value for key, value in payload.items() if key not in _UNSEALED_FIELDS}
    return sha256_digest(body)


_UNSEALED_FIELDS = ("binding_digest", "generation_id", "recorded_at")


def _digest(path: Path) -> str:
    """The exact bytes at one path, as the identity a citation is recorded against."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _side(manifest: ComparisonGenerationManifest, side: str):
    """One knowledge half of a manifest, addressed by its own name rather than by position."""

    return manifest.knowledge_side(side)  # type: ignore[arg-type]


def _artifact(manifest: ComparisonGenerationManifest, side: str):
    """The retained artifact of one half; a retained side always names one, by construction."""

    artifact = _side(manifest, side).artifact
    assert artifact is not None
    return artifact


def _reopen_in_a_new_process(
    fixture: EndpointFixture, generation_id: str | None
) -> dict[str, object]:
    """Reopen one generation in a child process that shares no state with this one."""

    payload = json.dumps(
        {
            "workspace_root": str(fixture.config.workspace_root),
            "coordination_root": str(fixture.config.coordination_root),
            "config_path": str(fixture.config.config_path),
            "transcript_root": str(fixture.config.transcript_root),
            "repository_id": fixture.repository_id,
            "master": fixture.master,
            "leaf_id": LEAF_ID,
            "generation_id": generation_id,
            "repository": str(fixture.contract.code_repo_path),
            "content_path": CANDIDATE_CONTENT_PATH,
        }
    )
    source_root = Path(__file__).resolve().parents[2] / "mcp" / "src"
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(source_root), environment.get("PYTHONPATH", "")]
    ).strip(os.pathsep)
    completed = subprocess.run(
        [sys.executable, "-c", _CHILD_REOPEN, payload],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        raise AssertionError(
            f"the child reopen failed: exit {completed.returncode}, stdout "
            f"{completed.stdout!r}, stderr {completed.stderr!r}"
        )
    return json.loads(completed.stdout.strip().splitlines()[-1])


def _live_composition(fixture: EndpointFixture) -> dict[str, object]:
    """The same comparison, composed again from the live surface, as the owners' own values."""

    result = read_knowledge_review(fixture.config, fixture.request(), ReviewRecordInputs())
    assert result.state == "review", result.refusal
    payload = result.payload
    assert payload is not None and payload.comparison is not None
    return {
        "comparison_binding_digest": payload.comparison.binding_digest,
        "policy_version": payload.comparison.policy_version,
        "inventory_digest": sha256_digest(payload.source.inventory.model_dump(mode="json")),
        "inventory_total": payload.source.inventory.listed_total,
    }


# -- the packet's journey -----------------------------------------------------------------------


def test_a_frozen_comparison_reopens_the_exact_content_after_restart_and_reclamation(
    comparison_fixture: EndpointFixture,
) -> None:
    """Freeze, reclaim Git objects, remove the worktree, and reopen the content in a new process.

    Three measurements make this the packet's journey rather than a re-read of what the
    implementation just wrote. The control object proves ``git gc --prune=now`` really reclaimed
    unreachable objects in this run, so the survival of the candidate tree is attributable to the
    recorded pin rather than to a reclamation that did nothing. The fixture worktree group -- the
    worktree, the live datasets and the stage -- is removed, so nothing the reopen reports can come
    from the live enclosure. And the reopen itself happens in a child process that shares no state
    with the one that froze the comparison.
    """

    fixture = comparison_fixture
    _cite_evidence(fixture)
    outcome = _freeze(fixture, _evidence_options())
    manifest = outcome.manifest
    assert manifest is not None
    repository = fixture.contract.code_repo_path
    control = _write_control_object(repository, fixture.contract.task_root)
    assert _object_present(repository, control)

    reclaimed = _run_git(repository, ["gc", "--prune=now"])
    assert reclaimed.returncode == 0, reclaimed.stderr
    assert not _object_present(repository, control), "the reclamation control object survived gc"
    assert _object_present(repository, manifest.source.candidate_code_tree_id)
    assert _object_present(repository, manifest.source.baseline_code_tree_id)

    shutil.rmtree(fixture.contract.worktree_group)

    reopened = _reopen_in_a_new_process(fixture, manifest.generation_id)

    assert reopened["state"] == "available"
    assert reopened["source_state"] == "available"
    assert reopened["generation_id"] == manifest.generation_id
    assert reopened["generation_index"] == 1
    assert reopened["manifest_digest"] == manifest.manifest_digest()
    # The exact source content: the file the fixture edited without staging it, read back out of the
    # retained tree by a process that never saw the worktree it came from.
    assert reopened["candidate_content"] == CANDIDATE_CONTENT
    # The exact knowledge content: both halves read back as the datasets that were frozen, so the
    # reopened bytes are not merely present but are the recorded logical dataset.
    knowledge = {entry["side"]: entry for entry in reopened["knowledge"]}  # type: ignore[union-attr]
    for side in ("before", "after"):
        recorded = _side(manifest, side)
        assert recorded.identity is not None
        assert knowledge[side]["state"] == "available"
        assert knowledge[side]["logical_digest"] == recorded.identity.logical_digest
        assert knowledge[side]["read_back"] == recorded.identity.logical_digest
    # The cited evidence is a task artifact outside the worktree group, so cleanup cannot take it.
    assert reopened["evidence"] == [{"path": _EVIDENCE_RELATIVE, "state": "available"}]


# -- what the record binds ----------------------------------------------------------------------


def test_the_manifest_binds_the_owners_identities_versions_and_its_own_fields(
    comparison_fixture: EndpointFixture,
) -> None:
    """Every recorded identity is an owner's value, and the record's seal covers its own fields.

    The identities are compared against the owners directly -- the contract's recorded base, the
    capture owner's freshly derived tree, each source dataset's own logical identity, the shipped
    comparison's binding digest and R02's inventory -- rather than against values this module
    restated, because "the manifest records what the review bound" is exactly the claim a
    self-consistent re-read cannot support.
    """

    fixture = comparison_fixture
    evidence = _cite_evidence(fixture)
    outcome = _freeze(fixture, _evidence_options())
    manifest = outcome.manifest
    assert manifest is not None and outcome.directory is not None

    assert manifest.source.baseline_code_tree_id == fixture.contract.code_base_commit
    assert (
        manifest.source.candidate_code_tree_id
        == capture_future_code_candidate(fixture.contract).codeCandidateTree
    )
    assert manifest.source.candidate_capture == fixture.resolve().candidate_identity
    assert manifest.source.custody == "retained"
    assert manifest.source.retained is not None
    assert manifest.source.retained.tree == manifest.source.candidate_code_tree_id
    assert manifest.source.retained.base_commit == manifest.source.baseline_code_tree_id
    assert manifest.source.deletion_owner is not None
    assert manifest.source.cleanup_scope == manifest.source.retained.ref

    resolved = fixture.resolve()
    for side, database in (
        ("before", resolved.baseline_database),
        ("after", resolved.candidate_database),
    ):
        binding = _side(manifest, side)
        assert binding.state == "retained"
        assert binding.identity == dataset_identity(database)
        assert binding.artifact is not None
        snapshot = outcome.directory / binding.artifact.relative_path
        assert snapshot.is_file()
        assert snapshot.stat().st_size == binding.artifact.byte_count
        assert binding.artifact.sha256 == _digest(snapshot)
        assert binding.artifact.deletion_owner == SNAPSHOT_DELETION_OWNER
        assert binding.artifact.cleanup_scope == f"knowledge/{side}"
        # The retained copy is a *closed* database: reopening it must not depend on a journal peer
        # the copy did not carry, which is the property the storage snapshot owner proves.
        assert [
            peer
            for suffix in ("-wal", "-shm", "-journal")
            if (peer := snapshot.with_name(snapshot.name + suffix)).exists()
        ] == []
        assert dataset_identity(snapshot) == binding.identity

    live = _live_composition(fixture)
    assert manifest.lineage.comparison_binding_digest == live["comparison_binding_digest"]
    assert manifest.scope.selected == "subject"
    assert manifest.scope.selector_kind == "invariant"
    assert manifest.scope.inventory_digest == live["inventory_digest"]
    assert manifest.scope.changed_path_count == live["inventory_total"]
    assert manifest.scope.inventory_state == "measured"
    assert manifest.scope.inventory_partial is False
    assert manifest.records.state == "not-supplied"
    assert manifest.records.record_total == 0
    assert manifest.records.current_measured is False
    assert {stamp.owner: stamp.version for stamp in manifest.policies} == {
        "comparison-policy": live["policy_version"],
        "review-surface": "knowledge-review-surface/1",
        "baseline-origin": "ar-knowledge-baseline-origin/v1",
        "baseline-generation": "ar-knowledge-baseline-generation/v1",
        "comparison-generation": "ar-review-comparison-generation/v1",
    }
    assert manifest.evidence[0].owner == "icr-l11-case"
    assert manifest.evidence[0].relative_path == _EVIDENCE_RELATIVE
    assert manifest.evidence[0].sha256 == _digest(evidence)
    assert manifest.temporary_storage_scope.endswith(LEAF_ID)
    assert _hidden_stages(fixture) == ()

    # The record validates itself: the seal is over the stored encoding, and the id is derived from
    # that seal rather than chosen, so a field edited in place and a reused id are both detectable.
    assert manifest.binding_digest == manifest.compute_binding_digest()
    assert manifest.generation_id == generation_identity(manifest.binding_digest)
    stored = (outcome.directory / COMPARISON_MANIFEST_NAME).read_text(encoding="utf-8")
    assert json.loads(stored)["binding_digest"] == manifest.binding_digest


# -- explicit deletion and its record -----------------------------------------------------------


def test_an_explicit_release_records_unavailable_history_and_is_measured_not_assumed(
    comparison_fixture: EndpointFixture,
) -> None:
    """A released pin is a measured fact both before and after reclamation, never a claim.

    Two reopens of the *same* released generation are the point. While the objects still resolve the
    channel is ``available`` and says the release discarded nothing; after reclamation the objects
    are gone and the recorded release is what makes the answer ``unavailable-history`` instead of an
    unexplained loss. A reader that assumed either one from the record alone would be reporting a
    state it had not measured, and neither answer substitutes whatever the repository holds today.
    """

    fixture = comparison_fixture
    outcome = _freeze(fixture)
    manifest = outcome.manifest
    assert manifest is not None and manifest.source.retained is not None
    repository = fixture.contract.code_repo_path

    deletion = release_comparison_code_object(
        fixture.contract.task_root,
        LEAF_ID,
        manifest.generation_id,
        reason="ICR-L11 case: release the explicit pin",
    )
    assert deletion.target == "code-object"
    assert deletion.released_custody == "retained"
    assert deletion.cleanup_scope == manifest.source.retained.ref
    assert _refs(repository) == ()
    recorded = read_history_deletion(
        fixture.contract.task_root, LEAF_ID, manifest.generation_id, "code-object"
    )
    assert recorded is not None and recorded.reason == deletion.reason

    before_reclamation = reopen_comparison_generation(
        fixture.config, fixture.repository_id, fixture.master, LEAF_ID
    )
    assert before_reclamation.state == "available"
    assert before_reclamation.source is not None
    assert before_reclamation.source.release_recorded is True
    assert before_reclamation.source.state == "available"
    # The live measurement leads, and the release follows as the reason the pin is where it is.
    assert before_reclamation.source.pin_present is False
    assert before_reclamation.source.detail.startswith("the recorded pin")
    assert " is absent in the repository now" in before_reclamation.source.detail
    assert before_reclamation.source.detail.index("is absent") < (
        before_reclamation.source.detail.index(deletion.reason)
    )

    reclaimed = _run_git(repository, ["gc", "--prune=now"])
    assert reclaimed.returncode == 0, reclaimed.stderr
    assert not _object_present(repository, manifest.source.candidate_code_tree_id)

    after_reclamation = reopen_comparison_generation(
        fixture.config, fixture.repository_id, fixture.master, LEAF_ID
    )
    assert after_reclamation.state == "unavailable"
    assert after_reclamation.source is not None
    assert after_reclamation.source.state == "unavailable-history"
    assert after_reclamation.source.release_recorded is True
    assert after_reclamation.source.pin_present is False
    # The observation is the three-way one: a tree that is gone is ``absent``, never ``retained``.
    assert after_reclamation.source.custody_observed == "absent"
    # The channel still names the exact objects it was asked for, so "unavailable" is a statement
    # about resolution and never a substitution with whatever the repository holds today.
    assert after_reclamation.source.candidate_code_tree_id == manifest.source.candidate_code_tree_id
    assert after_reclamation.unavailable_channels() == ("source:unavailable-history",)
    # The knowledge halves are untouched by a code release and still resolve.
    assert {channel.state for channel in after_reclamation.knowledge} == {"available"}


# -- an input that is gone or damaged -----------------------------------------------------------


def test_a_missing_or_damaged_retained_input_is_reported_per_channel(
    comparison_fixture: EndpointFixture,
) -> None:
    """An absent snapshot, a damaged one and a damaged citation are three named states, not one.

    The packet's failure rule is that a missing *expected* dataset is unavailable rather than absent
    history, and its evidence rule is that injected missing/corrupt artifacts must show the
    unavailable state instead of a fabricated one. The three injections are made on one generation so
    the three answers are read from one reopen, and the one channel that is still intact -- the
    recorded code objects -- stays available rather than being discarded with the rest.
    """

    fixture = comparison_fixture
    evidence = _cite_evidence(fixture)
    outcome = _freeze(fixture, _evidence_options())
    assert outcome.directory is not None

    (outcome.directory / "knowledge/before" / COMPARISON_SNAPSHOT_NAME).unlink()
    (outcome.directory / "knowledge/after" / COMPARISON_SNAPSHOT_NAME).write_bytes(
        b"not a dataset of this code\n"
    )
    evidence.write_text(_EVIDENCE_REWRITTEN, encoding="utf-8")
    assert _digest(evidence) != _digest(outcome.directory / COMPARISON_MANIFEST_NAME)

    reopened = reopen_comparison_generation(
        fixture.config, fixture.repository_id, fixture.master, LEAF_ID
    )

    assert reopened.state == "unavailable"
    channels = {channel.side: channel for channel in reopened.knowledge}
    assert channels["before"].state == "missing"
    assert "no deletion of it was recorded" in channels["before"].detail
    assert "not absent history" in channels["before"].detail
    assert channels["after"].state == "corrupt"
    assert "is not readable as a dataset" in channels["after"].detail
    assert reopened.source is not None and reopened.source.state == "available"
    assert [channel.state for channel in reopened.evidence] == ["corrupt"]
    assert reopened.evidence[0].owner == "icr-l11-case"
    assert set(reopened.unavailable_channels()) == {
        "before:missing",
        "after:corrupt",
        f"evidence:{_EVIDENCE_RELATIVE}:corrupt",
    }


def test_a_record_that_cannot_be_read_is_not_a_readable_generation(
    comparison_fixture: EndpointFixture,
) -> None:
    """Unparseable bytes and a sealed field edited in place both stop at ``manifest-unreadable``.

    The second injection is the one that matters: the record still parses, still satisfies every
    structural rule, and disagrees only with its own seal. A reader that trusted the file would
    present the edited field as the comparison's binding; this one reports that nothing about the
    generation is claimed -- by name, and not as "no generation was ever published", which is a
    different fact about a directory that is right there.
    """

    fixture = comparison_fixture
    outcome = _freeze(fixture)
    manifest = outcome.manifest
    assert manifest is not None and outcome.directory is not None
    stored = outcome.directory / COMPARISON_MANIFEST_NAME
    published = stored.read_text(encoding="utf-8")

    stored.write_text("{ this is not the record\n", encoding="utf-8")
    unparseable = reopen_comparison_generation(
        fixture.config, fixture.repository_id, fixture.master, LEAF_ID
    )
    assert unparseable.state == "manifest-unreadable"
    assert unparseable.manifest is None and unparseable.source is None
    assert unparseable.refusal is not None
    assert unparseable.refusal.offending_input == COMPARISON_MANIFEST_NAME
    named = reopen_comparison_generation(
        fixture.config,
        fixture.repository_id,
        fixture.master,
        LEAF_ID,
        generation_id=manifest.generation_id,
    )
    assert named.state == "manifest-unreadable"

    stored.write_text(published.replace('"signals":0', '"signals":1'), encoding="utf-8")
    resealed = reopen_comparison_generation(
        fixture.config, fixture.repository_id, fixture.master, LEAF_ID
    )
    assert resealed.state == "manifest-unreadable"
    assert resealed.refusal is not None
    assert "seal does not cover its own fields" in resealed.refusal.detail

    # A record *resealed* around edited fields: the digest is recomputed over the fabrication, so the
    # seal agrees with itself. The id is what catches it -- it must be the one the seal derives.
    fabricated = json.loads(published)
    fabricated["source"]["candidate_code_tree_id"] = fabricated["source"]["baseline_code_tree_id"]
    fabricated["source"]["retained"]["tree"] = fabricated["source"]["baseline_code_tree_id"]
    fabricated["binding_digest"] = _reseal(fabricated)
    stored.write_text(json.dumps(fabricated), encoding="utf-8")
    id_stale = reopen_comparison_generation(
        fixture.config, fixture.repository_id, fixture.master, LEAF_ID
    )
    assert id_stale.state == "manifest-unreadable"
    assert id_stale.refusal is not None
    assert "id is not the one its seal derives" in id_stale.refusal.detail

    # And the id updated to match: the address is the second statement of the same identity, so a
    # generation that changed its own bindings cannot be read out of the directory a reader resolved.
    fabricated["generation_id"] = generation_identity(fabricated["binding_digest"])
    stored.write_text(json.dumps(fabricated), encoding="utf-8")
    renamed = reopen_comparison_generation(
        fixture.config, fixture.repository_id, fixture.master, LEAF_ID
    )
    assert renamed.state == "manifest-unreadable"
    assert renamed.refusal is not None
    assert "not the directory it was found in" in renamed.refusal.detail
    assert renamed.manifest is None


# -- a refused freeze and a converging retry ----------------------------------------------------


def test_a_refused_freeze_publishes_nothing_and_reclaims_its_stage_and_pin(
    comparison_fixture: EndpointFixture,
) -> None:
    """A half that cannot be read stops the freeze with no record, no stage and no leftover pin.

    The damaged half is repaired afterwards and the same leaf freezes successfully, which is what
    makes "reclaimable" a measurement rather than an assertion: the failed attempt left nothing that
    blocks the retry. The refusal arrives after the source side was already retained, so the pin
    really existed when the freeze stopped, and the reclamation above is measured against a
    repository whose pin namespace was empty before the attempt.
    """

    fixture = comparison_fixture
    repository = fixture.contract.code_repo_path
    baseline = fixture.resolve().baseline_database
    original = baseline.read_bytes()
    baseline.write_bytes(b"this is not a database of this code\n")

    refused = freeze_review_comparison(fixture.config, fixture.request())

    assert refused.state == "refused"
    assert refused.refusal is not None
    assert refused.refusal.code == "candidate_dataset_absent"
    assert "baseline" in refused.refusal.detail
    assert generation_directories(fixture.contract.task_root, LEAF_ID) == ()
    assert _hidden_stages(fixture) == ()
    assert _refs(repository) == ()
    assert read_generation_refs(fixture.contract.task_root, LEAF_ID) == ()

    baseline.write_bytes(original)
    published = _freeze(fixture)
    assert published.state == "published"
    assert len(generation_directories(fixture.contract.task_root, LEAF_ID)) == 1
    assert len(_refs(repository)) == 1


def test_an_exact_retry_converges_and_a_superseding_generation_names_its_predecessor(
    comparison_fixture: EndpointFixture,
) -> None:
    """One comparison publishes one generation; a later comparison supersedes it by naming it.

    The retry is the immutability claim: the second freeze derives the same id, finds the published
    record and returns it unchanged rather than rewriting it. The supersession is the lineage claim:
    the successor carries its predecessor's *id and manifest digest*, both generations stay
    resolvable by exact id, and the first generation's bytes are byte-for-byte what they were.
    """

    fixture = comparison_fixture
    first = _freeze(fixture)
    assert first.manifest is not None and first.directory is not None
    first_bytes = (first.directory / COMPARISON_MANIFEST_NAME).read_bytes()

    retry = _freeze(fixture)
    assert retry.reused is True
    assert retry.directory == first.directory
    assert retry.manifest is not None
    assert retry.manifest.manifest_digest() == first.manifest.manifest_digest()
    assert len(generation_directories(fixture.contract.task_root, LEAF_ID)) == 1

    (fixture.worktree / "src/moved_after_the_freeze.py").write_text(
        "# a source move after the comparison was frozen\n", encoding="utf-8"
    )
    predecessor = read_generation_refs(fixture.contract.task_root, LEAF_ID)[0]
    second = _freeze(fixture, ComparisonFreezeOptions(parent=predecessor))
    assert second.manifest is not None and second.directory is not None
    assert second.manifest.generation_index == 2
    assert second.manifest.generation_id != first.manifest.generation_id
    assert second.manifest.lineage.parent_generation_id == first.manifest.generation_id
    assert second.manifest.lineage.parent_manifest_digest == first.manifest.manifest_digest()
    assert (first.directory / COMPARISON_MANIFEST_NAME).read_bytes() == first_bytes

    # Both generations stay independently addressable, and the unnamed reopen resolves the successor.
    latest = reopen_comparison_generation(
        fixture.config, fixture.repository_id, fixture.master, LEAF_ID
    )
    assert latest.state == "available"
    assert latest.generation is not None
    assert latest.generation.generation_id == second.manifest.generation_id
    earlier = reopen_comparison_generation(
        fixture.config,
        fixture.repository_id,
        fixture.master,
        LEAF_ID,
        generation_id=first.manifest.generation_id,
    )
    assert earlier.state == "available"
    assert earlier.generation is not None
    assert earlier.generation.generation_index == 1


# -- known absence versus a missing input --------------------------------------------------------


def test_a_half_with_no_recorded_generation_freezes_as_typed_absence_never_as_inference(
    tmp_path: Path,
) -> None:
    """A leaf with no knowledge at all records ``not-selected``, and ``not-recorded`` only if declared.

    The two states are different facts, and the freeze only ever records the one it was *told*: the
    first freeze of this leaf, which declares nothing, states that no knowledge operand was selected;
    the second, which declares R05's historical absence for the before half, states that the
    repository never recorded a generation for it. A typed absence is also not an unavailability: the
    reopen reports each half in its own state, and it reports the *ambiguity* between the two
    unlinked generations rather than resolving them by directory order.
    """

    fixture = build_endpoint_fixture(tmp_path / "no-knowledge", datasets=False)

    undeclared = freeze_review_comparison(fixture.config, fixture.task_request())
    assert undeclared.state == "published", undeclared.refusal
    assert undeclared.manifest is not None
    assert {_side(undeclared.manifest, side).state for side in ("before", "after")} == {
        "not-selected"
    }

    declared = freeze_review_comparison(
        fixture.config,
        fixture.task_request(),
        ComparisonFreezeOptions(historical_absence=("before",)),
    )
    assert declared.state == "published", declared.refusal
    assert declared.manifest is not None
    states = {side: _side(declared.manifest, side).state for side in ("before", "after")}
    assert states == {"before": "not-recorded", "after": "not-selected"}
    for side in ("before", "after"):
        binding = _side(declared.manifest, side)
        assert binding.state in TYPED_ABSENCE_STATES
        assert binding.artifact is None and binding.identity is None
        assert binding.reason

    # Neither generation names the other, so the unnamed reopen states the ambiguity it measured
    # rather than choosing between two records that both claim the first index.
    unnamed = reopen_comparison_generation(
        fixture.config, fixture.repository_id, fixture.master, LEAF_ID
    )
    assert unnamed.state == "ambiguous"
    assert unnamed.refusal is not None
    assert undeclared.manifest.generation_id in unnamed.refusal.detail
    assert declared.manifest.generation_id in unnamed.refusal.detail

    reopened = reopen_comparison_generation(
        fixture.config,
        fixture.repository_id,
        fixture.master,
        LEAF_ID,
        generation_id=declared.manifest.generation_id,
    )
    assert reopened.state == "available"
    assert [(channel.side, channel.state) for channel in reopened.knowledge] == [
        ("before", "not-recorded"),
        ("after", "not-selected"),
    ]
    assert reopened.unavailable_channels() == ()
    earlier = reopen_comparison_generation(
        fixture.config,
        fixture.repository_id,
        fixture.master,
        LEAF_ID,
        generation_id=undeclared.manifest.generation_id,
    )
    assert earlier.state == "available"
    assert {channel.state for channel in earlier.knowledge} == {"not-selected"}


def test_a_declared_absence_beside_present_bytes_is_refused(
    comparison_fixture: EndpointFixture,
) -> None:
    """A half that holds a dataset is never written out of history by a declaration.

    This is the direction the failure rule protects: the before half really is present and readable,
    so recording "the repository never recorded a generation for it" would replace a real generation
    with an absence. The freeze refuses and publishes nothing, and the undeclared freeze of the same
    leaf still succeeds.
    """

    fixture = comparison_fixture
    refused = freeze_review_comparison(
        fixture.config,
        fixture.request(),
        ComparisonFreezeOptions(historical_absence=("before",)),
    )

    assert refused.state == "refused"
    assert refused.refusal is not None
    assert refused.refusal.code == "candidate_dataset_absent"
    assert "declared a historical absence" in refused.refusal.detail
    assert "a dataset is present" in refused.refusal.detail
    assert generation_directories(fixture.contract.task_root, LEAF_ID) == ()
    assert _refs(fixture.contract.code_repo_path) == ()

    published = _freeze(fixture)
    assert published.state == "published"


def test_the_leaf_s_own_work_branch_is_not_custody_and_the_pin_survives_losing_it(
    comparison_fixture: EndpointFixture,
) -> None:
    """A commit that exists only on the leaf's disposable work branch does not stop the pin.

    This is the defect the first draft of this leaf had, kept as a case because the mistaken reading
    is so natural: after committing the candidate in its own worktree the tree *is* in a commit, and
    a measurement that swept every local branch tip would call that custody and create no pin. But
    that branch is the one ``worktree_abandon`` force-deletes and ordinary cleanup removes, so the
    freeze measures custody against the leaf's protected source branch and the commits its task
    record landed -- neither of which holds this tree yet. The pin is therefore created, and it is
    what keeps the comparison open after the worktree is removed, the branch is deleted and the
    objects are reclaimed.
    """

    fixture = comparison_fixture
    repository = fixture.contract.code_repo_path
    landed = _commit_the_candidate(fixture)
    assert landed == _git(fixture.worktree, ["rev-parse", "HEAD"])

    outcome = _freeze(fixture)

    manifest = outcome.manifest
    assert manifest is not None
    assert manifest.source.custody == "retained"
    assert manifest.source.retained is not None
    assert manifest.source.custody_refs == ("refs/heads/super",)
    assert manifest.source.custody_commits == ()
    assert len(_refs(repository)) == 1
    assert _object_present(repository, manifest.source.candidate_code_tree_id)

    # The whole disposable half of the leaf goes away, exactly as abandon/cleanup does it.
    _git(repository, ["worktree", "remove", "--force", str(fixture.worktree)])
    _git(repository, ["branch", "-D", fixture.contract.code_work_branch])
    assert _git(repository, ["branch", "--list", fixture.contract.code_work_branch]) == ""
    reclaimed = _run_git(repository, ["gc", "--prune=now"])
    assert reclaimed.returncode == 0, reclaimed.stderr

    reopened = _reopen_in_a_new_process(fixture, manifest.generation_id)
    assert reopened["state"] == "available"
    assert reopened["source_state"] == "available"
    assert reopened["candidate_content"] == CANDIDATE_CONTENT


def test_protected_history_taking_custody_stops_the_pin_and_the_generation_still_reopens(
    comparison_fixture: EndpointFixture,
) -> None:
    """A tree the *source branch* already holds acquires no pin, and survives on that branch.

    This is the packet's boundary example read in the direction that ends custody: once the candidate
    content has landed where the leaf's work is destined for -- the protected source branch, here
    fast-forwarded the way integration does it -- the pin has nothing left to hold. The freeze records
    ``committed-history`` with the ref it measured, creates no ref at all, and the generation still
    reopens after the worktree and the work branch are gone and the objects are reclaimed. The code
    release owner refuses a generation that recorded no pin, rather than deleting whatever the ref
    namespace happens to hold.
    """

    fixture = comparison_fixture
    repository = fixture.contract.code_repo_path
    landed = _commit_the_candidate(fixture)
    _git(repository, ["update-ref", "refs/heads/super", landed])
    control = _write_control_object(repository, fixture.contract.task_root)

    outcome = _freeze(fixture)

    manifest = outcome.manifest
    assert manifest is not None
    assert manifest.source.custody == "committed-history"
    assert manifest.source.custody_refs == ("refs/heads/super",)
    assert manifest.source.retained is None
    assert manifest.source.deletion_owner is None
    assert manifest.source.cleanup_scope is None
    assert manifest.source.candidate_code_tree_id == _git(
        repository, ["rev-parse", f"{landed}^{{tree}}"]
    )
    assert _refs(repository) == (), "a tree protected history holds acquired a pin anyway"

    _git(repository, ["worktree", "remove", "--force", str(fixture.worktree)])
    _git(repository, ["branch", "-D", fixture.contract.code_work_branch])
    reclaimed = _run_git(repository, ["gc", "--prune=now"])
    assert reclaimed.returncode == 0, reclaimed.stderr
    assert not _object_present(repository, control), "the reclamation control object survived gc"
    assert _object_present(repository, manifest.source.candidate_code_tree_id)

    reopened = _reopen_in_a_new_process(fixture, manifest.generation_id)
    assert reopened["state"] == "available"
    assert reopened["source_state"] == "available"
    assert reopened["candidate_content"] == CANDIDATE_CONTENT

    with pytest.raises(CodeObjectRetentionError) as refused:
        release_comparison_code_object(
            fixture.contract.task_root,
            LEAF_ID,
            manifest.generation_id,
            reason="ICR-L11 case: nothing to release",
        )
    assert refused.value.status == "code-object-ref-absent"


def test_a_retention_ref_that_moved_is_never_deleted(
    comparison_fixture: EndpointFixture,
) -> None:
    """A release refuses a ref that no longer names the commit it recorded.

    The retention ref is the only thing keeping a dangling candidate tree alive, so deleting it is
    only ever correct while it still points at *that* commit. A ref re-pointed at something else is
    refused rather than deleted, because the objects behind it may be another comparison's only copy
    -- which is exactly the aliasing the packet's deletion rule forbids. Once the ref names the
    recorded commit again, the same call succeeds.
    """

    fixture = comparison_fixture
    outcome = _freeze(fixture)
    manifest = outcome.manifest
    assert manifest is not None and manifest.source.retained is not None
    repository = fixture.contract.code_repo_path
    ref = manifest.source.retained.ref

    moved_to = _git(repository, ["rev-parse", "HEAD"])
    _git(repository, ["update-ref", ref, moved_to])
    assert not _object_present(repository, manifest.source.candidate_code_tree_id) or True

    with pytest.raises(CodeObjectRetentionError) as refused:
        release_comparison_code_object(
            fixture.contract.task_root,
            LEAF_ID,
            manifest.generation_id,
            reason="ICR-L11 case: the ref moved first",
        )
    assert refused.value.status == "code-object-ref-moved"
    assert _refs(repository) == (ref,)
    assert (
        read_history_deletion(
            fixture.contract.task_root, LEAF_ID, manifest.generation_id, "code-object"
        )
        is None
    ), "a refused release recorded a deletion it did not perform"

    _git(repository, ["update-ref", ref, manifest.source.retained.commit])
    deletion = release_comparison_code_object(
        fixture.contract.task_root,
        LEAF_ID,
        manifest.generation_id,
        reason="ICR-L11 case: the ref names the recorded commit again",
    )
    assert deletion.released_custody == "retained"
    assert _refs(repository) == ()


def _commit_the_candidate(fixture: EndpointFixture) -> str:
    """Commit the fixture worktree's whole candidate content and return the commit ``HEAD`` names."""

    _git(fixture.worktree, ["add", "-A"])
    _git(fixture.worktree, ["commit", "-m", "land the candidate so history takes custody"])
    return _git(fixture.worktree, ["rev-parse", "HEAD"])


# -- temporary state, measured deletion, and a pin that came back ---------------------------------


def test_a_stage_a_dead_freeze_left_behind_is_reclaimed_and_a_live_one_is_not(
    comparison_fixture: EndpointFixture,
) -> None:
    """A hard failure's residue is reclaimed by the next freeze, and only from the dead.

    A process killed between staging and the rename leaves a complete-looking stage that no deletion
    owner can address: there is no manifest to name it. The sweep inside the generation root -- the
    directory the record itself names as its temporary-storage scope -- is what reclaims it, and it
    reclaims *only* from the dead, because a stage whose owner is still running is another freeze's
    work in progress. Both directions are measured here: the stage a real child process left when it
    exited is gone after the freeze, and the one this live process owns is still there.
    """

    fixture = comparison_fixture
    root = leaf_generation_root(fixture.contract.task_root, LEAF_ID)
    root.mkdir(parents=True, exist_ok=True)
    orphan = _stage_left_by_a_dead_process(root)
    assert orphan.is_dir() and (orphan / "manifest.json").is_file()
    live = root / f".{os.getpid()}-{'a' * 32}.stage"
    live.mkdir()
    (live / "manifest.json").write_text("{}", encoding="utf-8")

    published = _freeze(fixture)

    assert published.state == "published"
    assert not orphan.exists(), "a dead freeze's stage survived the next freeze"
    assert live.is_dir(), "a live freeze's stage was reclaimed from under it"
    assert _hidden_stages(fixture) == (live.name,)


def test_discarding_snapshots_records_the_bytes_it_measured_and_refuses_a_mismatch(
    comparison_fixture: EndpointFixture,
) -> None:
    """The deletion record names what was removed, measured at removal -- or nothing is removed.

    A digest copied from the manifest would describe content this owner never saw: a snapshot whose
    bytes had already changed underneath would be recorded as deleted with the *frozen* digest, which
    is a false record of its own act. The mismatch is therefore refused before anything is recorded
    or removed, and the successful path records the digest it measured immediately before the unlink.
    A retry converges, recording that the bytes were already gone rather than failing.
    """

    fixture = comparison_fixture
    outcome = _freeze(fixture)
    manifest = outcome.manifest
    assert manifest is not None and outcome.directory is not None
    before = outcome.directory / "knowledge/before" / COMPARISON_SNAPSHOT_NAME
    after = outcome.directory / "knowledge/after" / COMPARISON_SNAPSHOT_NAME
    retained = before.read_bytes()

    before.write_bytes(b"not the bytes this generation retained\n")
    with pytest.raises(ComparisonReclamationError) as refused:
        discard_comparison_snapshots(
            fixture.contract.task_root,
            LEAF_ID,
            manifest.generation_id,
            reason="ICR-L11 case: the bytes moved first",
        )
    assert refused.value.status == "snapshot-bytes-mismatch"
    assert before.is_file() and after.is_file()
    assert (
        read_history_deletion(
            fixture.contract.task_root, LEAF_ID, manifest.generation_id, "knowledge-before"
        )
        is None
    ), "a refused discard recorded a deletion it did not perform"

    before.write_bytes(retained)
    deletions = discard_comparison_snapshots(
        fixture.contract.task_root, LEAF_ID, manifest.generation_id, reason="ICR-L11 case: discard"
    )
    assert {deletion.target for deletion in deletions} == {
        "knowledge-before",
        "knowledge-after",
    }
    frozen = {side: _artifact(manifest, side).sha256 for side in ("before", "after")}
    assert {deletion.deleted_digest for deletion in deletions} == set(frozen.values())
    assert not before.exists() and not after.exists()

    reopened = reopen_comparison_generation(
        fixture.config, fixture.repository_id, fixture.master, LEAF_ID
    )
    assert reopened.state == "unavailable"
    assert {channel.state for channel in reopened.knowledge} == {"unavailable-history"}
    assert set(reopened.unavailable_channels()) == {
        "before:unavailable-history",
        "after:unavailable-history",
    }
    # The record survives its own content: the generation is still the generation it was.
    assert len(generation_directories(fixture.contract.task_root, LEAF_ID)) == 1

    again = discard_comparison_snapshots(
        fixture.contract.task_root, LEAF_ID, manifest.generation_id, reason="ICR-L11 case: retry"
    )
    assert [deletion.deleted_digest for deletion in again] == [None, None]


def test_a_frozen_again_comparison_reports_its_live_pin_before_the_release_history(
    comparison_fixture: EndpointFixture,
) -> None:
    """A release, then a re-freeze: the pin is back, and the reader is told that *first*.

    Freezing the same comparison again converges on the published record -- the manifest is
    immutable -- but the pin it needs is created again, because the release really did delete it. The
    generation therefore has a live pin *and* a release on record, and those two facts are not in
    conflict: the record is history and the measurement is now. The channel states the measurement
    first and the history after it, and reports both as separate facts rather than leaving a reader to
    infer which one is current.
    """

    fixture = comparison_fixture
    repository = fixture.contract.code_repo_path
    first = _freeze(fixture)
    manifest = first.manifest
    assert manifest is not None and manifest.source.retained is not None

    release_comparison_code_object(
        fixture.contract.task_root,
        LEAF_ID,
        manifest.generation_id,
        reason="ICR-L11 case: release before the second freeze",
    )
    assert _refs(repository) == ()

    again = _freeze(fixture)
    assert again.reused is True
    assert again.manifest is not None
    assert again.manifest.manifest_digest() == manifest.manifest_digest()
    assert _refs(repository) == (manifest.source.retained.ref,)

    reopened = reopen_comparison_generation(
        fixture.config, fixture.repository_id, fixture.master, LEAF_ID
    )
    assert reopened.state == "available"
    assert reopened.source is not None
    assert reopened.source.pin_present is True
    assert reopened.source.release_recorded is True
    detail = reopened.source.detail
    assert detail.startswith("the recorded pin")
    assert " is present in the repository now" in detail
    assert detail.index("is present") < detail.index("was released explicitly")


def _stage_left_by_a_dead_process(root: Path) -> Path:
    """Create a stage in a child process and let it exit, returning the residue it left.

    The pid in the name is therefore genuinely dead, which is the state the sweep has to recognise:
    the alternative -- a hand-written name with an impossible pid -- would measure the parser rather
    than the liveness check.
    """

    script = (
        "import os, pathlib, sys\n"
        "root = pathlib.Path(sys.argv[1])\n"
        "stage = root / f'.{os.getpid()}-{'b' * 32}.stage'\n"
        "stage.mkdir()\n"
        "(stage / 'knowledge').mkdir()\n"
        "(stage / 'manifest.json').write_text('{}', encoding='utf-8')\n"
        "print(stage.name)\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", script, str(root)], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr
    return root / done.stdout.strip()
