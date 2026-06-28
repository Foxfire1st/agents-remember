"""Provider-node builders for observer snapshot surfaces."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from agents_remember.observer.projection import ProviderNode


def provider_role(provider_id: str) -> str:
    """Map a provider id to its repo role: GrepAI serves memory, CGC serves code."""
    return "memory" if "memory" in provider_id or "grepai" in provider_id else "code"


def workspace_provider_nodes(
    providers: dict[str, Any], *, stale_seconds: float | None
) -> list[ProviderNode]:
    """Project workspace provider state, expanding explicit per-repo evidence when present."""
    nodes: list[ProviderNode] = []
    for key, value in providers.items():
        if not isinstance(value, dict):
            continue
        provider_id = str(value.get("id", key))
        if _is_cgc_provider(provider_id):
            repo_nodes = _cgc_repo_provider_nodes(provider_id, value, stale_seconds=stale_seconds)
            if repo_nodes:
                nodes.extend(repo_nodes)
                continue
        target_nodes = _target_repo_provider_nodes(
            provider_id,
            value,
            stale_seconds=stale_seconds,
        )
        if target_nodes:
            nodes.extend(target_nodes)
            continue
        nodes.append(_workspace_provider_node(provider_id, value, stale_seconds=stale_seconds))
    return nodes


def worktree_provider_node(
    provider_id: str,
    *,
    group: str,
    repo_id: str | None,
    stale_seconds: float | None,
    runtime: dict[str, Any] | None = None,
) -> ProviderNode:
    """Project one isolated worktree provider stack member."""
    runtime = runtime or {}
    ok = runtime.get("ok")
    return ProviderNode(
        id=f"{provider_id}@{group}",
        state=str(runtime.get("state") or "configured"),
        ok=ok if isinstance(ok, bool) else None,
        watcherUp=runtime.get("watcherUp") is True,
        indexingState=str(runtime.get("indexingState") or "unknown"),
        snapshotStaleSeconds=stale_seconds,
        scope="worktree",
        role=provider_role(provider_id),
        repoId=repo_id,
        worktreeGroup=group,
    )


def _workspace_provider_node(
    provider_id: str, value: dict[str, Any], *, stale_seconds: float | None
) -> ProviderNode:
    ok = value.get("ok")
    return ProviderNode(
        id=provider_id,
        state=str(value.get("state") or "unknown"),
        ok=ok if isinstance(ok, bool) else None,
        watcherUp=value.get("watcherUp") is True,
        indexingState=str(value.get("indexingState") or "unknown"),
        snapshotStaleSeconds=stale_seconds,
        scope="workspace",
        role=provider_role(provider_id),
    )


def _cgc_repo_provider_nodes(
    provider_id: str, value: dict[str, Any], *, stale_seconds: float | None
) -> list[ProviderNode]:
    watchers = _cgc_watchers(value)
    if watchers is None:
        return []
    return [
        _cgc_repo_provider_node(
            provider_id,
            value,
            repo_id=repo_id,
            raw_repo=raw_repo,
            stale_seconds=stale_seconds,
        )
        for repo_id, raw_repo in _repo_entries(watchers)
    ]


def _cgc_watchers(value: dict[str, Any]) -> dict[Any, Any] | None:
    resources = value.get("resources")
    watchers = resources.get("watchers") if isinstance(resources, dict) else None
    return watchers if isinstance(watchers, dict) else None


def _repo_entries(watchers: dict[Any, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    for key, raw_repo in sorted(watchers.items(), key=lambda item: str(item[0])):
        if not isinstance(raw_repo, dict):
            continue
        repo_id = _repo_id(key, raw_repo)
        if repo_id is None:
            continue
        yield repo_id, raw_repo


def _cgc_repo_provider_node(
    provider_id: str,
    value: dict[str, Any],
    *,
    repo_id: str,
    raw_repo: dict[str, Any],
    stale_seconds: float | None,
) -> ProviderNode:
    ok = raw_repo.get("ok")
    return ProviderNode(
        id=f"{provider_id}:{repo_id}",
        state=str(raw_repo.get("state") or value.get("state") or "unknown"),
        ok=ok if isinstance(ok, bool) else None,
        watcherUp=raw_repo.get("watcherUp") is True,
        indexingState=str(raw_repo.get("indexingState") or value.get("indexingState") or "unknown"),
        snapshotStaleSeconds=stale_seconds,
        scope="workspace",
        role="code",
        repoId=repo_id,
    )


def _target_repo_provider_nodes(
    provider_id: str, value: dict[str, Any], *, stale_seconds: float | None
) -> list[ProviderNode]:
    return [
        _target_repo_provider_node(
            provider_id,
            value,
            repo_id=repo_id,
            stale_seconds=stale_seconds,
        )
        for repo_id in _target_repo_ids(value)
    ]


def _target_repo_provider_node(
    provider_id: str,
    value: dict[str, Any],
    *,
    repo_id: str,
    stale_seconds: float | None,
) -> ProviderNode:
    ok = value.get("ok")
    return ProviderNode(
        id=f"{provider_id}:{repo_id}",
        state=str(value.get("state") or "unknown"),
        ok=ok if isinstance(ok, bool) else None,
        watcherUp=value.get("watcherUp") is True,
        indexingState=str(value.get("indexingState") or "unknown"),
        snapshotStaleSeconds=stale_seconds,
        scope="workspace",
        role=provider_role(provider_id),
        repoId=repo_id,
    )


def _target_repo_ids(value: dict[str, Any]) -> list[str]:
    targets = value.get("targetRepos")
    if not isinstance(targets, list):
        return []
    repo_ids = {
        repo_id
        for raw_target in targets
        if isinstance(raw_target, dict)
        for repo_id in [_repo_id(None, raw_target)]
        if repo_id is not None
    }
    return sorted(repo_ids)


def _is_cgc_provider(provider_id: str) -> bool:
    return provider_id == "codegraphcontext-code" or provider_id.startswith("codegraphcontext")


def _repo_id(key: Any | None, raw_repo: dict[str, Any]) -> str | None:
    value = raw_repo.get("repoId")
    if isinstance(value, str) and value:
        return value
    if key is None:
        return None
    key_text = str(key)
    return key_text if key_text else None
