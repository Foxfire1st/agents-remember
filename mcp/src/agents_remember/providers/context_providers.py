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
CGC_VIZ_REPO_QUERY_PATCH_ID = "codegraphcontext-0.4.10-viz-repo-query-v1"
CGC_VIZ_SERVER_ROUTE_PATCH_ID = "codegraphcontext-0.4.10-viz-server-route-v1"
CGC_VIZ_CLI_ROUTE_PATCH_ID = "codegraphcontext-0.4.10-viz-cli-route-v1"
CGC_FALKORDB_BACKEND_ID = "codegraphcontext-falkordb"
CGC_FALKORDB_CONTAINER_NAME = "ar-cgc-falkordb"
CGC_FALKORDB_DEFAULT_HOST = "127.0.0.1"
CGC_FALKORDB_DEFAULT_PORT = "6379"
GREPAI_PROVIDER = "grepai"
GREPAI_PIN = "grepai==0.35.0"
GREPAI_POSTGRES_BACKEND_ID = "grepai-postgres"
GREPAI_POSTGRES_CONTAINER_NAME = "ar-grepai-postgres"
GREPAI_POSTGRES_DEFAULT_HOST = "127.0.0.1"
GREPAI_POSTGRES_DEFAULT_PORT = "5432"
SOURCE_ARTIFACT_NAMES = (".cgcignore", ".codegraphcontext", "CGC_REPORT.md")
GREPAI_ROOT_ARTIFACT_NAMES = (".grepai",)
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
CGC_ORIGINAL_SNIPPET = """    if local_cgcignore_path is None:
        local_cgcignore_path = ignore_root / ".cgcignore"
        ensure_default_cgcignore(local_cgcignore_path, default_patterns)
"""
CGC_OLD_PATCHED_SNIPPET = """    if local_cgcignore_path is None:
        # Agents Remember patch: prefer explicit .cgcignore path before repo-local creation.
        if explicit_cgcignore_path is not None:
            local_cgcignore_path = explicit_cgcignore_path
        else:
            local_cgcignore_path = ignore_root / ".cgcignore"
        ensure_default_cgcignore(local_cgcignore_path, default_patterns)
"""
CGC_PATCHED_SNIPPET = f"""    if local_cgcignore_path is None:
        # {CGC_PATCH_MARKER}.
        if explicit_cgcignore_path is not None:
            local_cgcignore_path = explicit_cgcignore_path
        else:
            local_cgcignore_path = ignore_root / ".cgcignore"
        if not local_cgcignore_path.exists():
            ensure_default_cgcignore(local_cgcignore_path, default_patterns)
"""

CGC_DELETE_PATCH_MARKER = (
    "Agents Remember patch: delete repository paths with slash and backslash child prefixes"
)
CGC_DELETE_PREFIX_ORIGINAL_SNIPPET = """        repo_path_str = repo_path
        path_prefix = repo_path_str + "/"
        with self.driver.session() as session:
"""
CGC_DELETE_PREFIX_PATCHED_SNIPPET = f"""        repo_path_str = repo_path
        path_prefix = repo_path_str + "/"
        # {CGC_DELETE_PATCH_MARKER}.
        path_prefix_backslash = repo_path_str + "\\\\"
        with self.driver.session() as session:
"""
CGC_DELETE_REL_ORIGINAL_SNIPPET = """                    result = session.run(
                        f"MATCH (a)-[r:{rel_type}]->(b) "
                        "WHERE a.path STARTS WITH $prefix OR b.path STARTS WITH $prefix "
                        "WITH r LIMIT 5000 DELETE r RETURN count(r) AS deleted",
                        prefix=path_prefix,
                    ).single()
"""
CGC_DELETE_REL_PATCHED_SNIPPET = """                    result = session.run(
                        f"MATCH (a)-[r:{rel_type}]->(b) "
                        "WHERE a.path STARTS WITH $prefix OR b.path STARTS WITH $prefix "
                        "OR a.path STARTS WITH $prefix_backslash OR b.path STARTS WITH $prefix_backslash "
                        "WITH r LIMIT 5000 DELETE r RETURN count(r) AS deleted",
                        prefix=path_prefix,
                        prefix_backslash=path_prefix_backslash,
                    ).single()
"""
CGC_DELETE_CONTAINS_ORIGINAL_SNIPPET = """                    "MATCH (a)-[r:CONTAINS]->(b) "
                    "WHERE a.path STARTS WITH $prefix OR a.path = $path "
                    "WITH r LIMIT 10000 DELETE r RETURN count(r) AS deleted",
                    prefix=path_prefix,
                    path=repo_path_str,
"""
CGC_DELETE_CONTAINS_PATCHED_SNIPPET = """                    "MATCH (a)-[r:CONTAINS]->(b) "
                    "WHERE a.path STARTS WITH $prefix OR a.path STARTS WITH $prefix_backslash OR a.path = $path "
                    "WITH r LIMIT 10000 DELETE r RETURN count(r) AS deleted",
                    prefix=path_prefix,
                    prefix_backslash=path_prefix_backslash,
                    path=repo_path_str,
"""
CGC_DELETE_NODE_ORIGINAL_SNIPPET = """                    result = session.run(
                        f"MATCH (n:{label}) WHERE n.path STARTS WITH $prefix "
                        "WITH n LIMIT 10000 DETACH DELETE n RETURN count(n) AS deleted",
                        prefix=path_prefix,
                    ).single()
"""
CGC_DELETE_NODE_PATCHED_SNIPPET = """                    result = session.run(
                        f"MATCH (n:{label}) WHERE n.path STARTS WITH $prefix OR n.path STARTS WITH $prefix_backslash "
                        "WITH n LIMIT 10000 DETACH DELETE n RETURN count(n) AS deleted",
                        prefix=path_prefix,
                        prefix_backslash=path_prefix_backslash,
                    ).single()
"""
CGC_GRAPH_BUILDER_EXTENSIONS_PATCH_MARKER = (
    "Agents Remember patch: include TensorFlow C++ source extensions"
)
CGC_GRAPH_BUILDER_TABLEGEN_PATCH_MARKER = (
    "Agents Remember patch: keep TensorFlow TableGen files discoverable"
)
CGC_GRAPH_BUILDER_PARSER_ORIGINAL_SNIPPET = """            ".cpp": "cpp",
            ".h": "cpp",
"""
CGC_GRAPH_BUILDER_PARSER_PATCHED_SNIPPET = f"""            ".cpp": "cpp",
            # {CGC_GRAPH_BUILDER_EXTENSIONS_PATCH_MARKER}.
            ".cc": "cpp",
            ".cxx": "cpp",
            ".c++": "cpp",
            ".C": "cpp",
            ".h": "cpp",
"""
CGC_GRAPH_BUILDER_GENERIC_ORIGINAL_SNIPPET = """        self.generic_extensions = {
            ".toml", ".sh", ".yaml", ".yml", ".json", ".ini", ".cfg", ".md", ".txt", ".env",
            ".bat", ".ps1", ".dockerignore", ".gitignore"
        }
"""
CGC_GRAPH_BUILDER_GENERIC_PATCHED_SNIPPET = f"""        self.generic_extensions = {{
            ".toml", ".sh", ".yaml", ".yml", ".json", ".ini", ".cfg", ".md", ".txt", ".env",
            ".bat", ".ps1", ".dockerignore", ".gitignore",
            # {CGC_GRAPH_BUILDER_TABLEGEN_PATCH_MARKER}.
            ".td",
        }}
"""
CGC_GRAPH_BUILDER_PRESCAN_ORIGINAL_SNIPPET = """        if '.cpp' in files_by_lang:
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
"""
CGC_GRAPH_BUILDER_PRESCAN_PATCHED_SNIPPET = """        cpp_files = []
        for cpp_ext in ('.cpp', '.cc', '.cxx', '.c++', '.C', '.h', '.hpp', '.hh'):
            cpp_files.extend(files_by_lang.get(cpp_ext, []))
        if cpp_files:
            from .languages import cpp as cpp_lang_module
            imports_map.update(cpp_lang_module.pre_scan_cpp(cpp_files, self.get_parser('.cpp')))
"""
CGC_DISCOVERY_GENERIC_ORIGINAL_SNIPPET = """_GENERIC_EXTENSIONS: FrozenSet[str] = frozenset({
    ".toml", ".sh", ".yaml", ".yml", ".json", ".ini", ".cfg",
    ".md", ".txt", ".env", ".bat", ".ps1", ".dockerignore", ".gitignore",
})
"""
CGC_DISCOVERY_GENERIC_PATCHED_SNIPPET = f"""_GENERIC_EXTENSIONS: FrozenSet[str] = frozenset({{
    ".toml", ".sh", ".yaml", ".yml", ".json", ".ini", ".cfg",
    ".md", ".txt", ".env", ".bat", ".ps1", ".dockerignore", ".gitignore",
    # {CGC_GRAPH_BUILDER_TABLEGEN_PATCH_MARKER}.
    ".td",
}})
"""
CGC_VIZ_REPO_QUERY_PATCH_MARKER = (
    "Agents Remember patch: bound visualizer repo graph query by path prefix"
)
CGC_VIZ_REPO_QUERY_ORIGINAL_SNIPPET = '''                # Get all nodes within the repository scope
                query = """
                MATCH (r:Repository {path: $repo_path})
                OPTIONAL MATCH (r)-[:CONTAINS*0..]->(n)
                WITH DISTINCT r, COLLECT(DISTINCT n) as repo_nodes
                UNWIND repo_nodes as node
                OPTIONAL MATCH (node)-[rel]->(target)
                WITH r, node, rel, target, repo_nodes
                WHERE target IN repo_nodes OR target = r
                RETURN node as n, rel, target as m
                """
                result = session.run(query, repo_path=repo_path)
'''
CGC_VIZ_REPO_QUERY_PATCHED_SNIPPET = f'''                # {CGC_VIZ_REPO_QUERY_PATCH_MARKER}.
                query = """
                WITH $repo_path AS repo_path, $repo_path + "/" AS repo_prefix
                MATCH (node)
                WHERE node.path = repo_path OR node.path STARTS WITH repo_prefix
                WITH repo_path, repo_prefix, node LIMIT 3000
                OPTIONAL MATCH (node)-[rel]->(target)
                WHERE target.path = repo_path OR target.path STARTS WITH repo_prefix
                RETURN node as n, rel, target as m
                LIMIT 5000
                """
                result = session.run(query, repo_path=repo_path)
'''
CGC_VIZ_SERVER_ROUTE_PATCH_MARKER = "Agents Remember patch: route local visualizer root to explorer"
CGC_VIZ_SERVER_RESPONSES_ORIGINAL_SNIPPET = (
    "from fastapi.responses import HTMLResponse, FileResponse\n"
)
CGC_VIZ_SERVER_RESPONSES_PATCHED_SNIPPET = (
    "from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, RedirectResponse\n"
)
CGC_VIZ_SERVER_GLOBAL_ORIGINAL_SNIPPET = """# Path to static directory
_static_dir: Optional[str] = None
"""
CGC_VIZ_SERVER_GLOBAL_PATCHED_SNIPPET = """# Path to static directory
_static_dir: Optional[str] = None
# Default SPA route used when the local server is opened at /.
_default_route: Optional[str] = None
"""
CGC_VIZ_SERVER_FALLBACK_ORIGINAL_SNIPPET = """    global _static_dir
    if not _static_dir:
        return HTMLResponse("Static directory not configured", status_code=500)
"""
CGC_VIZ_SERVER_FALLBACK_PATCHED_SNIPPET = f"""    global _static_dir, _default_route
    if full_path in ("", "/") and _default_route:
        # {CGC_VIZ_SERVER_ROUTE_PATCH_MARKER}.
        return RedirectResponse(_default_route)
    if full_path.startswith("api/"):
        return JSONResponse({{"detail": "Not found"}}, status_code=404)
    if not _static_dir:
        return HTMLResponse("Static directory not configured", status_code=500)
"""
CGC_VIZ_SERVER_RUN_ORIGINAL_SNIPPET = """def run_server(host: str = "127.0.0.1", port: int = 8000, static_dir: Optional[str] = None):
    global _static_dir
    _static_dir = static_dir
    uvicorn.run(app, host=host, port=port)
"""
CGC_VIZ_SERVER_RUN_PATCHED_SNIPPET = """def run_server(
    host: str = "127.0.0.1",
    port: int = 8000,
    static_dir: Optional[str] = None,
    default_route: Optional[str] = None,
):
    global _static_dir, _default_route
    _static_dir = static_dir
    _default_route = default_route
    uvicorn.run(app, host=host, port=port)
"""
CGC_VIZ_CLI_ROUTE_PATCH_MARKER = (
    "Agents Remember patch: make visualizer root open the explorer route"
)
CGC_VIZ_CLI_URL_ORIGINAL_SNIPPET = (
    "    query_string = urllib.parse.urlencode(params)\n"
    '    visualization_url = f"{backend_url}/explore?{query_string}"\n'
    "    \n"
    '    console.print(f"[green]Starting visualizer server on {backend_url}...[/green]")\n'
)
CGC_VIZ_CLI_URL_PATCHED_SNIPPET = (
    f"    query_string = urllib.parse.urlencode(params)\n"
    f"    # {CGC_VIZ_CLI_ROUTE_PATCH_MARKER}.\n"
    f'    default_route = f"/explore?{{query_string}}"\n'
    f'    visualization_url = f"{{backend_url}}{{default_route}}"\n'
    f"    \n"
    f'    console.print(f"[green]Starting visualizer server on {{backend_url}}...[/green]")\n'
)
CGC_VIZ_CLI_RUN_ORIGINAL_SNIPPET = """        run_server(host="127.0.0.1", port=port, static_dir=str(static_dir))
"""
CGC_VIZ_CLI_RUN_PATCHED_SNIPPET = """        run_server(host="127.0.0.1", port=port, static_dir=str(static_dir), default_route=default_route)
"""


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


@dataclass(frozen=True)
class GrepaiMemoryRoot:
    project_id: str
    path: Path
    source_path: Path | None = None


@dataclass(frozen=True)
class GrepaiRuntimeLayout:
    coordination_root: Path
    workspace_name: str
    roots: tuple[GrepaiMemoryRoot, ...]
    providers_root: Path
    runtime_root: Path
    requirements_file: Path
    binary_path: Path
    config_root: Path
    workspace_config_file: Path
    state_root: Path
    state_file: Path
    logs_root: Path
    home_root: Path
    run_root: Path
    cache_root: Path
    backend_root: Path
    backend_data_root: Path
    backend_state_file: Path

    def env(self) -> dict[str, str]:
        """Return process env that keeps GrepAI runtime state under providers/runners/grepai."""

        env = {
            "HOME": self.home_root.as_posix(),
            "XDG_STATE_HOME": (self.state_root / "xdg").as_posix(),
            "XDG_CACHE_HOME": (self.cache_root / "xdg").as_posix(),
        }
        if os.name == "nt":
            env.update(
                {
                    "USERPROFILE": str(self.home_root),
                    "APPDATA": str(self.run_root / "appdata"),
                    "LOCALAPPDATA": str(self.run_root / "localappdata"),
                }
            )
        return env


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


def provider_binary_path(coordination_root: Path, name: str) -> Path:
    """Return the runtime-owned provider binary path for this platform."""

    suffix = ".exe" if os.name == "nt" and not name.endswith(".exe") else ""
    return coordination_root.resolve() / "providers" / "_bin" / f"{name}{suffix}"


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
        raise ContextProviderError(
            f"provider requirements file does not exist: {requirements_file}"
        )

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
        raise ContextProviderError(
            f"expected exactly one {package_name} pin in {requirements_file}"
        )
    return pins[0]


def ensure_grepai_requirements_file(coordination_root: Path) -> Path:
    return ensure_provider_requirements_file(coordination_root, GREPAI_PROVIDER, GREPAI_PIN)


def grepai_runtime_layout(
    *,
    coordination_root: Path,
    workspace_name: str = "agents-remember-memory",
    roots: tuple[GrepaiMemoryRoot, ...] = (),
    runtime_root: Path | None = None,
    logs_root: Path | None = None,
    requirements_file: Path | None = None,
    state_file: Path | None = None,
    backend_root: Path | None = None,
    backend_data_root: Path | None = None,
    backend_state_file: Path | None = None,
) -> GrepaiRuntimeLayout:
    """Build the managed GrepAI runtime layout for memory-root indexing."""

    coordination_root = coordination_root.resolve()
    providers_root = coordination_root / "providers"
    provider_data_root = coordination_root / "providers" / "data"
    runtime_root = (runtime_root or providers_root / "runners" / GREPAI_PROVIDER).resolve()
    backend_root = (backend_root or provider_data_root / GREPAI_PROVIDER / "postgres").resolve()
    backend_data_root = (backend_data_root or backend_root / "data").resolve()
    workspace_name = stable_provider_id(workspace_name)
    return GrepaiRuntimeLayout(
        coordination_root=coordination_root,
        workspace_name=workspace_name,
        roots=tuple(roots),
        providers_root=providers_root,
        runtime_root=runtime_root,
        requirements_file=(
            requirements_file or provider_requirements_file(coordination_root, GREPAI_PROVIDER)
        ).resolve(),
        binary_path=provider_binary_path(coordination_root, GREPAI_PROVIDER),
        config_root=runtime_root / "config",
        workspace_config_file=runtime_root / "home" / ".grepai" / "workspace.yaml",
        state_root=runtime_root / "state",
        state_file=(state_file or runtime_root / "state" / "provider-state.json").resolve(),
        logs_root=(logs_root or runtime_root / "logs").resolve(),
        home_root=runtime_root / "home",
        run_root=runtime_root / "run",
        cache_root=runtime_root / "cache",
        backend_root=backend_root,
        backend_data_root=backend_data_root,
        backend_state_file=(backend_state_file or backend_root / "backend-state.json").resolve(),
    )


def grepai_roots_from_provider_settings(
    coordination_root: Path,
    provider_settings: dict[str, Any],
) -> tuple[GrepaiMemoryRoot, ...]:
    roots = provider_settings.get("roots")
    if not isinstance(roots, list) or not roots:
        raise ContextProviderError("grepai-memory.roots must be a non-empty array")

    base_variables = {
        "coordination_root": coordination_root.as_posix(),
        "workspace_root": coordination_root.parent.as_posix(),
    }
    normalized: list[GrepaiMemoryRoot] = []
    seen: set[str] = set()
    for root in roots:
        if isinstance(root, str):
            raw_path = root
            project_id = stable_provider_id(Path(raw_path).name)
        elif isinstance(root, dict):
            if "path" not in root:
                raise ContextProviderError("each grepai-memory root must define path")
            raw_path = str(root["path"])
            project_id = stable_provider_id(
                str(root.get("projectId") or root.get("repoId") or Path(raw_path).name)
            )
        else:
            raise ContextProviderError("each grepai-memory root must be a path string or object")

        expanded = Path(expand_template(raw_path, base_variables)).resolve()
        if "<" in expanded.as_posix() or ">" in expanded.as_posix():
            raise ContextProviderError(
                f"unresolved grepai root path placeholder: {expanded.as_posix()}"
            )
        if not expanded.exists() or not expanded.is_dir():
            raise ContextProviderError(
                f"grepai root path does not exist or is not a directory: {expanded.as_posix()}"
            )
        if project_id in seen:
            raise ContextProviderError(f"duplicate grepai project id: {project_id}")
        seen.add(project_id)
        normalized.append(GrepaiMemoryRoot(project_id=project_id, path=expanded))
    return tuple(normalized)


def grepai_runtime_layout_from_provider_settings(
    *,
    coordination_root: Path,
    provider_settings: dict[str, Any],
) -> GrepaiRuntimeLayout:
    coordination_root = coordination_root.resolve()
    base_variables = {
        "coordination_root": coordination_root.as_posix(),
        "workspace_root": coordination_root.parent.as_posix(),
    }
    provider_runtime_root = Path(
        expand_template(
            str(
                provider_settings.get("runtimeRoot", "<coordination_root>/providers/runners/grepai")
            ),
            base_variables,
        )
    ).resolve()
    backend_settings = provider_settings.get("backend", {})
    if not isinstance(backend_settings, dict):
        backend_settings = {}
    backend_runtime_root = Path(
        expand_template(
            str(
                backend_settings.get(
                    "runtimeRoot", "<coordination_root>/providers/data/grepai/postgres"
                )
            ),
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
                "runtimeRoot": provider_runtime_root.as_posix(),
                "backendRuntimeRoot": backend_runtime_root.as_posix(),
            },
        )
    ).resolve()
    requirements_file = Path(
        expand_template(
            str(
                provider_settings.get(
                    "requirementsFile", "<coordination_root>/providers/requirements/grepai.txt"
                )
            ),
            base_variables,
        )
    ).resolve()
    state_file = Path(
        expand_template(
            str(provider_settings.get("stateFile", "<runtimeRoot>/state/provider-state.json")),
            {
                "coordination_root": coordination_root.as_posix(),
                "runtimeRoot": provider_runtime_root.as_posix(),
            },
        )
    ).resolve()
    watch_settings = provider_settings.get("watch", {})
    if not isinstance(watch_settings, dict):
        watch_settings = {}
    logs_root = Path(
        expand_template(
            str(watch_settings.get("logDir", "<coordination_root>/providers/logs/grepai")),
            {
                "coordination_root": coordination_root.as_posix(),
                "runtimeRoot": provider_runtime_root.as_posix(),
            },
        )
    ).resolve()
    workspace_name = str(provider_settings.get("workspace", "agents-remember-memory"))
    roots = grepai_roots_from_provider_settings(coordination_root, provider_settings)
    mirror_roots = provider_settings.get("mirrorRoots", True) is not False
    if mirror_roots:
        roots = tuple(
            GrepaiMemoryRoot(
                project_id=root.project_id,
                path=provider_runtime_root / "index-roots" / root.project_id,
                source_path=root.path,
            )
            for root in roots
        )
    return grepai_runtime_layout(
        coordination_root=coordination_root,
        workspace_name=workspace_name,
        roots=roots,
        runtime_root=provider_runtime_root,
        logs_root=logs_root,
        requirements_file=requirements_file,
        state_file=state_file,
        backend_root=backend_runtime_root,
        backend_data_root=backend_data_root,
        backend_state_file=backend_runtime_root / "backend-state.json",
    )


def ensure_grepai_runtime_layout(layout: GrepaiRuntimeLayout) -> None:
    """Create provider-owned GrepAI runtime directories and requirement pin."""

    for path in [
        layout.runtime_root,
        layout.requirements_file.parent,
        layout.config_root,
        layout.workspace_config_file.parent,
        layout.state_root,
        layout.logs_root,
        layout.home_root,
        layout.run_root,
        layout.run_root / "appdata",
        layout.run_root / "localappdata",
        layout.cache_root,
        layout.backend_data_root,
        layout.backend_state_file.parent,
        layout.runtime_root / "index-roots",
    ]:
        path.mkdir(parents=True, exist_ok=True)
    if not layout.requirements_file.exists():
        layout.requirements_file.write_text(f"{GREPAI_PIN}\n", encoding="utf-8")


def sync_grepai_index_roots(layout: GrepaiRuntimeLayout) -> list[dict[str, str]]:
    """Refresh provider-owned GrepAI mirror roots from durable memory roots."""

    synced: list[dict[str, str]] = []
    runtime_root = layout.runtime_root.resolve()
    for root in layout.roots:
        if root.source_path is None:
            continue
        source = root.source_path.resolve()
        target = root.path.resolve()
        if not source.exists() or not source.is_dir():
            raise ContextProviderError(
                f"grepai source root does not exist or is not a directory: {source.as_posix()}"
            )
        if not target.is_relative_to(runtime_root):
            raise ContextProviderError(
                f"refusing to sync GrepAI mirror outside provider runtime root: {target.as_posix()}"
            )
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            source,
            target,
            symlinks=True,
            ignore=shutil.ignore_patterns(".git", ".grepai", "__pycache__"),
        )
        synced.append(
            {"projectId": root.project_id, "source": source.as_posix(), "path": target.as_posix()}
        )
    return synced


def _yaml_quote(value: str) -> str:
    return json.dumps(value)


def _yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return _yaml_quote(str(value))


def grepai_workspace_config_text(
    *,
    layout: GrepaiRuntimeLayout,
    dsn: str,
    embedder_settings: dict[str, Any] | None = None,
) -> str:
    if embedder_settings is None:
        embedder_settings = {}
    provider = str(embedder_settings.get("provider", "ollama"))
    model = str(embedder_settings.get("model", "nomic-embed-text"))
    lines = [
        "version: 1",
        "workspaces:",
        f"  {layout.workspace_name}:",
        f"    name: {_yaml_quote(layout.workspace_name)}",
        "    store:",
        "      backend: postgres",
        "      postgres:",
        f"        dsn: {_yaml_quote(dsn)}",
        "    embedder:",
        f"      provider: {_yaml_quote(provider)}",
        f"      model: {_yaml_quote(model)}",
    ]
    endpoint = embedder_settings.get("endpoint")
    if not endpoint:
        if provider == "ollama":
            endpoint = "http://localhost:11434"
        elif provider == "lmstudio":
            endpoint = "http://127.0.0.1:1234"
    if endpoint:
        lines.append(f"      endpoint: {_yaml_quote(str(endpoint))}")
    dimensions = embedder_settings.get("dimensions")
    if (
        dimensions is None
        and provider == "ollama"
        and model in {"nomic-embed-text", "nomic-embed-text-v2-moe"}
    ):
        dimensions = 768
    if dimensions is not None:
        lines.append(f"      dimensions: {_yaml_scalar(dimensions)}")
    lines.extend(["    projects:"])
    for root in layout.roots:
        lines.extend(
            [
                f"      - name: {_yaml_quote(root.project_id)}",
                f"        path: {_yaml_quote(root.path.as_posix())}",
            ]
        )
    return "\n".join(lines) + "\n"


def write_grepai_workspace_config(
    layout: GrepaiRuntimeLayout,
    *,
    dsn: str,
    embedder_settings: dict[str, Any] | None = None,
) -> None:
    layout.workspace_config_file.parent.mkdir(parents=True, exist_ok=True)
    layout.workspace_config_file.write_text(
        grepai_workspace_config_text(layout=layout, dsn=dsn, embedder_settings=embedder_settings),
        encoding="utf-8",
    )


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
    provider_data_root = coordination_root / "providers" / "data"
    runtime_root = (runtime_root or providers_root / "runners" / CGC_PROVIDER / repo_id).resolve()
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
        requirements_file=(
            requirements_file or provider_requirements_file(coordination_root, CGC_PROVIDER)
        ).resolve(),
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
        raise ContextProviderError(
            f"unresolved codegraphcontext root path placeholder: {code_repo_root.as_posix()}"
        )
    if not code_repo_root.exists() or not code_repo_root.is_dir():
        raise ContextProviderError(
            f"codegraphcontext root path does not exist or is not a directory: {code_repo_root.as_posix()}"
        )
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
            str(
                backend_settings.get(
                    "runtimeRoot", "<coordination_root>/providers/data/codegraphcontext/falkordb"
                )
            ),
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
    state_ports = (
        backend_state.get("backend", {}).get("ports", {}) if isinstance(backend_state, dict) else {}
    )
    state_falkordb_port = state_ports.get("falkordb", {}) if isinstance(state_ports, dict) else {}
    if not isinstance(state_falkordb_port, dict):
        state_falkordb_port = {}
    ports = backend_settings.get("ports", {})
    falkordb_port = ports.get("falkordb", {}) if isinstance(ports, dict) else {}
    backend_bind_host = str(
        state_falkordb_port.get(
            "bindHost", falkordb_port.get("bindHost", CGC_FALKORDB_DEFAULT_HOST)
        )
    )
    backend_host_port = str(
        state_falkordb_port.get(
            "hostPort", falkordb_port.get("hostPort", CGC_FALKORDB_DEFAULT_PORT)
        )
    )
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
            cgcignore_patterns.extend(
                str(pattern).strip() for pattern in pattern_group if str(pattern).strip()
            )
    watch_settings = provider_settings.get("watch", {})
    watch_cwd = Path(
        expand_template(
            str(watch_settings.get("cwdTemplate", "<instanceRoot>")),
            {
                "instanceRoot": instance_root.as_posix(),
                "runtimeRoot": provider_runtime_root.as_posix(),
                "repoId": repo_id,
            },
        )
    ).resolve()
    watch_log_file = Path(
        expand_template(
            str(
                watch_settings.get(
                    "logFileTemplate", "<instanceRoot>/.codegraphcontext/logs/watch.log"
                )
            ),
            {
                "instanceRoot": instance_root.as_posix(),
                "runtimeRoot": provider_runtime_root.as_posix(),
                "repoId": repo_id,
            },
        )
    ).resolve()
    state_file = Path(
        expand_template(
            str(provider_settings.get("stateFileTemplate", "<instanceRoot>/provider-state.json")),
            {
                "instanceRoot": instance_root.as_posix(),
                "runtimeRoot": provider_runtime_root.as_posix(),
                "repoId": repo_id,
            },
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
    layout.state_file.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


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


def grepai_root_provider_artifacts(root: Path) -> list[Path]:
    """Return GrepAI runtime artifacts that should not exist in indexed roots."""

    resolved = root.resolve()
    return [resolved / name for name in GREPAI_ROOT_ARTIFACT_NAMES if (resolved / name).exists()]


def assert_no_grepai_root_provider_artifacts(roots: tuple[GrepaiMemoryRoot, ...]) -> None:
    artifacts: list[Path] = []
    for root in roots:
        artifacts.extend(grepai_root_provider_artifacts(root.source_path or root.path))
    if artifacts:
        rendered = ", ".join(path.as_posix() for path in artifacts)
        raise ContextProviderError(f"grepai provider artifacts found in indexed roots: {rendered}")


def remove_grepai_root_provider_artifacts(
    roots: tuple[GrepaiMemoryRoot, ...],
    *,
    dry_run: bool = False,
) -> list[dict[str, str]]:
    """Remove disposable GrepAI artifacts from indexed roots after direct-child validation."""

    removals: list[dict[str, str]] = []
    for root in roots:
        root_path = (root.source_path or root.path).resolve()
        for artifact in grepai_root_provider_artifacts(root_path):
            resolved_artifact = artifact.resolve()
            if (
                artifact.name not in GREPAI_ROOT_ARTIFACT_NAMES
                or resolved_artifact.parent != root_path
            ):
                raise ContextProviderError(
                    f"refusing to remove unexpected GrepAI artifact path: {artifact.as_posix()}"
                )
            removals.append(
                {
                    "projectId": root.project_id,
                    "path": artifact.as_posix(),
                }
            )
            _remove_runtime_path(artifact, dry_run=dry_run)
    return removals


def _remove_runtime_path(path: Path, dry_run: bool) -> None:
    if dry_run:
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def cleanup_cgc_runtime_artifacts(
    layouts: list[CgcRuntimeLayout], *, dry_run: bool = False
) -> list[dict[str, str]]:
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
            raise ContextProviderError(
                f"refusing to remove CGC path outside provider root: {child}"
            )
        generated_instance = (child / ".codegraphcontext").exists() or (
            child / "provider-state.json"
        ).exists()
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
                raise ContextProviderError(
                    f"refusing to remove CGC path outside instance root: {target}"
                )
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
        raise ContextProviderError(
            f"could not find CodeGraphContext cgcignore.py under {venv_root}"
        )
    if len(matches) > 1:
        raise ContextProviderError(
            f"multiple CodeGraphContext cgcignore.py files found under {venv_root}"
        )
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
        raise ContextProviderError(
            f"multiple CodeGraphContext writer.py files found under {venv_root}"
        )
    return matches[0]


def find_cgc_graph_builder_module(venv_root: Path) -> Path:
    """Find CodeGraphContext's graph_builder.py in a provider venv."""

    patterns = [
        "lib/python*/site-packages/codegraphcontext/tools/graph_builder.py",
        "Lib/site-packages/codegraphcontext/tools/graph_builder.py",
    ]
    matches = sorted({path.resolve() for pattern in patterns for path in venv_root.glob(pattern)})
    if not matches:
        raise ContextProviderError(
            f"could not find CodeGraphContext graph_builder.py under {venv_root}"
        )
    if len(matches) > 1:
        raise ContextProviderError(
            f"multiple CodeGraphContext graph_builder.py files found under {venv_root}"
        )
    return matches[0]


def find_cgc_discovery_module(venv_root: Path) -> Path:
    """Find CodeGraphContext's discovery.py in a provider venv."""

    patterns = [
        "lib/python*/site-packages/codegraphcontext/tools/indexing/discovery.py",
        "Lib/site-packages/codegraphcontext/tools/indexing/discovery.py",
    ]
    matches = sorted({path.resolve() for pattern in patterns for path in venv_root.glob(pattern)})
    if not matches:
        raise ContextProviderError(
            f"could not find CodeGraphContext discovery.py under {venv_root}"
        )
    if len(matches) > 1:
        raise ContextProviderError(
            f"multiple CodeGraphContext discovery.py files found under {venv_root}"
        )
    return matches[0]


def find_cgc_viz_server_module(venv_root: Path) -> Path:
    """Find CodeGraphContext's visualizer FastAPI server module in a provider venv."""

    patterns = [
        "lib/python*/site-packages/codegraphcontext/viz/server.py",
        "Lib/site-packages/codegraphcontext/viz/server.py",
    ]
    matches = sorted({path.resolve() for pattern in patterns for path in venv_root.glob(pattern)})
    if not matches:
        raise ContextProviderError(
            f"could not find CodeGraphContext viz/server.py under {venv_root}"
        )
    if len(matches) > 1:
        raise ContextProviderError(
            f"multiple CodeGraphContext viz/server.py files found under {venv_root}"
        )
    return matches[0]


def find_cgc_cli_helpers_module(venv_root: Path) -> Path:
    """Find CodeGraphContext's CLI helper module in a provider venv."""

    patterns = [
        "lib/python*/site-packages/codegraphcontext/cli/cli_helpers.py",
        "Lib/site-packages/codegraphcontext/cli/cli_helpers.py",
    ]
    matches = sorted({path.resolve() for pattern in patterns for path in venv_root.glob(pattern)})
    if not matches:
        raise ContextProviderError(
            f"could not find CodeGraphContext cli/cli_helpers.py under {venv_root}"
        )
    if len(matches) > 1:
        raise ContextProviderError(
            f"multiple CodeGraphContext cli/cli_helpers.py files found under {venv_root}"
        )
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
        path.write_text(
            text.replace(CGC_OLD_PATCHED_SNIPPET, CGC_PATCHED_SNIPPET), encoding="utf-8"
        )
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
            raise ContextProviderError(
                "CGC writer.py did not match the expected unpatched delete snippet"
            )
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
            raise ContextProviderError(
                "CGC graph_builder.py did not match the expected unpatched extension snippet"
            )
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
        raise ContextProviderError(
            "CGC discovery.py did not match the expected unpatched generic extension snippet"
        )
    path.write_text(
        text.replace(
            CGC_DISCOVERY_GENERIC_ORIGINAL_SNIPPET, CGC_DISCOVERY_GENERIC_PATCHED_SNIPPET, 1
        ),
        encoding="utf-8",
    )
    return True


def cgc_viz_repo_query_patch_applied(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    return (
        CGC_VIZ_REPO_QUERY_PATCH_MARKER in text
        and "WITH repo_path, repo_prefix, node LIMIT 3000" in text
    )


def apply_cgc_viz_repo_query_patch(path: Path) -> bool:
    """Patch CGC visualizer repo graph query so large repos do not time out."""

    text = path.read_text(encoding="utf-8")
    if cgc_viz_repo_query_patch_applied(path):
        return False
    if CGC_VIZ_REPO_QUERY_ORIGINAL_SNIPPET not in text:
        raise ContextProviderError(
            "CGC viz/server.py did not match the expected unpatched repo query snippet"
        )
    path.write_text(
        text.replace(CGC_VIZ_REPO_QUERY_ORIGINAL_SNIPPET, CGC_VIZ_REPO_QUERY_PATCHED_SNIPPET, 1),
        encoding="utf-8",
    )
    return True


def cgc_viz_server_route_patch_applied(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    return (
        CGC_VIZ_SERVER_ROUTE_PATCH_MARKER in text
        and "RedirectResponse(_default_route)" in text
        and 'JSONResponse({"detail": "Not found"}, status_code=404)' in text
        and "default_route: Optional[str] = None" in text
    )


def apply_cgc_viz_server_route_patch(path: Path) -> bool:
    """Patch CGC visualizer server routing for local explorer launches."""

    text = path.read_text(encoding="utf-8")
    if cgc_viz_server_route_patch_applied(path):
        return False

    replacements = [
        (CGC_VIZ_SERVER_RESPONSES_ORIGINAL_SNIPPET, CGC_VIZ_SERVER_RESPONSES_PATCHED_SNIPPET),
        (CGC_VIZ_SERVER_GLOBAL_ORIGINAL_SNIPPET, CGC_VIZ_SERVER_GLOBAL_PATCHED_SNIPPET),
        (CGC_VIZ_SERVER_FALLBACK_ORIGINAL_SNIPPET, CGC_VIZ_SERVER_FALLBACK_PATCHED_SNIPPET),
        (CGC_VIZ_SERVER_RUN_ORIGINAL_SNIPPET, CGC_VIZ_SERVER_RUN_PATCHED_SNIPPET),
    ]
    for original, patched in replacements:
        if original not in text:
            raise ContextProviderError(
                "CGC viz/server.py did not match the expected unpatched route snippet"
            )
        text = text.replace(original, patched, 1)

    path.write_text(text, encoding="utf-8")
    return True


def cgc_viz_cli_route_patch_applied(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    return (
        CGC_VIZ_CLI_ROUTE_PATCH_MARKER in text
        and 'default_route = f"/explore?{query_string}"' in text
        and "default_route=default_route" in text
    )


def apply_cgc_viz_cli_route_patch(path: Path) -> bool:
    """Patch CGC visualize helper so opening / redirects to the explorer route."""

    text = path.read_text(encoding="utf-8")
    if cgc_viz_cli_route_patch_applied(path):
        return False

    replacements = [
        (CGC_VIZ_CLI_URL_ORIGINAL_SNIPPET, CGC_VIZ_CLI_URL_PATCHED_SNIPPET),
        (CGC_VIZ_CLI_RUN_ORIGINAL_SNIPPET, CGC_VIZ_CLI_RUN_PATCHED_SNIPPET),
    ]
    for original, patched in replacements:
        if original not in text:
            raise ContextProviderError(
                "CGC cli/cli_helpers.py did not match the expected unpatched visualizer snippet"
            )
        text = text.replace(original, patched, 1)

    path.write_text(text, encoding="utf-8")
    return True
