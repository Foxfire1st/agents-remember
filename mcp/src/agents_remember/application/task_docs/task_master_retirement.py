"""Retiring a master: one operation, one state machine, one record.

A retirement is read from disk, never from the way a request arrived:

* **the record** -- the sprint row that carries the proof (when a sprint commanded the master), or
  the proof file ``notes/reports/master-retirement.json`` in the master's own folder (when no
  sprint did). A master has at most one of the two.
* **the folder** -- live under ``tasks/<repository>/``, or archived under ``0_archive/``.
* **the cleanup** -- what the archive hook still finds to delete, and its numbered receipts.

======================  ================  ========  ==========================  =================
State                   Record            Folder    The same request            Another request
======================  ================  ========  ==========================  =================
``not-recorded``        none              live      checks everything, then     (it is the first)
                                                    writes the record: the
                                                    sprint edit when one
                                                    sprint commands the
                                                    master, the proof file
                                                    when none does
``sprint-edited``       row on a sprint   live      moves the folder            refused
``proof-written``       proof file        live      moves the folder            refused
``folder-archived``     row or proof      archived  runs the archive hook       refused
``hook-failed``         row or proof      archived  runs the hook again, which  refused
                                                    does only what is left
``hook-finished``       row or proof      archived  nothing, and says so        refused
======================  ================  ========  ==========================  =================

Transitions, each one function below; a process may die between any two of them:

* *record*: ``not-recorded`` to ``sprint-edited`` (:func:`_record_on_sprint`) or to
  ``proof-written`` (:func:`_record_in_folder`);
* *archive*: ``sprint-edited`` or ``proof-written`` to ``folder-archived``
  (:func:`_archive_folder`);
* *cleanup*: ``folder-archived`` or ``hook-failed`` to ``hook-finished`` or ``hook-failed``
  (:func:`_clean_up`).

An exception inside *record* or *archive* restores what this request changed, so a request leaves
the state it found or a later one, never something in between. After *archive* nothing is undone:
the hook deletes, and a deletion cannot be taken back.

The three states of an archived folder are told apart by the hook and its receipts, in a dry run
as in a real one: ``hook-failed`` while the hook names a failure, or while its last attempt
recorded one and something is still left to do; ``hook-finished`` when the hook has nothing left
to do; ``folder-archived`` otherwise.

Guards that hold in every state:

* The request is addressed to the document that owns the record: the sprint that commands the
  master or recorded its retirement, or the master itself when no sprint does. Any other address
  is refused and names the right one.
* The folder moves only while no sprint commands the master, so no sprint ever names a master it
  cannot resolve.
* A request that differs from the record (reason, named edges, repository, archive location) is
  refused and changes nothing.
* Nothing is decided from what cannot be read: while the addressed document or any task document
  of the repository cannot be opened, decoded or parsed, every request is refused and names the
  file. An archive cannot be undone.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents_remember.kernel.atomic_write import atomic_write_bytes, atomic_write_text
from agents_remember.models.task_retirement import MasterRetirementProof
from agents_remember.tasks import (
    SubTaskRef,
    TaskDocSourceSnapshot,
    TaskDocument,
    read_graph_titles,
    write_task_doc_batch,
)
from agents_remember.tasks.document_refs import ResolvedTaskDocument
from agents_remember.tasks.sprint_rows import membership_removal_action
from agents_remember.worktrees.queue.closeout_projection_publication import (
    preview_closeout_projection_effect,
)
from agents_remember.worktrees.task_fact_publication import publish_task_fact_mutation
from agents_remember.worktrees.task_retirement import (
    MasterRetirementScope,
    RetirementReadiness,
    require_master_retirement_ready,
)
from agents_remember.worktrees.worktree_contract import ContractError

from .task_doc_publication import (
    TaskDocPublicationConflict,
    preview_task_doc_transaction_projection_effects,
    publish_task_doc_transaction_and_refresh,
    require_task_doc_sources_current,
    validate_task_doc_transaction,
)
from .task_retirement_answers import (
    RetirementPlan,
    RetirementState,
    first_answer,
    preview_answer,
)
from .task_retirement_projection import preview_stale_projection, refresh_stale_projection
from .task_retirement_records import (
    Observed,
    RecordingRow,
    observe,
    proof_path,
    read_proof,
    require_addressed_readable,
    retirement_recorded,
    sprints_naming,
)
from .task_retirement_shared import (
    RESTART_NOTICE,
    RetireMasterPayload,
    archive_hook,
    digest,
    hook_failed,
    optional_digest,
)
from .task_retirement_sprint_edit import (
    SprintEdit,
    new_proof,
    retirement_sprint_edit,
    selection_keys,
)
from .task_sprint_candidates import SprintLinkageError
from .task_sprint_context import (
    SprintLinkageRequest,
    _document_preview,
    _parse_payload,
    _publication_transaction,
    _require_serving_retirement_schema,
    _require_serving_topology_schema,
    _validate_candidate,
)


def retire_master(request: SprintLinkageRequest) -> dict[str, Any]:
    """Retire one master, through the sprint that commands it or on its own when none does.

    Every answer carries the restart notice once a retirement record exists or this request would
    write one: every success, and every refusal that follows a record, also one raised while the
    payload is parsed. A refusal before anything is recorded carries none.
    """

    try:
        payload = _parse_payload(RetireMasterPayload, request.fields, "retire_master")
        return _retire(request, payload)
    except ValueError as exc:
        if retirement_recorded(request):
            raise SprintLinkageError(f"{str(exc).rstrip('. ')}. {RESTART_NOTICE}") from exc
        raise


def _retire(request: SprintLinkageRequest, payload: RetireMasterPayload) -> dict[str, Any]:
    if payload.masterRef.repository != request.repo_id:
        raise SprintLinkageError(
            f"master {payload.masterRef.key} is outside the repository of the document this call addresses; select its owning sprint for task_doc.retire_master"
        )
    require_addressed_readable(request)
    try:
        _require_serving_topology_schema()
        _require_serving_retirement_schema()
        plan = _admit(request, payload)
    except (OSError, ValueError) as exc:
        if isinstance(exc, SprintLinkageError):
            raise
        # A refusal that already names the retry is not given a second one.
        action = (
            ""
            if "task_doc.retire_master" in str(exc)
            else "; repair the named source and retry task_doc.retire_master"
        )
        raise SprintLinkageError(
            f"master {payload.masterRef.key} retirement refused: {exc}{action}"
        ) from exc
    result = first_answer(request, plan)
    if plan.state == "folder-archived":
        return _archived(request, plan, result)
    if plan.owner is None:
        _publish_in_folder(request, plan)
    else:
        _publish_on_sprint(request, plan, result)
    if request.dry_run:
        return preview_answer(request, plan, result, plan.observed.live)
    return _clean_up(request, plan, result)


# -- admission: which state the request finds, and whether it may act on it -----------------------


def _admit(request: SprintLinkageRequest, payload: RetireMasterPayload) -> RetirementPlan:
    observed = observe(request, payload.masterRef)
    proof = read_proof(observed)
    if len(observed.recording) > 1 or (observed.recording and proof is not None):
        records = [
            f"row {entry.row.number!r} of sprint {entry.sprint.ref.key}"
            for entry in observed.recording
        ] + ([f"the proof file {observed.proof_file}"] if proof is not None else [])
        raise SprintLinkageError(
            f"master {observed.key} has more than one retirement record: {'; '.join(records)}. A "
            "master has exactly one; remove the surplus record by hand, keeping the one whose "
            "request is to be repeated, before task_doc.retire_master"
        )
    if observed.recording:
        return _admit_recorded_on_sprint(request, payload, observed, observed.recording[0])
    if proof is not None:
        return _admit_recorded_in_folder(request, payload, observed, proof)
    return _admit_fresh(request, payload, observed)


def _admit_fresh(
    request: SprintLinkageRequest, payload: RetireMasterPayload, observed: Observed
) -> RetirementPlan:
    key = observed.key
    if not observed.live_exists:
        raise SprintLinkageError(
            f"master {key} sits under 0_archive at {observed.archive} without a retirement record; "
            f"restore its folder to {observed.live}, then repeat task_doc.retire_master"
        )
    if observed.archive_exists:
        raise SprintLinkageError(
            f"master {key} archive target already exists: {observed.archive}; reconcile that directory before task_doc.retire_master"
        )
    if len(observed.commanding) > 1:
        raise SprintLinkageError(
            f"master {key} is commanded by several sprints "
            f"({', '.join(sprint.ref.key for sprint in observed.commanding)}); remove it from all "
            f"but one ({_removal_actions(observed)}), then repeat task_doc.retire_master on the "
            "sprint that still commands it"
        )
    owner = observed.commanding[0] if observed.commanding else None
    _require_addressed_to_owner(request, observed, owner, recorded_row=None)
    scope = _scope(request, observed)
    # The readiness is read first, because its facts go into the record, and its refusal is
    # raised last, so that a request is told about its own arguments before it is told about
    # the master's open work.
    readiness, open_work = _readiness(scope)
    proof = new_proof(
        observed.master.ref,
        observed.archive_ref,
        payload,
        observed.master_source,
        readiness.facts if readiness is not None else (),
    )
    if owner is None:
        if payload.removeEdges:
            raise SprintLinkageError(
                f"master {key} is commanded by no sprint, so removeEdges has no graph "
                "edge to remove; repeat task_doc.retire_master without removeEdges"
            )
        if open_work is not None:
            raise open_work
        return RetirementPlan("not-recorded", observed, proof, None, None, None, readiness, scope)
    edit: SprintEdit = retirement_sprint_edit(
        observed.topology, owner, observed.master, payload, proof
    )
    if open_work is not None:
        raise open_work
    assert edit.row.retirement is not None
    return RetirementPlan(
        "not-recorded",
        observed,
        edit.row.retirement,
        owner,
        edit.candidate,
        edit.row,
        readiness,
        scope,
    )


def _readiness(
    scope: MasterRetirementScope,
) -> tuple[RetirementReadiness | None, SprintLinkageError | None]:
    """The master's readiness, or the refusal for its open work, in the operation's own words."""

    try:
        return require_master_retirement_ready(scope), None
    except ContractError as refusal:
        return None, SprintLinkageError(str(refusal))


def _require_ready(scope: MasterRetirementScope) -> RetirementReadiness:
    readiness, open_work = _readiness(scope)
    if readiness is None:
        assert open_work is not None
        raise open_work
    return readiness


def _removal_actions(observed: Observed) -> str:
    """For each sprint that commands the master: the edit that removes it there, and works."""

    master = observed.master
    names = {master.path.parent.name, master.document.id, master.document.title}
    return "; ".join(
        membership_removal_action(sprint.ref.key, sprint.document, master.ref, names)
        for sprint in observed.commanding
    )


def _admit_recorded_on_sprint(
    request: SprintLinkageRequest,
    payload: RetireMasterPayload,
    observed: Observed,
    recording: RecordingRow,
) -> RetirementPlan:
    key, sprint, row = observed.key, recording.sprint, recording.row
    proof = row.retirement
    assert proof is not None
    _require_addressed_to_owner(request, observed, sprint, recorded_row=row)
    if (
        proof.reason != payload.reason
        or selection_keys(proof.affirmedEdges) != selection_keys(payload.removeEdges)
        or proof.archiveRef != observed.archive_ref
    ):
        raise SprintLinkageError(
            f"master {key} retirement request differs from its retained proof; repeat the original reason and removeEdges"
        )
    if observed.commanding:
        raise SprintLinkageError(
            f"master {key} was reattached after retirement: sprint "
            f"{', '.join(commander.ref.key for commander in observed.commanding)} commands it "
            f"again. Remove it there ({_removal_actions(observed)}), then repeat this request"
        )
    if observed.live_exists == observed.archive_exists:
        raise SprintLinkageError(
            f"master {key} must exist at exactly one of {observed.live} or {observed.archive}; restore its recorded folder before retrying task_doc.retire_master"
        )
    _require_admitted_source_pair(observed, proof)
    scope = _scope(request, observed)
    readiness = _require_ready(scope) if observed.live_exists else None
    _validate_candidate(observed.topology, sprint.ref, {sprint.ref: sprint.document})
    state: RetirementState = "sprint-edited" if observed.live_exists else "folder-archived"
    return RetirementPlan(state, observed, proof, sprint, sprint.document, row, readiness, scope)


def _admit_recorded_in_folder(
    request: SprintLinkageRequest,
    payload: RetireMasterPayload,
    observed: Observed,
    proof: MasterRetirementProof,
) -> RetirementPlan:
    key, stored = observed.key, observed.proof_file
    if observed.commanding:
        commanders = ", ".join(sprint.ref.key for sprint in observed.commanding)
        raise SprintLinkageError(
            f"master {key} holds its own retirement record {stored}, written while no sprint "
            f"commanded it, and sprint {commanders} commands it now, so its folder stays where "
            "that sprint resolves it. To complete the recorded retirement, remove the master "
            f"there ({_removal_actions(observed)}) and repeat the recorded request on the master "
            f"itself; to keep the master and retire it through the sprint instead, delete {stored} "
            "and call task_doc.retire_master on that sprint"
        )
    _require_addressed_to_owner(request, observed, None, recorded_row=None)
    if (
        proof.reason != payload.reason
        or proof.masterRef != observed.master.ref
        or proof.archiveRef != observed.archive_ref
        or payload.removeEdges
    ):
        raise SprintLinkageError(
            f"master {key} retirement request differs from its retained proof {stored}; repeat the original masterRef and reason"
        )
    if observed.live_exists and observed.archive_exists:
        raise SprintLinkageError(
            f"master {key} exists at both {observed.live} and {observed.archive}; "
            "reconcile that directory before task_doc.retire_master"
        )
    _require_admitted_source_pair(observed, proof)
    scope = _scope(request, observed)
    readiness = _require_ready(scope) if observed.live_exists else None
    state: RetirementState = "proof-written" if observed.live_exists else "folder-archived"
    return RetirementPlan(state, observed, proof, None, None, None, readiness, scope)


def _require_addressed_to_owner(
    request: SprintLinkageRequest,
    observed: Observed,
    owner: ResolvedTaskDocument | None,
    *,
    recorded_row: SubTaskRef | None,
) -> None:
    """One route: the request is addressed to the document that owns, or will own, the record."""

    key = observed.key
    addressed = (request.task_root / f"{request.slug or 'task'}.json").resolve(strict=False)
    own = observed.master.path.resolve(strict=False)
    if addressed == (owner.path.resolve(strict=False) if owner is not None else own):
        return
    if owner is None:
        raise SprintLinkageError(
            f"task_doc.retire_master was called on {addressed}, which does not command master "
            f"{key}; no sprint commands that master, so call task_doc.retire_master on the "
            f"master's own task.json ({own}) with fields={{masterRef:{key}, reason}}"
        )
    if recorded_row is not None:
        raise SprintLinkageError(
            f"the retirement of master {key} is recorded on row {recorded_row.number!r} of "
            f"sprint {owner.ref.key}; repeat task_doc.retire_master on that sprint with the "
            "recorded masterRef, reason and removeEdges. A master is retired through one route"
        )
    if addressed != own:
        raise SprintLinkageError(
            f"task_doc.retire_master was called on {addressed}, but master {key} is commanded "
            f"by sprint {owner.ref.key}; call task_doc.retire_master on that sprint with "
            f"fields={{masterRef:{key}, reason, removeEdges?}}"
        )
    raise SprintLinkageError(
        f"master {key} is commanded by sprint {owner.ref.key}; call task_doc.retire_master on that "
        f"sprint with fields={{masterRef:{key}, reason, removeEdges?}} so its "
        "membership and graph are repaired before the folder is archived"
    )


def _require_admitted_source_pair(observed: Observed, proof: MasterRetirementProof) -> None:
    source = observed.master_source
    if (
        digest(source.json_bytes or b"") != proof.masterJsonSha256
        or optional_digest(source.markdown_bytes) != proof.masterMarkdownSha256
    ):
        raise SprintLinkageError(
            f"master {observed.key} source pair changed since retirement admission: {observed.folder}; restore the admitted pair before retrying task_doc.retire_master"
        )


def _scope(request: SprintLinkageRequest, observed: Observed) -> MasterRetirementScope:
    return MasterRetirementScope(
        request.coordination_root,
        request.repo_id,
        request.code_repository,
        request.memory_repository,
        observed.master,
    )


# -- transitions before the folder is archived ----------------------------------------------------


def _publish_on_sprint(
    request: SprintLinkageRequest, plan: RetirementPlan, result: dict[str, Any]
) -> None:
    """Record on the sprint (unless it is recorded) and archive the folder, as one publication."""

    owner, candidate = plan.owner, plan.candidate
    assert owner is not None and candidate is not None
    graph = candidate.executionGraph
    graph_titles = (
        read_graph_titles(request.coordination_root / "tasks", graph) if graph is not None else None
    )

    def publish() -> list[tuple[Path, Path]]:
        _require_evidence_unchanged(plan)
        archive_root_existed = plan.observed.archive.parent.exists()
        try:
            written = _record_on_sprint(owner.path.parent, candidate, graph_titles)
            _archive_folder(request, plan.observed)
            return written
        except Exception as exc:  # any failure before archival ends restored
            _restore_folder(plan.observed, archive_root_existed)
            _restore_source(plan.observed.source_of(owner))
            raise SprintLinkageError(
                f"master {plan.observed.key} retirement failed before archival; sprint sources and task folder restored: {exc}; retry task_doc.retire_master"
            ) from exc

    transaction = _publication_transaction(
        request, {owner.ref: candidate}, plan.observed.sources, publish
    )
    if request.dry_run:
        validate_task_doc_transaction(transaction)
        # A completing request writes the sprint's pair again, so its preview is listed too:
        # the Markdown page may be behind a document that an interrupted write left.
        result["documents"] = [_document_preview(owner.ref, owner.path.parent, candidate)]
        effects = (
            (preview_closeout_projection_effect(request.coordination_root, owner.ref),)
            if plan.resumed
            else preview_task_doc_transaction_projection_effects(transaction)
        )
        result["projectionEffects"] = [effect.model_dump(mode="json") for effect in effects]
        return
    if plan.resumed:
        # The sprint already reads as retired, so no document changes and no scope would be
        # derived from one: the completing request names the sprint's projection itself.
        published = publish_task_fact_mutation(
            request.coordination_root,
            validate=lambda: _require_sources_unchanged(plan),
            projection_scopes=lambda: (owner.ref,),
            publication=publish,
        )
        written, effects = published.result, published.projection_effects
    else:
        publication = publish_task_doc_transaction_and_refresh(transaction)
        written, effects = publication.written, publication.projection_effects
    result["documents"] = [
        {
            "taskDocumentRef": owner.ref.model_dump(mode="json"),
            "docPath": path.as_posix(),
            "renderedPath": markdown.as_posix(),
        }
        for path, markdown in written
    ]
    result["projectionEffects"] = [effect.model_dump(mode="json") for effect in effects]


def _publish_in_folder(request: SprintLinkageRequest, plan: RetirementPlan) -> None:
    """Record in the master's own folder (unless it is recorded) and archive the folder."""

    _require_sources_unchanged(plan)
    _require_evidence_unchanged(plan)
    if request.dry_run:
        return
    stored = proof_path(plan.observed.live)
    archive_root_existed = plan.observed.archive.parent.exists()
    created = [folder for folder in (stored.parent, stored.parent.parent) if not folder.exists()]
    try:
        if not plan.resumed:
            _record_in_folder(stored, plan.proof)
        _archive_folder(request, plan.observed)
    except Exception as exc:  # any failure before archival ends restored
        _restore_folder(plan.observed, archive_root_existed)
        if not plan.resumed:
            stored.unlink(missing_ok=True)
            # A refused request changes nothing: the folders made for the record go with it.
            for folder in created:
                if folder.is_dir() and not any(folder.iterdir()):
                    folder.rmdir()
        raise SprintLinkageError(
            f"master {plan.observed.key} retirement failed before archival; its task folder "
            f"and proof were restored: {exc}; retry task_doc.retire_master"
        ) from exc


def _record_on_sprint(
    sprint_root: Path, candidate: TaskDocument, graph_titles: Any
) -> list[tuple[Path, Path]]:
    """Transition *record*, with a sprint: the sprint stops commanding the master and records why.

    A completing request writes the sprint as it already reads, which re-renders a Markdown page
    that an interrupted write left behind its JSON.
    """

    return write_task_doc_batch([(sprint_root, candidate)], graph_titles=graph_titles)


def _record_in_folder(stored: Path, proof: MasterRetirementProof) -> None:
    """Transition *record*, without a sprint: the proof goes into the folder that will move."""

    atomic_write_text(stored, proof.model_dump_json(indent=2) + "\n")


def _archive_folder(request: SprintLinkageRequest, observed: Observed) -> None:
    """Transition *archive*: the one move of the task folder under ``0_archive/``.

    It looks once more, at the last moment, for a sprint that commands the master: the folder
    never moves from under a sprint that names it.
    """

    commanding, _recording = sprints_naming(request, observed.topology, observed.master)
    if commanding:
        raise SprintLinkageError(
            f"sprint {', '.join(sprint.ref.key for sprint in commanding)} commands master "
            f"{observed.key}, so its folder stays where that sprint resolves it"
        )
    observed.archive.parent.mkdir(parents=True, exist_ok=True)
    observed.live.rename(observed.archive)


def _require_sources_unchanged(plan: RetirementPlan) -> None:
    """The checks of admission, repeated at publication: every document read is still the same."""

    try:
        require_task_doc_sources_current(plan.observed.sources)
    except TaskDocPublicationConflict as exc:
        raise SprintLinkageError(
            f"master {plan.observed.key} source or lifecycle evidence changed before publication: "
            f"{exc}; re-read it and retry task_doc.retire_master"
        ) from exc


def _require_evidence_unchanged(plan: RetirementPlan) -> None:
    """The checks of admission, repeated at publication: lifecycle evidence is what was validated."""

    admitted = plan.readiness.evidence if plan.readiness is not None else ()
    if _require_ready(plan.scope).evidence != admitted:
        raise SprintLinkageError(
            f"master {plan.observed.key} enclosure/operation source changed before publication; re-read its lifecycle evidence and retry task_doc.retire_master"
        )


def _restore_folder(observed: Observed, archive_root_existed: bool) -> None:
    if not observed.live.exists() and observed.archive.exists():
        observed.archive.rename(observed.live)
    if not archive_root_existed and observed.archive.parent.is_dir():
        observed.archive.parent.rmdir()


def _restore_source(source: TaskDocSourceSnapshot) -> None:
    for path, payload in (
        (source.json_path, source.json_bytes),
        (source.markdown_path, source.markdown_bytes),
    ):
        if payload is None:
            path.unlink(missing_ok=True)
        else:
            atomic_write_bytes(path, payload)


# -- after the folder is archived -----------------------------------------------------------------


def _archived(
    request: SprintLinkageRequest, plan: RetirementPlan, result: dict[str, Any]
) -> dict[str, Any]:
    """The folder is archived: only the cleanup, and a stale queue projection, are left to do."""

    result["documents"] = []
    result["projectionEffects"] = []
    _require_sources_unchanged(plan)
    if plan.owner is not None:
        refresh = preview_stale_projection if request.dry_run else refresh_stale_projection
        result["projectionEffects"] = [
            effect.model_dump(mode="json") for effect in refresh(request, plan.owner.ref)
        ]
    if request.dry_run:
        return preview_answer(request, plan, result, plan.observed.archive)
    return _clean_up(request, plan, result)


def _clean_up(
    request: SprintLinkageRequest, plan: RetirementPlan, result: dict[str, Any]
) -> dict[str, Any]:
    """Transition *cleanup*: run the archive hook; a partial or failed hook keeps the retirement."""

    report = archive_hook(request, plan.observed.archive)
    result["taskArchive"]["reviewArtifacts"] = report
    result["retirementState"] = "hook-finished"
    if hook_failed(report):
        result["ok"] = False
        result["state"] = "retired-with-hook-failures"
        result["retirementState"] = "hook-failed"
        result["nextAction"] = (
            f"Repeat task_doc.retire_master with the same {plan.same_request} to retry the archive hook."
        )
    return result
