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
    resolves recorded source anchors against when the caller names none. Everything else a handler
    needs -- the dataset path, the namespace and the destination -- is caller-supplied, because the
    substrate decides nothing about which dataset or which vault is meant.
    """

    _register_knowledge_read(server, config)
    _register_knowledge_change(server)
    _register_knowledge_diff(server)
    _register_knowledge_integrity_check(server)
    _register_knowledge_project(server)


def _register_knowledge_read(server: FastMCP, config: McpRuntimeConfig) -> None:
    """The one read operation, over the five named views."""

    @server.tool()
    def knowledge_read(
        databasePath: str,
        repositoryId: str,
        view: str,
        *,
        orderingInput: str = "stable_ordering",
        limit: int = 32,
        continuation: str | None = None,
        invariantRevisionId: str | None = None,
        familyRevisionId: str | None = None,
        repositoryRoot: str | None = None,
        codeTreeId: str | None = None,
    ) -> dict[str, Any]:
        """Retrieve one named view from supplied seeds at one snapshot, using explicit filters and a
        snapshot-bound continuation. The five views are source_context, invariant, family,
        review_matrix and curation_queue. Returns recorded claims and assessments as attributed
        records: every ordered position and every no-consequence statement carries an `authored` or
        `mechanical` provenance class, and a value that cannot be classified is reported as an
        unresolved limitation rather than returned with an empty class."""
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
                repository_root=(
                    repositoryRoot if repositoryRoot is not None else str(config.workspace_root)
                ),
                code_tree_id=codeTreeId,
            )
        )


def _register_knowledge_change(server: FastMCP) -> None:
    """The record operation's mount point: it declares the shape and writes nothing.

    The tool is still mounted because its *name* is part of the published family -- a caller asking
    for it must get a typed refusal rather than "no such tool" -- but the handler now says exactly
    what it does, which is refuse and point at the entry point that can write.
    """

    @server.tool()
    def knowledge_change(
        databasePath: str,
        repositoryId: str,
        recordKind: str,
        request: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Refuse a mount-side change request and name the entry point that can write it. Do not use
        this tool to record: it has no admitted write operation for any kind, so every kind is
        refused with `registration_absent` and nothing is written. The knowledge write plane's
        reachable entry point is the `agents-remember knowledge-ingest` subcommand, which commits a
        whole curator hand-off list through the admitted batch; read the committed result back with
        `knowledge_read`."""
        return knowledge_change_payload(
            ChangeToolRequest(
                database_path=databasePath,
                repository_id=repositoryId,
                record_kind=recordKind,
                body=request,
            )
        )


def _register_knowledge_diff(server: FastMCP) -> None:
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
            )
        )


def _register_knowledge_integrity_check(server: FastMCP) -> None:
    """The report operation, which produces no verdict."""

    @server.tool()
    def knowledge_integrity_check(
        databasePath: str,
        repositoryId: str,
        scopeId: str | None = None,
    ) -> dict[str, Any]:
        """Report declared structural-rule violations, mechanically matched review conditions, their
        registered traversal scope and the observable mapping and scan limitations. It produces no
        compatibility verdict and no causal explanation: `compatible` is absent by design, not
        omitted by accident, and an unresolved assessment stays unresolved."""
        return knowledge_integrity_check_payload(
            databasePath=databasePath,
            repositoryId=repositoryId,
            scopeId=scopeId,
        )


def _register_knowledge_project(server: FastMCP) -> None:
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
            )
        )
