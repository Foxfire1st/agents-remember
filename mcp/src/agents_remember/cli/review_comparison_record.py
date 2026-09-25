"""CLI adapter: record one leaf's review comparison as a durable generation.

    agents-remember review-record-comparison --config <mcp authority settings>
        --contract <leaf enclosure contract> [--evidence <owner>:<task-relative path>]
        [--historical-absence before|after] [--json]

**Why this command exists.** The Intent Reviewer reads a *per-leaf comparison generation*: a closed
leaf's review reopens from ``history:recorded-comparison`` and falls back to a bare
``history:recorded-source-range`` when the leaf published none. The owner that produces a generation
-- :func:`~agents_remember.application.review_comparison_freeze.freeze_review_comparison` -- is a
complete production operation, but until this command it had **no caller outside the test suite**, so
no leaf could ever publish one and the reviewer's whole knowledge column rendered its empty state.
This is that caller, and it adds no mechanism: it composes the review exactly as the surface does,
through the same resolution and the same composition, and publishes only what the composition
actually bound.

**``--contract`` is REQUIRED and is the write guard**, exactly as ``memory-citations``,
``memory-backfill`` and ``knowledge-ingest`` use it. A generation is published under the *task root*
its enclosure contract records, so there is no argument list that can aim a comparison record at
another leaf's line: the contract names the repository, the master and the leaf, and every one of
them is read from that document rather than spelled again here.

**``--config`` is REQUIRED** for the same reason the daemon requires it. The freeze resolves a
candidate from canonical task context and retains knowledge snapshots through the storage owner, and
both read the coordination root the authority settings name. The command is reached from a
non-editable install of the package -- the same route ``knowledge-ingest`` documents, because an
undeclared CLI loaded from a source *checkout* is refused at the primary checkout and isolated to a
disposable coordination root in a linked worktree (:mod:`agents_remember.kernel.primitives.
checkout_coordination`) -- so the live authority is named explicitly rather than inferred.

**What this command does NOT do.** It authors no knowledge, places no dataset and establishes no
before half. A comparison is *between* two operands, and those operands are the write plane's own
artifacts: ``knowledge-ingest`` authors the candidate half and places the dataset the leaf forks from,
and the shipped first-generation owner establishes an identified empty before half for a repository
whose knowledge begins at this leaf. This command records the comparison those owners made; a leaf
whose halves are absent is refused by the freeze's own named state rather than by a second rule
invented here.

PLANNING IS NOT OFFERED, AND THAT IS DELIBERATE. A freeze either publishes or refuses, and it is
already idempotent: an exact retry of the same comparison converges on the published record instead of
rewriting it (``reused`` says which happened), so the honest way to ask "what would this do" is to run
it and read whether the record was written or reused. A preview flag would have to describe the
generation id it had not derived yet, which is a number this surface must not invent.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from agents_remember.application.review_comparison_freeze import (
    ComparisonEvidenceInput,
    ComparisonFreezeOptions,
    ComparisonGenerationFreeze,
    freeze_review_comparison,
)
from agents_remember.application.review_comparison_generation import (
    COMPARISON_MANIFEST_NAME,
    ComparisonGenerationRef,
    read_generation_refs,
    read_manifest,
)
from agents_remember.kernel.primitives.runtime_config import ConfigError, load_config
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.models.knowledge.review import ReviewSurfaceRequest
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    WorktreeContract,
    load_contract,
)

__all__ = [
    "EXIT_PUBLISHED",
    "EXIT_REFUSED",
    "add_arguments",
    "report_payload",
    "run",
]

# The two exits this command answers with, and they are the ingest CLI's own two: a published
# generation is the operation's result, and a refusal is a named outcome a caller acts on rather than
# a crash. ``EXIT_REFUSED`` is not an error code for a broken command -- it is the code for "the
# freeze answered, and its answer was no".
EXIT_PUBLISHED = 0
EXIT_REFUSED = 2

# The one separator between an evidence citation's owner and its task-relative path. It is a colon
# rather than a path separator so an owner name can never be read out of a path by accident.
_EVIDENCE_SEPARATOR = ":"

# The two knowledge halves a caller can declare a historical absence for, spelled as the sides are.
_ABSENCE_SIDES: tuple[str, ...] = ("before", "after")


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        required=True,
        help="Path to the MCP authority settings that name the coordination root. Required, and "
        "absolute: the freeze resolves a canonical candidate and retains snapshots under the root "
        "those settings declare.",
    )
    parser.add_argument(
        "--contract",
        required=True,
        help="Path to the leaf enclosure contract. Required: the generation is published under the "
        "task root that contract records, so no argument list can aim it at another leaf's line.",
    )
    parser.add_argument(
        "--evidence",
        action="append",
        default=[],
        dest="evidence",
        metavar="OWNER:PATH",
        help="One owner-produced artifact this generation should cite, as <owner>:<task-relative "
        "path>. Repeatable. The bytes are read and digested while freezing, so a citation nobody can "
        "follow is refused rather than recorded.",
    )
    parser.add_argument(
        "--historical-absence",
        action="append",
        default=[],
        dest="historical_absence",
        choices=_ABSENCE_SIDES,
        metavar="SIDE",
        help="Declare that one knowledge half carries no recorded generation, for a repository whose "
        "intent history begins later. Repeatable. A declaration standing beside bytes that are "
        "present is refused: 'nothing was recorded' and 'here are the bytes' cannot both be true.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Print the whole outcome as one JSON object instead of the report.",
    )


def run(args: argparse.Namespace) -> int:
    """Record one leaf's comparison generation and print its outcome; the report IS the result."""

    invocation = _invocation(args)
    if isinstance(invocation, str):
        print(invocation)
        return EXIT_REFUSED
    contract, options = invocation
    standing = _standing_generation(contract)
    if standing is not None:
        options = replace(options, parent=standing)
    request = ReviewSurfaceRequest(
        repository_id=contract.repo_name,
        # The master is the task the leaf was cut from, and the enclosure records both spellings:
        # a leaf enclosure names its parent task, and a master enclosure names itself.
        master=contract.parent_task_name or contract.task_name,
        leaf_id=contract.leaf_id,
    )
    try:
        config = load_config(Path(args.config))
    except (ConfigError, ValueError, OSError) as error:
        print(
            f"the comparison was not recorded: the authority settings could not be read ({error})"
        )
        return EXIT_REFUSED
    freeze = freeze_review_comparison(config, request, options)
    payload = report_payload(freeze)
    if args.as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        _print_report(payload)
    return EXIT_PUBLISHED if freeze.published() else EXIT_REFUSED


def _standing_generation(contract: WorktreeContract) -> ComparisonGenerationRef | None:
    """The generation this leaf currently stands on, or ``None`` when it has published none.

    ``ComparisonFreezeOptions.parent`` is the caller's own statement of *which generation this one
    supersedes*, and the freeze derives the successor's recorded index from it: a caller that names
    none publishes an **index 1** generation. That is right for a leaf's first record and wrong for
    every later one. A leaf whose comparison legitimately changes would come to hold two generations
    that both claim index 1 with different bindings, and the reopen refuses a tied index by design
    (``review_comparison_reopen._reopen_latest``) -- so the leaf's own review could no longer say
    *which* comparison it is reading. Since the standing generation is discoverable from the leaf's
    own records, it is decided here, once, and the report prints the one that was named.

    The highest recorded index is the standing generation; a tie at that index is broken by the
    record's own ``recorded_at``, because "the one a reader was last shown" is the honest choice and
    directory order is not.
    """

    refs = read_generation_refs(contract.task_root, contract.leaf_id)
    if not refs:
        return None
    highest = max(ref.generation_index for ref in refs)
    at_highest = [ref for ref in refs if ref.generation_index == highest]
    if len(at_highest) == 1:
        return at_highest[0]
    return max(at_highest, key=lambda ref: (_recorded_at(ref), ref.generation_id))


def _recorded_at(ref: ComparisonGenerationRef) -> str:
    """One generation's own recorded instant, or the empty string when its record will not read.

    The discovery pass already skipped unreadable records, so this is belt and braces: a record that
    cannot be read here must not become an exception in the middle of naming a predecessor.
    """

    try:
        return read_manifest(ref.directory / COMPARISON_MANIFEST_NAME).recorded_at
    except KnowledgeStorageError:
        return ""


def _invocation(args: argparse.Namespace) -> tuple[WorktreeContract, ComparisonFreezeOptions] | str:
    """The contract this run is admitted under and what it contributes, or why it is refused.

    Every one of these is a fact about the argument list, so they are answered before a contract, a
    dataset or a byte is touched.
    """

    if not str(args.contract).strip():
        return (
            "--contract must not be blank: a comparison record is published under a leaf's contract"
        )
    if not str(args.config).strip():
        return "--config must not be blank: the freeze reads the coordination authority it names"
    try:
        contract = load_contract(Path(args.contract))
    except (ContractError, ValueError, OSError) as error:
        return (
            f"the comparison was not recorded: the enclosure contract could not be read ({error})"
        )
    evidence = _evidence_inputs(args.evidence)
    if isinstance(evidence, str):
        return evidence
    return contract, ComparisonFreezeOptions(
        evidence=evidence,
        historical_absence=tuple(dict.fromkeys(args.historical_absence)),
    )


def _evidence_inputs(raw: list[str]) -> tuple[ComparisonEvidenceInput, ...] | str:
    """The caller's evidence citations, or the reason one of them cannot be a citation.

    The split is checked here rather than where the citation is read, because an owner with no path
    and a path with no owner are both caller mistakes, and the freeze's own refusal would name the
    path it could not read rather than the argument that had no separator in it.
    """

    citations: list[ComparisonEvidenceInput] = []
    for entry in raw:
        owner, separator, relative = str(entry).partition(_EVIDENCE_SEPARATOR)
        if not separator or not owner.strip() or not relative.strip():
            return (
                f"--evidence {entry!r} is not an <owner>:<task-relative path> citation: an owner and "
                "a path are both required"
            )
        if Path(relative).is_absolute() or ".." in Path(relative).parts:
            return (
                f"--evidence {entry!r} names a path outside the task root; a citation is recorded "
                "with the task-relative path its bytes were read from, so it stays followable"
            )
        citations.append(ComparisonEvidenceInput(owner=owner.strip(), relative_path=relative))
    return tuple(citations)


def report_payload(freeze: ComparisonGenerationFreeze) -> dict[str, Any]:
    """One freeze's outcome as plain JSON-ready facts, with no live object in it.

    Every value is read off the record's own fields rather than restated, so the report cannot come to
    describe a generation the manifest does not. A refusal is reported in the comparison vocabulary's
    own three fields -- code, detail and the action that yields a record -- because a caller decides
    what to do next from those and not from a message this command composed.
    """

    manifest = freeze.manifest
    payload: dict[str, Any] = {
        "state": freeze.state,
        "reused": freeze.reused,
        "manifest_path": None if freeze.manifest_path is None else str(freeze.manifest_path),
        "generation_directory": None if freeze.directory is None else str(freeze.directory),
    }
    if freeze.refusal is not None:
        payload["refusal"] = {
            "code": freeze.refusal.code,
            "detail": freeze.refusal.detail,
            "next_action": freeze.refusal.next_action,
            "offending_input": freeze.refusal.offending_input,
        }
    if manifest is None:
        return payload
    payload["generation"] = {
        "generation_id": manifest.generation_id,
        "generation_index": manifest.generation_index,
        "binding_digest": manifest.binding_digest,
        "recorded_at": manifest.recorded_at,
        "repository_id": manifest.repository_id,
        "master": manifest.master,
        "leaf_id": manifest.leaf_id,
        "task_root": manifest.task_root,
        "contract_path": manifest.contract_path,
        "temporary_storage_scope": manifest.temporary_storage_scope,
    }
    payload["source"] = {
        "code_repository_root": manifest.source.code_repository_root,
        "baseline_code_tree_id": manifest.source.baseline_code_tree_id,
        "candidate_code_tree_id": manifest.source.candidate_code_tree_id,
        "custody": manifest.source.custody,
        "custody_refs": list(manifest.source.custody_refs),
        "custody_commits": list(manifest.source.custody_commits),
    }
    payload["scope"] = {
        "selected": manifest.scope.selected,
        "selector_kind": manifest.scope.selector_kind,
        "selector_id": manifest.scope.selector_id,
        "inventory_state": manifest.scope.inventory_state,
        "inventory_digest": manifest.scope.inventory_digest,
        "changed_path_count": manifest.scope.changed_path_count,
        "inventory_partial": manifest.scope.inventory_partial,
        "detail": manifest.scope.detail,
    }
    payload["knowledge"] = [
        {
            "side": binding.side,
            "state": binding.state,
            "reason": binding.reason,
            "identity": None
            if binding.identity is None
            else binding.identity.model_dump(mode="json"),
        }
        for binding in manifest.knowledge
    ]
    payload["records"] = manifest.records.model_dump(mode="json")
    payload["evidence"] = [
        {
            "owner": citation.owner,
            "relative_path": citation.relative_path,
            "sha256": citation.sha256,
            "byte_count": citation.byte_count,
        }
        for citation in manifest.evidence
    ]
    payload["policies"] = [stamp.model_dump(mode="json") for stamp in manifest.policies]
    payload["lineage"] = manifest.lineage.model_dump(mode="json")
    return payload


def _print_report(payload: dict[str, Any]) -> None:
    """Print the outcome as lines; the JSON form is the same facts, and this is the readable one."""

    if payload["state"] != "published":
        refusal = payload.get("refusal") or {}
        print("refused: no comparison generation was published")
        print(f"  code: {refusal.get('code')}")
        print(f"  detail: {refusal.get('detail')}")
        print(f"  next: {refusal.get('next_action')}")
        if refusal.get("offending_input"):
            print(f"  offending input: {refusal.get('offending_input')}")
        return
    generation = payload["generation"]
    source = payload["source"]
    scope = payload["scope"]
    print(
        "published: the comparison generation is recorded"
        if not payload["reused"]
        else "published: this exact comparison was already recorded, and its record was reused"
    )
    print(f"  generation: {generation['generation_id']} (index {generation['generation_index']})")
    print(f"  seal: {generation['binding_digest']}")
    print(f"  recorded at: {generation['recorded_at']}")
    print(
        f"  leaf: {generation['repository_id']} / {generation['master']} / {generation['leaf_id']}"
    )
    print(f"  manifest: {payload['manifest_path']}")
    print(f"  task root: {generation['task_root']}")
    print(f"  temporary storage scope: {generation['temporary_storage_scope']}")
    print(
        f"  scope: {scope['selected']} · inventory {scope['inventory_state']} · {scope['changed_path_count']} changed path(s)"
    )
    print(
        f"  source: {source['baseline_code_tree_id']} -> {source['candidate_code_tree_id']} "
        f"({source['custody']})"
    )
    for binding in payload["knowledge"]:
        identity = binding["identity"]
        named = (
            "" if identity is None else f" {identity['repository_id']}/{identity['logical_digest']}"
        )
        print(f"  knowledge {binding['side']}: {binding['state']}{named}")
    for citation in payload["evidence"]:
        print(f"  evidence: {citation['owner']} {citation['relative_path']} {citation['sha256']}")
    parent = payload["lineage"]["parent_generation_id"]
    print(f"  supersedes: {parent if parent is not None else 'nothing (first generation)'}")
