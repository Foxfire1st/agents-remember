"""Provider-backed search tools: GrepAI semantics and the CodeGraphContext graph."""

from typing import Any

from mcp.server.fastmcp import FastMCP

from agents_remember.application.provider_tools import (
    GrepaiRepoScope,
    GrepaiSearchQuery,
    GrepaiTraceQuery,
    ProviderQueryScope,
)

from ..config import McpRuntimeConfig
from ..tools import (
    cgc_callees_payload,
    cgc_callers_payload,
    cgc_complexity_payload,
    cgc_dependencies_payload,
    cgc_symbol_search_payload,
    cgc_visualize_payload,
    grepai_search_payload,
    grepai_trace_payload,
)


def register_code_search_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    """Register both search backends, grouped by backend and by what the reader wants."""
    _register_grepai_tools(server, config)
    _register_cgc_lookup_tools(server, config)
    _register_cgc_analysis_tools(server, config)


def _register_grepai_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    """Semantic search and relationship tracing over the GrepAI memory index."""

    @server.tool()
    def grepai_search(
        query: str,
        repo_ids: list[str] | None = None,
        all_repos: bool = True,
        limit: int = 10,
        output_format: str = "json",
        dry_run: bool = False,
        timeout: int | None = None,
        worktree: str | None = None,
    ) -> dict[str, Any]:
        """Semantic search over memory/onboarding via the grepai provider. Read-only; needs the
        grepai-memory provider enabled, running, and indexed. output_format is 'json' or 'toon'.
        dry_run=true returns the planned provider command without running it. `worktree` targets a
        worktree's isolated stack by name; omit it and a single active worktree for one repo is the
        default, otherwise the workspace stack is used."""
        return grepai_search_payload(
            config,
            GrepaiSearchQuery(query=query, limit=limit, output_format=output_format),
            repos=GrepaiRepoScope(repo_ids=repo_ids, all_repos=all_repos),
            scope=ProviderQueryScope(worktree=worktree, dry_run=dry_run, timeout=timeout),
        )

    @server.tool()
    def grepai_trace(
        trace_action: str,
        symbol: str,
        repo_ids: list[str] | None = None,
        all_repos: bool = True,
        depth: int | None = None,
        output_format: str = "json",
        dry_run: bool = False,
        timeout: int | None = None,
        worktree: str | None = None,
    ) -> dict[str, Any]:
        """Trace relationships in the grepai semantic graph for a symbol. trace_action is
        'callers', 'callees', or 'graph' (depth applies only to 'graph'). output_format is 'json'
        or 'toon'. Read-only; needs grepai-memory enabled and indexed. dry_run=true returns the
        planned command. `worktree` targets a worktree's isolated stack (see grepai_search)."""
        return grepai_trace_payload(
            config,
            GrepaiTraceQuery(
                trace_action=trace_action,
                symbol=symbol,
                depth=depth,
                output_format=output_format,
            ),
            repos=GrepaiRepoScope(repo_ids=repo_ids, all_repos=all_repos),
            scope=ProviderQueryScope(worktree=worktree, dry_run=dry_run, timeout=timeout),
        )


def _register_cgc_lookup_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    """CodeGraphContext lookups that answer "where is it" and "who calls it"."""

    @server.tool()
    def cgc_symbol_search(
        repo_id: str,
        name: str,
        dry_run: bool = False,
        timeout: int | None = None,
        worktree: str | None = None,
    ) -> dict[str, Any]:
        """Find a symbol in the CodeGraphContext code graph. Read-only; needs the
        codegraphcontext-code provider enabled, running, and the repo indexed. dry_run=true returns
        the planned command. `worktree` targets a worktree's isolated graph by name; omit it and a
        single active worktree for the repo is the default, otherwise the workspace graph is used."""
        return cgc_symbol_search_payload(
            config,
            repo_id,
            name,
            scope=ProviderQueryScope(worktree=worktree, dry_run=dry_run, timeout=timeout),
        )

    @server.tool()
    def cgc_callers(
        repo_id: str,
        function: str,
        file: str | None = None,
        dry_run: bool = False,
        timeout: int | None = None,
        worktree: str | None = None,
    ) -> dict[str, Any]:
        """List the callers of a function from the CodeGraphContext graph. Read-only; needs the cgc
        provider indexed. Optional `file` disambiguates same-named functions. `worktree` targets a
        worktree's isolated graph (see cgc_symbol_search)."""
        return cgc_callers_payload(
            config,
            repo_id,
            function,
            file=file,
            scope=ProviderQueryScope(worktree=worktree, dry_run=dry_run, timeout=timeout),
        )

    @server.tool()
    def cgc_callees(
        repo_id: str,
        function: str,
        dry_run: bool = False,
        timeout: int | None = None,
        worktree: str | None = None,
    ) -> dict[str, Any]:
        """List what a function calls (its callees) from the CodeGraphContext graph. Read-only;
        needs the cgc provider indexed. `worktree` targets a worktree's isolated graph (see
        cgc_symbol_search)."""
        return cgc_callees_payload(
            config,
            repo_id,
            function,
            scope=ProviderQueryScope(worktree=worktree, dry_run=dry_run, timeout=timeout),
        )


def _register_cgc_analysis_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    """CodeGraphContext reports over a whole module or repository."""

    @server.tool()
    def cgc_dependencies(
        repo_id: str,
        module: str,
        dry_run: bool = False,
        timeout: int | None = None,
        worktree: str | None = None,
    ) -> dict[str, Any]:
        """Report a module's dependencies from the CodeGraphContext graph. Read-only; needs the cgc
        provider indexed. `worktree` targets a worktree's isolated graph (see cgc_symbol_search)."""
        return cgc_dependencies_payload(
            config,
            repo_id,
            module,
            scope=ProviderQueryScope(worktree=worktree, dry_run=dry_run, timeout=timeout),
        )

    @server.tool()
    def cgc_complexity(
        repo_id: str,
        function: str | None = None,
        dry_run: bool = False,
        timeout: int | None = None,
        worktree: str | None = None,
    ) -> dict[str, Any]:
        """Report complexity metrics from the CodeGraphContext graph (whole repo, or one function
        if given). Read-only; needs the cgc provider indexed. `worktree` targets a worktree's
        isolated graph (see cgc_symbol_search)."""
        return cgc_complexity_payload(
            config,
            repo_id,
            function=function,
            scope=ProviderQueryScope(worktree=worktree, dry_run=dry_run, timeout=timeout),
        )

    @server.tool()
    def cgc_visualize(
        repo_id: str,
        port: int = 8000,
        context: str | None = None,
        dry_run: bool = False,
        timeout: int | None = None,
        worktree: str | None = None,
    ) -> dict[str, Any]:
        """Produce a CodeGraphContext graph visualization (serves a browser view on `port`). Needs
        the cgc provider running and indexed. dry_run=true returns the planned command. `worktree`
        targets a worktree's isolated graph (see cgc_symbol_search)."""
        return cgc_visualize_payload(
            config,
            repo_id,
            port=port,
            context=context,
            scope=ProviderQueryScope(worktree=worktree, dry_run=dry_run, timeout=timeout),
        )
