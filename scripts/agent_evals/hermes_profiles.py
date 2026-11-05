"""Admitted Hermes actors: route bindings and absolute compaction."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents_remember.errors import AuthorityError

from .adapters.execution import HERMES_ACTOR, HERMES_GPT_ACTOR, HERMES_KIMI_ACTOR, LaunchProfile
from .artifacts import read_object, write_object


def hermes_route(profile: LaunchProfile = HERMES_ACTOR) -> dict[str, Any]:
    """Keep model, seed selection and destination bound to the same actor."""
    if profile == HERMES_ACTOR:
        return {
            "provider": "opencode-go",
            "model": "deepseek-v4.1-flash",
            "expectedHost": "opencode.ai",
            "origin": "agent_evals.adapters.execution.HERMES_ACTOR",
            "seedNames": ["OPENCODE_GO_API_KEY", "OPENCODE_GO_BASE_URL"],
            "effortPolicy": "max",
        }
    if profile == HERMES_KIMI_ACTOR:
        return {
            "provider": "opencode-go",
            "model": "kimi-k3.1-pro",
            "expectedHost": "opencode.ai",
            "origin": "agent_evals.adapters.execution.HERMES_KIMI_ACTOR",
            "seedNames": ["OPENCODE_GO_API_KEY", "OPENCODE_GO_BASE_URL"],
            "effortPolicy": "provider-default",
        }
    if profile == HERMES_GPT_ACTOR:
        return {
            "provider": "openai-codex",
            "model": "gpt-6.1-sol-900k",
            "expectedHost": "chatgpt.com",
            "origin": "agent_evals.adapters.execution.HERMES_GPT_ACTOR",
            "seedNames": [],
            "effortPolicy": "high",
        }
    raise AuthorityError("Hermes setup requires an admitted Hermes actor profile")


def absolute_compaction(window: int) -> dict[str, int | float | str]:
    """Derive the fraction from the resolved window, preserving the absolute."""
    target = 400_000
    if type(window) is not int or window < target:
        raise AuthorityError("binding-unavailable: resolved Hermes window cannot hold 400000")
    fraction = target / window
    actual = int(window * fraction)
    if actual != target:
        raise AuthorityError(
            "binding-unavailable: Hermes fraction does not reproduce exact 400000 "
            f"for resolved window {window}"
        )
    return {
        "resolvedWindow": window,
        "fraction": fraction,
        "absoluteThreshold": actual,
        "claim": "absolute 400000; fraction derived per resolved window",
    }


def hermes_auxiliary_tasks(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Only the three configured DeepSeek tasks admit the additional seed."""
    auxiliary = document.get("auxiliary", {})
    if not isinstance(auxiliary, dict):
        return []
    tasks = []
    for task in ("approval", "compression", "mcp"):
        configured = auxiliary.get(task)
        if not isinstance(configured, dict) or configured.get("provider") != "deepseek":
            continue
        tasks.append(
            {
                "task": task,
                "provider": "deepseek",
                "model": configured.get("model"),
                "reasoningEffort": configured.get("reasoning_effort"),
                "expectedHost": "api.deepseek.com",
                "seedNames": ["DEEPSEEK_API_KEY"],
            }
        )
    return tasks


def bind_compaction(home: Path, window: int) -> dict[str, int | float | str]:
    """Set the exact derived fraction before the actor's policy baseline exists."""
    chain = absolute_compaction(window)
    path = home / "config.yaml"
    document = read_object(path)
    document.setdefault("compression", {})["threshold"] = chain["fraction"]
    write_object(path, document)
    path.chmod(0o600)
    return chain


def effort_declaration(profile: LaunchProfile) -> dict[str, Any] | None:
    """Keep provider defaults distinct from an unobserved outgoing carrier."""
    if profile == HERMES_GPT_ACTOR:
        return {
            "effort": "high",
            "requestCarrierObserved": False,
            "mechanism": "native ACP reasoning configuration; outgoing request unobserved",
            "effortParity": "unresolved until outgoing carrier is observed",
            "scope": "configured high; no outgoing carrier or priority claim",
        }
    if profile != HERMES_KIMI_ACTOR:
        return None
    return {
        "effort": "provider default",
        "requestCarrierObserved": False,
        "mechanism": "unobserved in retained capture",
        "effortParity": "unresolved; neither matched nor mismatched is established",
        "scope": "provider default only; no max claim or request logger",
    }
