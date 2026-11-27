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

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig

from .lazy import tools


def _worktree_relative_locations(config: McpRuntimeConfig, memory_root: str) -> bool:
    """Whether one read names worktree-relative locations (MIK-R109 rule 2).

    A caller reading its own memory worktree joins the answered absolute
    ``memoryRoot`` with relative row files, so large answers stay within
    their bound on any root length. A caller with no memory worktree names
    the server's configured memory checkout itself and keeps absolute
    files with the commit beside the answer.
    """

    try:
        root = Path(memory_root).resolve()
    except OSError:
        return False
    repositories = getattr(config, "repositories", None)
    if not repositories:
        # No configured checkout to compare against: keep the absolute
        # no-worktree form rather than guessing a worktree.
        return False
    try:
        configured = set()
        for scope in repositories.values():
            configured_root = scope.memory_root
            if configured_root is None:
                continue
            configured.add(Path(configured_root).resolve())
    except OSError:
        return True
    return root not in configured


def register_knowledge_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    """Register the knowledge read, diff and integrity operations.

    The runtime configuration supplies two things to this family. The workspace root is the
    *default repository* a read falls back to when the caller names no repository of its own; the
    read builder completes the source-resolution pair or names neither half. The coordination root
    is where a memory tree's derived index is cached (MIK-R23). The memory tree itself is
    caller-supplied, because the substrate decides nothing about which tree is meant.
    """

    _register_knowledge_read(server, config)
    _register_knowledge_diff(server, config)
    _register_knowledge_integrity_check(server)
    _register_knowledge_write(server, config)


def _register_knowledge_read(server: FastMCP, config: McpRuntimeConfig) -> None:
    """The one read operation, over the three named views."""

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
        recordId: str | None = None,
        sourcePath: str | None = None,
        repositoryRoot: str | None = None,
        codeTreeId: str | None = None,
        weaknessKind: str | None = None,
        includeClosed: bool = False,
        staleOnly: bool = False,
        standsOn: str | None = None,
        repo_id: str | None = None,
        contractPath: str | None = None,
        files: list[str] | None = None,
        folder: str | None = None,
        query: str | None = None,
        kinds: list[str] | None = None,
        recordIds: list[str] | None = None,
        compact: bool = False,
        masterId: str | None = None,
    ) -> dict[str, Any]:
        """Read memory. Views: source_context, invariant, family, record, records (batch text), impact (files/folder), search (FTS5), weakness, dependent (compact), writers (counts+report), consolidation_proposal (masterId). Auto-pairs code tree (repo_id/contractPath/memoryRoot; repositoryRoot/codeTreeId override). recordId/recordIds take text IDs (INV-/WKN-/DPD-). Writes: `agents-remember knowledge-ingest` or `agents-remember knowledge-bootstrap`. Docs: docs/reference/mcp-tools.md."""

        return tools.knowledge_read_payload(
            tools.ReadToolRequest(
                memory_root=memoryRoot,
                view=view,
                ordering_input=orderingInput,
                limit=limit,
                continuation=continuation,
                invariant_revision_id=invariantRevisionId,
                family_revision_id=familyRevisionId,
                record_id=recordId,
                source_path=sourcePath,
                repository_root=repositoryRoot,
                code_tree_id=codeTreeId,
                weakness_kind=weaknessKind,
                include_closed=includeClosed,
                stale_only=staleOnly,
                stands_on=standsOn,
                repo_id=repo_id,
                contract_path=contractPath,
                files=None if files is None else tuple(files),
                folder=folder,
                query=query,
                kinds=None if kinds is None else tuple(kinds),
                record_ids=None if recordIds is None else tuple(recordIds),
                compact=compact,
                master_id=masterId,
            ),
            workspace_root=str(config.workspace_root),
            coordination_root=str(config.coordination_root),
            relative_locations=_worktree_relative_locations(config, memoryRoot),
            repositories=getattr(config, "repositories", None),
        )


def _register_knowledge_write(server: FastMCP, config: McpRuntimeConfig) -> None:
    """The writer's mounted route: the same function the write commands run."""

    @server.tool()
    def knowledge_write(
        contractPath: str | None = None,
        *,
        handoff: dict[str, Any] | None = None,
        handoffName: str | None = None,
        handoffPath: str | None = None,
        authorizationRef: str = "",
        commit: bool = False,
        crossing: str | None = None,
        repoId: str | None = None,
        wave: str | None = None,
    ) -> dict[str, Any]:
        """Plan or write one knowledge handoff. Target a leaf via contractPath (with crossing for a master's open crossing sync) or a taskless wave via repoId+wave; pass handoff+handoffName or handoffPath. Default commit=false plans and validates only; commit=true writes files without a Git commit. Refusals list all problems and return the report path. Docs: docs/reference/mcp-tools.md."""
        return tools.knowledge_write_payload(
            tools.WriteToolRequest(
                contract_path=contractPath,
                handoff=handoff,
                handoff_name=handoffName,
                handoff_path=handoffPath,
                authorization_ref=authorizationRef,
                commit=commit,
                crossing=crossing,
                repo_id=repoId,
                wave=wave,
            ),
            config,
        )


def _register_knowledge_diff(server: FastMCP, config: McpRuntimeConfig) -> None:
    """The comparison operation, which infers no semantic label."""

    @server.tool()
    def knowledge_diff(
        memoryRoot: str,
        beforeRevision: str = "HEAD",
        afterRevision: str | None = None,
        recordId: str | None = None,
        path: str | None = None,
    ) -> dict[str, Any]:
        """Compare two memory trees and write a caller-readable patch report; returns both trees, counts by kind, changed records, and report path. With no `afterRevision`, compares against the working tree, which writes loose, unreferenced objects. Filter with recordId/path; refuses absent selectors or legacy-format sides. Docs: docs/reference/mcp-tools.md."""
        return tools.knowledge_diff_payload(
            tools.DiffToolRequest(
                memory_root=memoryRoot,
                before=beforeRevision,
                after=afterRevision,
                record_id=recordId,
                path=path,
            ),
            coordination_root=str(config.coordination_root),
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
        """Validate a converted memory tree (read-only): validation.ok, rule counts, bounded violations. Pass contractPath for a leaf (adds its latest worklist) or memoryRoot+codeRoot (optional baseCommits; omitted checks anchor paths). Refuses unconverted trees as legacy-format. Docs: docs/reference/mcp-tools.md."""
        return tools.knowledge_integrity_check_payload(
            tools.IntegrityCheckRequest(
                memoryRoot=memoryRoot,
                codeRoot=codeRoot,
                baseCommits=None if baseCommits is None else tuple(baseCommits),
                contractPath=contractPath,
            )
        )
