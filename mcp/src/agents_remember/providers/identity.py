"""Provider instance identity helpers."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

MAX_DOCKER_NAME_COMPONENT = 40


def stable_slug(value: str, *, fallback: str = "instance") -> str:
    lowered = value.strip().lower()
    slug = re.sub(r"[^a-z0-9._-]+", "-", lowered).strip(".-_")
    return slug or fallback


def short_hash(value: str, *, length: int = 10) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def provider_instance_id(
    scope: str,
    anchor: str | Path,
    *,
    workspace_name: str | None = None,
) -> str:
    scope_slug = stable_slug(scope, fallback="scope")
    anchor_path = Path(anchor).resolve() if isinstance(anchor, Path) else None
    workspace_slug = stable_slug(
        workspace_name or _workspace_name_from_anchor(scope_slug, anchor, anchor_path),
        fallback="workspace",
    )
    if scope_slug == "workspace":
        return docker_component(workspace_slug)
    if scope_slug == "worktree":
        worktree_slug = stable_slug(
            _workflow_name_from_anchor(anchor, anchor_path),
            fallback="worktree",
        )
        return docker_component(f"{workspace_slug}-{worktree_slug}")
    if scope_slug == "benchmark":
        return docker_component(f"{workspace_slug}-benchmark")
    anchor_slug = stable_slug(
        _workflow_name_from_anchor(anchor, anchor_path),
        fallback="instance",
    )
    return docker_component(f"{workspace_slug}-{scope_slug}-{anchor_slug}")


def _workspace_name_from_anchor(
    scope_slug: str,
    anchor: str | Path,
    anchor_path: Path | None,
) -> str:
    if anchor_path is None:
        return str(anchor)
    if scope_slug == "worktree":
        coordination_parent = _coordination_parent_from_workflow_path(anchor_path)
        if coordination_parent is not None:
            return coordination_parent.name
    if scope_slug == "benchmark" and anchor_path.name == "ar-coordination":
        return anchor_path.parent.name
    return anchor_path.name


def _coordination_parent_from_workflow_path(path: Path) -> Path | None:
    parts = path.parts
    if "worktrees" not in parts:
        return None
    index = parts.index("worktrees")
    if index < 1:
        return None
    coordination_root = Path(*parts[:index])
    return coordination_root.parent


def _workflow_name_from_anchor(anchor: str | Path, anchor_path: Path | None) -> str:
    if anchor_path is None:
        return str(anchor)
    if anchor_path.name == "provider-runtime":
        return anchor_path.parent.name
    return anchor_path.name


def explicit_provider_instance_id(value: str) -> str:
    instance_id = docker_component(value)
    if not instance_id:
        raise ValueError("provider instance id must not be empty")
    return instance_id


def docker_component(value: str) -> str:
    slug = stable_slug(value)
    if len(slug) <= MAX_DOCKER_NAME_COMPONENT:
        return slug
    return f"{slug[: MAX_DOCKER_NAME_COMPONENT - 11].rstrip('-')}-{short_hash(slug)}"


def scoped_name(prefix: str, instance_id: str, *suffixes: str) -> str:
    parts = [stable_slug(prefix), docker_component(instance_id)]
    parts.extend(docker_component(suffix) for suffix in suffixes if suffix)
    return "-".join(parts)


def provider_ownership_labels(
    *,
    provider_id: str,
    instance_id: str,
    scope: str,
    coordination_root: Path,
) -> dict[str, str]:
    return {
        "agents-remember.provider": provider_id,
        "agents-remember.instance-id": instance_id,
        "agents-remember.scope": scope,
        "agents-remember.coordination-root": coordination_root.resolve().as_posix(),
    }
