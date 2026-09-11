from __future__ import annotations

from pathlib import Path

from agents_remember.worktrees.integration.lifecycle.lifecycle_public_evidence import (
    public_failure_evidence,
)
from agents_remember.worktrees.services import worktree_services


def provider_enablement_state(
    target_coordination_root: Path,
    provider_settings_path: Path,
    *,
    target_memory_root: Path | None,
) -> dict[str, object]:
    try:
        settings = worktree_services().provider_lifecycle.load_settings(provider_settings_path)
    except RuntimeError as error:
        return {
            "state": "blocked",
            "reason": "provider settings are unreadable",
            "failure": public_failure_evidence(
                stage="worktree-start-provider-settings",
                side="provider-settings",
                name=provider_settings_path.name,
                error_type=type(error).__name__,
                observed={"state": "unreadable"},
            ),
            "targetCoordinationRoot": target_coordination_root.as_posix(),
        }
    cgc_enabled = bool(settings) and worktree_services().provider_lifecycle.provider_enabled(
        settings, "codegraphcontext-code"
    )
    grepai_enabled = bool(settings) and worktree_services().provider_lifecycle.provider_enabled(
        settings, "grepai-memory"
    )
    grepai_worktree_enabled = grepai_enabled and target_memory_root is not None
    if cgc_enabled or grepai_worktree_enabled:
        return _enabled_provider_state(cgc_enabled, grepai_worktree_enabled)
    return {
        "state": "skipped",
        "reason": _provider_enablement_skip_reason(
            cgc_enabled=cgc_enabled,
            grepai_enabled=grepai_enabled,
            target_memory_root=target_memory_root,
        ),
        "settingsFile": worktree_services()
        .provider_lifecycle.settings_path(provider_settings_path)
        .as_posix(),
    }


def _enabled_provider_state(
    cgc_enabled: bool,
    grepai_worktree_enabled: bool,
) -> dict[str, object]:
    return {
        "state": "enabled",
        "codegraphcontext-code": cgc_enabled,
        "grepai-memory": grepai_worktree_enabled,
    }


def _provider_enablement_skip_reason(
    *,
    cgc_enabled: bool,
    grepai_enabled: bool,
    target_memory_root: Path | None,
) -> str:
    reasons = []
    if not cgc_enabled:
        reasons.append("codegraphcontext-code is not enabled")
    if not grepai_enabled:
        reasons.append("grepai-memory is not enabled")
    elif target_memory_root is None:
        reasons.append("grepai-memory requires worktree memory")
    return "; ".join(reasons)
