from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.kernel.primitives.paseo_runtime_settings import (
    PaseoRuntimeNotConfigured,
    require_paseo_runtime,
)
from agents_remember.kernel.primitives.runtime_config import (
    ConfigError,
    McpRuntimeConfig,
    load_config,
    load_paseo_runtime_settings,
)
from agents_remember.providers.settings import lifecycle_settings_from_config
from test_worktree_support import init_repo


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def settings_payload(root: Path) -> dict:
    coordination_root = root / "ar-coordination"
    workspace_root = root / "workspace"
    return {
        "version": 1,
        "coordinationRoot": str(coordination_root),
        "workspaceRoot": str(workspace_root),
        "repositories": {"agents-remember": {}},
        "providers": {
            "codegraphcontext-code": {},
            "grepai-memory": {},
        },
        "timeoutCaps": {
            "toolSeconds": 30,
            "providerSetupSeconds": 1800,
        },
        "benchmarksEnabled": True,
    }


class McpConfigTests(unittest.TestCase):
    def test_optional_paseo_runtime_block_is_exact_and_fail_loud(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir).resolve()
            path = root / "mcp-settings.json"
            payload = settings_payload(root)

            write_json(path, payload)
            self.assertIsNone(load_config(path).paseo_runtime)
            self.assertIsNone(load_paseo_runtime_settings(path))
            with self.assertRaisesRegex(PaseoRuntimeNotConfigured, "^no Paseo runtime configured"):
                require_paseo_runtime(None, source=path)

            block = {
                "installPrefix": (root / "paseo").as_posix(),
                "home": (root / "paseo-home").as_posix(),
                "listen": "127.0.0.1:6820",
                "version": "0.11.0-beta.2",
                "providers": {"hermes": {"extends": "acp", "command": ["hermes", "acp"]}},
                "embed": [
                    {
                        "dashboardOrigin": "http://127.0.0.1:9797",
                        "frameBaseUrl": "http://127.0.0.1:6820",
                    },
                    {
                        "dashboardOrigin": "https://fox.example.ts.net",
                        "frameBaseUrl": "https://fox.example.ts.net:8443",
                    },
                ],
            }
            payload["paseoRuntime"] = block
            write_json(path, payload)
            configured = load_config(path).paseo_runtime
            assert configured is not None
            self.assertEqual(configured, load_paseo_runtime_settings(path))
            self.assertEqual(configured.install_prefix, root / "paseo")
            self.assertEqual(configured.home, root / "paseo-home")
            self.assertEqual((configured.listen_host, configured.listen_port), ("127.0.0.1", 6820))
            self.assertEqual(configured.version, "0.11.0-beta.2")
            self.assertEqual(configured.providers, block["providers"])
            self.assertEqual(configured.embed_payload(), block["embed"])
            self.assertEqual(require_paseo_runtime(configured, source=path), configured)

            # The spellings a browser itself reports are kept as written.
            canonical = [
                {"dashboardOrigin": origin, "frameBaseUrl": "http://127.0.0.1:6820"}
                for origin in (
                    "http://127.0.0.1:9797",
                    "http://localhost:9797",
                    "http://[::1]:9797",
                    "http://[::ffff:7f00:1]:9797",
                    "http://[2001:db8::1:0:0:1]",
                    "https://xn--bcher-kva.example",
                )
            ]
            payload["paseoRuntime"] = {**block, "embed": canonical}
            write_json(path, payload)
            kept = require_paseo_runtime(load_config(path).paseo_runtime, source=path)
            self.assertEqual(kept.embed_payload(), canonical)

            payload["paseoRuntime"] = {**block, "providers": {}, "embed": []}
            write_json(path, payload)
            self.assertEqual(load_paseo_runtime_settings(path), load_config(path).paseo_runtime)
            self.assertEqual(
                require_paseo_runtime(load_config(path).paseo_runtime, source=path).embed, ()
            )

            origin = {"dashboardOrigin": "http://127.0.0.1:9797"}
            invalid: list[tuple[dict, str]] = [
                ({key: value for key, value in block.items() if key != fact}, f"must define {fact}")
                for fact in block
            ] + [
                ({**block, "password": "x"}, "unsupported paseoRuntime setting"),
                ({**block, "home": "relative/home"}, "must be an absolute path"),
                ({**block, "listen": "127.0.0.1"}, "host:port"),
                ({**block, "listen": "127.0.0.1:0"}, "host:port"),
                ({**block, "providers": {"hermes": "hermes acp"}}, "provider id to an object"),
                ({**block, "embed": {}}, "embed must be a list"),
                ({**block, "embed": [origin]}, "exactly dashboardOrigin and frameBaseUrl"),
                (
                    {**block, "embed": [{**origin, "frameBaseUrl": "http://u:p@127.0.0.1:6820"}]},
                    "frameBaseUrl must be",
                ),
                (
                    {
                        **block,
                        "embed": [
                            {
                                "dashboardOrigin": "http://127.0.0.1:9797/",
                                "frameBaseUrl": "http://127.0.0.1:6820",
                            }
                        ],
                    },
                    "dashboardOrigin must be",
                ),
                ({**block, "embed": [block["embed"][0]] * 2}, "more than once"),
            ]
            invalid += [
                ({**block, "version": version}, "one exact Paseo version")
                for version in (
                    "^0.11.0-beta.2",
                    "~0.11.0",
                    ">=0.11.0",
                    "0.11",
                    "0.11.x",
                    "beta",
                    "0.11.0 || 0.12.0",
                    "0.11.0 - 0.12.0",
                    "0.11.0-beta.2+build",
                    "v0.11.0-beta.2",
                    " 0.11.0-beta.2 ",
                )
            ]
            # A dashboard origin must be spelled the way a browser reports it.
            invalid += [
                (
                    {**block, "embed": [{**block["embed"][0], "dashboardOrigin": spelling}]},
                    "exactly as a browser reports it",
                )
                for spelling in (
                    "HTTP://127.0.0.1:9797",
                    "http://LocalHost:9797",
                    "http://localhost:80",
                    "https://fox.example.ts.net:443",
                    "http://b\u00fccher.example",
                    "http://[0:0:0:0:0:0:0:1]:9797",
                    "http://[::ffff:127.0.0.1]:9797",
                    "http://[::A]:9797",
                    "http://[fe80::1%25eth0]:9797",
                    "http://127.1:9797",
                    "http://0x7f.0.0.1:9797",
                    "http://2130706433:9797",
                    "http://127.0.0.01:9797",
                    "http://a%41:9797",
                    "http://a b:9797",
                    "http://ex_ample:9797",
                )
            ]
            for candidate, message in invalid:
                payload["paseoRuntime"] = candidate
                write_json(path, payload)
                with self.subTest(message=message), self.assertRaisesRegex(ConfigError, message):
                    load_config(path)

    def test_two_repository_ids_cannot_share_one_git_common_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            workspace = root / "workspace"
            physical = workspace / "repo-a"
            init_repo(physical, "main")
            (workspace / "repo-b").symlink_to(physical, target_is_directory=True)
            payload = settings_payload(root)
            payload["repositories"] = {"repo-a": {}, "repo-b": {}}
            path = root / "mcp-settings.json"
            write_json(path, payload)

            with self.assertRaisesRegex(ConfigError, "share Git common-dir"):
                load_config(path)

    def test_external_memory_cannot_alias_another_configured_code_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            workspace = root / "workspace"
            init_repo(workspace / "repo-a", "main")
            other = workspace / "repo-b"
            init_repo(other, "main")
            memory = root / "ar-coordination" / "memory-repos" / "ar-repo-a"
            memory.parent.mkdir(parents=True)
            memory.symlink_to(other, target_is_directory=True)
            payload = settings_payload(root)
            payload["repositories"] = {"repo-a": {}, "repo-b": {}}
            path = root / "mcp-settings.json"
            write_json(path, payload)

            with self.assertRaisesRegex(ConfigError, "share Git common-dir"):
                load_config(path)

    def test_config_must_not_live_inside_coordination_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = root / "ar-coordination" / "mcp-settings.json"
            write_json(path, settings_payload(root))

            with self.assertRaisesRegex(ConfigError, "inside the coordinator root"):
                load_config(path)

    def test_loads_authority_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = root / ".codex" / "mcp" / "settings.json"
            write_json(path, settings_payload(root))

            config = load_config(path)

            self.assertEqual(config.allowed_repo_ids, ("agents-remember",))
            self.assertEqual(
                config.allowed_provider_ids,
                ("codegraphcontext-code", "grepai-memory"),
            )
            self.assertEqual(config.timeout_caps["toolSeconds"], 30)
            self.assertEqual(
                config.transcript_root,
                root / "ar-coordination" / "logs" / "mcp",
            )
            self.assertEqual(config.harness_skill_root, root / ".codex" / "skills")
            self.assertEqual(
                config.repositories["agents-remember"].path,
                root / "workspace" / "agents-remember",
            )
            memory_root = config.repositories["agents-remember"].memory_root
            assert memory_root is not None
            self.assertEqual(memory_root.name, "ar-agents-remember")
            self.assertEqual(config.providers["grepai-memory"].scope, "workspace")
            self.assertEqual(config.providers["grepai-memory"].instance_id, "workspace")
            self.assertEqual(
                config.providers["grepai-memory"].log_root,
                root
                / "ar-coordination"
                / "logs"
                / "providers"
                / "grepai"
                / config.providers["grepai-memory"].instance_id,
            )
            self.assertEqual(
                config.providers["codegraphcontext-code"].runtime_root,
                root
                / "ar-coordination"
                / "providers"
                / "runners"
                / "codegraphcontext"
                / config.providers["codegraphcontext-code"].instance_id,
            )

            lifecycle_settings = lifecycle_settings_from_config(config)
            providers = lifecycle_settings["contextProviders"]["providers"]
            grepai = providers["grepai-memory"]
            grepai_instance = config.providers["grepai-memory"].instance_id
            cgc_instance = config.providers["codegraphcontext-code"].instance_id
            self.assertEqual(
                grepai["runtimeRoot"],
                (
                    root / "ar-coordination" / "providers" / "runners" / "grepai" / grepai_instance
                ).as_posix(),
            )
            self.assertEqual(grepai["instance"]["id"], grepai_instance)
            self.assertEqual(grepai["instance"]["scope"], "workspace")
            self.assertEqual(
                grepai["instance"]["labels"]["agents-remember.instance-id"],
                grepai_instance,
            )
            self.assertEqual(
                grepai["instance"]["labels"]["agents-remember.provider"],
                "grepai-memory",
            )
            self.assertEqual(grepai["runtime"]["mode"], "docker")
            self.assertEqual(
                grepai["runtime"]["composeProject"],
                f"agents-remember-grepai-{grepai_instance}",
            )
            self.assertEqual(
                grepai["runtime"]["network"]["name"],
                f"ar-grepai-memory-{grepai_instance}",
            )
            self.assertEqual(grepai["runtime"]["runner"]["image"], "agents-remember/grepai:0.35.0")
            self.assertEqual(
                grepai["runtime"]["runner"]["containerName"],
                f"ar-grepai-watcher-{grepai_instance}",
            )
            self.assertEqual(
                grepai["backend"]["runtimeRoot"],
                (
                    root
                    / "ar-coordination"
                    / "providers"
                    / "data"
                    / "grepai"
                    / grepai_instance
                    / "postgres"
                ).as_posix(),
            )
            self.assertEqual(grepai["embedder"]["provider"], "ollama")
            self.assertEqual(grepai["embedder"]["backend"]["image"], "ollama/ollama:latest")
            self.assertEqual(
                grepai["embedder"]["backend"]["containerName"],
                f"ar-grepai-ollama-{grepai_instance}",
            )
            self.assertEqual(providers["codegraphcontext-code"]["instance"]["id"], cgc_instance)
            self.assertEqual(
                providers["codegraphcontext-code"]["instance"]["scope"],
                "workspace",
            )
            self.assertEqual(
                providers["codegraphcontext-code"]["runtime"]["composeProject"],
                f"agents-remember-cgc-{cgc_instance}",
            )
            self.assertEqual(
                providers["codegraphcontext-code"]["runtime"]["runner"]["containerNameTemplate"],
                f"ar-cgc-watcher-{cgc_instance}-<repoId>",
            )
            self.assertEqual(
                providers["codegraphcontext-code"]["backend"]["runtimeRoot"],
                (
                    root
                    / "ar-coordination"
                    / "providers"
                    / "data"
                    / "codegraphcontext"
                    / cgc_instance
                    / "falkordb"
                ).as_posix(),
            )
            self.assertEqual(
                providers["codegraphcontext-code"]["backend"]["network"]["name"],
                f"ar-cgc-code-{cgc_instance}",
            )

    def test_repository_certification_profile_reference_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            payload = settings_payload(root)
            path = root / "mcp-settings.json"
            for invalid in (
                ["one.json", "two.json"],
                "../outside.json",
                "/absolute/profile.json",
                "C:/absolute/profile.json",
                "mcp\\profile.json",
                ".",
                "mcp/",
            ):
                payload["repositories"]["agents-remember"]["certificationProfile"] = invalid
                write_json(path, payload)
                with (
                    self.subTest(invalid=invalid),
                    self.assertRaisesRegex(ConfigError, "certificationProfile"),
                ):
                    load_config(path)

    def test_repository_contract_path_cannot_escape_coordination_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            payload = settings_payload(root)
            payload["repositories"]["agents-remember"]["contractPath"] = str(
                root / "outside" / "series-contract.md"
            )
            path = root / "mcp-settings.json"
            write_json(path, payload)

            with self.assertRaisesRegex(ConfigError, "contractPath must be inside"):
                load_config(path)


class OrchestrationSettingsTests(unittest.TestCase):
    """gateDelegation boot sourcing (260703-L13, GQ1).

    The key's home is the GLOBAL agentic settings file
    (``<coordinationRoot>/system/settings.json``), read once at boot through the
    kernel loader (boot-snapshot). An authority-file value is a one-cycle
    legacy fallback with a boot warning; every other ``orchestration.*`` key in
    the authority file fails loud naming the new home.
    """

    def _load(
        self,
        *,
        authority: object | None = None,
        global_orchestration: object | None = None,
    ) -> McpRuntimeConfig:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            payload = settings_payload(root)
            if authority is not None:
                payload["orchestration"] = authority
            if global_orchestration is not None:
                global_path = root / "ar-coordination" / "system" / "settings.json"
                write_json(global_path, {"orchestration": global_orchestration})
            path = root / "mcp-settings.json"
            write_json(path, payload)
            return load_config(path)

    def test_human_pinned_kind_in_global_file_fails_boot(self) -> None:
        with self.assertRaisesRegex(ConfigError, "human-pinned"):
            self._load(
                global_orchestration={
                    "gateDelegation": {"kinds": {"push-approval": {"role": "manager"}}}
                }
            )


if __name__ == "__main__":
    unittest.main()
