"""CLI adapter: the taskless knowledge bootstrap, its resume view and its cleanup owner.

    agents-remember knowledge-bootstrap --repo <repo_id> --list <hand-off list>
        --authorization-ref <ref> [--commit] [--config <MCP settings>] [--json]
    agents-remember knowledge-bootstrap --repo <repo_id> --status [--config <MCP settings>] [--json]
    agents-remember knowledge-bootstrap --repo <repo_id> --discard-staging [--json]

This is the **taskless** entry the knowledge write plane did not have. The ordinary
``knowledge-ingest`` subcommand requires ``--contract`` -- a leaf enclosure contract -- because that
is the admission its operation was bound to, and a repository whose first knowledge is being written
has no leaf and no enclosure. Fabricating one to satisfy that shape is refused by the bootstrap
handover, and a second write path is refused by the preservation boundaries, so this subcommand
resolves the second *real* admission instead: the repository entry the MCP settings document declares,
the memory layer the ordinary read route resolves, and the exact code and memory revisions the real
checkouts stand at. It then drives the same operation the leaf path drives.

``--repo`` is the repository id the MCP settings document declares. It is resolved through
:func:`~agents_remember.application.knowledge_bootstrap_admission.admit_bootstrap_context`, so a
repository the document does not list, a repository with no external memory root, a coordination root
that does not exist, an unreadable revision and a memory root that is not the one the ordinary read
route selects are each refused by name before anything is read or written.

``--config`` names the MCP settings document and defaults to the umbrella CLI's own trusted-settings
discovery, so the command runs from anywhere under the workspace exactly as ``memory-citations`` does.
There is no second settings convention and no environment variable this command invents.

PLANNING IS THE DEFAULT, AND PLANNING IS ALSO THE DRY RUN. Without ``--commit`` the list is read, the
candidate is planned, the destination is read, and **nothing is written**: no batch, no publication
and no retained progress record. ``--commit`` is the developer's commit word, and it is the whole of
the write act -- the batch into the staging candidate, the publication to the declared location the
ordinary read route selects when that batch committed, and the retained progress record, which is
written by **any** run given the commit word (a run whose batch wrote nothing still has a true
observation to retain, and its own fields say the batch was not attempted or published nothing).
``--commit`` never means "and also something else".

EXIT ZERO IS NOT A PUBLICATION CLAIM. The report carries the destination read before the run, the
batch's own state, the publication owner's result, an independent read-back of the declared location
through the read route's own owner, a contents read of what the dataset holds, and the named
remaining work. A run whose entries all committed can still have published nothing, and the report
says so in ``publication.state`` rather than leaving the exit code to be read as success.

``--status`` writes nothing and reports only current measurements: what the staging root retains, what
the declared location holds now, and what the dataset's invariant view returns now. It deliberately
does not restate the retained record's own numbers, because a record written by an earlier run is an
observation at that run's identity and not a statement about the store as it stands.

``--discard-staging`` is the bounded cleanup owner. It removes the staging root only when the
declared location provably holds the very dataset the staged candidate holds now -- read from both
files, not inferred from a finished-looking run -- and refuses by name in every other state, leaving
the bytes exactly as they are.

Exit status: 0 when the invocation produced its report, and 2 when the invocation itself is refused
(a missing or unreadable list, a blank authorization reference, an unusable destination, a context
that cannot be admitted, an undecidable settings path, a cleanup the guard refused).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from agents_remember.application.knowledge_bootstrap import (
    BootstrapRunRefusal,
    BootstrapRunResult,
    bootstrap_knowledge,
)
from agents_remember.application.knowledge_bootstrap_admission import (
    AdmittedKnowledgeBootstrap,
    BootstrapRefusal,
    admit_bootstrap_context,
)
from agents_remember.application.knowledge_bootstrap_staging import (
    StagingCleanup,
    discard_bootstrap_staging,
    progress_path,
    read_progress,
    staged_candidate_directory,
)
from agents_remember.application.published_intent import (
    PublishedIntentUnavailable,
    resolve_published_intent,
)
from agents_remember.cli.discovery import ConfigDiscoveryError, discover_config
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
    load_config,
    require_config_path,
)
from agents_remember.models.knowledge.snapshot import candidate_database_path

EXIT_REPORTED = 0
EXIT_REFUSED = 2


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo", required=True, help="The repository id the MCP settings declare.")
    parser.add_argument(
        "--list",
        dest="hand_off_list",
        default=None,
        help="Path to the curator's hand-off list. Required unless --status or --discard-staging "
        "is given, which read the repository's state rather than a list.",
    )
    parser.add_argument(
        "--authorization-ref",
        default=None,
        help="The authorization this bootstrap is admitted under; it is also the actor the "
        "authorship envelope names. Required for a run, and not read by --status.",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Write the batch into the staging candidate and publish it to the declared location. "
        "Without it this reports and writes nothing, which is the dry run.",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Report what the staging root retains and what the declared location holds now, "
        "writing nothing.",
    )
    parser.add_argument(
        "--discard-staging",
        dest="discard_staging",
        action="store_true",
        help="Remove the staging root, and only when the declared location holds the dataset the "
        "staged candidate holds now.",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to the MCP authority settings file. Omit to use the umbrella CLI's trusted "
        "settings discovery from the current directory.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Print the whole report as JSON instead of the human-readable summary.",
    )


def _invocation_refusal(args: argparse.Namespace) -> str | None:
    """Why this invocation is not one coherent request, or ``None`` when it is.

    Every one of these is a fact about the argument list, so they are answered before a settings
    document, a context, a list or a byte is touched. Two contradictory modes are refused rather than
    resolved by precedence: a run that named ``--status`` and ``--commit`` has not said whether it
    wants to write, and silently picking one is how a dry run becomes a real one.
    """

    mode_refusal = _mode_refusal(args)
    if mode_refusal is not None or args.status or args.discard_staging:
        return mode_refusal
    return _list_refusal(args)


def _mode_refusal(args: argparse.Namespace) -> str | None:
    """Why the mode arguments this invocation named are not one coherent request, or ``None``."""

    if args.status and args.discard_staging:
        return "--status reports and --discard-staging removes; one run cannot mean both"
    if args.commit and (args.status or args.discard_staging):
        return (
            "--commit writes and --status/--discard-staging are read-only modes; one run cannot "
            "mean both"
        )
    return None


def _list_refusal(args: argparse.Namespace) -> str | None:
    """Why a *run* invocation has no list to read or no authority to write under, or ``None``."""

    if args.hand_off_list is None:
        return "--list names the curator's hand-off list, and a run needs it"
    if not Path(args.hand_off_list).is_file():
        return f"the hand-off list {args.hand_off_list} is not a file this command can read"
    if not str(args.authorization_ref or "").strip():
        return "--authorization-ref must not be blank: an admitted write needs an authorization"
    return None


def _settings(args: argparse.Namespace) -> McpRuntimeConfig | str:
    """The MCP authority settings path this invocation resolved, or the reason it could not."""

    try:
        return load_config(
            require_config_path(args.config) if args.config else discover_config(Path.cwd())
        )
    except ConfigDiscoveryError as error:
        return (
            f"no MCP authority settings could be resolved ({error}); pass --config with the "
            "document the harness registers"
        )
    except (OSError, ValueError) as error:
        return f"the MCP authority settings could not be read: {error}"


def _admitted(args: argparse.Namespace) -> AdmittedKnowledgeBootstrap | BootstrapRefusal | str:
    """The admitted context this invocation runs under, or the refusal that stopped it."""

    config = _settings(args)
    if isinstance(config, str):
        return config
    return admit_bootstrap_context(config, args.repo)


def _refusal_payload(refusal: BootstrapRefusal | BootstrapRunRefusal) -> dict[str, Any]:
    return {
        "state": "refused",
        "code": refusal.code,
        "detail": refusal.detail,
        "nextAction": refusal.next_action,
    }


def _status_payload(admitted: AdmittedKnowledgeBootstrap) -> dict[str, Any]:
    """The read-only report: retention, the destination now, and nothing restated from a record."""

    retention = read_progress(
        admitted.staging_root,
        scope=admitted.admission.scope,
        destination_path=admitted.destination_path,
    )
    staged = candidate_database_path(staged_candidate_directory(admitted.staging_root))
    destination = admitted.destination_path
    resolved = resolve_published_intent(admitted.context)
    if isinstance(resolved, PublishedIntentUnavailable):
        destination_payload: dict[str, Any] = {
            "state": resolved.state,
            "datasetPath": destination.as_posix(),
            "refusalCode": resolved.code,
            "detail": resolved.detail,
            "identity": None,
        }
    else:
        destination_payload = {
            "state": "recorded",
            "datasetPath": destination.as_posix(),
            "refusalCode": None,
            "detail": (
                f"the declared location holds dataset {resolved.logical_digest} bound to "
                f"{resolved.repository_id}"
            ),
            "identity": {
                "repositoryId": resolved.repository_id,
                "schemaVersion": resolved.schema_version,
                "logicalDigest": resolved.logical_digest,
            },
        }
    return {
        "state": "status",
        "repositoryId": admitted.repo_id,
        "authoritySource": admitted.authority.source,
        "admissionProvenance": {
            "kind": admitted.admission.provenance.kind,
            "authority": admitted.admission.provenance.authority,
            "reference": admitted.admission.provenance.reference,
            "detail": admitted.admission.provenance.detail,
        },
        "scope": admitted.admission.scope,
        "destinationPath": destination.as_posix(),
        "destinationNow": destination_payload,
        "staging": {
            "root": admitted.staging_root.as_posix(),
            "retentionState": retention.state,
            "retentionDetail": retention.detail,
            "progressPath": progress_path(admitted.staging_root).as_posix(),
            "stagedCandidate": staged.as_posix(),
            "stagedCandidatePresent": staged.is_file(),
        },
        "wrote": False,
    }


def _run_payload(result: BootstrapRunResult) -> dict[str, Any]:
    """One bootstrap run as the report: the authority, the batch, the readback and what remains."""

    report = result.report
    admitted = result.admitted
    published = report.publication
    return {
        "state": "run",
        "repositoryId": admitted.repo_id,
        "authoritySource": admitted.authority.source,
        "authority": {
            "configPath": admitted.authority.config_path.as_posix(),
            "entry": admitted.authority.authority_entry,
            "codeRepositoryRoot": admitted.authority.code_repository_root.as_posix(),
            "declaredMemoryRoot": admitted.authority.declared_memory_root.as_posix(),
            "resolvedMemoryRoot": admitted.authority.resolved_memory_root.as_posix(),
            "coordinationRoot": admitted.authority.coordination_root.as_posix(),
            "topology": admitted.authority.topology,
            "settingsPath": admitted.authority.settings_path.as_posix(),
        },
        "admissionProvenance": {
            "kind": admitted.admission.provenance.kind,
            "authority": admitted.admission.provenance.authority,
            "reference": admitted.admission.provenance.reference,
            "detail": admitted.admission.provenance.detail,
        },
        "scope": admitted.admission.scope,
        "sourceRevisions": {
            "codeBaseCommit": admitted.admission.code_base_commit,
            "memoryBaseCommit": admitted.admission.memory_base_commit,
        },
        "destinationPath": admitted.destination_path.as_posix(),
        "destinationBefore": {
            "state": result.destination_before.state,
            "detail": result.destination_before.detail,
            "identity": _identity_record(result.destination_before.identity),
        },
        "staging": {
            "root": admitted.staging_root.as_posix(),
            "retainedBefore": result.retention.state,
            "retainedBeforeDetail": result.retention.detail,
            "progressPath": (
                None if result.progress_path is None else result.progress_path.as_posix()
            ),
        },
        "run": {
            "mode": "committed" if result.progress.run_mode == "committed" else "planned",
            "dryRun": report.dry_run,
            "batchState": report.batch_state,
            "entriesRead": list(report.entries_read),
            "committed": [one.entry_id for one in report.committed],
            "skipped": [one.entry_id for one in report.rulings],
            "refused": [one.entry_id for one in report.refused],
            "heldOperations": [one.as_record() for one in report.held_operations],
            "allocationJournal": report.allocation_journal,
        },
        "publication": {
            "state": "not-selected" if published is None else str(published.state),
            "destinationRef": (
                None
                if published is None or published.destination_ref is None
                else str(published.destination_ref)
            ),
            "identity": _identity_record(None if published is None else published.identity),
            "refusalCode": (
                None
                if published is None or published.refusal is None
                else str(published.refusal.code)
            ),
        },
        "publishedIdentity": (
            None
            if result.readback is None
            else {
                "state": result.readback.state,
                "datasetPath": result.readback.dataset_path.as_posix(),
                "identity": _identity_record(result.readback.identity),
                "refusalCode": result.readback.refusal_code,
                "detail": result.readback.detail,
            }
        ),
        # NAMED FOR WHAT IT IS: these are the contents found **at the declared destination**, read
        # through the invariant view whether or not this run published into it. Calling the block
        # "published contents" made a reader take the previous dataset's contents for this run's
        # publication in exactly the state where nothing was published at all.
        "destinationContents": {
            "state": result.contents.state,
            "revisions": len(result.contents.revisions),
            "pages": result.contents.pages,
            "publishedByThisRun": report.publication is not None
            and report.publication.identity is not None,
            "detail": (
                result.contents.detail
                if report.publication is not None and report.publication.identity is not None
                else (
                    "this run published nothing, so nothing here is this run's work; the sentence "
                    f"above describes the location as a read of it found it ({result.contents.detail})"
                )
            ),
        },
        "entries": [one.as_record() for one in result.entries],
        "remaining": list(result.remaining),
        "unmeasured": list(result.unmeasured),
        "carried": list(result.carried),
        "remainingBasis": result.progress.remaining_basis,
        "progressRecordWritten": result.progress_path is not None,
    }


def _identity_record(identity: Any) -> dict[str, str] | None:
    if identity is None:
        return None
    return {
        "repositoryId": identity.repository_id,
        "schemaVersion": identity.schema_version,
        "logicalDigest": identity.logical_digest,
    }


def _cleanup_payload(cleanup: StagingCleanup) -> dict[str, Any]:
    return {
        "state": cleanup.state,
        "stagingRoot": cleanup.staging_root.as_posix(),
        "code": cleanup.code,
        "detail": cleanup.detail,
    }


def _print(as_json: bool, payload: dict[str, Any]) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    print(_summary(payload))


def _summary(payload: dict[str, Any]) -> str:
    """The report as a reader scans it: the mode, the state, and one line per consequential fact."""

    state = payload.get("state")
    if state == "refused":
        return (
            f"knowledge-bootstrap REFUSED ({payload['code']}): {payload['detail']}\n"
            f"  next action: {payload['nextAction']}"
        )
    if state == "status":
        destination = payload["destinationNow"]
        staging = payload["staging"]
        return "\n".join(
            [
                f"knowledge-bootstrap status for {payload['repositoryId']} "
                f"(nothing written by this run)",
                f"  authority: {payload['authoritySource']}",
                f"  scope: {payload['scope']}",
                f"  destination: {payload['destinationPath']} -> {destination['state']}",
                f"    {destination['detail']}",
                f"  staging: {staging['root']} -> retained {staging['retentionState']}",
                f"    staged candidate present: {staging['stagedCandidatePresent']}",
                f"    retained record: {staging['progressPath']}",
            ]
        )
    if state == "cleanup":
        return (
            f"knowledge-bootstrap cleanup -> {payload['state']} ({payload['code']})\n"
            f"  {payload['detail']}"
        )
    run = payload["run"]
    publication = payload["publication"]
    contents = payload["destinationContents"]
    lines = [
        f"knowledge-bootstrap {run['mode']} on {payload['repositoryId']} "
        f"({payload['admissionProvenance']['kind']})",
        f"  authority: {payload['authoritySource']}",
        f"  source revisions: code {payload['sourceRevisions']['codeBaseCommit']}, "
        f"memory {payload['sourceRevisions']['memoryBaseCommit']}",
        f"  destination: {payload['destinationPath']} (before: "
        f"{payload['destinationBefore']['state']})",
        f"  staging: {payload['staging']['root']} (retained before: "
        f"{payload['staging']['retainedBefore']})",
        f"  batch: {run['batchState']}  committed {len(run['committed'])}  "
        f"skipped {len(run['skipped'])}  refused {len(run['refused'])}  "
        f"held operations {len(run['heldOperations'])}",
        f"  publication: {publication['state']}",
        f"  destination contents: {contents['state']} ({contents['revisions']} revision(s) in "
        f"{contents['pages']} page(s); published by this run: "
        f"{contents['publishedByThisRun']})",
        f"  remaining: {len(payload['remaining'])}  unmeasured: {len(payload['unmeasured'])}  "
        f"carried: {len(payload['carried'])}",
        f"  progress record written: {payload['progressRecordWritten']}",
    ]
    if payload["publishedIdentity"] is not None:
        read_back = payload["publishedIdentity"]
        lines.append(f"  read-back: {read_back['state']} -- {read_back['detail']}")
    lines.append(f"  remaining basis: {payload['remainingBasis']}")
    return "\n".join(lines)


def run(args: argparse.Namespace) -> int:
    """Run one bootstrap mode and print its report; the report IS the result."""

    refusal = _invocation_refusal(args)
    if refusal is not None:
        print(refusal)
        return EXIT_REFUSED
    return _dispatch(args)


def _dispatch(args: argparse.Namespace) -> int:
    """Resolve the admitted context, then run the one mode this invocation selected."""

    admitted = _admitted(args)
    if isinstance(admitted, str):
        print(admitted)
        return EXIT_REFUSED
    if isinstance(admitted, BootstrapRefusal):
        _print(args.as_json, _refusal_payload(admitted))
        return EXIT_REFUSED
    if args.discard_staging:
        return _cleanup(args, admitted)
    if args.status:
        _print(args.as_json, _status_payload(admitted))
        return EXIT_REPORTED
    result = bootstrap_knowledge(
        admitted,
        Path(args.hand_off_list),
        authorization_ref=args.authorization_ref,
        commit=args.commit,
    )
    if isinstance(result, BootstrapRunRefusal):
        _print(args.as_json, _refusal_payload(result))
        return EXIT_REFUSED
    _print(args.as_json, _run_payload(result))
    return EXIT_REPORTED


def _cleanup(args: argparse.Namespace, admitted: AdmittedKnowledgeBootstrap) -> int:
    """The bounded cleanup owner's one outcome: discarded, absent, or the refusal that kept it."""

    cleanup = discard_bootstrap_staging(admitted.staging_root, admitted.context)
    payload = _cleanup_payload(cleanup)
    payload["state"] = "cleanup"
    _print(args.as_json, payload)
    return EXIT_REPORTED if cleanup.state in ("discarded", "absent") else EXIT_REFUSED
