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

from ..tools.knowledge import (
    DiffToolRequest,
    IntegrityCheckRequest,
    ReadToolRequest,
    WriteToolRequest,
    knowledge_diff_payload,
    knowledge_integrity_check_payload,
    knowledge_read_payload,
    knowledge_write_payload,
)


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
    ) -> dict[str, Any]:
        """
        Name records of one memory-tree view.

        Views: source_context, invariant, family, record, weakness (open by file, kind or sentence),
        dependent (documents and their links). `recordId` takes a text ID (`INV-…`, `WKN-…`, `DPD-…`).
        Rows name ID, kind, state and location, never text. Filters: `sourcePath`, `weaknessKind`,
        `includeClosed`, `staleOnly`, `standsOn`. `repositoryRoot` and `codeTreeId` select the measured
        code tree. Writes: `agents-remember knowledge-ingest` or `agents-remember knowledge-bootstrap`.
        Docs: docs/reference/mcp-tools.md.
        """

        return knowledge_read_payload(
            ReadToolRequest(
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
            ),
            workspace_root=str(config.workspace_root),
            coordination_root=str(config.coordination_root),
            relative_locations=_worktree_relative_locations(config, memoryRoot),
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
        """
        Plan or commit one knowledge write (MIK-R73 rule 1).

        `contractPath` names a leaf, or with `crossing` a master's open crossing sync; the
        contract guards the write and names the only memory worktree it may write. `repoId`
        with `wave` writes a taskless wave. Give the hand-off as `handoff` with
        `handoffName`, or as `handoffPath`. Without `commit` (the default) it plans,
        validates and writes nothing; with it, it writes files and commits nothing to Git. A
        refusal lists every problem, and the whole report is a file the answer names. Longer
        explanation: docs/reference/mcp-tools.md.
        """
        return knowledge_write_payload(
            WriteToolRequest(
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
        """Name the changed records of two memory trees beside a report (MIK-R109).

        The whole comparison is a caller-readable report: every changed file
        with its patch, grouped by record and source path. The answer holds
        the two trees, the counts by kind, the changed records, and the
        report's location. With no `afterRevision`, HEAD meets the working
        tree, which writes loose, unreferenced objects. Narrow with
        `recordId`/`path`; absent selectors refused, unconverted sides as
        `legacy-format`. Longer explanation: docs/reference/mcp-tools.md."""
        return knowledge_diff_payload(
            DiffToolRequest(
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
