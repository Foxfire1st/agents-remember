"""Launch-scoped Codex override and the one admitted MCP instance for a leaf launch."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import os
import selectors
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from agents_remember.application.task_scoped_mcp import (
    PROJECTS_MCP_PROFILE_SCHEMA,
    TASK_SCOPED_MCP_PROFILE_SCHEMA,
    mcp_config_from_scope_profile,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.kernel.sidecar_pairing import sidecar_body
from agents_remember.models.task_document_ref import TaskDocumentRef

_MCP_SERVER_NAME = "agents-remember"
_PROFILE_SCHEMA = TASK_SCOPED_MCP_PROFILE_SCHEMA


@dataclass(frozen=True)
class ScopedNativeMcp:
    """The admitted MCP config and per-launch Codex CLI override."""

    config: McpRuntimeConfig
    profile_path: Path | None
    launch_args: tuple[str, ...]
    context_packet: dict[str, Any] | None
    verification: dict[str, Any]


@dataclass(slots=True)
class _AppServerReader:
    process: subprocess.Popen[bytes]
    selector: selectors.BaseSelector
    buffer: bytearray = field(default_factory=bytearray)

    def request(self, request_id: int, method: str, params: dict[str, Any]) -> dict[str, Any]:
        assert self.process.stdin is not None and self.process.stdout is not None
        message = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        self.process.stdin.write((json.dumps(message, separators=(",", ":")) + "\n").encode())
        self.process.stdin.flush()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if not self.selector.select(max(0, deadline - time.monotonic())):
                break
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                break
            self.buffer.extend(chunk)
            while b"\n" in self.buffer:
                line, _, remainder = self.buffer.partition(b"\n")
                self.buffer[:] = remainder
                try:
                    response = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if isinstance(response, dict) and response.get("id") == request_id:
                    return response
        raise ValueError("Codex app-server did not answer the workspace config/read request.")


def prepare_codex_scoped_mcp(
    config: McpRuntimeConfig,
    *,
    task_document_ref: TaskDocumentRef,
    workspace: dict[str, str],
) -> ScopedNativeMcp:
    """Bind one task's complete MCP tool registry to its admitted enclosure.

    A private per-launch Codex CLI ``-c`` override replaces the existing ``agents-remember`` MCP
    entry and starts the normal AR MCP server with one private scope profile. No project or global
    Codex configuration is changed.
    """

    if not all(
        isinstance(workspace.get(name), str) and workspace[name]
        for name in ("path", "contractPath", "codeRoot", "memoryRoot")
    ):
        raise ValueError(
            "The AR leaf workspace is missing its exact code, memory, or contract path."
        )
    workspace_root = Path(workspace["path"]).resolve()
    code_root = Path(workspace["codeRoot"]).resolve()
    memory_root = Path(workspace["memoryRoot"]).resolve()
    contract_path = Path(workspace["contractPath"]).resolve()
    profile_root = _private_binding_root(config)
    scope_key = _scope_key(task_document_ref, contract_path, workspace_root, code_root, memory_root)
    profile_path = profile_root / f"leaf-{scope_key}.json"
    profile = {
        "schema": _PROFILE_SCHEMA,
        "baseConfigPath": config.config_path.resolve().as_posix(),
        "taskDocumentRef": task_document_ref.model_dump(mode="json"),
        "contractPath": contract_path.as_posix(),
        "workspaceRoot": workspace_root.as_posix(),
        "codeRoot": code_root.as_posix(),
        "memoryRoot": memory_root.as_posix(),
    }
    scoped_config = mcp_config_from_scope_profile(config, profile)
    _write_private_profile(profile_path, profile, config.coordination_root)

    entry = _codex_mcp_entry(config, workspace_root, profile_path=profile_path)
    launch_args = _codex_config_override_args(entry)
    codex_profile = _read_codex_mcp_entry(workspace_root, entry, launch_args)
    context_packet, read_proof = _probe_scoped_mcp(codex_profile, profile, code_root, memory_root)
    _assert_context_packet_roots(context_packet, profile, config.coordination_root)
    verification = {
        "serverName": _MCP_SERVER_NAME,
        "registration": "per-launch-codex-config-override",
        "profilePath": profile_path.as_posix(),
        "cliOverrideVerified": True,
        "taskDocumentRef": task_document_ref.model_dump(mode="json"),
        "contractPath": contract_path.as_posix(),
        "workspaceRoot": workspace_root.as_posix(),
        "codeRoot": code_root.as_posix(),
        "memoryRoot": memory_root.as_posix(),
        "contextPacketVerified": True,
        "readArFiles": read_proof,
    }
    return ScopedNativeMcp(
        config=scoped_config,
        profile_path=profile_path,
        launch_args=launch_args,
        context_packet=context_packet,
        verification=verification,
    )


def prepare_codex_projects_mcp(
    config: McpRuntimeConfig,
    *,
    workspace_root: Path,
    repository_id: str | None,
) -> ScopedNativeMcp:
    """Bind a Codex Projects role to this AR config through the existing per-launch override.

    Unlike a leaf, a Projects role has no enclosure profile. Taskless roles verify the effective
    MCP entry and initialized tool surface without selecting a repository; task-bound roles also
    read context for their canonical repository.
    """

    workspace_root = workspace_root.resolve()
    if workspace_root != config.workspace_root.resolve():
        raise ValueError("The Projects MCP scope must use the configured Projects workspace.")
    profile_root = _private_binding_root(config)
    scope_identity = {
        "schema": PROJECTS_MCP_PROFILE_SCHEMA,
        "baseConfigPath": config.config_path.resolve().as_posix(),
        "workspaceRoot": workspace_root.as_posix(),
        "repositoryId": repository_id,
    }
    scope_digest = hashlib.sha256(
        json.dumps(scope_identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]
    profile_path = profile_root / f"projects-{scope_digest}.json"
    _write_private_profile(profile_path, scope_identity, config.coordination_root)
    scoped_config = mcp_config_from_scope_profile(config, scope_identity)
    entry = _codex_mcp_entry(scoped_config, workspace_root, profile_path=profile_path)
    launch_args = _codex_config_override_args(entry)
    _read_codex_mcp_entry(workspace_root, entry, launch_args)
    context_packet, server_identity, read_proof = _probe_projects_mcp(
        entry, scoped_config, repository_id
    )
    git_identity = (
        _assert_projects_context_packet_roots(context_packet, scoped_config, repository_id)
        if repository_id is not None
        else None
    )
    scoped_repository = scoped_config.repositories.get(repository_id) if repository_id else None
    verification = {
        "serverName": _MCP_SERVER_NAME,
        "registration": "per-launch-codex-config-override",
        "scopeKind": "configured-projects",
        "profilePath": profile_path.as_posix(),
        "cliOverrideVerified": True,
        "initializedMcpVerified": True,
        "workspaceRoot": workspace_root.as_posix(),
        "selectedRepository": repository_id,
        "codeRoot": scoped_repository.path.resolve().as_posix() if scoped_repository else None,
        "memoryRoot": (
            scoped_repository.memory_root.resolve().as_posix()
            if scoped_repository and scoped_repository.memory_root
            else None
        ),
        "gitIdentity": git_identity,
        "server": server_identity,
        "readArFiles": read_proof,
        "repositoryContextVerified": repository_id is not None,
        "noRepositorySelected": repository_id is None,
    }
    return ScopedNativeMcp(
        config=scoped_config,
        profile_path=profile_path,
        launch_args=launch_args,
        context_packet=context_packet,
        verification=verification,
    )


def _private_binding_root(config: McpRuntimeConfig) -> Path:
    settings_dir = config.config_path.resolve().parent
    owner_root = settings_dir.parent
    binding_root = (owner_root / "mcp-bindings").resolve(strict=False)
    if binding_root.is_relative_to(config.coordination_root.resolve()):
        raise ValueError("The task MCP profile store must be outside coordinationRoot.")
    for repository in config.repositories.values():
        if binding_root.is_relative_to(repository.path.resolve()):
            raise ValueError("The task MCP profile store must be outside code repositories.")
        if repository.memory_root and binding_root.is_relative_to(repository.memory_root.resolve()):
            raise ValueError("The task MCP profile store must be outside memory repositories.")
    return binding_root


def _scope_key(
    task_ref: TaskDocumentRef,
    contract_path: Path,
    workspace_root: Path,
    code_root: Path,
    memory_root: Path,
) -> str:
    value = {
        "taskDocumentRef": task_ref.model_dump(mode="json"),
        "contractPath": contract_path.as_posix(),
        "workspaceRoot": workspace_root.as_posix(),
        "codeRoot": code_root.as_posix(),
        "memoryRoot": memory_root.as_posix(),
    }
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]


def _write_private_profile(path: Path, profile: dict[str, Any], coordination_root: Path) -> None:
    path = Path(os.path.abspath(path))
    if path.is_relative_to(coordination_root.resolve()):
        raise ValueError("The task MCP profile cannot be stored under coordinationRoot.")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if _existing_profile_matches(path, profile):
        return
    serialized = json.dumps(profile, ensure_ascii=False, indent=2) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        Path(temporary).replace(path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _existing_profile_matches(path: Path, profile: dict[str, Any]) -> bool:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return False
    except OSError as error:
        raise ValueError("The private task MCP profile cannot be inspected safely.") from error
    if not stat.S_ISREG(mode):
        raise ValueError("The private task MCP profile path is not a regular file.")
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("The existing private task MCP profile is unreadable.") from error
    if not isinstance(existing, dict):
        raise ValueError("The existing private task MCP profile is malformed.")
    prior_scope = {key: value for key, value in existing.items() if key != "baseConfigPath"}
    next_scope = {key: value for key, value in profile.items() if key != "baseConfigPath"}
    if prior_scope != next_scope:
        raise ValueError("The existing task MCP profile belongs to a different enclosure binding.")
    return existing == profile


def _codex_mcp_entry(
    config: McpRuntimeConfig,
    workspace_root: Path,
    *,
    profile_path: Path | None = None,
) -> dict[str, Any]:
    package = importlib.util.find_spec("agents_remember")
    if package is None or package.origin is None:
        raise ValueError(
            "The active AR package location cannot be resolved for the native MCP profile."
        )
    package_root = Path(package.origin).resolve().parent.parent
    args = ["-m", "agents_remember.mcp", "--config", config.config_path.resolve().as_posix()]
    if profile_path is not None:
        args.extend(["--scope-profile", profile_path.resolve().as_posix()])
    env = {"PYTHONPATH": package_root.as_posix()}
    for name in ("AR_ORCA_RUNTIME_ROOT", "ORCA_USER_DATA_PATH"):
        value = os.environ.get(name, "").strip()
        if value:
            env[name] = value
    return {
        "command": sys.executable,
        "args": args,
        "env": env,
        "cwd": workspace_root.as_posix(),
    }


def _codex_config_override_args(entry: dict[str, Any]) -> tuple[str, ...]:
    command = entry.get("command")
    args = entry.get("args")
    env = entry.get("env", {})
    cwd = entry.get("cwd")
    if (
        not isinstance(command, str)
        or not isinstance(args, list)
        or not all(isinstance(value, str) for value in args)
        or not isinstance(env, dict)
        or not all(isinstance(key, str) and isinstance(value, str) for key, value in env.items())
        or not isinstance(cwd, str)
    ):
        raise ValueError(
            "The selected AR MCP entry cannot be represented as a Codex config override."
        )
    args_toml = ", ".join(_toml_basic_string(value) for value in args)
    env_toml = ", ".join(
        f"{key} = {_toml_basic_string(value)}" for key, value in sorted(env.items())
    )
    inline_table = (
        "{ command = "
        + _toml_basic_string(command)
        + ", args = ["
        + args_toml
        + "], env = { "
        + env_toml
        + " }, cwd = "
        + _toml_basic_string(cwd)
        + " }"
    )
    return ("-c", f"mcp_servers.{_MCP_SERVER_NAME}={inline_table}")


def _toml_basic_string(value: str) -> str:
    # JSON string escaping is identical for the UTF-8 TOML basic-string values used here.
    return json.dumps(value, ensure_ascii=False)


def _read_codex_mcp_entry(
    workspace_root: Path,
    expected: dict[str, Any],
    launch_args: tuple[str, ...],
) -> dict[str, Any]:
    codex_cli = shutil.which("codex")
    if not codex_cli:
        raise ValueError(
            "The native Codex CLI is unavailable; the per-launch AR MCP override was not verified."
        )
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            [codex_cli, "app-server", "--stdio", *launch_args],
            cwd=workspace_root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=os.environ.copy(),
            bufsize=0,
        )
        response = _read_effective_codex_config(process, workspace_root)
        if "error" in response:
            raise ValueError(
                "Codex app-server could not read the selected workspace's effective config."
            )
        result = response.get("result")
        if not isinstance(result, dict):
            raise ValueError("Codex app-server returned no effective workspace config.")
        return _select_codex_mcp_entry(result, expected)
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError(
            "Codex app-server could not inspect the per-launch AR MCP override."
        ) from error
    finally:
        if process is not None:
            _stop_app_server(process)


def _read_effective_codex_config(
    process: subprocess.Popen[bytes], workspace_root: Path
) -> dict[str, Any]:
    if process.stdin is None or process.stdout is None:
        raise ValueError("Codex app-server did not expose its local configuration protocol.")
    selector = selectors.DefaultSelector()
    try:
        selector.register(process.stdout, selectors.EVENT_READ)
        reader = _AppServerReader(process, selector)
        initialized = reader.request(
            1,
            "initialize",
            {
                "clientInfo": {"name": "agents-remember-scope-check", "version": "1"},
                "capabilities": {},
            },
        )
        if "error" in initialized:
            raise ValueError("Codex app-server rejected its read-only config inspection.")
        process.stdin.write(b'{"jsonrpc":"2.0","method":"initialized","params":{}}\n')
        process.stdin.flush()
        return reader.request(
            2,
            "config/read",
            {"cwd": workspace_root.as_posix(), "includeLayers": True},
        )
    finally:
        selector.close()


def _stop_app_server(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def _select_codex_mcp_entry(
    config_read: dict[str, Any], expected: dict[str, Any]
) -> dict[str, Any]:
    effective_config = config_read.get("config")
    effective_servers = (
        effective_config.get("mcp_servers") if isinstance(effective_config, dict) else None
    )
    effective_entry = (
        effective_servers.get(_MCP_SERVER_NAME) if isinstance(effective_servers, dict) else None
    )
    if _codex_transport_fields(effective_entry) != expected:
        raise ValueError("Codex did not select the exact per-launch AR MCP override.")
    return expected


def _codex_transport_fields(entry: Any) -> dict[str, Any] | None:
    if not isinstance(entry, dict) or entry.get("enabled", True) is not True:
        return None
    return {key: entry.get(key) for key in ("command", "args", "env", "cwd")}


def _probe_scoped_mcp(
    entry: dict[str, Any],
    profile: dict[str, Any],
    code_root: Path,
    memory_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    source_rel, sidecar_rel = _mcp_probe_paths(code_root, memory_root)
    params = StdioServerParameters(
        command=entry["command"],
        args=entry["args"],
        env={**os.environ, **entry["env"]},
        cwd=entry["cwd"],
    )
    context, files = asyncio.run(_call_scoped_mcp(params, profile, source_rel, sidecar_rel))
    return context, _assert_read_ar_files_roots(
        files,
        RepositoryScope(profile["taskDocumentRef"]["repository"], code_root, memory_root),
        source_rel,
        sidecar_rel,
    )


def _assert_read_ar_files_roots(
    files: dict[str, Any],
    repository: RepositoryScope,
    source_rel: str,
    sidecar_rel: str | None,
) -> dict[str, Any]:
    repository_id = repository.repo_id
    file_rows = files.get("files", [])
    if not isinstance(file_rows, list) or len(file_rows) < 1:
        raise ValueError("The scoped MCP read_ar_files call returned no file result.")
    source_row = next(
        (row for row in file_rows if isinstance(row, dict) and row.get("path") == source_rel),
        None,
    )
    if not isinstance(source_row, dict) or not isinstance(source_row.get("source"), str):
        raise ValueError(
            f"The scoped MCP read_ar_files call did not return source for {repository_id!r}."
        )
    expected_source = (repository.path / source_rel).read_text(encoding="utf-8")
    if source_row["source"] != expected_source:
        raise ValueError("The scoped MCP read_ar_files result differs from its admitted code root.")
    onboarding_status = source_row.get("status")
    expected_onboarding = (
        sidecar_body(repository.memory_root / "onboarding", source_rel)
        if sidecar_rel is not None and repository.memory_root is not None
        else None
    )
    observed_onboarding = source_row.get("onboarding")
    if onboarding_status == "found" and (
        not isinstance(expected_onboarding, str) or observed_onboarding != expected_onboarding
    ):
        raise ValueError("The scoped MCP onboarding body differs from its admitted memory root.")
    return {
        "repositoryId": repository_id,
        "sourcePath": source_rel,
        "sourceMatchesCodeRoot": True,
        "onboardingPath": source_rel if sidecar_rel else None,
        "onboardingStatus": onboarding_status,
        "memorySidecarPresent": bool(sidecar_rel and expected_onboarding),
        "onboardingMatchesMemoryRoot": bool(
            sidecar_rel
            and onboarding_status == "found"
            and expected_onboarding == observed_onboarding
        ),
    }


def _probe_projects_mcp(
    entry: dict[str, Any],
    config: McpRuntimeConfig,
    repository_id: str | None,
) -> tuple[dict[str, Any] | None, dict[str, str], dict[str, Any] | None]:
    params = StdioServerParameters(
        command=entry["command"],
        args=entry["args"],
        env={**os.environ, **entry["env"]},
        cwd=entry["cwd"],
    )
    with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as server_log:
        try:

            async def inspect() -> tuple[
                dict[str, Any] | None,
                dict[str, str],
                dict[str, Any] | None,
            ]:
                async with (
                    stdio_client(params, errlog=server_log) as (read_stream, write_stream),
                    ClientSession(read_stream, write_stream) as session,
                ):
                    await session.initialize()
                    info_result = await session.call_tool("server_info", {})
                    info = _unpack_tool_payload(info_result)
                    tools = info.get("tools")
                    if not isinstance(tools, list) or not {
                        "context_packet",
                        "task_doc",
                    } <= set(tools):
                        raise ValueError(
                            "The selected AR MCP registration lacks required role tools."
                        )
                    serving_build = info.get("servingBuild")
                    package_root = (
                        serving_build.get("packageRoot")
                        if isinstance(serving_build, dict)
                        else None
                    )
                    source_digest = (
                        serving_build.get("sourceDigest")
                        if isinstance(serving_build, dict)
                        else None
                    )
                    expected_package_root = (
                        Path(entry["env"]["PYTHONPATH"]) / "agents_remember"
                    ).resolve()
                    expected_info = {
                        "configPath": config.config_path.resolve().as_posix(),
                        "coordinationRoot": config.coordination_root.resolve().as_posix(),
                        "workspaceRoot": config.workspace_root.resolve().as_posix(),
                    }
                    if any(info.get(key) != value for key, value in expected_info.items()):
                        raise ValueError(
                            "The initialized AR MCP server reports another config or workspace."
                        )
                    allowed_repositories = info.get("allowedRepoIds")
                    if not isinstance(allowed_repositories, list) or set(
                        allowed_repositories
                    ) != set(config.allowed_repo_ids):
                        raise ValueError(
                            "The initialized AR MCP server did not select the Projects repository scope."
                        )
                    version = info.get("version")
                    if (
                        package_root != expected_package_root.as_posix()
                        or not isinstance(source_digest, str)
                        or not isinstance(version, str)
                    ):
                        raise ValueError(
                            "The initialized AR MCP server source identity is not the active package."
                        )
                    if repository_id is None:
                        return (
                            None,
                            {
                                "version": version,
                                **expected_info,
                                "packageRoot": str(package_root),
                                "sourceDigest": source_digest,
                            },
                            None,
                        )
                    repository = config.repositories[repository_id]
                    source_rel, sidecar_rel = _mcp_probe_paths(
                        repository.path, repository.memory_root
                    )
                    result = await session.call_tool(
                        "context_packet",
                        {"repo_id": repository_id, "include_providers": False},
                    )
                    files_result = await session.call_tool(
                        "read_ar_files",
                        {
                            "repo_id": repository_id,
                            "files": [
                                {
                                    "path": source_rel,
                                    "source": "full",
                                    "onboarding": sidecar_rel is not None,
                                }
                            ],
                        },
                    )
                    read_proof = _assert_read_ar_files_roots(
                        _unpack_tool_payload(files_result),
                        repository,
                        source_rel,
                        sidecar_rel,
                    )
                    return (
                        _unpack_tool_payload(result),
                        {
                            "version": version,
                            **expected_info,
                            "packageRoot": str(package_root),
                            "sourceDigest": source_digest,
                        },
                        read_proof,
                    )

            return asyncio.run(inspect())
        except Exception as error:
            server_log.flush()
            server_log.seek(0)
            log_tail = server_log.read()[-600:].strip()
            detail = log_tail or str(error)
            raise ValueError(
                f"The Projects AR MCP startup/read failed ({type(error).__name__}): {detail[:800]}"
            ) from error


def _assert_projects_context_packet_roots(
    context: dict[str, Any] | None,
    config: McpRuntimeConfig,
    repository_id: str,
) -> dict[str, str]:
    repository = config.repositories.get(repository_id)
    if repository is None or context is None:
        raise ValueError("The selected task repository is not admitted by the active AR config.")
    repo_packet = context.get("repo", {})
    paths_packet = context.get("paths", {})
    observed = {
        "repository": repo_packet.get("root"),
        "coordinationRoot": paths_packet.get("coordinationRoot"),
        "memoryRoot": paths_packet.get("memoryRoot"),
    }
    expected = {
        "repository": repository.path.resolve().as_posix(),
        "coordinationRoot": config.coordination_root.resolve().as_posix(),
        "memoryRoot": repository.memory_root.resolve().as_posix()
        if repository.memory_root
        else None,
    }
    if observed != expected:
        raise ValueError(
            "The initialized AR context_packet did not return the exact configured project code and memory roots."
        )
    git_identity = {
        key: repo_packet.get(key)
        for key in ("state", "branch", "head")
        if isinstance(repo_packet.get(key), str)
    }
    if git_identity.get("state") not in {"available", "detached"} or not git_identity.get("head"):
        raise ValueError("The selected Projects repository has no verified Git context identity.")
    worktree = context.get("worktree", {})
    if (
        worktree.get("state") != "inactive"
        or _wire_path(worktree, "contract_path", "contractPath") is not None
    ):
        raise ValueError("The Projects AR context retained a task-enclosure binding.")
    return git_identity


async def _call_scoped_mcp(
    params: StdioServerParameters,
    profile: dict[str, Any],
    source_rel: str,
    sidecar_rel: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as server_log:
        try:
            async with (
                stdio_client(params, errlog=server_log) as (read_stream, write_stream),
                ClientSession(read_stream, write_stream) as session,
            ):
                await session.initialize()
                context_result = await session.call_tool(
                    "context_packet",
                    {
                        "repo_id": profile["taskDocumentRef"]["repository"],
                        "include_providers": False,
                    },
                )
                context = _unpack_tool_payload(context_result)
                files_request = [
                    {
                        "path": source_rel,
                        "source": "full",
                        "onboarding": sidecar_rel is not None,
                    }
                ]
                files_result = await session.call_tool(
                    "read_ar_files",
                    {
                        "repo_id": profile["taskDocumentRef"]["repository"],
                        "files": files_request,
                    },
                )
                files = _unpack_tool_payload(files_result)
                return context, files
        except Exception as error:
            server_log.flush()
            server_log.seek(0)
            log_tail = server_log.read()[-600:].strip()
            detail = log_tail or str(error)
            raise ValueError(
                f"The workspace-scoped AR MCP startup/read failed ({type(error).__name__}): {detail[:800]}"
            ) from error


def _unpack_tool_payload(result: Any) -> dict[str, Any]:
    if getattr(result, "isError", False):
        raise ValueError("The scoped AR MCP tool returned an error.")
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        value = structured.get("payload", structured)
        if isinstance(value, dict):
            return value
    for block in getattr(result, "content", []):
        if getattr(block, "type", None) != "text":
            continue
        try:
            payload = json.loads(block.text)
        except (TypeError, ValueError):
            continue
        if isinstance(payload, dict):
            value = payload.get("payload", payload)
            if isinstance(value, dict):
                return value
    raise ValueError("The scoped AR MCP tool returned no JSON payload.")


def _mcp_probe_paths(code_root: Path, memory_root: Path | None) -> tuple[str, str | None]:
    if memory_root is not None:
        paired = _paired_onboarding_probe_path(code_root, memory_root / "onboarding")
        if paired is not None:
            return paired
    for root, _dirs, files in os.walk(code_root):
        _dirs[:] = [name for name in _dirs if name != ".git" and not name.startswith(".")]
        for name in sorted(files):
            if name.startswith("."):
                continue
            source = Path(root) / name
            if source.suffix.lower() in {".md", ".json", ".py", ".ts", ".tsx", ".js"}:
                return source.relative_to(code_root).as_posix(), None
    raise ValueError("No bounded text source is available to verify the selected code worktree.")


def _paired_onboarding_probe_path(code_root: Path, onboarding_root: Path) -> tuple[str, str] | None:
    if not onboarding_root.is_dir():
        return None
    for sidecar in sorted(onboarding_root.rglob("*.md")):
        try:
            sidecar_rel = sidecar.relative_to(onboarding_root).as_posix()
        except ValueError:
            continue
        source_rel = sidecar_rel[:-3]
        if any(part.startswith(".") for part in Path(source_rel).parts):
            continue
        if Path(source_rel).name == "overview":
            continue
        source = (code_root / source_rel).resolve(strict=False)
        if (
            source.is_file()
            and source.is_relative_to(code_root)
            and sidecar_body(onboarding_root, source_rel)
        ):
            return source_rel, sidecar_rel
    return None


def _assert_context_packet_roots(
    context: dict[str, Any], profile: dict[str, Any], coordination_root: Path
) -> None:
    repo = context.get("repo", {})
    paths = context.get("paths", {})
    worktree = context.get("worktree", {})
    observed = {
        "repoRoot": repo.get("root"),
        "coordinationRoot": paths.get("coordinationRoot"),
        "memoryRoot": paths.get("memoryRoot"),
        "worktreeGroup": _wire_path(worktree, "worktree_group", "worktreeGroup"),
        "codeWorktree": _wire_path(worktree, "code_worktree", "codeWorktree"),
        "memoryWorktree": _wire_path(worktree, "memory_worktree", "memoryWorktree"),
        "contractPath": _wire_path(worktree, "contract_path", "contractPath"),
    }
    expected = {
        "repoRoot": profile["codeRoot"],
        "coordinationRoot": coordination_root.resolve().as_posix(),
        "memoryRoot": profile["memoryRoot"],
        "worktreeGroup": profile["workspaceRoot"],
        "codeWorktree": profile["codeRoot"],
        "memoryWorktree": profile["memoryRoot"],
        "contractPath": profile["contractPath"],
    }
    if observed != expected:
        raise ValueError(
            "The initialized AR context_packet did not return the exact selected leaf roots."
        )


def _wire_path(payload: dict[str, Any], snake: str, camel: str) -> Any:
    return payload.get(snake, payload.get(camel))


__all__ = ["ScopedNativeMcp", "prepare_codex_projects_mcp", "prepare_codex_scoped_mcp"]
