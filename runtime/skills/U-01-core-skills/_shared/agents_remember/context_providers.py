"""Context provider runtime layout and patch helpers."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CGC_PROVIDER = "codegraphcontext"
CGC_PIN = "codegraphcontext==0.4.10"
CGC_CGCIGNORE_PATCH_ID = "codegraphcontext-0.4.10-cgcignore-runtime-root"
SOURCE_ARTIFACT_NAMES = (".cgcignore", ".codegraphcontext", "CGC_REPORT.md")
CGC_ENV_FILE_EXCLUDED_KEYS = {
    "HOME",
    # CGC uses these when passed as process env, but v0.4.10 reports them as
    # invalid if they are persisted in .codegraphcontext/.env.
    "CGC_RUNTIME_DB_TYPE",
    "KUZUDB_PATH",
    "CGC_RUNTIME_DB_PATH",
}

DEFAULT_CGCIGNORE = "\n".join(
    [
        "# Managed by Agents Remember for CodeGraphContext",
        "node_modules/",
        "venv/",
        ".venv/",
        "env/",
        ".env/",
        "dist/",
        "build/",
        "target/",
        "out/",
        ".git/",
        "__pycache__/",
        "*.pyc",
        "*.pyo",
        "*.png",
        "*.jpg",
        "*.jpeg",
        "*.gif",
        "*.svg",
        "*.zip",
        "*.tar",
        "*.gz",
        "",
    ]
)

CGC_PATCH_MARKER = "Agents Remember patch: prefer explicit .cgcignore path before repo-local creation"
CGC_ORIGINAL_SNIPPET = '''    if local_cgcignore_path is None:
        local_cgcignore_path = ignore_root / ".cgcignore"
        ensure_default_cgcignore(local_cgcignore_path, default_patterns)
'''
CGC_PATCHED_SNIPPET = f'''    if local_cgcignore_path is None:
        # {CGC_PATCH_MARKER}.
        if explicit_cgcignore_path is not None:
            local_cgcignore_path = explicit_cgcignore_path
        else:
            local_cgcignore_path = ignore_root / ".cgcignore"
        ensure_default_cgcignore(local_cgcignore_path, default_patterns)
'''


class ContextProviderError(ValueError):
    """Raised when a provider layout or patch check fails."""


@dataclass(frozen=True)
class CgcRuntimeLayout:
    coordination_root: Path
    repo_id: str
    code_repo_root: Path
    providers_root: Path
    runtime_root: Path
    cgc_root: Path
    venv_root: Path
    requirements_file: Path
    patches_root: Path
    state_file: Path
    cgcignore_path: Path
    config_file: Path
    env_file: Path
    db_path: Path
    run_root: Path
    logs_root: Path

    def env(self) -> dict[str, str]:
        """Return the environment required to keep CGC under the runtime root."""

        return {
            "HOME": self.runtime_root.as_posix(),
            "CGC_RUNTIME_DB_TYPE": "kuzudb",
            "DEFAULT_DATABASE": "kuzudb",
            "KUZUDB_PATH": self.db_path.as_posix(),
            "CGC_RUNTIME_DB_PATH": self.db_path.as_posix(),
            "FALKORDB_PATH": (self.cgc_root / "db" / "falkordb.db").as_posix(),
            "FALKORDB_SOCKET_PATH": (self.run_root / "falkordb.sock").as_posix(),
            "LOG_FILE_PATH": (self.logs_root / "cgc.log").as_posix(),
            "DEBUG_LOG_PATH": (self.logs_root / "debug.log").as_posix(),
            "ENABLE_AUTO_WATCH": "false",
        }

    def cgc_executable(self) -> Path:
        return self.venv_root / "bin" / "cgc"


def stable_provider_id(value: str) -> str:
    """Return a stable provider id component."""

    lowered = value.strip().lower()
    slug = re.sub(r"[^a-z0-9._-]+", "-", lowered).strip(".-_")
    return slug or "repo"


def cgc_runtime_layout(*, coordination_root: Path, repo_id: str, code_repo_root: Path) -> CgcRuntimeLayout:
    """Build the managed CGC runtime layout for one code repository."""

    coordination_root = coordination_root.resolve()
    repo_id = stable_provider_id(repo_id)
    providers_root = coordination_root / "providers"
    runtime_root = providers_root / CGC_PROVIDER / repo_id
    cgc_root = runtime_root / ".codegraphcontext"
    return CgcRuntimeLayout(
        coordination_root=coordination_root,
        repo_id=repo_id,
        code_repo_root=code_repo_root.resolve(),
        providers_root=providers_root,
        runtime_root=runtime_root,
        cgc_root=cgc_root,
        venv_root=providers_root / "_venvs" / CGC_PROVIDER,
        requirements_file=providers_root / "requirements" / f"{CGC_PROVIDER}.txt",
        patches_root=providers_root / "patches" / CGC_PROVIDER,
        state_file=runtime_root / "provider-state.json",
        cgcignore_path=cgc_root / ".cgcignore",
        config_file=cgc_root / "config.yaml",
        env_file=cgc_root / ".env",
        db_path=cgc_root / "db" / "kuzu",
        run_root=cgc_root / "run",
        logs_root=cgc_root / "logs",
    )


def ensure_cgc_runtime_layout(layout: CgcRuntimeLayout) -> None:
    """Create runtime directories and default CGC config files."""

    for path in [
        layout.venv_root,
        layout.requirements_file.parent,
        layout.patches_root,
        layout.cgc_root,
        layout.db_path.parent,
        layout.run_root,
        layout.logs_root,
    ]:
        path.mkdir(parents=True, exist_ok=True)

    if not layout.requirements_file.exists():
        layout.requirements_file.write_text(f"{CGC_PIN}\n", encoding="utf-8")
    if not layout.cgcignore_path.exists():
        layout.cgcignore_path.write_text(DEFAULT_CGCIGNORE, encoding="utf-8")
    if not layout.config_file.exists():
        layout.config_file.write_text("database: kuzudb\n", encoding="utf-8")
    if not layout.env_file.exists():
        lines = ["# Managed by Agents Remember for CodeGraphContext"]
        lines.extend(
            f"{key}={value}"
            for key, value in layout.env().items()
            if key not in CGC_ENV_FILE_EXCLUDED_KEYS
        )
        layout.env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_provider_state(layout: CgcRuntimeLayout, data: dict[str, Any]) -> None:
    """Write provider state as pretty JSON."""

    layout.state_file.parent.mkdir(parents=True, exist_ok=True)
    layout.state_file.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_provider_artifacts(code_repo_root: Path) -> list[Path]:
    """Return provider-created artifacts that should not exist in source repos."""

    root = code_repo_root.resolve()
    return [root / name for name in SOURCE_ARTIFACT_NAMES if (root / name).exists()]


def assert_no_source_provider_artifacts(code_repo_root: Path) -> None:
    artifacts = source_provider_artifacts(code_repo_root)
    if artifacts:
        rendered = ", ".join(path.name for path in artifacts)
        raise ContextProviderError(f"provider artifacts found in source repo: {rendered}")


def find_cgc_cgcignore_module(venv_root: Path) -> Path:
    """Find CodeGraphContext's cgcignore.py in a provider venv."""

    matches = sorted(venv_root.glob("lib/python*/site-packages/codegraphcontext/core/cgcignore.py"))
    if not matches:
        raise ContextProviderError(f"could not find CodeGraphContext cgcignore.py under {venv_root}")
    if len(matches) > 1:
        raise ContextProviderError(f"multiple CodeGraphContext cgcignore.py files found under {venv_root}")
    return matches[0]


def cgc_cgcignore_patch_applied(path: Path) -> bool:
    return CGC_PATCH_MARKER in path.read_text(encoding="utf-8")


def apply_cgc_cgcignore_patch(path: Path) -> bool:
    """Patch CGC so an explicit .cgcignore path wins before repo-local creation.

    Returns true when the file was changed and false when the patch was already
    present.
    """

    text = path.read_text(encoding="utf-8")
    if CGC_PATCH_MARKER in text:
        return False
    if CGC_ORIGINAL_SNIPPET not in text:
        raise ContextProviderError("CGC cgcignore.py did not match the expected unpatched snippet")
    path.write_text(text.replace(CGC_ORIGINAL_SNIPPET, CGC_PATCHED_SNIPPET), encoding="utf-8")
    return True
