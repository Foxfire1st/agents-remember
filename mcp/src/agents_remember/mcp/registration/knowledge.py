"""The knowledge operation family: the mounted surface over a memory tree's text knowledge.

This is the family module ``registration/__init__.py``'s docstring describes: one
``register_knowledge_tools(server, config)`` that declares its family against the server it is
handed, delegating to the payload builders in :mod:`agents_remember.mcp.tools.knowledge`. It is
**appended** to ``TOOL_REGISTRARS``, never inserted: FastMCP publishes tools in registration order,
so every existing name keeps the position it was advertised at.

Three operations: ``knowledge_read``, ``knowledge_diff`` and ``knowledge_integrity_check``.

**No registered tool accepts a database path (MIK-R26 rule 5).** Each operation selects a memory
tree by its root directory and reads the tree's text knowledge through its derived index
(MIK-R23). The namespace is the index's own constant; the server supplies it. An unconverted tree
or a database file is refused as ``legacy-format``.

**Nothing here writes.** Knowledge is written by the curator file writer, reached through the
``agents-remember knowledge-ingest`` and ``agents-remember knowledge-bootstrap`` commands
(MIK-R12). The earlier ``knowledge_change`` and ``knowledge_project`` tools are removed from the
registered set: the first only ever refused, and the second rendered views of the database.

**The surface performs no domain reasoning.** Each handler validates its wire request, delegates, and
returns the typed shape the response model declares. ``knowledge_read`` returns recorded claims and
assessments as attributed records; ``knowledge_diff`` returns the files' own Git diff and no effect
label; ``knowledge_integrity_check`` returns the validator's report and a leaf's worklist.
"""

from typing import Any

from mcp.server.fastmcp import FastMCP

from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig

from ..tools.knowledge import (
    DiffToolRequest,
    IntegrityCheckRequest,
    ReadToolRequest,
    knowledge_diff_payload,
    knowledge_integrity_check_payload,
    knowledge_read_payload,
)


def register_knowledge_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    """Register the knowledge read, diff and integrity operations.

    The runtime configuration supplies two things to this family. The workspace root is the
    *default repository* a read falls back to when the caller names no repository of its own; the
    read builder completes the source-resolution pair or names neither half. The coordination root
    is where a memory tree's derived index is cached (MIK-R23). The memory tree itself is
    caller-supplied, because the substrate decides nothing about which tree is meant.
    """

    _register_knowledge_read(server, config)
    _register_knowledge_diff(server)
    _register_knowledge_integrity_check(server)


def _register_knowledge_read(server: FastMCP, config: McpRuntimeConfig) -> None:
    """The one read operation, over the five named views."""

    @server.tool()
    def knowledge_read(
        memoryRoot: str,
        view: str,
        *,
        orderingInput: str | None = None,
        limit: int = 32,
        continuation: str | None = None,
        invariantRevisionId: str | None = None,
        familyRevisionId: str | None = None,
        sourcePath: str | None = None,
        repositoryRoot: str | None = None,
        codeTreeId: str | None = None,
    ) -> dict[str, Any]:
        """Retrieve one named view of a memory tree's knowledge from supplied seeds at one snapshot,
        using explicit filters and a snapshot-bound continuation. `memoryRoot` is the root
        directory of a converted memory tree (one that holds knowledge/layout.json): a leaf's
        memory worktree, or `memoryTree.memoryRoot` of a read_ar_files published-intent block. An
        unconverted tree or a database file is refused as `legacy-format`. The five views are
        source_context, invariant, family, review_matrix and curation_queue. Returns recorded
        claims and assessments as attributed records: every ordered position and every
        no-consequence statement carries an `authored` or `mechanical` provenance class, and a
        value that cannot be classified is reported as an unresolved limitation rather than
        returned with an empty class. orderingInput defaults to stable_ordering. Every response is
        a page within one token threshold (`page`, and `threshold` on a refusal), and
        `continuation` accepts the token any page minted, including the published-intent block of
        read_ar_files: pass it with its `continuationView` as `view` and no other subject (the
        token binds its ordering and code tree; repositoryRoot relocates the code repository).
        `currentness` gives each returned invariant's state (stale, unverifiable, unrealized,
        current) at the walk's code tree: the one named by `codeTreeId`, or the continuation's on a
        resumed page; without either they are unverifiable. source_context with sourcePath is the
        family-complete leaf read: the path's own invariants, then each containing family's header
        (guarantee, routes, members) and its remaining members with their entries, then the
        advertised families -- the same selection and manifestDigest read_ar_files returns -- then
        one compact chain_family row per family routed at the path's directory or an ancestor
        (`payload.routeChain`; no_governing_family when none). source_context with a family ID in
        familyRevisionId and no sourcePath returns that family's full content. The invariant view
        names the invariant's families in `families`. No mounted tool writes knowledge: the
        curator file writer does, through `agents-remember knowledge-ingest` for a leaf and
        `agents-remember knowledge-bootstrap` for a repository with no leaf."""
        return knowledge_read_payload(
            ReadToolRequest(
                memory_root=memoryRoot,
                view=view,
                ordering_input=orderingInput,
                limit=limit,
                continuation=continuation,
                invariant_revision_id=invariantRevisionId,
                family_revision_id=familyRevisionId,
                source_path=sourcePath,
                repository_root=repositoryRoot,
                code_tree_id=codeTreeId,
            ),
            workspace_root=str(config.workspace_root),
            coordination_root=str(config.coordination_root),
        )


def _register_knowledge_diff(server: FastMCP) -> None:
    """The comparison operation, which infers no semantic label."""

    @server.tool()
    def knowledge_diff(
        memoryRoot: str,
        beforeRevision: str = "HEAD",
        afterRevision: str | None = None,
        recordId: str | None = None,
        path: str | None = None,
    ) -> dict[str, Any]:
        """Return the Git diff of the knowledge files of a memory repository: every changed file
        under knowledge/ and onboarding/ with its patch. Record files (and a record's .md prose)
        are grouped by record, with the realization and proof entries that changed; an onboarding
        card (.md) and its sidecar (.json) are grouped under the source path the sidecar declares;
        history files and any other changed knowledge file are listed beside them.
        `memoryRoot` is the root of the memory repository (or of one of its worktrees); a
        directory inside it is refused with the root to pass. With `afterRevision` given, the two
        Git revisions or trees are compared and nothing is written. With no `afterRevision`,
        `beforeRevision` (default `HEAD`) is compared with the memory WORKING TREE, so uncommitted
        knowledge changes show: to do that the working tree is captured as a Git tree, which
        writes loose, unreferenced objects into the repository's object store (no ref, branch,
        index or working file changes; Git's own garbage collection removes them later).
        One answer stays within the token `threshold` that `knowledge_read` states. `complete`
        says whether it holds every selected file's whole patch; when it does not, `leftOut` names
        the files whose patch is absent (`paths`, in the answer's order; `pathsNotNamed` counts any
        that could not even be named), the patches cut short (`cutPatches`), and how to reach each
        (`nextAction`). `recordId` narrows the answer to one record and the source paths that name
        it; `path` narrows it to one changed file (a record file, a sidecar or a card, by its
        memory-repository path) or, given a source path, to that source's card and sidecar. A
        `recordId` or a `path` that names nothing in either tree is refused by name
        (`selector_absent`); one that names something unchanged answers with no file. A side whose
        tree is unconverted (it holds no knowledge/layout.json) is refused as `legacy-format`. The
        diff is the files' own change: no semantic effect label is inferred from it."""
        return knowledge_diff_payload(
            DiffToolRequest(
                memory_root=memoryRoot,
                before=beforeRevision,
                after=afterRevision,
                record_id=recordId,
                path=path,
            )
        )


def _register_knowledge_integrity_check(server: FastMCP) -> None:
    """The validator's report and the leaf's worklist."""

    @server.tool()
    def knowledge_integrity_check(
        memoryRoot: str | None = None,
        *,
        codeRoot: str | None = None,
        baseCommits: list[str] | None = None,
        contractPath: str | None = None,
    ) -> dict[str, Any]:
        """Run the knowledge validator (MIK-R22) over one converted memory tree and return its
        report: `validation.ok`, the refusal and report-only counts, the counts by rule, and the
        first violations (refusing ones first; `violationsTruncated` says when more exist). Name a
        leaf by `contractPath`: the validator reads the leaf's memory worktree against its
        recorded memory base and its code worktree, and the response adds the leaf's latest
        change-to-knowledge worklist (MIK-R08): its state, digest, item counts and items, with the
        persisted file's path for every item's facts. Or name a tree directly with `memoryRoot`
        and its paired code checkout `codeRoot`. `baseCommits` are memory commits to compare
        anchors with; with none, every anchor is checked for path existence. An unconverted tree
        or a database file is refused as `legacy-format`. The tool reads only: it writes no file
        and opens no database."""
        return knowledge_integrity_check_payload(
            IntegrityCheckRequest(
                memoryRoot=memoryRoot,
                codeRoot=codeRoot,
                baseCommits=None if baseCommits is None else tuple(baseCommits),
                contractPath=contractPath,
            )
        )
