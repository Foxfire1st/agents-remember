"""The knowledge operation family: the mounted surface for the application read/render API.

This is the family module ``registration/__init__.py``'s docstring describes: one
``register_knowledge_tools(server, config)`` that declares its family against the server it is
handed, delegating to the payload builders in :mod:`agents_remember.mcp.tools.knowledge`. It is
**appended** to ``TOOL_REGISTRARS``, never inserted: FastMCP publishes tools in registration order,
so every existing name keeps the position it was advertised at.

Five operation families, spelled as ``Doc13:181-187`` spells them: ``knowledge_read``,
``knowledge_change``, ``knowledge_diff``, ``knowledge_integrity_check``, ``knowledge_project``.

**The surface performs no domain reasoning.** Each handler validates its wire request, delegates, and
returns the typed shape the response model declares. ``knowledge_read`` returns recorded claims and
assessments as attributed records; ``knowledge_change`` records a caller-authored proposal through an
admitted operation another leaf owns and authors nothing; ``knowledge_diff`` carries only effect
labels an identified agent or assessment supplied; ``knowledge_integrity_check`` reports conditions
and their limits and produces no verdict; ``knowledge_project`` renders through the projection writer
and writes no file itself.

**Nothing is mounted for the reviewer.** ``KS-R22@v1`` owns the Intent Reviewer, the cockpit route and
the browser client. What this module publishes is the interface L22 mounts -- the five operations, the
review-matrix view and the typed models behind them -- and it adds no panel, no route and no client.
"""

from typing import Any

from mcp.server.fastmcp import FastMCP

from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig

from ..tools.knowledge import (
    ChangeToolRequest,
    DiffToolRequest,
    IntegrityCheckRequest,
    ProjectToolRequest,
    ReadToolRequest,
    knowledge_change_payload,
    knowledge_diff_payload,
    knowledge_integrity_check_payload,
    knowledge_project_payload,
    knowledge_read_payload,
)


def register_knowledge_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    """Register the knowledge read, change, diff, integrity and projection operations.

    The runtime configuration supplies exactly one thing to this family: the workspace root a read
    falls back to when the caller names no repository of its own. It is handed over as the *default
    repository*, not as half of a source-resolution pair: the read builder completes the pair or
    names neither half, because a context carrying ``repository_root`` without ``code_tree_id`` is
    refused by its own model and a minimal schema-conformant call would then raise instead of
    returning a view. It also supplies the coordination root, under whose runtime directory a
    converted memory tree's derived index is cached when a read selects that tree (MIK-R23).
    Everything else a handler needs -- the dataset path, the namespace and the
    destination -- is caller-supplied, because the substrate decides nothing about which dataset or
    which vault is meant.
    """

    _register_knowledge_read(server, config)
    _register_knowledge_change(server)
    _register_knowledge_diff(server, config)
    _register_knowledge_integrity_check(server)
    _register_knowledge_project(server, config)


def _register_knowledge_read(server: FastMCP, config: McpRuntimeConfig) -> None:
    """The one read operation, over the five named views."""

    @server.tool()
    def knowledge_read(
        databasePath: str,
        repositoryId: str,
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
        """Retrieve one named view from supplied seeds at one snapshot, using explicit filters and a
        snapshot-bound continuation. The five views are source_context, invariant, family,
        review_matrix and curation_queue. Returns recorded claims and assessments as attributed
        records: every ordered position and every no-consequence statement carries an `authored` or
        `mechanical` provenance class, and a value that cannot be classified is reported as an
        unresolved limitation rather than returned with an empty class. orderingInput defaults to
        stable_ordering. For a converted memory tree (databasePath is its root) every response is a
        page within one token threshold (`page`, and `threshold` on a refusal), and `continuation`
        accepts the token any page minted, including the published-intent block of read_ar_files:
        pass it with its `continuationView` as `view` and no other subject (the token binds its
        ordering and code tree; repositoryRoot relocates the code repository). `currentness`
        gives each returned invariant's state (stale, unverifiable, unrealized, current) at the
        walk's code tree: the one named by `codeTreeId`, or the continuation's on a resumed page;
        without either they are unverifiable. On a converted tree, source_context with sourcePath
        is the family-complete leaf read: the path's own invariants, then each containing family's
        header (guarantee, routes, members) and its remaining members with their entries, then the
        advertised families -- the same selection and manifestDigest read_ar_files returns. The
        invariant view names the invariant's families in `families`."""
        return knowledge_read_payload(
            ReadToolRequest(
                database_path=databasePath,
                repository_id=repositoryId,
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


def _register_knowledge_change(server: FastMCP) -> None:
    """The record operation's mount point: it declares the shape and writes nothing.

    The tool is still mounted because its *name* is part of the published family -- a caller asking
    for it must get a typed refusal rather than "no such tool" -- but the handler now says exactly
    what it does, which is refuse and point at the writer that can record.
    """

    @server.tool()
    def knowledge_change(
        databasePath: str,
        repositoryId: str,
        recordKind: str,
        request: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Refuse a mount-side change request and name the writer that can record. Do not use this
        tool to record: it has no admitted write operation for any kind, so every kind is refused
        with `registration_absent` and nothing is written. The knowledge write plane has one writer
        -- the batch operation that commits a whole curator hand-off list -- and both shipped CLI
        subcommands reach it: `agents-remember knowledge-ingest` for a leaf enclosure's ordinary
        route, and `agents-remember knowledge-bootstrap` for a repository with no enclosure in
        scope. On a converted memory tree (it holds `knowledge/layout.json`) both write
        knowledge files through the curator file writer instead. `knowledge-ingest` additionally
        publishes that candidate to the repository's one declared published dataset location and
        reads the published identity back; read the committed result back with `knowledge_read`."""
        return knowledge_change_payload(
            ChangeToolRequest(
                database_path=databasePath,
                repository_id=repositoryId,
                record_kind=recordKind,
                body=request,
            )
        )


def _register_knowledge_diff(server: FastMCP, config: McpRuntimeConfig) -> None:
    """The comparison operation, which infers no semantic label."""

    @server.tool()
    def knowledge_diff(
        databasePath: str,
        repositoryId: str,
        beforePath: str,
        afterPath: str,
        request: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return field and record changes grouped by invariant content, realization claims, evidence
        records and relationships between exact states. Semantic effect labels are included only when
        supplied by an identified agent or assessment, never inferred from the diff."""
        return knowledge_diff_payload(
            DiffToolRequest(
                database_path=databasePath,
                repository_id=repositoryId,
                before_path=beforePath,
                after_path=afterPath,
                body=request,
            ),
            coordination_root=str(config.coordination_root),
        )


def _register_knowledge_integrity_check(server: FastMCP) -> None:
    """The report operation, which produces no verdict."""

    @server.tool()
    def knowledge_integrity_check(
        databasePath: str | None = None,
        repositoryId: str | None = None,
        scopeId: str | None = None,
        *,
        runId: str | None = None,
        inputDigest: str | None = None,
        contractPath: str | None = None,
    ) -> dict[str, Any]:
        """Report declared structural-rule violations, mechanically matched review conditions, their
        registered traversal scope and the observable mapping and scan limitations. It produces no
        compatibility verdict and no causal explanation: `compatible` is absent by design, not
        omitted by accident, and an unresolved assessment stays unresolved. The scope selects the
        recorded run; `runId` or `inputDigest` selects one exact run among several in that scope, and
        the response names the selected run and its input identities so the conditions cannot be
        read as belonging to a run they were not measured over. `contractPath` names a leaf by its
        series contract and adds that leaf's latest change-to-knowledge worklist (MIK-R08): its
        state, digest, item counts and items, with the persisted file's path for every item's
        facts; a leaf may be named without a dataset."""
        return knowledge_integrity_check_payload(
            IntegrityCheckRequest(
                databasePath=databasePath,
                repositoryId=repositoryId,
                scopeId=scopeId,
                runId=runId,
                inputDigest=inputDigest,
                contractPath=contractPath,
            )
        )


def _register_knowledge_project(server: FastMCP, config: McpRuntimeConfig) -> None:
    """The projection operation, the only write path to a destination."""

    @server.tool()
    def knowledge_project(
        databasePath: str,
        repositoryId: str,
        destinationRoot: str,
        *,
        profileId: str = "default",
        formats: list[str] | None = None,
        views: list[dict[str, Any]] | None = None,
        authorizedOverwrites: list[str] | None = None,
    ) -> dict[str, Any]:
        """Render named read-only views into an explicitly authorized destination. Authored
        explanations are copied with their provenance and are never invented or reassessed; Markdown
        and JSON are sibling views from the same resolved records and a JSON projection is not a
        portable database export. Managed outputs are tracked in projection-manifest.json, writes are
        confined and staged, and an externally edited file is reported and preserved unless the
        caller authorizes an overwrite for that exact path."""
        return knowledge_project_payload(
            ProjectToolRequest(
                database_path=databasePath,
                repository_id=repositoryId,
                destination_root=destinationRoot,
                profile_id=profileId,
                formats=tuple(formats or ("markdown",)),
                views=tuple(views or ()),
                authorized_overwrites=tuple(authorizedOverwrites or ()),
            ),
            coordination_root=str(config.coordination_root),
        )
