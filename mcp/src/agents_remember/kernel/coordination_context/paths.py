from __future__ import annotations

import re
from pathlib import Path

from agents_remember.kernel.memory_mode import (
    LEGACY_INTERNAL_MEMORY_DIRNAME,
    Topology,
    refuse_removed_memory_mode,
)

DEFAULT_AR_COORDINATION_ROOT = "../ar-coordination"


def looks_like_installed_coordination_root(path: Path) -> bool:
    return (
        (path / "skills").is_dir()
        and (path / "system").is_dir()
        and (path / "memory-repos").is_dir()
        and (path / "tasks").is_dir()
    )


def agents_repo_from_script() -> Path:
    script_path = Path(__file__).resolve()
    for parent in script_path.parents:
        if (parent / "runtime" / "skills").exists() and (parent / "AGENTS.md").exists():
            return parent
        if looks_like_installed_coordination_root(parent):
            return parent
    return script_path.parents[6]


def clean_scalar(value: str) -> str:
    value = value.strip()
    if value.startswith("`") and value.endswith("`"):
        value = value[1:-1]
    if value.startswith(('"', "'")) and value.endswith(('"', "'")) and len(value) >= 2:
        value = value[1:-1]
    return value.strip()


def normalize_rel_path(value: str) -> str:
    return value.replace("\\", "/").strip().strip("/")


def mirror_onboarding_path(onboarding_root: Path, source_file: str) -> Path:
    """Where ``source_file``'s sidecar onboarding lives: the same relative path, plus ``.md``.

    The 1:1 mirror rule, expressed once. It is a pure derivation over
    :func:`normalize_rel_path` -- no filesystem access, no memory-quality vocabulary -- and it
    is asked by three different substrates: the drift checker, the missing-onboarding check,
    and the sidecar pairing the read tools and the dashboard file API share. It used to be
    declared by the drift checker, which made ``kernel`` import ``memory_quality``
    (``layers.toml``) for a path join.
    """
    return onboarding_root / f"{normalize_rel_path(source_file)}.md"


def extract_yaml_blocks(markdown_text: str) -> list[str]:
    return [
        match.group(1)
        for match in re.finditer(r"```(?:yaml|yml)?\n(.*?)```", markdown_text, re.DOTALL)
    ]


def external_memory_root(coordination_root: Path, code_repository_name: str) -> Path:
    return (coordination_root / "memory-repos" / f"ar-{code_repository_name}").resolve()


def settings_path_for_roots(memory_root: Path, coordination_root: Path) -> Path:
    memory_settings = memory_root / "system" / "settings.md"
    coordination_settings = coordination_root / "system" / "settings.md"
    if memory_settings.exists():
        return memory_settings
    if coordination_settings.exists():
        return coordination_settings
    return memory_settings


def memory_roots_from_settings(
    settings_path: Path,
    code_repository_name: str,
) -> tuple[Path, Path]:
    """The coordination and memory roots a settings file implies.

    Two shapes are admitted: a settings file inside a per-repo memory repo, and one at a
    coordination root. A settings file anywhere else is not a supported memory location, so
    it is refused by its own path rather than silently resolving to some other root.
    """
    settings_root = settings_path.resolve().parent.parent
    if (
        settings_root.name == f"ar-{code_repository_name}"
        and settings_root.parent.name == "memory-repos"
    ):
        return settings_root.parent.parent, settings_root
    if settings_root.name == LEGACY_INTERNAL_MEMORY_DIRNAME:
        refuse_removed_memory_mode("internal", artifact=settings_path.as_posix())
    return settings_root, external_memory_root(settings_root, code_repository_name)


def resolve_coordination_root_hint(coordination_root: Path | None) -> Path:
    if coordination_root is not None:
        return coordination_root.resolve()

    runtime_root = agents_repo_from_script().resolve()
    if looks_like_installed_coordination_root(runtime_root):
        return runtime_root
    return (runtime_root / DEFAULT_AR_COORDINATION_ROOT).resolve()


def find_code_repository_root(workspace_root: Path, code_repository_name: str) -> Path:
    repo_path = Path(code_repository_name).expanduser()
    if repo_path.is_absolute() and repo_path.exists():
        return repo_path.resolve()

    direct = (workspace_root / code_repository_name).resolve()
    if direct.exists():
        return direct

    raise ValueError(
        f"code repository {code_repository_name!r} was not found under {workspace_root}"
    )


def infer_settings_path(onboarding_root: Path) -> Path:
    if onboarding_root.name == "onboarding":
        context_root = onboarding_root.parent
    elif onboarding_root.parent.name == "onboarding":
        context_root = onboarding_root.parent.parent
    else:
        context_root = onboarding_root.parent
    return context_root / "system" / "settings.md"


def path_settings_path_for(settings_path: Path) -> Path:
    return settings_path.with_suffix(".json")


def infer_topology_from_onboarding_root(onboarding_root: Path) -> Topology:
    """The topology an onboarding root belongs to, or a refusal naming that root.

    An onboarding root under the removed repo-sidecar layout is refused by name and by exact
    path: it is real existing state, and reporting it is the whole point -- resolving it to
    ``external``, or to any other root, would silently move a repository's memory.
    """
    if onboarding_root.parent.name == LEGACY_INTERNAL_MEMORY_DIRNAME:
        refuse_removed_memory_mode("internal", artifact=onboarding_root.as_posix())
    if (
        onboarding_root.parent.parent.name == "memory-repos"
        and onboarding_root.parent.name.startswith("ar-")
    ):
        return "external"
    raise ValueError(
        "onboarding_root must point to a supported memory location: "
        "<ar-coordination>/memory-repos/ar-<code-repository-name>/onboarding"
    )
