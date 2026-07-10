"""Stdio MCP server wiring for Agents Remember."""

import argparse
from typing import Any

from mcp.server.fastmcp import FastMCP

from agents_remember.benchmarks.runner import CODEX_BENCHMARK_SANDBOX
from agents_remember.controlplane.operator_inbox_records import AgentRole, InboxMessageKind
from agents_remember.controlplane.orchestration_nudges import NudgeReason
from agents_remember.observer import AmbientLifecycle, EventStore, install_ambient, observer_root
from agents_remember.serving.daemon import maybe_autostart_dashboard

from .compact_content import install_compact_content
from .config import ConfigError, McpRuntimeConfig, load_config
from .tools import (
    attach_terminal_session_to_leaf_payload,
    cgc_callees_payload,
    cgc_callers_payload,
    cgc_complexity_payload,
    cgc_dependencies_payload,
    cgc_symbol_search_payload,
    cgc_visualize_payload,
    codex_benchmark_prepare_payload,
    codex_benchmark_run_payload,
    context_packet_payload,
    drift_check_payload,
    gate_decide_payload,
    gate_list_payload,
    grepai_search_payload,
    grepai_trace_payload,
    lifecycle_end_payload,
    lifecycle_finalize_task_payload,
    lifecycle_gate_payload,
    lifecycle_phase_payload,
    lifecycle_resume_payload,
    lifecycle_start_payload,
    lifecycle_turn_end_notification_payload,
    memory_baseline_adopt_payload,
    memory_baseline_status_payload,
    memory_carryover_apply_payload,
    memory_carryover_plan_payload,
    memory_init_payload,
    memory_quality_check_payload,
    operator_inbox_consume_payload,
    operator_inbox_poll_payload,
    operator_inbox_post_payload,
    orchestration_nudge_manager_payload,
    ping_payload,
    provider_diagnostics_payload,
    provider_status_payload,
    provider_watchers_payload,
    read_ar_files_payload,
    resolve_context_payload,
    route_index_refresh_payload,
    runtime_install_payload,
    server_info_payload,
    session_rename_payload,
    session_retire_payload,
    skills_install_payload,
    spawn_agent_session_payload,
    switch_lifecycle_payload,
    task_doc_payload,
    task_reopen_payload,
    worktree_abandon_payload,
    worktree_attach_payload,
    worktree_cleanup_payload,
    worktree_closeout_apply_payload,
    worktree_closeout_preview_payload,
    worktree_integrate_payload,
    worktree_start_payload,
    worktree_status_payload,
    worktree_sync_payload,
)


def create_server(config: McpRuntimeConfig) -> Any:
    install_compact_content()
    # One ambient lifecycle per server process; the _tool_payload choke point
    # tags tool calls onto it once a lifecycle is started.
    install_ambient(AmbientLifecycle(EventStore(observer_root(config))))
    server = FastMCP("Agents Remember")

    @server.tool()
    def ping() -> dict[str, Any]:
        """Liveness check. Returns server name, version, and transport. Read-only; no side effects."""
        return ping_payload()

    @server.tool()
    def server_info() -> dict[str, Any]:
        """Report the resolved configuration: coordination/workspace/transcript roots, allowed
        repo ids, allowed provider ids, and the full tool list. Read-only; reflects the settings
        loaded at startup (settings-file edits need a harness restart to take effect)."""
        return server_info_payload(config)

    @server.tool()
    def context_packet(
        repo_id: str,
        include_providers: bool = True,
        include_drift: bool = False,
        include_freshness: bool = False,
    ) -> dict[str, Any]:
        """Bundle a repository's current state into one packet: repo/git state, resolved paths,
        memory mode/storage, worktree state, and (optionally) provider status, drift, and branch
        freshness (include_freshness fetches remote-tracking refs and reports ahead/behind for the
        code and memory checkouts plus whether the ledger maps code HEAD). Read-only apart from
        that optional fetch. Preferred single call to orient at task start."""
        return context_packet_payload(
            config,
            repo_id,
            include_providers=include_providers,
            include_drift=include_drift,
            include_freshness=include_freshness,
        )

    @server.tool()
    def read_ar_files(
        repo_id: str,
        files: list[dict[str, Any]],
        refresh: bool = False,
    ) -> dict[str, Any]:
        """Read-only batch read of up to 5 repo-relative paths inside an AR-managed repo,
        each paired with its file-level onboarding. Per file pass {"path": "...", "source":
        "full" | {"startLine": N, "endLine": M}, "onboarding": false?}; the response returns
        {path, status, source?, onboarding?} where status is the onboarding-lookup outcome
        (found | missing | disabled | unsupported | not_requested) and source is the full file
        or the exact requested range (omitted for absent or non-UTF-8 files). It also
        auto-attaches the repo overview and the governing route-overview chain, deduplicated
        per session (served once, re-served only when changed; pass refresh=true to force
        re-serve, e.g. after a compaction). Route-index rule: a file inside sourceScope but
        absent from coveredFiles reports missing without probing. In a managed repo this is
        the read for the research phase (the lifecycle up to the build decision): use it
        instead of a native read to get each file paired with its onboarding plus the
        repository and governing route overviews. Native read is the edit precondition once
        building begins."""
        return read_ar_files_payload(config, repo_id, files, refresh=refresh)

    @server.tool()
    def attach_terminal_session_to_leaf(
        session_id: str,
        leaf_key: str,
        role: str | None = None,
    ) -> dict[str, Any]:
        """Move an existing hosted terminal/chat session to a durable task leaf.

        Reuses the dashboard terminal catalog's server-authoritative `(leaf, role)` uniqueness
        rules. Returns status 'attached', 'leaf-taken', or 'unknown-session'; it does not spawn a
        new session or require a worktree enclosure.
        """
        return attach_terminal_session_to_leaf_payload(
            config,
            session_id=session_id,
            leaf_key=leaf_key,
            role=role,
        )

    @server.tool()
    def spawn_agent_session(
        harness: str | None = None,
        leaf_key: str | None = None,
        replacement_for_leaf: str | None = None,
        context: str | None = None,
        submit: bool = False,
        label: str | None = None,
        model: str | None = None,
        effort: str | None = None,
        env: dict[str, str] | None = None,
        launch_args: list[str] | None = None,
        prompt_keywords: list[str] | None = None,
        session_commands: list[str] | None = None,
        level: str | None = None,
        spawned_by_session: str | None = None,
        spawned_by_lifecycle: str | None = None,
        kind: str = "harness",
    ) -> dict[str, Any]:
        """Spawn a role-configured, leaf-attached, context-primed hosted agent session.

        Composes the EXISTING session primitives so an orchestrator can spawn a manager and a manager
        a worker without dashboard clicks: create a hosted session via the serving opener, attach it
        to `leaf_key` (server-arbitrated uniqueness — a taken leaf returns status 'leaf-taken', never
        overridden), resolve the role knobs from developer-owned agentic settings, seed resolved
        `model`/`effort` into spawn env, map them onto the harness argv per-harness via the registry,
        and deliver `context` as an id-bearing, harness-log-confirmed paste. Ordinary callers declare
        the seat (`env.AR_SPAWN_ROLE`) and dispatch `level`; they do not choose harness/model/effort or direct
        launch/session spend controls. Legacy non-null `harness`, `model`, `effort`, `launch_args`,
        `prompt_keywords`, `session_commands`, `env.AR_SPAWN_MODEL`, `env.AR_SPAWN_EFFORT`, or
        harness-native spend/endpoint env keys such as `ANTHROPIC_MODEL` and `OPENAI_BASE_URL`
        return status 'spend-override-unsupported' before spawning, with guidance to configure
        `orchestration.roles`, `orchestration.rolesPerLevel`, `orchestration.spawn`, or
        `orchestration.harnesses` instead.
        An unbound replacement declares its actual work with `replacement_for_leaf`; that provenance
        is the leaf-chain discriminator while `leaf_key` remains free for the occupied role seat.
        `level` declares the dispatch level (leaf|master|portfolio, default leaf — a manager
        dispatching leaf seats passes leaf, the seam reviewer master, portfolio seats portfolio):
        knobs resolve from the agentic settings as `orchestration.rolesPerLevel[level]` deep-merged
        over the flat `orchestration.roles` default, keyed by the AR_SPAWN_ROLE riding `env`; the
        resolved level + source land in spawn provenance. `submit=true` presses Enter so a worker
        auto-starts; leave it false for a draft. If role settings do not choose a harness, the
        dispatch falls through to repo-local/global `orchestration.spawn.harness`, then the first
        detected registry harness. Each spawned session is its own harness process (the
        ambient-lifecycle singleton is untouched). Spawned-by provenance (`spawned_by_session` + the
        active/`spawned_by_lifecycle` lifecycle) is recorded on the catalog row so the dashboard can
        render the orchestration tree. Status 'spawned' on success; 'spend-override-unsupported',
        'harness-unknown'/'harness-not-detected'/'effort-invalid'/'model-invalid'/'level-invalid'/
        'bad-kind' are pre-spawn refusals."""
        return spawn_agent_session_payload(
            config,
            harness=harness,
            leaf_key=leaf_key,
            replacement_for_leaf=replacement_for_leaf,
            context=context,
            submit=submit,
            label=label,
            model=model,
            effort=effort,
            env=env,
            launch_args=launch_args,
            prompt_keywords=prompt_keywords,
            session_commands=session_commands,
            level=level,
            spawned_by_session=spawned_by_session,
            spawned_by_lifecycle=spawned_by_lifecycle,
            kind=kind,
        )

    @server.tool()
    def session_retire(
        actor_session_id: str,
        session_id: str,
        reason: str = "manual retire",
    ) -> dict[str, Any]:
        """Retire a tracked chat/terminal session (260707-HFX-L8, issue #12): kill its tmux session,
        mark the catalog row terminated with retirement provenance (who/why/when/edge), and remove
        it from the active rail. Transcripts are never deleted.

        `actor_session_id` is the RETIRING seat's own catalog session id (self-declared -- there is
        no ambient "who am I" resolution, mirroring `spawn_agent_session`'s `spawned_by_session`).
        Authority is enforced server-side and refusals are loud and policy-naming: a seat never
        retires itself (`retire-refused`); a manager may retire only worker/reviewer seats of its
        OWN master; the orchestrator may retire any seat, including a completed manager. Status
        'retired' on success, 'already-retired' when the target was already terminated (idempotent),
        'unknown-session'/'unknown-actor' when a session id has no catalog row, 'retire-refused' for
        every authority-policy refusal."""
        return session_retire_payload(
            config,
            actor_session_id=actor_session_id,
            session_id=session_id,
            reason=reason,
        )

    @server.tool()
    def session_rename(session_id: str, label: str) -> dict[str, Any]:
        """Update a chat/terminal session's display label post-spawn (260707-HFX-L8, issue #4).

        Identity text only: the seat's spawned role never changes (L6 role-seat immutability). The
        FIRST rename freezes the original spawn-time label into spawn provenance for audit (later
        renames leave it alone). Status 'renamed' on success, 'unknown-session' when the session has
        no catalog row or is already retired."""
        return session_rename_payload(config, session_id=session_id, label=label)

    @server.tool()
    def runtime_install(
        dry_run: bool = False,
        include_benchmarks: bool = False,
        install_provider_deps: bool = True,
        no_cache: bool = False,
    ) -> dict[str, Any]:
        """Install/refresh the packaged coordinator runtime into the coordination root. Safe to
        re-run over an existing install.

        PRESERVES user data: memory-repos/ (onboarding) and providers/data/ (DBs, Ollama model,
        FalkorDB graph). REPLACES managed scaffold: skills/, AGENTS.md templates, provider
        compose/docker/requirements ("shape"), and with install_provider_deps=true may refresh
        providers/runners/ after stopping watchers so containers rebind cleanly. Removes the
        legacy scripts/ dir.

        With install_provider_deps=true (default) it also builds provider images, but SKIPS any
        image whose tag already exists, then starts/rechecks watchers without rebuilding indexes.
        Pass no_cache=true to force a true from-scratch rebuild (bypasses that skip AND adds
        --no-cache to docker build). include_benchmarks=true also installs benchmark fixtures.
        ALWAYS preview with dry_run=true first."""
        return runtime_install_payload(
            config,
            dry_run=dry_run,
            include_benchmarks=include_benchmarks,
            install_provider_deps=install_provider_deps,
            no_cache=no_cache,
        )

    @server.tool()
    def resolve_context(
        repo_id: str,
        task_name: str | None = None,
        parent_task: str | None = None,
        leaf_id: str | None = None,
        contract_path: str | None = None,
        worktree_name: str | None = None,
        topology: str | None = None,
    ) -> dict[str, Any]:
        """Resolve a repository's coordination/memory context: topology (internal/external), code
        and memory roots, settings paths, storage mode, and path rules. Read-only. Use this (or
        context_packet) before relying on onboarding, task files, or provider tools."""
        return resolve_context_payload(
            config,
            repo_id,
            task_name=task_name,
            parent_task=parent_task,
            leaf_id=leaf_id,
            contract_path=contract_path,
            worktree_name=worktree_name,
            topology=topology,
        )

    @server.tool()
    def drift_check(repo_id: str, detail_limit: int = 50) -> dict[str, Any]:
        """Task-start gate: classify how far onboarding has drifted from the code since it was last
        verified. Read-only (writes only a temp drift report). A nonzero actionable count is
        expected after code changes, not a failure."""
        return drift_check_payload(config, repo_id, detail_limit=detail_limit)

    @server.tool()
    def memory_quality_check(
        repo_id: str,
        checks: list[str] | None = None,
        detail_limit: int = 50,
    ) -> dict[str, Any]:
        """Closeout memory-quality gate: runs drift-integrity and style checks over onboarding.
        Read-only. ok=false means findings exist (e.g. drifted onboarding), not that the tool
        failed. Pass `checks` to run a subset; default runs all."""
        return memory_quality_check_payload(
            config,
            repo_id,
            checks=checks,
            detail_limit=detail_limit,
        )

    @server.tool()
    def route_index_refresh(repo_id: str, dry_run: bool = False) -> dict[str, Any]:
        """Regenerate the overview.index.json route indexes so they match the current onboarding
        tree. Writes index files under the memory root; does not touch source or onboarding
        content. Preview with dry_run=true."""
        return route_index_refresh_payload(config, repo_id, dry_run=dry_run)

    @server.tool()
    def memory_init(
        repo_id: str,
        dry_run: bool = False,
        initialize_git: bool = True,
    ) -> dict[str, Any]:
        """Initialize or repair a repository's memory root (scaffold system/ files, onboarding
        layout, optionally `git init`). Does not overwrite existing onboarding content. Preview
        with dry_run=true. Usually driven by the c-00-initialize-memory-repo skill."""
        return memory_init_payload(
            config,
            repo_id,
            dry_run=dry_run,
            initialize_git=initialize_git,
        )

    @server.tool()
    def skills_install(
        dry_run: bool = False,
        overwrite: bool = False,
        archive_existing: bool = False,
    ) -> dict[str, Any]:
        """Copy the packaged skills into the harness skill root (e.g. .claude/skills) so the harness
        can discover them. The packaged skills are flat (one folder per skill), so each is copied by
        its frontmatter name. overwrite replaces existing skill files; archive_existing backs them up
        first. Most harnesses only discover newly-installed skills after a restart. Preview with
        dry_run=true."""
        return skills_install_payload(
            config,
            dry_run=dry_run,
            overwrite=overwrite,
            archive_existing=archive_existing,
        )

    @server.tool()
    def provider_status(detail_limit: int = 20) -> dict[str, Any]:
        """Compact provider readiness summary (per-provider state ready/degraded/stopped, watcher
        up, indexing state). Read-only. Returns noProviders when none are enabled in settings."""
        return provider_status_payload(config, detail_limit=detail_limit)

    @server.tool()
    def provider_diagnostics(detail_limit: int = 20) -> dict[str, Any]:
        """Raw provider-native diagnostic detail (container states, ports, backend/embedder health,
        ping output). Read-only. Use when provider_status reports degraded and you need the cause."""
        return provider_diagnostics_payload(config, detail_limit=detail_limit)

    @server.tool()
    def provider_watchers(action: str, dry_run: bool = False) -> dict[str, Any]:
        """Control provider watchers. action: 'start' (recreate containers from current compose and
        begin indexing), 'restart' (stop then start; watchers come back up and pick up changes via
        their incremental scan WITHOUT rebuilding indexes -- use this to wake a stale watcher),
        'stop'/'shutdown-all', 'invalidate-indexes' (DELETE and rebuild every index from scratch:
        full re-embed + full graph re-index, slow and CPU-heavy), or 'status'. Mutating except
        status. Indexing runs in the watcher and is never time-capped. Preview with dry_run=true."""
        return provider_watchers_payload(config, action=action, dry_run=dry_run)

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
            query,
            repo_ids=repo_ids,
            all_repos=all_repos,
            limit=limit,
            output_format=output_format,
            dry_run=dry_run,
            timeout=timeout,
            worktree=worktree,
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
            trace_action,
            symbol,
            repo_ids=repo_ids,
            all_repos=all_repos,
            depth=depth,
            output_format=output_format,
            dry_run=dry_run,
            timeout=timeout,
            worktree=worktree,
        )

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
            dry_run=dry_run,
            timeout=timeout,
            worktree=worktree,
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
            dry_run=dry_run,
            timeout=timeout,
            worktree=worktree,
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
            dry_run=dry_run,
            timeout=timeout,
            worktree=worktree,
        )

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
            dry_run=dry_run,
            timeout=timeout,
            worktree=worktree,
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
            dry_run=dry_run,
            timeout=timeout,
            worktree=worktree,
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
            dry_run=dry_run,
            timeout=timeout,
            worktree=worktree,
        )

    @server.tool()
    def worktree_start(
        repo_id: str,
        task_name: str,
        worktree_name: str,
        leaf_id: str | None = None,
        parent_task: str | None = None,
        workflow_kind: str = "light-task",
        source_branch: str | None = None,
        work_branch: str | None = None,
        memory_mode: str | None = None,
        memory_choice: str | None = None,
        stale_base_choice: str | None = None,
        skip_provider_setup: bool = False,
        retry_provider_setup: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Create or load a task contract plus code (and external-memory) git worktrees. Mutating:
        creates branches/worktrees on disk. Preview with dry_run=true. Driven by the
        c-09-git-worktree-manager skill workflow; workflow_kind is the task format ('light-task' or 'chat-task').
        memory_mode is 'internal', 'external', or 'disabled'.

        Stale-base preflight (issue #54): start refuses when a source branch is behind or
        diverged from its remote tracking branch (a stale base produces wrong code and
        defeats the provider seed fast-path). On a blocked 'choose_stale_base_recovery'
        result, re-run with stale_base_choice='fast-forward' (ff the local branches, then
        start) or 'proceed-stale' (explicit override). A missing external-memory source
        branch is auto-created off the official memory tip using the code source branch
        name as template.

        Returns within seconds: provider setup runs in the background and the response's
        providers block reports state 'starting' with a progressFile. Poll worktree_status
        until providers reaches a terminal state (seed copy = seconds; a refused seed falls
        back to a full reindex = minutes, flagged as seedFallback). On a failed or stale
        setup, re-run with retry_provider_setup=true to relaunch it for the existing
        contract."""
        return worktree_start_payload(
            config,
            repo_id,
            task_name,
            worktree_name,
            leaf_id=leaf_id,
            parent_task=parent_task,
            workflow_kind=workflow_kind,
            source_branch=source_branch,
            work_branch=work_branch,
            memory_mode=memory_mode,
            memory_choice=memory_choice,
            stale_base_choice=stale_base_choice,
            skip_provider_setup=skip_provider_setup,
            retry_provider_setup=retry_provider_setup,
            dry_run=dry_run,
        )

    @server.tool()
    def worktree_attach(
        repo_id: str,
        task_name: str | None = None,
        contract_path: str | None = None,
        leaf_id: str | None = None,
        parent_task: str | None = None,
        on_unsaved: str | None = None,
    ) -> dict[str, Any]:
        """Re-attach to an existing task contract without mutating git, resuming its lifecycle.
        Read-only; resume a task by task_name or contract_path. If an unsaved fleeting lifecycle is
        active, on_unsaved='save' (promote) or 'discard' (abandon) resolves the save gate."""
        return worktree_attach_payload(
            config,
            repo_id,
            task_name=task_name,
            contract_path=contract_path,
            leaf_id=leaf_id,
            parent_task=parent_task,
            on_unsaved=on_unsaved,
        )

    @server.tool()
    def worktree_status(
        repo_id: str,
        task_name: str | None = None,
        contract_path: str | None = None,
        leaf_id: str | None = None,
        parent_task: str | None = None,
    ) -> dict[str, Any]:
        """Report a task's worktree lifecycle phase, dirty flags, and next-step hints. Read-only.
        While background provider setup runs, the providers block carries the live phase,
        heartbeat age, and seedFallback; terminal states are ok / ready-with-failed-phases /
        failed (stale = setup thread died; retry via worktree_start retry_provider_setup)."""
        return worktree_status_payload(
            config,
            repo_id,
            task_name=task_name,
            contract_path=contract_path,
            leaf_id=leaf_id,
            parent_task=parent_task,
        )

    @server.tool()
    def worktree_sync(
        contract_path: str,
        memory_sync_choice: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Pull the moved official line into a live worktree (issue #54). Mutating: fetches
        upstreams, merges the source branch into the code work branch (ff when unchanged),
        fast-forwards the memory work branch under parked memory, and advances the contract's
        recorded base pair atomically (the new code tip must be ledger-mapped at the official
        memory tip — a mid-cycle official line blocks with guidance to run carryover first).
        Preview with dry_run=true. worktree_status's freshness block recommends this tool when
        the recorded bases fall behind the local source branch tips. If the memory work branch
        has local commits and official memory moved, the result blocks with
        memory_sync_choice='merge-memory' (ledger conflicts abort cleanly) or 'skip-memory'
        (defer to end-of-task carryover). Sync early — before memories are written — for the
        friction-free fast-forward path."""
        return worktree_sync_payload(
            config,
            contract_path,
            memory_sync_choice=memory_sync_choice,
            dry_run=dry_run,
        )

    @server.tool()
    def worktree_closeout_preview(
        contract_path: str,
        code_commit_message: str,
        memory_commit_message: str = "",
        ledger_commit_message: str = "",
    ) -> dict[str, Any]:
        """Non-mutating preview of a worktree-backed closeout (what code/memory/ledger commits would
        be made). Nothing is committed. Pair with worktree_closeout_apply."""
        return worktree_closeout_preview_payload(
            config,
            contract_path,
            code_commit_message,
            memory_commit_message=memory_commit_message,
            ledger_commit_message=ledger_commit_message,
        )

    @server.tool()
    def worktree_closeout_apply(
        contract_path: str,
        intent_note: str,
        code_commit_message: str,
        memory_commit_message: str = "",
        ledger_commit_message: str = "",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Apply a worktree closeout: commits code, then memory, then ledger. MUTATING and
        commit-gated — only after explicit developer commit approval; preview first
        (worktree_closeout_preview or dry_run=true). Requires intent_note."""
        return worktree_closeout_apply_payload(
            config,
            contract_path,
            intent_note,
            code_commit_message,
            memory_commit_message=memory_commit_message,
            ledger_commit_message=ledger_commit_message,
            dry_run=dry_run,
        )

    @server.tool()
    def worktree_integrate(
        contract_path: str,
        strategy: str = "ff-only",
        ledger_commit_message: str = "",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Land a closed task branch back onto its source branch (strategy 'ff-only' or 'replay').
        MUTATING: moves branch refs. Do not move protected branches without explicit approval.
        Preview with dry_run=true."""
        return worktree_integrate_payload(
            config,
            contract_path,
            strategy=strategy,
            ledger_commit_message=ledger_commit_message,
            dry_run=dry_run,
        )

    @server.tool()
    def worktree_cleanup(
        contract_path: str, dry_run: bool = False, teardown_providers: bool = True
    ) -> dict[str, Any]:
        """Remove a task's worktrees and merged task branches after integration. MUTATING and
        destructive (deletes worktrees/branches) — run only after worktree_integrate. Preview with
        dry_run=true. teardown_providers=true (default) also reclaims the worktree's isolated
        provider stack (containers, networks, provider-runtime tree)."""
        return worktree_cleanup_payload(
            config, contract_path, dry_run=dry_run, teardown_providers=teardown_providers
        )

    @server.tool()
    def worktree_abandon(
        contract_path: str, dry_run: bool = False, force: bool = False
    ) -> dict[str, Any]:
        """Discard a worktree-backed task WITHOUT integrating it: reclaim its isolated provider
        stack (containers, networks, provider-runtime tree), remove the code and memory worktrees,
        delete the task branches, and remove the worktree group dir. MUTATING and destructive.
        Unlike worktree_cleanup it needs no completed integration. Without force it refuses dirty
        worktrees and unmerged branches (reporting the commits); force=true discards them
        (git worktree remove --force, git branch -D). Preview with dry_run=true."""
        return worktree_abandon_payload(config, contract_path, dry_run=dry_run, force=force)

    @server.tool()
    def task_reopen(contract_path: str, dry_run: bool = False) -> dict[str, Any]:
        """Reopen a COMPLETED leaf under its exact same leaf id (no -rN suffix). A state
        reset, not a worktree creator: the enclosure contract's review/closeout/integration
        state returns to virgin (cleanup=reopened, stale lifecycle binding cleared) and the
        leaf's task document goes back to planning (lifecycleId cleared, master sub-task
        index entry flipped, audit decision appended). MUTATING (contract + task docs; no
        git effects). Refuses masters, in-flight leaves, and leaves whose worktrees still
        exist. Afterwards: edit the doc's steps via task_doc, then run a NORMAL
        worktree_start with the same leaf id — it recreates worktrees/branches off the
        current source tips, promotes/mints a fresh lifecycle, and restamps the doc, so
        doc/chat/dashboard bindings hold by construction. Preview with dry_run=true."""
        return task_reopen_payload(config, contract_path, dry_run=dry_run)

    @server.tool()
    def lifecycle_finalize_task(
        contract_path: str,
        task_doc_path: str | None = None,
        master_doc_path: str | None = None,
        subtask_number: str = "",
        dry_run: bool = False,
        teardown_providers: bool = True,
    ) -> dict[str, Any]:
        """Finalize one parent-child task lifecycle edge. The task's landed commit must be
        reachable from the contract's local target/source branch; PR-gated flows must complete the
        PR merge and pull first, making the proof structurally identical to a non-PR edge. After
        landed-state and memory carryover checks, this runs or verifies cleanup and reconciles the
        supplied JSON-primary task documents. No squash-merge equivalence is attempted. Preview with
        dry_run=true."""
        return lifecycle_finalize_task_payload(
            config,
            contract_path,
            task_doc_path=task_doc_path,
            master_doc_path=master_doc_path,
            subtask_number=subtask_number,
            dry_run=dry_run,
            teardown_providers=teardown_providers,
        )

    @server.tool()
    def memory_baseline_status(repo_id: str) -> dict[str, Any]:
        """Report drift and ledger state to decide whether an external-memory baseline can be
        adopted. Read-only."""
        return memory_baseline_status_payload(config, repo_id)

    @server.tool()
    def memory_baseline_adopt(
        repo_id: str,
        accept_drift: bool = False,
        source_branch: str | None = None,
        work_branch: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Create the first ledgered memory baseline for an external memory repo. Mutating: writes
        the ledger and commits memory. Gated on clean drift unless accept_drift=true. Preview with
        dry_run=true. Usually driven by the c-10-adopt-memory-baseline skill."""
        return memory_baseline_adopt_payload(
            config,
            repo_id,
            accept_drift=accept_drift,
            source_branch=source_branch,
            work_branch=work_branch,
            dry_run=dry_run,
        )

    @server.tool()
    def memory_carryover_plan(
        repo_id: str,
        source_memory: str,
        official_code_ref: str,
        source_code_ref: str,
        old_base: str,
        replace_existing: bool = False,
    ) -> dict[str, Any]:
        """Plan (non-mutating) carrying richer onboarding from a source/feature branch into official
        memory once code has landed. Returns a plan to review; apply with memory_carryover_apply."""
        return memory_carryover_plan_payload(
            config,
            repo_id,
            source_memory,
            official_code_ref,
            source_code_ref,
            old_base,
            replace_existing=replace_existing,
        )

    @server.tool()
    def memory_carryover_apply(
        repo_id: str,
        source_memory: str,
        official_code_ref: str,
        source_code_ref: str,
        old_base: str,
        intent_note: str,
        replace_existing: bool = False,
        include_review_required: list[str] | None = None,
        memory_commit_message: str = "Carry over landed branch memory",
        ledger_commit_message: str = "Record branch memory carryover",
    ) -> dict[str, Any]:
        """Apply an approved carryover plan: writes onboarding and commits memory + ledger. Mutating
        and approval-gated — run memory_carryover_plan first and only apply after the code has landed
        officially. Requires intent_note."""
        return memory_carryover_apply_payload(
            config,
            repo_id,
            source_memory,
            official_code_ref,
            source_code_ref,
            old_base,
            intent_note,
            replace_existing=replace_existing,
            include_review_required=include_review_required,
            memory_commit_message=memory_commit_message,
            ledger_commit_message=ledger_commit_message,
        )

    @server.tool()
    def codex_benchmark_prepare(
        target: str = "all",
        case_id: str | None = None,
        benchmarks_root: str | None = None,
        dry_run: bool = True,
        force_clone: bool = False,
        skill_exposure_mode: str = "copy",
        provider_timeout: int = 1800,
    ) -> dict[str, Any]:
        """Prepare resettable benchmark case workspaces (clones repos, materializes coordination).
        Defaults to dry_run=true because a real prepare clones repos and writes workspaces. Set
        dry_run=false to actually prepare."""
        return codex_benchmark_prepare_payload(
            config,
            target=target,
            case_id=case_id,
            benchmarks_root=benchmarks_root,
            dry_run=dry_run,
            force_clone=force_clone,
            skill_exposure_mode=skill_exposure_mode,
            provider_timeout=provider_timeout,
        )

    @server.tool()
    def codex_benchmark_run(
        target: str = "all",
        case_id: str | None = None,
        benchmarks_root: str | None = None,
        prompt: str | None = None,
        variant: str | None = None,
        repetitions: int | None = None,
        jobs: int | None = None,
        dry_run: bool = True,
        skip_prepare: bool = False,
        force_clone: bool = False,
        skill_exposure_mode: str = "copy",
        provider_timeout: int = 1800,
        codex_sandbox: str = CODEX_BENCHMARK_SANDBOX,
    ) -> dict[str, Any]:
        """Run a Codex benchmark case (executes Codex agents in a sandbox). Refused unless the MCP
        settings enable benchmarks (benchmarksEnabled). Defaults to dry_run=true because a real run
        clones third-party repos and runs agents. codex_sandbox defaults to Codex's own 'default'
        sandbox; pass 'danger-full-access' to grant full host access (trusted local runs only)."""
        return codex_benchmark_run_payload(
            config,
            target=target,
            case_id=case_id,
            benchmarks_root=benchmarks_root,
            prompt=prompt,
            variant=variant,
            repetitions=repetitions,
            jobs=jobs,
            dry_run=dry_run,
            skip_prepare=skip_prepare,
            force_clone=force_clone,
            skill_exposure_mode=skill_exposure_mode,
            provider_timeout=provider_timeout,
            codex_sandbox=codex_sandbox,
        )

    @server.tool()
    def lifecycle_start() -> dict[str, Any]:
        """Begin a new session lifecycle and become its running owner. Guarded: rejected
        (with a reminder naming the active lifecycle) when one is already active in this
        session -- end or switch it first. Takes no identifier; the server mints and tracks
        the id. Signal this once governance is confirmed at the trust checkpoint."""
        return lifecycle_start_payload()

    @server.tool()
    def lifecycle_resume() -> dict[str, Any]:
        """Resume the active lifecycle from blocked back to running once the gate or question
        that blocked it is resolved."""
        return lifecycle_resume_payload()

    @server.tool()
    def lifecycle_turn_end_notification(summary: str) -> dict[str, Any]:
        """Notify the developer the turn is complete and stop -- no wait, no gate; the next AR
        tool next turn resumes automatically."""
        return lifecycle_turn_end_notification_payload(summary)

    @server.tool()
    def lifecycle_end(outcome: str) -> dict[str, Any]:
        """End the active lifecycle. outcome is 'completed' (the human declared done) or
        'abandoned' (otherwise). Clears the session's active lifecycle."""
        return lifecycle_end_payload(outcome)

    @server.tool()
    def switch_lifecycle(on_unsaved: str | None = None) -> dict[str, Any]:
        """Leave the current lifecycle and begin a fresh one. A persistent lifecycle is paused;
        leaving an unsaved fleeting one needs on_unsaved='save' (promote) or 'discard' (abandon).
        The model never handles ids; resuming an existing lifecycle is worktree_attach."""
        return switch_lifecycle_payload(on_unsaved)

    @server.tool()
    def lifecycle_phase(phase: str) -> dict[str, Any]:
        """Move the active lifecycle along its phase axis (orthogonal to state): one of
        request | trust-checkpoint | reframe-research | decide | build | close."""
        return lifecycle_phase_payload(phase)

    @server.tool()
    def task_doc(
        repo_id: str,
        operation: str,
        task_name: str | None = None,
        contract_path: str | None = None,
        slug: str | None = None,
        fields: dict[str, Any] | None = None,
        step: dict[str, Any] | None = None,
        decision: dict[str, Any] | None = None,
        subtask: dict[str, Any] | None = None,
        section: dict[str, Any] | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Author the JSON-primary task document (ar-task-document/v1) and re-render its
        markdown. The JSON is the source of truth; task.md / <slug>.md is generated and never
        parsed back. Mutating (writes the doc's .json and .md) except operation='get'.

        operation: 'create' | 'replace' | 'set_status' | 'set_step' | 'set_subtask' | 'remove_subtask' |
        'set_section' | 'append_decision' | 'set_field' | 'get'. Locate the doc by task_name (also resolves the
        contract for the lifecycle key) or contract_path; pass slug for a series sub-task
        ('<slug>.json'), omit for a standalone task ('task.json'). 'create' takes fields (id, slug,
        title, kind ['light'|'subTask'|'master'], repo, type, createdAt, objective, requirements,
        steps, ... — a master takes subTasks + ordered sections instead of steps); 'replace' takes a
        full replacement document in fields and rewrites the existing JSON+markdown after schema
        validation; 'set_step' takes
        step={id, title, status, parent?, note?}; 'set_subtask' (master) takes subtask={number, name,
        file?, status?, scope?}; 'remove_subtask' (master) takes subtask={number, keep_file?} and drops that
        sub-task row AND deletes its leaf doc (json+md) unless keep_file=true; 'set_section' (master) takes
        section={heading, kind?, body?};
        'append_decision' takes decision={at, decision, rationale}; 'set_field' takes fields with
        scalar/list updates; 'set_status' takes fields.status. dry_run=true builds + validates and
        returns rendered/diff/wouldLose WITHOUT writing — the preview before adopting a hand .md."""
        return task_doc_payload(
            config,
            repo_id=repo_id,
            operation=operation,
            task_name=task_name,
            contract_path=contract_path,
            slug=slug,
            fields=fields,
            step=step,
            decision=decision,
            subtask=subtask,
            section=section,
            dry_run=dry_run,
        )

    @server.tool()
    def lifecycle_gate(
        kind: str,
        ask: dict[str, Any] | None = None,
        lifecycle_id: str | None = None,
        enclosure: str | None = None,
        repo_id: str | None = None,
        packet: dict[str, Any] | None = None,
        required_decision: list[str] | None = None,
        evidence_refs: list[dict[str, Any]] | None = None,
        wait: bool = True,
    ) -> dict[str, Any]:
        """Public lifecycle-gate junction for agents. Creates the durable typed gate,
        blocks the active lifecycle with the developer-facing ask, and waits for the
        developer decision or gate-specific response in one operation. kind is the dashboard junction
        (plan-approval, worktree-intent, closeout-approval, etc.); ask.kind is the
        answer shape (decision, question, conflict). Do not add a separate wait call
        as live gate choreography. wait=false raises without blocking — reserved for
        delegated seam kinds (master-handover-approval under a delegating policy; any
        other kind blocks) and it requires enclosure=<master task name>, the address
        integration enforcement matches the gate by: the call returns the gateId, the
        raiser carries it in the handover packet, and the delegated decider resolves
        it by id via gate_decide(deciding_role=...)."""
        return lifecycle_gate_payload(
            config,
            kind=kind,
            ask=ask,
            lifecycle_id=lifecycle_id,
            enclosure=enclosure,
            repo_id=repo_id,
            packet=packet,
            required_decision=required_decision,
            evidence_refs=evidence_refs,
            wait=wait,
        )

    @server.tool()
    def gate_decide(
        gate_id: str,
        decision: str,
        lifecycle_id: str | None = None,
        note: str | None = None,
        deciding_role: str | None = None,
        evidence_refs: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Record a decision on an open gate (decision: approve | reject | request-revision
        | cancel). Append-only -- the decision is a new snapshot, never an overwrite. By
        default the decision is attributed to the model via the cli; with deciding_role it is
        attributed to the active lifecycle via orchestration and checked against the configured
        gate policy."""
        decided_via = "orchestration" if deciding_role is not None else "cli"
        return gate_decide_payload(
            config,
            gate_id=gate_id,
            lifecycle_id=lifecycle_id,
            decision=decision,
            decided_by=None if deciding_role is not None else "model",
            decided_via=decided_via,
            deciding_role=deciding_role,
            note=note,
            evidence_refs=evidence_refs,
        )

    @server.tool()
    def gate_list(lifecycle_id: str | None = None) -> dict[str, Any]:
        """List the current (folded) gates for a lifecycle. With no lifecycle id it
        defaults to the ACTIVE (ambient) lifecycle — poll your own raised gate without
        handling lifecycle ids — and lists the workspace gates only when no lifecycle
        is active. Read-only."""
        return gate_list_payload(config, lifecycle_id=lifecycle_id)

    @server.tool()
    def operator_inbox_post(
        ask: str,
        response: str,
        lifecycle_id: str | None = None,
        agent_id: str | None = None,
        gate_id: str | None = None,
        sender_agent_id: str | None = None,
        sender_role: AgentRole | None = None,
        recipient_role: AgentRole | None = None,
        message_kind: InboxMessageKind = "message",
        artifact_path: str | None = None,
        deliver_to_hosted: bool = True,
    ) -> dict[str, Any]:
        """Queue an operator response for an external chat to poll. Supply lifecycle_id
        and/or agent_id as the mailbox key. Agent-to-agent messages can include sender /
        recipient role metadata; hosted targets are push-delivered through the terminal paste seam
        while the durable inbox row remains dashboard-visible. Over MCP this route is attributed to
        the model via cli; trusted dashboard code can call the payload builder directly with
        developer/dashboard attribution."""
        return operator_inbox_post_payload(
            config,
            lifecycle_id=lifecycle_id,
            agent_id=agent_id,
            gate_id=gate_id,
            sender_agent_id=sender_agent_id,
            sender_role=sender_role,
            recipient_role=recipient_role,
            message_kind=message_kind,
            artifact_path=artifact_path,
            deliver_to_hosted=deliver_to_hosted,
            ask=ask,
            response=response,
            created_by="model",
            created_via="cli",
        )

    @server.tool()
    def operator_inbox_poll(
        lifecycle_id: str | None = None,
        agent_id: str | None = None,
        recipient_role: AgentRole | None = None,
    ) -> dict[str, Any]:
        """List pending external-chat inbox entries for a lifecycle_id, agent_id, and/or role
        mailbox key. Consuming an entry is explicit via operator_inbox_consume."""
        return operator_inbox_poll_payload(
            config,
            lifecycle_id=lifecycle_id,
            agent_id=agent_id,
            recipient_role=recipient_role,
        )

    @server.tool()
    def operator_inbox_consume(entry_id: str) -> dict[str, Any]:
        """Mark an external-chat inbox entry consumed. The entry remains in the append-only
        inbox log; repeated consume calls are idempotent."""
        return operator_inbox_consume_payload(
            config,
            entry_id=entry_id,
            consumed_by="model",
            consumed_via="cli",
        )

    @server.tool()
    def orchestration_nudge_manager(
        reason: NudgeReason,
        subject: str,
        manager_agent_id: str | None = None,
        manager_lifecycle_id: str | None = None,
        subject_agent_id: str | None = None,
        subject_lifecycle_id: str | None = None,
        artifact_path: str | None = None,
        rate_limit_seconds: int = 900,
    ) -> dict[str, Any]:
        """Rate-limit, log, and push a manager nudge for inactivity or a missing turn report."""
        return orchestration_nudge_manager_payload(
            config,
            reason=reason,
            subject=subject,
            manager_agent_id=manager_agent_id,
            manager_lifecycle_id=manager_lifecycle_id,
            subject_agent_id=subject_agent_id,
            subject_lifecycle_id=subject_lifecycle_id,
            artifact_path=artifact_path,
            rate_limit_seconds=rate_limit_seconds,
        )

    return server


def run_server(config: McpRuntimeConfig) -> None:
    create_server(config).run()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Agents Remember MCP server.")
    parser.add_argument(
        "--config",
        required=True,
        help="Absolute path to trusted MCP settings JSON.",
    )
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as error:
        parser.error(str(error))

    # Boot-time dashboard supervision (dashboard.autoStart): total and threaded —
    # it must never delay or break the stdio handshake this process exists for.
    maybe_autostart_dashboard(config)
    run_server(config)
    return 0
