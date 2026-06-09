"""CodeGraphContext context provider constants and patch snippets."""

from __future__ import annotations

from pathlib import Path

CGC_PROVIDER = "codegraphcontext"
CGC_PIN = "codegraphcontext==0.4.10"
CGC_RUNNER_IMAGE_REPOSITORY = "agents-remember/codegraphcontext"
# Bump when the runner Docker layer changes without a cgc version change
# (runtime_install skips building image tags that already exist).
CGC_RUNNER_IMAGE_LAYER_REVISION = "ar1"
CGC_WATCHER_CONTAINER_PREFIX = "ar-cgc-watcher"
CGC_NETWORK_NAME = "ar-cgc-code"
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
