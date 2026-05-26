from __future__ import annotations

import pathlib
import sysconfig

base = pathlib.Path(sysconfig.get_paths()["purelib"]) / "codegraphcontext"
operations = [
    {
        "file": "core/cgcignore.py",
        "originals": [
            "    if local_cgcignore_path is None:\n        # Agents Remember patch: prefer explicit .cgcignore path before repo-local creation.\n        if explicit_cgcignore_path is not None:\n            local_cgcignore_path = explicit_cgcignore_path\n        else:\n            local_cgcignore_path = ignore_root / \".cgcignore\"\n        ensure_default_cgcignore(local_cgcignore_path, default_patterns)\n",
            "    if local_cgcignore_path is None:\n        local_cgcignore_path = ignore_root / \".cgcignore\"\n        ensure_default_cgcignore(local_cgcignore_path, default_patterns)\n",
        ],
        "patched": "    if local_cgcignore_path is None:\n        # Agents Remember patch: prefer explicit .cgcignore path without overwriting it.\n        if explicit_cgcignore_path is not None:\n            local_cgcignore_path = explicit_cgcignore_path\n        else:\n            local_cgcignore_path = ignore_root / \".cgcignore\"\n        if not local_cgcignore_path.exists():\n            ensure_default_cgcignore(local_cgcignore_path, default_patterns)\n",
    },
    {
        "file": "tools/indexing/persistence/writer.py",
        "originals": [
            "        repo_path_str = repo_path\n        path_prefix = repo_path_str + \"/\"\n        with self.driver.session() as session:\n"
        ],
        "patched": "        repo_path_str = repo_path\n        path_prefix = repo_path_str + \"/\"\n        # Agents Remember patch: delete repository paths with slash and backslash child prefixes.\n        path_prefix_backslash = repo_path_str + \"\\\\\"\n        with self.driver.session() as session:\n",
    },
    {
        "file": "tools/indexing/persistence/writer.py",
        "originals": [
            "                    result = session.run(\n                        f\"MATCH (a)-[r:{rel_type}]->(b) \"\n                        \"WHERE a.path STARTS WITH $prefix OR b.path STARTS WITH $prefix \"\n                        \"WITH r LIMIT 5000 DELETE r RETURN count(r) AS deleted\",\n                        prefix=path_prefix,\n                    ).single()\n"
        ],
        "patched": "                    result = session.run(\n                        f\"MATCH (a)-[r:{rel_type}]->(b) \"\n                        \"WHERE a.path STARTS WITH $prefix OR b.path STARTS WITH $prefix \"\n                        \"OR a.path STARTS WITH $prefix_backslash OR b.path STARTS WITH $prefix_backslash \"\n                        \"WITH r LIMIT 5000 DELETE r RETURN count(r) AS deleted\",\n                        prefix=path_prefix,\n                        prefix_backslash=path_prefix_backslash,\n                    ).single()\n",
    },
    {
        "file": "tools/indexing/persistence/writer.py",
        "originals": [
            "                    \"MATCH (a)-[r:CONTAINS]->(b) \"\n                    \"WHERE a.path STARTS WITH $prefix OR a.path = $path \"\n                    \"WITH r LIMIT 10000 DELETE r RETURN count(r) AS deleted\",\n                    prefix=path_prefix,\n                    path=repo_path_str,\n"
        ],
        "patched": "                    \"MATCH (a)-[r:CONTAINS]->(b) \"\n                    \"WHERE a.path STARTS WITH $prefix OR a.path STARTS WITH $prefix_backslash OR a.path = $path \"\n                    \"WITH r LIMIT 10000 DELETE r RETURN count(r) AS deleted\",\n                    prefix=path_prefix,\n                    prefix_backslash=path_prefix_backslash,\n                    path=repo_path_str,\n",
    },
    {
        "file": "tools/indexing/persistence/writer.py",
        "originals": [
            "                    result = session.run(\n                        f\"MATCH (n:{label}) WHERE n.path STARTS WITH $prefix \"\n                        \"WITH n LIMIT 10000 DETACH DELETE n RETURN count(n) AS deleted\",\n                        prefix=path_prefix,\n                    ).single()\n"
        ],
        "patched": "                    result = session.run(\n                        f\"MATCH (n:{label}) WHERE n.path STARTS WITH $prefix OR n.path STARTS WITH $prefix_backslash \"\n                        \"WITH n LIMIT 10000 DETACH DELETE n RETURN count(n) AS deleted\",\n                        prefix=path_prefix,\n                        prefix_backslash=path_prefix_backslash,\n                    ).single()\n",
    },
    {
        "file": "tools/graph_builder.py",
        "originals": ["            \".cpp\": \"cpp\",\n            \".h\": \"cpp\",\n"],
        "patched": "            \".cpp\": \"cpp\",\n            # Agents Remember patch: include TensorFlow C++ source extensions.\n            \".cc\": \"cpp\",\n            \".cxx\": \"cpp\",\n            \".c++\": \"cpp\",\n            \".C\": \"cpp\",\n            \".h\": \"cpp\",\n",
    },
    {
        "file": "tools/graph_builder.py",
        "originals": [
            "        self.generic_extensions = {\n            \".toml\", \".sh\", \".yaml\", \".yml\", \".json\", \".ini\", \".cfg\", \".md\", \".txt\", \".env\",\n            \".bat\", \".ps1\", \".dockerignore\", \".gitignore\"\n        }\n"
        ],
        "patched": "        self.generic_extensions = {\n            \".toml\", \".sh\", \".yaml\", \".yml\", \".json\", \".ini\", \".cfg\", \".md\", \".txt\", \".env\",\n            \".bat\", \".ps1\", \".dockerignore\", \".gitignore\",\n            # Agents Remember patch: keep TensorFlow TableGen files discoverable.\n            \".td\",\n        }\n",
    },
    {
        "file": "tools/graph_builder.py",
        "originals": [
            "        if '.cpp' in files_by_lang:\n            from .languages import cpp as cpp_lang_module\n            imports_map.update(cpp_lang_module.pre_scan_cpp(files_by_lang['.cpp'], self.get_parser('.cpp')))\n        if '.h' in files_by_lang:\n            from .languages import cpp as cpp_lang_module\n            imports_map.update(cpp_lang_module.pre_scan_cpp(files_by_lang['.h'], self.get_parser('.h')))\n        if '.hpp' in files_by_lang:\n            from .languages import cpp as cpp_lang_module\n            imports_map.update(cpp_lang_module.pre_scan_cpp(files_by_lang['.hpp'], self.get_parser('.hpp')))\n        if '.hh' in files_by_lang:\n            from .languages import cpp as cpp_lang_module\n            imports_map.update(cpp_lang_module.pre_scan_cpp(files_by_lang['.hh'], self.get_parser('.hh')))\n"
        ],
        "patched": "        cpp_files = []\n        for cpp_ext in ('.cpp', '.cc', '.cxx', '.c++', '.C', '.h', '.hpp', '.hh'):\n            cpp_files.extend(files_by_lang.get(cpp_ext, []))\n        if cpp_files:\n            from .languages import cpp as cpp_lang_module\n            imports_map.update(cpp_lang_module.pre_scan_cpp(cpp_files, self.get_parser('.cpp')))\n",
    },
    {
        "file": "tools/indexing/discovery.py",
        "originals": [
            "_GENERIC_EXTENSIONS: FrozenSet[str] = frozenset({\n    \".toml\", \".sh\", \".yaml\", \".yml\", \".json\", \".ini\", \".cfg\",\n    \".md\", \".txt\", \".env\", \".bat\", \".ps1\", \".dockerignore\", \".gitignore\",\n})\n"
        ],
        "patched": "_GENERIC_EXTENSIONS: FrozenSet[str] = frozenset({\n    \".toml\", \".sh\", \".yaml\", \".yml\", \".json\", \".ini\", \".cfg\",\n    \".md\", \".txt\", \".env\", \".bat\", \".ps1\", \".dockerignore\", \".gitignore\",\n    # Agents Remember patch: keep TensorFlow TableGen files discoverable.\n    \".td\",\n})\n",
    },
    {
        "file": "viz/server.py",
        "originals": [
            "                # Get all nodes within the repository scope\n                query = \"\"\"\n                MATCH (r:Repository {path: $repo_path})\n                OPTIONAL MATCH (r)-[:CONTAINS*0..]->(n)\n                WITH DISTINCT r, COLLECT(DISTINCT n) as repo_nodes\n                UNWIND repo_nodes as node\n                OPTIONAL MATCH (node)-[rel]->(target)\n                WITH r, node, rel, target, repo_nodes\n                WHERE target IN repo_nodes OR target = r\n                RETURN node as n, rel, target as m\n                \"\"\"\n                result = session.run(query, repo_path=repo_path)\n"
        ],
        "patched": "                # Agents Remember patch: bound visualizer repo graph query by path prefix.\n                query = \"\"\"\n                WITH $repo_path AS repo_path, $repo_path + \"/\" AS repo_prefix\n                MATCH (node)\n                WHERE node.path = repo_path OR node.path STARTS WITH repo_prefix\n                WITH repo_path, repo_prefix, node LIMIT 3000\n                OPTIONAL MATCH (node)-[rel]->(target)\n                WHERE target.path = repo_path OR target.path STARTS WITH repo_prefix\n                RETURN node as n, rel, target as m\n                LIMIT 5000\n                \"\"\"\n                result = session.run(query, repo_path=repo_path)\n",
    },
    {
        "file": "viz/server.py",
        "originals": ["from fastapi.responses import HTMLResponse, FileResponse\n"],
        "patched": "from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, RedirectResponse\n",
    },
    {
        "file": "viz/server.py",
        "originals": ["# Path to static directory\n_static_dir: Optional[str] = None\n"],
        "patched": "# Path to static directory\n_static_dir: Optional[str] = None\n# Default SPA route used when the local server is opened at /.\n_default_route: Optional[str] = None\n",
    },
    {
        "file": "viz/server.py",
        "originals": [
            "    global _static_dir\n    if not _static_dir:\n        return HTMLResponse(\"Static directory not configured\", status_code=500)\n"
        ],
        "patched": "    global _static_dir, _default_route\n    if full_path in (\"\", \"/\") and _default_route:\n        # Agents Remember patch: route local visualizer root to explorer.\n        return RedirectResponse(_default_route)\n    if full_path.startswith(\"api/\"):\n        return JSONResponse({\"detail\": \"Not found\"}, status_code=404)\n    if not _static_dir:\n        return HTMLResponse(\"Static directory not configured\", status_code=500)\n",
    },
    {
        "file": "viz/server.py",
        "originals": [
            "def run_server(host: str = \"127.0.0.1\", port: int = 8000, static_dir: Optional[str] = None):\n    global _static_dir\n    _static_dir = static_dir\n    uvicorn.run(app, host=host, port=port)\n"
        ],
        "patched": "def run_server(\n    host: str = \"127.0.0.1\",\n    port: int = 8000,\n    static_dir: Optional[str] = None,\n    default_route: Optional[str] = None,\n):\n    global _static_dir, _default_route\n    _static_dir = static_dir\n    _default_route = default_route\n    uvicorn.run(app, host=host, port=port)\n",
    },
    {
        "file": "cli/cli_helpers.py",
        "originals": [
            "    query_string = urllib.parse.urlencode(params)\n    visualization_url = f\"{backend_url}/explore?{query_string}\"\n    \n    console.print(f\"[green]Starting visualizer server on {backend_url}...[/green]\")\n"
        ],
        "patched": "    query_string = urllib.parse.urlencode(params)\n    # Agents Remember patch: make visualizer root open the explorer route.\n    default_route = f\"/explore?{query_string}\"\n    visualization_url = f\"{backend_url}{default_route}\"\n    \n    console.print(f\"[green]Starting visualizer server on {backend_url}...[/green]\")\n",
    },
    {
        "file": "cli/cli_helpers.py",
        "originals": [
            "        run_server(host=\"127.0.0.1\", port=port, static_dir=str(static_dir))\n"
        ],
        "patched": "        run_server(host=\"127.0.0.1\", port=port, static_dir=str(static_dir), default_route=default_route)\n",
    },
]

for operation in operations:
    path = base / operation["file"]
    text = path.read_text(encoding="utf-8")
    if operation["patched"] in text:
        continue
    for original in operation["originals"]:
        if original in text:
            break
    else:
        raise SystemExit(f"CodeGraphContext patch target did not match: {operation['file']}")
    patched = operation["patched"]
    if patched not in text:
        text = text.replace(original, patched, 1)
    path.write_text(text, encoding="utf-8")
