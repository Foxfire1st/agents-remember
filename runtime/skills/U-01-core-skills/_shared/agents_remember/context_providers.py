"""Context provider runtime layout and patch helpers."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CGC_PROVIDER = "codegraphcontext"
CGC_PIN = "codegraphcontext==0.4.10"
CGC_REQUIREMENTS = (
    CGC_PIN,
    "tree-sitter==0.25.2",
    "tree-sitter-language-pack==0.13.0",
    "tree-sitter-c-sharp==0.23.5",
)
CGC_CGCIGNORE_PATCH_ID = "codegraphcontext-0.4.10-cgcignore-runtime-root-v2"
CGC_DELETE_PATCH_ID = "codegraphcontext-0.4.10-windows-delete-prefix-v1"
CGC_GRAPH_BUILDER_EXTENSIONS_PATCH_ID = "codegraphcontext-0.4.10-cpp-cc-td-extensions-v1"
CGC_DISCOVERY_EXTENSIONS_PATCH_ID = "codegraphcontext-0.4.10-td-generic-discovery-v1"
CGC_FALKORDB_BACKEND_ID = "codegraphcontext-falkordb"
CGC_FALKORDB_CONTAINER_NAME = "ar-cgc-falkordb"
CGC_FALKORDB_DEFAULT_HOST = "127.0.0.1"
CGC_FALKORDB_DEFAULT_PORT = "6379"
GREPAI_PROVIDER = "grepai"
GREPAI_PIN = "grepai==0.35.0"
SOURCE_ARTIFACT_NAMES = (".cgcignore", ".codegraphcontext", "CGC_REPORT.md")
CGC_ENV_FILE_EXCLUDED_KEYS = {
    "HOME",
    # CGC uses these when passed as process env, but v0.4.10 reports them as
    # invalid if they are persisted in .codegraphcontext/.env.
    "CGC_RUNTIME_DB_TYPE",
    "FALKORDB_HOST",
    "FALKORDB_PORT",
    "FALKORDB_GRAPH_NAME",
    "PYTHONIOENCODING",
    "PYTHONUTF8",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
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


def read_gitignore_patterns(repo_root: Path) -> tuple[str, ...]:
    """Return non-empty top-level .gitignore lines for managed CGC ignores."""

    gitignore = repo_root / ".gitignore"
    if not gitignore.exists():
        return ()
    patterns: list[str] = []
    for raw_line in gitignore.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if line:
            patterns.append(line)
    return tuple(patterns)


CGC_PATCH_MARKER = "Agents Remember patch: prefer explicit .cgcignore path without overwriting it"
CGC_ORIGINAL_SNIPPET = '''    if local_cgcignore_path is None:
        local_cgcignore_path = ignore_root / ".cgcignore"
        ensure_default_cgcignore(local_cgcignore_path, default_patterns)
'''
CGC_OLD_PATCHED_SNIPPET = '''    if local_cgcignore_path is None:
        # Agents Remember patch: prefer explicit .cgcignore path before repo-local creation.
        if explicit_cgcignore_path is not None:
            local_cgcignore_path = explicit_cgcignore_path
        else:
            local_cgcignore_path = ignore_root / ".cgcignore"
        ensure_default_cgcignore(local_cgcignore_path, default_patterns)
'''
CGC_PATCHED_SNIPPET = f'''    if local_cgcignore_path is None:
        # {CGC_PATCH_MARKER}.
        if explicit_cgcignore_path is not None:
            local_cgcignore_path = explicit_cgcignore_path
        else:
            local_cgcignore_path = ignore_root / ".cgcignore"
        if not local_cgcignore_path.exists():
            ensure_default_cgcignore(local_cgcignore_path, default_patterns)
'''

CGC_DELETE_PATCH_MARKER = "Agents Remember patch: delete repository paths with slash and backslash child prefixes"
CGC_DELETE_PREFIX_ORIGINAL_SNIPPET = '''        repo_path_str = repo_path
        path_prefix = repo_path_str + "/"
        with self.driver.session() as session:
'''
CGC_DELETE_PREFIX_PATCHED_SNIPPET = f'''        repo_path_str = repo_path
        path_prefix = repo_path_str + "/"
        # {CGC_DELETE_PATCH_MARKER}.
        path_prefix_backslash = repo_path_str + "\\\\"
        with self.driver.session() as session:
'''
CGC_DELETE_REL_ORIGINAL_SNIPPET = '''                    result = session.run(
                        f"MATCH (a)-[r:{rel_type}]->(b) "
                        "WHERE a.path STARTS WITH $prefix OR b.path STARTS WITH $prefix "
                        "WITH r LIMIT 5000 DELETE r RETURN count(r) AS deleted",
                        prefix=path_prefix,
                    ).single()
'''
CGC_DELETE_REL_PATCHED_SNIPPET = '''                    result = session.run(
                        f"MATCH (a)-[r:{rel_type}]->(b) "
                        "WHERE a.path STARTS WITH $prefix OR b.path STARTS WITH $prefix "
                        "OR a.path STARTS WITH $prefix_backslash OR b.path STARTS WITH $prefix_backslash "
                        "WITH r LIMIT 5000 DELETE r RETURN count(r) AS deleted",
                        prefix=path_prefix,
                        prefix_backslash=path_prefix_backslash,
                    ).single()
'''
CGC_DELETE_CONTAINS_ORIGINAL_SNIPPET = '''                    "MATCH (a)-[r:CONTAINS]->(b) "
                    "WHERE a.path STARTS WITH $prefix OR a.path = $path "
                    "WITH r LIMIT 10000 DELETE r RETURN count(r) AS deleted",
                    prefix=path_prefix,
                    path=repo_path_str,
'''
CGC_DELETE_CONTAINS_PATCHED_SNIPPET = '''                    "MATCH (a)-[r:CONTAINS]->(b) "
                    "WHERE a.path STARTS WITH $prefix OR a.path STARTS WITH $prefix_backslash OR a.path = $path "
                    "WITH r LIMIT 10000 DELETE r RETURN count(r) AS deleted",
                    prefix=path_prefix,
                    prefix_backslash=path_prefix_backslash,
                    path=repo_path_str,
'''
CGC_DELETE_NODE_ORIGINAL_SNIPPET = '''                    result = session.run(
                        f"MATCH (n:{label}) WHERE n.path STARTS WITH $prefix "
                        "WITH n LIMIT 10000 DETACH DELETE n RETURN count(n) AS deleted",
                        prefix=path_prefix,
                    ).single()
'''
CGC_DELETE_NODE_PATCHED_SNIPPET = '''                    result = session.run(
                        f"MATCH (n:{label}) WHERE n.path STARTS WITH $prefix OR n.path STARTS WITH $prefix_backslash "
                        "WITH n LIMIT 10000 DETACH DELETE n RETURN count(n) AS deleted",
                        prefix=path_prefix,
                        prefix_backslash=path_prefix_backslash,
                    ).single()
'''
CGC_GRAPH_BUILDER_EXTENSIONS_PATCH_MARKER = "Agents Remember patch: include TensorFlow C++ source extensions"
CGC_GRAPH_BUILDER_TABLEGEN_PATCH_MARKER = "Agents Remember patch: keep TensorFlow TableGen files discoverable"
CGC_GRAPH_BUILDER_PARSER_ORIGINAL_SNIPPET = '''            ".cpp": "cpp",
            ".h": "cpp",
'''
CGC_GRAPH_BUILDER_PARSER_PATCHED_SNIPPET = f'''            ".cpp": "cpp",
            # {CGC_GRAPH_BUILDER_EXTENSIONS_PATCH_MARKER}.
            ".cc": "cpp",
            ".cxx": "cpp",
            ".c++": "cpp",
            ".C": "cpp",
            ".h": "cpp",
'''
CGC_GRAPH_BUILDER_GENERIC_ORIGINAL_SNIPPET = '''        self.generic_extensions = {
            ".toml", ".sh", ".yaml", ".yml", ".json", ".ini", ".cfg", ".md", ".txt", ".env",
            ".bat", ".ps1", ".dockerignore", ".gitignore"
        }
'''
CGC_GRAPH_BUILDER_GENERIC_PATCHED_SNIPPET = f'''        self.generic_extensions = {{
            ".toml", ".sh", ".yaml", ".yml", ".json", ".ini", ".cfg", ".md", ".txt", ".env",
            ".bat", ".ps1", ".dockerignore", ".gitignore",
            # {CGC_GRAPH_BUILDER_TABLEGEN_PATCH_MARKER}.
            ".td",
        }}
'''
CGC_GRAPH_BUILDER_PRESCAN_ORIGINAL_SNIPPET = '''        if '.cpp' in files_by_lang:
            from .languages import cpp as cpp_lang_module
            imports_map.update(cpp_lang_module.pre_scan_cpp(files_by_lang['.cpp'], self.get_parser('.cpp')))
        if '.h' in files_by_lang:
            from .languages import cpp as cpp_lang_module
            imports_map.update(cpp_lang_module.pre_scan_cpp(files_by_lang['.h'], self.get_parser('.h')))
        if '.hpp' in files_by_lang:
            from .languages import cpp as cpp_lang_module
            imports_map.update(cpp_lang_module.pre_scan_cpp(files_by_lang['.hpp'], self.get_parser('.hpp')))
        if '.hh' in files_by_lang:
            from .languages import cpp as cpp_lang_module
            imports_map.update(cpp_lang_module.pre_scan_cpp(files_by_lang['.hh'], self.get_parser('.hh')))
'''
CGC_GRAPH_BUILDER_PRESCAN_PATCHED_SNIPPET = '''        cpp_files = []
        for cpp_ext in ('.cpp', '.cc', '.cxx', '.c++', '.C', '.h', '.hpp', '.hh'):
            cpp_files.extend(files_by_lang.get(cpp_ext, []))
        if cpp_files:
            from .languages import cpp as cpp_lang_module
            imports_map.update(cpp_lang_module.pre_scan_cpp(cpp_files, self.get_parser('.cpp')))
'''
CGC_DISCOVERY_GENERIC_ORIGINAL_SNIPPET = '''_GENERIC_EXTENSIONS: FrozenSet[str] = frozenset({
    ".toml", ".sh", ".yaml", ".yml", ".json", ".ini", ".cfg",
    ".md", ".txt", ".env", ".bat", ".ps1", ".dockerignore", ".gitignore",
})
'''
CGC_DISCOVERY_GENERIC_PATCHED_SNIPPET = f'''_GENERIC_EXTENSIONS: FrozenSet[str] = frozenset({{
    ".toml", ".sh", ".yaml", ".yml", ".json", ".ini", ".cfg",
    ".md", ".txt", ".env", ".bat", ".ps1", ".dockerignore", ".gitignore",
    # {CGC_GRAPH_BUILDER_TABLEGEN_PATCH_MARKER}.
    ".td",
}})
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
    cgcignore_patterns: tuple[str, ...]
    config_file: Path
    env_file: Path
    backend_root: Path
    backend_data_root: Path
    backend_state_file: Path
    run_root: Path
    logs_root: Path
    process_env_template: dict[str, str] | None
    watch_cwd: Path
    watch_log_file: Path

    def env(self) -> dict[str, str]:
        """Return the environment required to keep CGC under the runtime root."""

        defaults = {
            "HOME": (self.run_root / "home").as_posix(),
            "CGC_RUNTIME_DB_TYPE": "falkordb-remote",
            "DEFAULT_DATABASE": "falkordb-remote",
            "FALKORDB_HOST": os.environ.get("FALKORDB_HOST", CGC_FALKORDB_DEFAULT_HOST),
            "FALKORDB_PORT": os.environ.get("FALKORDB_PORT", CGC_FALKORDB_DEFAULT_PORT),
            "FALKORDB_GRAPH_NAME": f"cgc_{self.repo_id.replace('-', '_')}",
            "LOG_FILE_PATH": (self.logs_root / "cgc.log").as_posix(),
            "DEBUG_LOG_PATH": (self.logs_root / "debug.log").as_posix(),
            "ENABLE_AUTO_WATCH": "false",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        }
        if os.name == "nt":
            defaults.update(
                {
                    "USERPROFILE": str(self.run_root / "home"),
                    "APPDATA": str(self.run_root / "appdata"),
                    "LOCALAPPDATA": str(self.run_root / "localappdata"),
                }
            )
        if self.process_env_template is None:
            return defaults

        variables = {
            "repoId": self.repo_id,
            "repoGraphId": self.repo_id.replace("-", "_"),
            "runtimeRoot": self.runtime_root.parent.as_posix(),
            "instanceRoot": self.runtime_root.as_posix(),
            "backendRuntimeRoot": self.backend_root.as_posix(),
            "backendDataRoot": self.backend_data_root.as_posix(),
        }
        env = {
            key: expand_template(str(value), variables)
            for key, value in self.process_env_template.items()
        }
        return env

    def cgc_executable(self) -> Path:
        if os.name == "nt":
            return self.venv_root / "Scripts" / "cgc.exe"
        return self.venv_root / "bin" / "cgc"


def stable_provider_id(value: str) -> str:
    """Return a stable provider id component."""

    lowered = value.strip().lower()
    slug = re.sub(r"[^a-z0-9._-]+", "-", lowered).strip(".-_")
    return slug or "repo"


def expand_template(value: str, variables: dict[str, str]) -> str:
    """Expand ``<name>`` tokens using the provided values."""

    expanded = value
    for key, replacement in variables.items():
        expanded = expanded.replace(f"<{key}>", replacement)
    return expanded


def provider_requirements_file(coordination_root: Path, provider: str) -> Path:
    """Return the copied runtime requirements file for a provider."""

    return coordination_root.resolve() / "providers" / "requirements" / f"{provider}.txt"


def ensure_provider_requirements_file(coordination_root: Path, provider: str, pin: str) -> Path:
    """Create the copied provider requirements file when an older runtime lacks it."""

    requirements_file = provider_requirements_file(coordination_root, provider)
    requirements_file.parent.mkdir(parents=True, exist_ok=True)
    if not requirements_file.exists():
        requirements_file.write_text(f"{pin}\n", encoding="utf-8")
    return requirements_file


def read_provider_pin(requirements_file: Path, package_name: str) -> str:
    """Read a single pinned requirement such as ``grepai==0.35.0``."""

    if not requirements_file.exists():
        raise ContextProviderError(f"provider requirements file does not exist: {requirements_file}")

    package_name = package_name.lower()
    pins: list[str] = []
    for raw_line in requirements_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if not line.lower().startswith(f"{package_name}=="):
            raise ContextProviderError(
                f"unsupported requirement in {requirements_file}: {line}; expected {package_name}==<version>"
            )
        pins.append(line.split("==", 1)[1].strip())

    if len(pins) != 1 or not pins[0]:
        raise ContextProviderError(f"expected exactly one {package_name} pin in {requirements_file}")
    return pins[0]


def ensure_grepai_requirements_file(coordination_root: Path) -> Path:
    return ensure_provider_requirements_file(coordination_root, GREPAI_PROVIDER, GREPAI_PIN)


def cgc_runtime_layout(
    *,
    coordination_root: Path,
    repo_id: str,
    code_repo_root: Path,
    runtime_root: Path | None = None,
    venv_root: Path | None = None,
    requirements_file: Path | None = None,
    patches_root: Path | None = None,
    state_file: Path | None = None,
    backend_root: Path | None = None,
    backend_data_root: Path | None = None,
    backend_state_file: Path | None = None,
    process_env_template: dict[str, str] | None = None,
    cgcignore_patterns: tuple[str, ...] = (),
    watch_cwd: Path | None = None,
    watch_log_file: Path | None = None,
) -> CgcRuntimeLayout:
    """Build the managed CGC runtime layout for one code repository."""

    coordination_root = coordination_root.resolve()
    repo_id = stable_provider_id(repo_id)
    providers_root = coordination_root / "providers"
    provider_data_root = coordination_root / "provider-data"
    runtime_root = (runtime_root or providers_root / CGC_PROVIDER / repo_id).resolve()
    cgc_root = runtime_root / ".codegraphcontext"
    backend_root = (backend_root or provider_data_root / CGC_PROVIDER / "falkordb").resolve()
    backend_data_root = (backend_data_root or backend_root / "data").resolve()
    return CgcRuntimeLayout(
        coordination_root=coordination_root,
        repo_id=repo_id,
        code_repo_root=code_repo_root.resolve(),
        providers_root=providers_root,
        runtime_root=runtime_root,
        cgc_root=cgc_root,
        venv_root=(venv_root or providers_root / "_venvs" / CGC_PROVIDER).resolve(),
        requirements_file=(requirements_file or provider_requirements_file(coordination_root, CGC_PROVIDER)).resolve(),
        patches_root=(patches_root or providers_root / "patches" / CGC_PROVIDER).resolve(),
        state_file=(state_file or runtime_root / "provider-state.json").resolve(),
        cgcignore_path=cgc_root / ".cgcignore",
        cgcignore_patterns=tuple(pattern for pattern in cgcignore_patterns if pattern),
        config_file=cgc_root / "config.yaml",
        env_file=cgc_root / ".env",
        backend_root=backend_root,
        backend_data_root=backend_data_root,
        backend_state_file=(backend_state_file or backend_root / "backend-state.json").resolve(),
        run_root=cgc_root / "run",
        logs_root=cgc_root / "logs",
        process_env_template=process_env_template,
        watch_cwd=(watch_cwd or runtime_root).resolve(),
        watch_log_file=(watch_log_file or cgc_root / "logs" / "watch.log").resolve(),
    )


def cgc_runtime_layout_from_provider_settings(
    *,
    coordination_root: Path,
    provider_settings: dict[str, Any],
    root_settings: dict[str, Any],
) -> CgcRuntimeLayout:
    """Build a CGC runtime layout from a codegraphcontext-code settings entry."""

    coordination_root = coordination_root.resolve()
    repo_id = stable_provider_id(str(root_settings["repoId"]))
    base_variables = {
        "coordination_root": coordination_root.as_posix(),
        "workspace_root": coordination_root.parent.as_posix(),
    }
    code_repo_root = Path(expand_template(str(root_settings["path"]), base_variables))
    if "<" in code_repo_root.as_posix() or ">" in code_repo_root.as_posix():
        raise ContextProviderError(f"unresolved codegraphcontext root path placeholder: {code_repo_root.as_posix()}")
    if not code_repo_root.exists() or not code_repo_root.is_dir():
        raise ContextProviderError(f"codegraphcontext root path does not exist or is not a directory: {code_repo_root.as_posix()}")
    provider_runtime_root = Path(
        expand_template(
            str(provider_settings["runtimeRoot"]),
            base_variables,
        )
    ).resolve()
    instance_root = Path(
        expand_template(
            str(provider_settings.get("instanceRootTemplate", "<runtimeRoot>/<repoId>")),
            {"runtimeRoot": provider_runtime_root.as_posix(), "repoId": repo_id},
        )
    ).resolve()

    backend_settings = provider_settings.get("backend", {})
    backend_runtime_root = Path(
        expand_template(
            str(backend_settings.get("runtimeRoot", "<coordination_root>/provider-data/codegraphcontext/falkordb")),
            {
                "coordination_root": coordination_root.as_posix(),
                "runtimeRoot": provider_runtime_root.as_posix(),
            },
        )
    ).resolve()
    backend_data_root = Path(
        expand_template(
            str(backend_settings.get("dataRoot", "<backendRuntimeRoot>/data")),
            {
                "coordination_root": coordination_root.as_posix(),
                "backendRuntimeRoot": backend_runtime_root.as_posix(),
                "runtimeRoot": provider_runtime_root.as_posix(),
            },
        )
    ).resolve()
    backend_state = {}
    backend_state_file = backend_runtime_root / "backend-state.json"
    if backend_state_file.exists():
        try:
            backend_state = json.loads(backend_state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            backend_state = {}
    state_ports = backend_state.get("backend", {}).get("ports", {}) if isinstance(backend_state, dict) else {}
    state_falkordb_port = state_ports.get("falkordb", {}) if isinstance(state_ports, dict) else {}
    if not isinstance(state_falkordb_port, dict):
        state_falkordb_port = {}
    ports = backend_settings.get("ports", {})
    falkordb_port = ports.get("falkordb", {}) if isinstance(ports, dict) else {}
    backend_bind_host = str(state_falkordb_port.get("bindHost", falkordb_port.get("bindHost", CGC_FALKORDB_DEFAULT_HOST)))
    backend_host_port = str(state_falkordb_port.get("hostPort", falkordb_port.get("hostPort", CGC_FALKORDB_DEFAULT_PORT)))
    if backend_host_port == "auto":
        backend_host_port = os.environ.get("FALKORDB_PORT", CGC_FALKORDB_DEFAULT_PORT)
    process_env_template = None
    if isinstance(provider_settings.get("processEnvTemplate"), dict):
        process_env_variables = {
            "backend.ports.falkordb.bindHost": backend_bind_host,
            "backend.ports.falkordb.hostPort": backend_host_port,
        }
        process_env_template = {
            str(key): expand_template(str(value), process_env_variables)
            for key, value in provider_settings["processEnvTemplate"].items()
        }
    provider_cgcignore_patterns = provider_settings.get("cgcignorePatterns", [])
    root_cgcignore_patterns = root_settings.get("cgcignorePatterns", [])
    cgcignore_patterns: list[str] = []
    for pattern_group in (provider_cgcignore_patterns, root_cgcignore_patterns):
        if isinstance(pattern_group, list):
            cgcignore_patterns.extend(str(pattern).strip() for pattern in pattern_group if str(pattern).strip())
    watch_settings = provider_settings.get("watch", {})
    watch_cwd = Path(
        expand_template(
            str(watch_settings.get("cwdTemplate", "<instanceRoot>")),
            {"instanceRoot": instance_root.as_posix(), "runtimeRoot": provider_runtime_root.as_posix(), "repoId": repo_id},
        )
    ).resolve()
    watch_log_file = Path(
        expand_template(
            str(watch_settings.get("logFileTemplate", "<instanceRoot>/.codegraphcontext/logs/watch.log")),
            {"instanceRoot": instance_root.as_posix(), "runtimeRoot": provider_runtime_root.as_posix(), "repoId": repo_id},
        )
    ).resolve()
    state_file = Path(
        expand_template(
            str(provider_settings.get("stateFileTemplate", "<instanceRoot>/provider-state.json")),
            {"instanceRoot": instance_root.as_posix(), "runtimeRoot": provider_runtime_root.as_posix(), "repoId": repo_id},
        )
    ).resolve()

    return cgc_runtime_layout(
        coordination_root=coordination_root,
        repo_id=repo_id,
        code_repo_root=code_repo_root,
        runtime_root=instance_root,
        venv_root=Path(
            expand_template(
                str(provider_settings["venvRoot"]),
                base_variables,
            )
        ),
        requirements_file=Path(
            expand_template(
                str(provider_settings["requirementsFile"]),
                base_variables,
            )
        ),
        patches_root=Path(
            expand_template(
                str(provider_settings["patchesRoot"]),
                base_variables,
            )
        ),
        state_file=state_file,
        backend_root=backend_runtime_root,
        backend_data_root=backend_data_root,
        process_env_template=process_env_template,
        cgcignore_patterns=tuple(cgcignore_patterns),
        watch_cwd=watch_cwd,
        watch_log_file=watch_log_file,
    )


def ensure_cgc_runtime_layout(layout: CgcRuntimeLayout) -> None:
    """Create runtime directories and default CGC config files."""

    for path in [
        layout.venv_root,
        layout.requirements_file.parent,
        layout.patches_root,
        layout.cgc_root,
        layout.backend_data_root,
        layout.backend_state_file.parent,
        layout.run_root,
        layout.run_root / "home",
        layout.run_root / "appdata",
        layout.run_root / "localappdata",
        layout.logs_root,
    ]:
        path.mkdir(parents=True, exist_ok=True)

    if not layout.requirements_file.exists():
        layout.requirements_file.write_text("\n".join(CGC_REQUIREMENTS) + "\n", encoding="utf-8")
    cgcignore_lines = DEFAULT_CGCIGNORE.rstrip("\n").splitlines()
    gitignore_patterns = read_gitignore_patterns(layout.code_repo_root)
    if gitignore_patterns:
        cgcignore_lines.extend(["", "# Inherited from source .gitignore"])
        cgcignore_lines.extend(gitignore_patterns)
    if layout.cgcignore_patterns:
        cgcignore_lines.extend(["", "# Repo-specific managed exclusions"])
        cgcignore_lines.extend(layout.cgcignore_patterns)
    layout.cgcignore_path.write_text("\n".join(cgcignore_lines) + "\n", encoding="utf-8")
    layout.config_file.write_text("database: falkordb-remote\n", encoding="utf-8")
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


def _remove_runtime_path(path: Path, dry_run: bool) -> None:
    if dry_run:
        return
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def cleanup_cgc_runtime_artifacts(layouts: list[CgcRuntimeLayout], *, dry_run: bool = False) -> list[dict[str, str]]:
    """Remove stale CGC runtime artifacts that are outside the desired layout."""

    if not layouts:
        return []

    provider_root = layouts[0].runtime_root.parent.resolve()
    configured_roots = {layout.runtime_root.resolve() for layout in layouts}
    removals: list[dict[str, str]] = []

    for child in sorted(provider_root.iterdir()) if provider_root.exists() else []:
        if not child.is_dir():
            continue
        resolved_child = child.resolve()
        if resolved_child in configured_roots:
            continue
        if not resolved_child.is_relative_to(provider_root):
            raise ContextProviderError(f"refusing to remove CGC path outside provider root: {child}")
        generated_instance = (child / ".codegraphcontext").exists() or (child / "provider-state.json").exists()
        legacy_provider_dir = child.name.startswith("_")
        if generated_instance or legacy_provider_dir:
            removals.append({"path": child.as_posix(), "reason": "unconfigured-cgc-instance"})
            _remove_runtime_path(child, dry_run)

    obsolete_names = ("db", "global", "kuzu", "kuzu.wal")
    for layout in layouts:
        cgc_root = layout.cgc_root.resolve()
        for name in obsolete_names:
            target = (layout.cgc_root / name).resolve()
            if not target.exists():
                continue
            if not target.is_relative_to(cgc_root):
                raise ContextProviderError(f"refusing to remove CGC path outside instance root: {target}")
            removals.append({"path": target.as_posix(), "reason": f"legacy-embedded-{name}"})
            _remove_runtime_path(target, dry_run)

    return removals


def find_cgc_cgcignore_module(venv_root: Path) -> Path:
    """Find CodeGraphContext's cgcignore.py in a provider venv."""

    patterns = [
        "lib/python*/site-packages/codegraphcontext/core/cgcignore.py",
        "Lib/site-packages/codegraphcontext/core/cgcignore.py",
    ]
    matches = sorted({path.resolve() for pattern in patterns for path in venv_root.glob(pattern)})
    if not matches:
        raise ContextProviderError(f"could not find CodeGraphContext cgcignore.py under {venv_root}")
    if len(matches) > 1:
        raise ContextProviderError(f"multiple CodeGraphContext cgcignore.py files found under {venv_root}")
    return matches[0]


def find_cgc_writer_module(venv_root: Path) -> Path:
    """Find CodeGraphContext's graph writer module in a provider venv."""

    patterns = [
        "lib/python*/site-packages/codegraphcontext/tools/indexing/persistence/writer.py",
        "Lib/site-packages/codegraphcontext/tools/indexing/persistence/writer.py",
    ]
    matches = sorted({path.resolve() for pattern in patterns for path in venv_root.glob(pattern)})
    if not matches:
        raise ContextProviderError(f"could not find CodeGraphContext writer.py under {venv_root}")
    if len(matches) > 1:
        raise ContextProviderError(f"multiple CodeGraphContext writer.py files found under {venv_root}")
    return matches[0]


def find_cgc_graph_builder_module(venv_root: Path) -> Path:
    """Find CodeGraphContext's graph_builder.py in a provider venv."""

    patterns = [
        "lib/python*/site-packages/codegraphcontext/tools/graph_builder.py",
        "Lib/site-packages/codegraphcontext/tools/graph_builder.py",
    ]
    matches = sorted({path.resolve() for pattern in patterns for path in venv_root.glob(pattern)})
    if not matches:
        raise ContextProviderError(f"could not find CodeGraphContext graph_builder.py under {venv_root}")
    if len(matches) > 1:
        raise ContextProviderError(f"multiple CodeGraphContext graph_builder.py files found under {venv_root}")
    return matches[0]


def find_cgc_discovery_module(venv_root: Path) -> Path:
    """Find CodeGraphContext's discovery.py in a provider venv."""

    patterns = [
        "lib/python*/site-packages/codegraphcontext/tools/indexing/discovery.py",
        "Lib/site-packages/codegraphcontext/tools/indexing/discovery.py",
    ]
    matches = sorted({path.resolve() for pattern in patterns for path in venv_root.glob(pattern)})
    if not matches:
        raise ContextProviderError(f"could not find CodeGraphContext discovery.py under {venv_root}")
    if len(matches) > 1:
        raise ContextProviderError(f"multiple CodeGraphContext discovery.py files found under {venv_root}")
    return matches[0]


def cgc_cgcignore_patch_applied(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    return CGC_PATCH_MARKER in text and "if not local_cgcignore_path.exists():" in text


def apply_cgc_cgcignore_patch(path: Path) -> bool:
    """Patch CGC so an explicit .cgcignore path wins before repo-local creation.

    Returns true when the file was changed and false when the patch was already
    present.
    """

    text = path.read_text(encoding="utf-8")
    if cgc_cgcignore_patch_applied(path):
        return False
    if CGC_OLD_PATCHED_SNIPPET in text:
        path.write_text(text.replace(CGC_OLD_PATCHED_SNIPPET, CGC_PATCHED_SNIPPET), encoding="utf-8")
        return True
    if CGC_ORIGINAL_SNIPPET not in text:
        raise ContextProviderError("CGC cgcignore.py did not match the expected unpatched snippet")
    path.write_text(text.replace(CGC_ORIGINAL_SNIPPET, CGC_PATCHED_SNIPPET), encoding="utf-8")
    return True


def cgc_delete_patch_applied(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    return CGC_DELETE_PATCH_MARKER in text and "prefix_backslash=path_prefix_backslash" in text


def apply_cgc_delete_patch(path: Path) -> bool:
    """Patch CGC repository deletion so Windows child paths are removed on --force."""

    text = path.read_text(encoding="utf-8")
    if cgc_delete_patch_applied(path):
        return False

    replacements = [
        (CGC_DELETE_PREFIX_ORIGINAL_SNIPPET, CGC_DELETE_PREFIX_PATCHED_SNIPPET),
        (CGC_DELETE_REL_ORIGINAL_SNIPPET, CGC_DELETE_REL_PATCHED_SNIPPET),
        (CGC_DELETE_CONTAINS_ORIGINAL_SNIPPET, CGC_DELETE_CONTAINS_PATCHED_SNIPPET),
        (CGC_DELETE_NODE_ORIGINAL_SNIPPET, CGC_DELETE_NODE_PATCHED_SNIPPET),
    ]
    for original, patched in replacements:
        if original not in text:
            raise ContextProviderError("CGC writer.py did not match the expected unpatched delete snippet")
        text = text.replace(original, patched, 1)

    path.write_text(text, encoding="utf-8")
    return True


def cgc_graph_builder_extensions_patch_applied(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    return (
        CGC_GRAPH_BUILDER_EXTENSIONS_PATCH_MARKER in text
        and CGC_GRAPH_BUILDER_TABLEGEN_PATCH_MARKER in text
        and '".cc": "cpp"' in text
        and '".td",' in text
        and "for cpp_ext in ('.cpp', '.cc', '.cxx', '.c++', '.C', '.h', '.hpp', '.hh')" in text
    )


def apply_cgc_graph_builder_extensions_patch(path: Path) -> bool:
    """Patch CGC graph builder discovery for TensorFlow C++ and TableGen files."""

    text = path.read_text(encoding="utf-8")
    if cgc_graph_builder_extensions_patch_applied(path):
        return False

    replacements = [
        (CGC_GRAPH_BUILDER_PARSER_ORIGINAL_SNIPPET, CGC_GRAPH_BUILDER_PARSER_PATCHED_SNIPPET),
        (CGC_GRAPH_BUILDER_GENERIC_ORIGINAL_SNIPPET, CGC_GRAPH_BUILDER_GENERIC_PATCHED_SNIPPET),
        (CGC_GRAPH_BUILDER_PRESCAN_ORIGINAL_SNIPPET, CGC_GRAPH_BUILDER_PRESCAN_PATCHED_SNIPPET),
    ]
    for original, patched in replacements:
        if original not in text:
            raise ContextProviderError("CGC graph_builder.py did not match the expected unpatched extension snippet")
        text = text.replace(original, patched, 1)

    path.write_text(text, encoding="utf-8")
    return True


def cgc_discovery_extensions_patch_applied(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    return CGC_GRAPH_BUILDER_TABLEGEN_PATCH_MARKER in text and '".td",' in text


def apply_cgc_discovery_extensions_patch(path: Path) -> bool:
    """Patch CGC file discovery so TensorFlow TableGen files become File nodes."""

    text = path.read_text(encoding="utf-8")
    if cgc_discovery_extensions_patch_applied(path):
        return False
    if CGC_DISCOVERY_GENERIC_ORIGINAL_SNIPPET not in text:
        raise ContextProviderError("CGC discovery.py did not match the expected unpatched generic extension snippet")
    path.write_text(
        text.replace(CGC_DISCOVERY_GENERIC_ORIGINAL_SNIPPET, CGC_DISCOVERY_GENERIC_PATCHED_SNIPPET, 1),
        encoding="utf-8",
    )
    return True
