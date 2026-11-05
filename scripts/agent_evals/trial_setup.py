"""Provision one concrete, disposable native Curator trial without starting its actor."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import socket
import subprocess
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agents_remember.cli.paseo_bridge import require_bridge_runtime
from agents_remember.errors import AuthorityError
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, config_from_mapping
from agents_remember.serving.paseo.paseo_command import PaseoCli
from pnt_sandbox.environment import is_removed

from .adapters.execution import HERMES_ACTOR, HERMES_GPT_ACTOR, LaunchProfile
from .adapters.stdio_probe import probe_tool_server
from .artifacts import json_digest, read_object, sha256_file, write_object
from .codex_access import install_access_pool
from .fixtures import NATIVE_LAYOUT_BYTES, CaseBundle, TrialFixture, release_fixture, reset_case
from .hermes_profiles import bind_compaction, effort_declaration
from .hermes_runtime import HermesRuntimeBinding, derive_hermes_runtime
from .hermes_window import resolve_hermes_window
from .trial_isolation import (
    path_snapshot,
    private_build,
    source_hermes_home,
    sweep_hermes_credentials,
    trial_environment,
)
from .trial_native_setup import (
    import_native_sides,
    place_native_leaf_repositories,
    prepare_native_leaf,
    resolve_hermes_auxiliary,
    resolve_hermes_destination,
)

REPOSITORY_ID = "code"
RECORDER_ID = "ar-eval-recorder"
TOOL_ENVIRONMENT = frozenset(
    {
        "PATH",
        "HOME",
        "HERMES_HOME",
        "PWD",
        "PASEO_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
        "XDG_CACHE_HOME",
        "PYTHONPYCACHEPREFIX",
        "TMPDIR",
        "AR_DAGGER_AUTHORITY_ROOT",
        "TMUX_TMPDIR",
        "GIT_OPTIONAL_LOCKS",
        "GIT_CONFIG_NOSYSTEM",
        "GIT_CONFIG_GLOBAL",
        "NPM_CONFIG_USERCONFIG",
        "NPM_CONFIG_GLOBALCONFIG",
        "NPM_CONFIG_CACHE",
    }
)


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@dataclass(frozen=True)
class TrialLaunch:
    """One actor profile and its explicitly admitted access-only binding."""

    profile: LaunchProfile = HERMES_ACTOR
    codex_access: Path | None = None
    expected_end: float | None = None


_DEFAULT_TRIAL_LAUNCH = TrialLaunch()


@dataclass
class BatchHost:
    """One explicitly admitted Paseo home for the whole command batch."""

    candidate_code_root: Path
    sandbox_home: Path
    settings_path: Path
    identity: str
    environment_values: dict[str, str] = field(repr=False)
    started: bool = False
    requested: bool = False
    recorder_installed: bool = False
    configured_generation: str = ""
    applied_generation: str = ""
    hermes_runtime: HermesRuntimeBinding | None = None
    deferred_cleanups: list[Callable[[], None]] = field(default_factory=list, repr=False)

    def start(self) -> dict[str, Any]:
        if self.started and self.configured_generation == self.applied_generation:
            return {"ok": True, "home": str(self.sandbox_home), "reusedInBatch": True}
        self.requested = True
        result = self._command("provision", 1200)
        self.started = True
        self.applied_generation = self.configured_generation
        return result

    def configure_trial(
        self, environment: dict[str, str], *, profile: LaunchProfile = HERMES_ACTOR
    ) -> None:
        document = read_object(self.settings_path)
        providers = document["paseoRuntime"]["providers"]
        routing = {key: value for key, value in environment.items() if key in TOOL_ENVIRONMENT}
        if self.hermes_runtime is not None:
            self.hermes_runtime.verify()
            provider_environment = {**routing, **self.hermes_runtime.environment}
            if profile == HERMES_GPT_ACTOR:
                provider_environment["CODEX_HOME"] = environment["CODEX_HOME"]
            providers["hermes"]["env"] = provider_environment
        providers["codex"] = {"env": routing}
        self.configured_generation = json_digest(providers)
        write_object(self.settings_path, document)

    def _command(self, operation: str, timeout: int) -> dict[str, Any]:
        marker = read_object(self.sandbox_home / "agent-eval/batch.json")
        if marker.get("identity") != self.identity or marker.get("sandboxHome") != str(
            self.sandbox_home
        ):
            raise AuthorityError("batch home marker changed; preserve host state")
        runtime = read_object(self.settings_path).get("paseoRuntime", {})
        if runtime.get("home") != str(self.sandbox_home) or runtime.get("installPrefix") != str(
            self.sandbox_home / "agent-eval/paseo-prefix"
        ):
            raise AuthorityError("batch settings changed their admitted host paths")
        argv = [
            str(self.candidate_code_root / "mcp/.venv/bin/python"),
            "-P",
            "-m",
            "agents_remember.cli",
            "paseo",
            operation,
            "--config",
            str(self.settings_path),
        ]
        done = subprocess.run(
            argv,
            cwd=self.sandbox_home,
            env=self.environment_values,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        log = self.sandbox_home / f"agent-eval/host-{operation}.json"
        write_object(
            log,
            {
                "argv": argv,
                "exitCode": done.returncode,
                "stdout": done.stdout,
                "stderr": done.stderr,
            },
        )
        try:
            result = json.loads(done.stdout)
        except ValueError as error:
            raise AuthorityError(f"Paseo {operation} returned no JSON; see {log}") from error
        if done.returncode or not isinstance(result, dict) or result.get("ok") is not True:
            raise AuthorityError(f"Paseo {operation} failed; see {log}")
        return result

    def cleanup(self) -> None:
        """Stop the batch host; purge credentials even when supported stop fails."""
        try:
            if self.requested or self.deferred_cleanups:
                self._command("stop", 120)
                self.started = self.requested = False
            while self.deferred_cleanups:
                self.deferred_cleanups[0]()
                self.deferred_cleanups.pop(0)
        finally:
            _credential_sweep(self.sandbox_home, "end")

    def defer_cleanup(self, callback: Callable[[], None]) -> None:
        """Preserve an uncertain actor's fixture until supported host stop succeeds."""
        self.deferred_cleanups.append(callback)


def _credential_sweep(home: Path, phase: str) -> None:
    result = sweep_hermes_credentials(home / "trials")
    write_object(home / f"agent-eval/credential-cleanup-{phase}.json", result)
    if result["remainingPaths"]:
        raise AuthorityError("Credential sweep left files: " + "; ".join(result["remainingPaths"]))


def provision_batch(
    sandbox_home: Path, candidate_code_root: Path, *, hermes: bool = False
) -> BatchHost:
    home = sandbox_home.expanduser().resolve()
    candidate = candidate_code_root.resolve(strict=True)
    home.mkdir(parents=True, exist_ok=True)
    control = home / "agent-eval"
    control.mkdir(exist_ok=True)
    _credential_sweep(home, "start")
    environment = {
        key: value
        for key, value in os.environ.items()
        if not is_removed(key) and not key.startswith("HERMES_")
    }
    environment.update(
        {
            "PASEO_HOME": str(home),
            "XDG_CONFIG_HOME": str(control / "config"),
            "XDG_DATA_HOME": str(control / "data"),
            "XDG_STATE_HOME": str(control / "state"),
            "XDG_CACHE_HOME": str(control / "cache"),
            "PYTHONPYCACHEPREFIX": str(control / "pycache"),
            "TMUX_TMPDIR": str(control / "tmux"),
            "TMPDIR": str(control / "tmp"),
            "AR_DAGGER_AUTHORITY_ROOT": str(control / "authority"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_OPTIONAL_LOCKS": "0",
            "NPM_CONFIG_USERCONFIG": str(control / "config/npmrc"),
            "NPM_CONFIG_GLOBALCONFIG": str(control / "config/global.npmrc"),
            "NPM_CONFIG_CACHE": str(control / "cache/npm"),
        }
    )
    for key in (
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
        "XDG_CACHE_HOME",
        "TMUX_TMPDIR",
        "TMPDIR",
    ):
        Path(environment[key]).mkdir(parents=True, exist_ok=True)
    Path(environment["TMUX_TMPDIR"]).chmod(0o700)
    Path(environment["NPM_CONFIG_USERCONFIG"]).touch()
    Path(environment["NPM_CONFIG_GLOBALCONFIG"]).touch()
    settings = control / "settings/host-settings.json"
    runtime = (
        derive_hermes_runtime(
            ambient_path=os.environ.get("PATH", os.defpath),
            source_home=source_hermes_home(),
            probe_root=control / "hermes-runtime-probe",
        )
        if hermes
        else None
    )
    port = _free_port()
    document = {
        "version": 1,
        "coordinationRoot": str(control / "coordination"),
        "workspaceRoot": str(control / "projects"),
        "transcriptRoot": str(control / "tool-logs"),
        "repositories": {},
        "providers": {},
        "dashboard": {"autoStart": False, "port": _free_port()},
        "paseoRuntime": {
            "installPrefix": str(control / "paseo-prefix"),
            "home": str(home),
            "listen": f"127.0.0.1:{port}",
            "providers": (
                {
                    "hermes": {
                        "extends": "acp",
                        "label": "Hermes",
                        "command": list(runtime.command),
                        "env": runtime.environment,
                    }
                }
                if runtime is not None
                else {}
            ),
            "embed": [],
        },
    }
    write_object(settings, document)
    identity = str(uuid.uuid4())
    write_object(
        control / "batch.json",
        {
            "schema": "ar-curator-eval-batch/v1",
            "identity": identity,
            "sandboxHome": str(home),
            "candidateCodeRoot": str(candidate),
        },
    )
    return BatchHost(candidate, home, settings, identity, environment, hermes_runtime=runtime)


def _recorder_command(
    cli: PaseoCli, log: Path, commands: list[dict[str, Any]], *arguments: str
) -> Any:
    receipt: dict[str, Any] = {"arguments": list(arguments), "state": "pending"}
    commands.append(receipt)
    write_object(log, {"commands": commands})
    result = cli.call("plugin", *arguments, "--json")
    receipt.update(
        state="completed", exitCode=result.returncode, stdout=result.stdout, stderr=result.stderr
    )
    write_object(log, {"commands": commands})
    if result.returncode:
        raise AuthorityError(f"fixture recorder command failed; see {log}")
    try:
        return json.loads(result.stdout)
    except ValueError as error:
        raise AuthorityError(
            f"fixture recorder command returned invalid JSON; see {log}"
        ) from error


def _recorder_entry(value: Any, target: Path) -> dict[str, Any] | None:
    if not isinstance(value, list) or not all(isinstance(entry, dict) for entry in value):
        raise AuthorityError("fixture recorder plugin list is not an entry array")
    entries = [entry for entry in value if entry.get("id") == RECORDER_ID]
    if not entries:
        return None
    if len(entries) != 1 or entries[0].get("path") != str(target):
        raise AuthorityError("existing fixture recorder does not name its exact owned source path")
    return entries[0]


def _load_recorder(cli: PaseoCli, target: Path, log: Path) -> None:
    commands: list[dict[str, Any]] = []
    entry = _recorder_entry(_recorder_command(cli, log, commands, "ls"), target)
    if entry is None:
        _recorder_command(cli, log, commands, "install", str(target))
    else:
        if entry.get("enabled") is False:
            _recorder_command(cli, log, commands, "enable", RECORDER_ID)
        # The daemon can load an older copy when a supplied home starts another batch.
        # Reload the one owned identity after copying the current recorder source.
        _recorder_command(cli, log, commands, "reload", RECORDER_ID)
    final = _recorder_entry(_recorder_command(cli, log, commands, "ls"), target)
    if final is None or final.get("status") != "running" or final.get("enabled") is False:
        raise AuthorityError(f"fixture recorder is not running; see {log}")


@dataclass
class ProvisionedTrial:
    """The paths and native authority of one reset; the orchestrator owns cleanup."""

    fixture: TrialFixture
    candidate_code_root: Path
    project_root: Path
    code_root: Path
    memory_root: Path
    paseo_home: Path
    settings_path: Path
    tool_config_path: Path
    config: McpRuntimeConfig
    server_definition: dict[str, Any]
    tool_build: str
    native_capture_roots: dict[str, Path]
    batch_host: BatchHost
    environment_values: dict[str, str] = field(repr=False)
    profile: LaunchProfile = HERMES_ACTOR
    host_event_paths: tuple[Path, ...] = ()
    turn_ends_path: Path | None = None
    native_setup: dict[str, Any] = field(default_factory=dict)

    @property
    def identity(self) -> str:
        return self.fixture.identity

    def environment(self) -> dict[str, str]:
        return dict(self.environment_values)

    @property
    def scratch_root(self) -> Path:
        return self.project_root / "reports/scratch"

    def binding_prompt(self) -> str:
        """Factual native addresses, without modifying the measured Curator instructions."""
        return (
            f"Native fixture binding: project {self.project_root}; code {self.code_root}; "
            f"memory {self.memory_root}. The configured repository id is {REPOSITORY_ID!r}. "
            f"This trial's writable scratch root is {self.scratch_root}. "
            "Write temporary scripts, handoff inputs and probes there, within the admitted "
            "reports surface; a native harness's global cache/scratch is outside this trial. "
            + (
                f"The native knowledge_write contract_path is {self.native_setup['leaf']['contractPath']}; "
                f"authorizationRef is {self.native_setup['leaf']['authorization']}. "
                if self.native_setup.get("leaf")
                else f"The local knowledge_write wave is {self.fixture.case_id}-{self.identity!s}; use repo_id and wave. "
            )
            + "Read/check tools must name "
            "these fixture roots explicitly. Native tools are attached as agents-remember-task."
        )

    @contextlib.contextmanager
    def activated(self) -> Iterator[None]:
        """The bridge's native subprocesses use this trial's XDG and runtime authority."""
        previous = dict(os.environ)
        os.environ.clear()
        os.environ.update(self.environment_values)
        try:
            yield
        finally:
            os.environ.clear()
            os.environ.update(previous)

    def start_host(self) -> dict[str, Any]:
        self.batch_host.configure_trial(self.environment_values, profile=self.profile)
        return self.batch_host.start()

    def install_plugin(self, source: Path) -> None:
        """Install a fixture recorder through Paseo's supported plugin lifecycle."""
        if not self.batch_host.started:
            raise AuthorityError("start the batch host before installing its recorder")
        target = self.paseo_home / "agent-eval/recorder-plugin"
        write_object(
            self.paseo_home / "agent-eval/recorder-settings.json",
            {"projectRoot": str(self.project_root)},
        )
        if not self.batch_host.recorder_installed:
            if any(path.is_symlink() for path in source.rglob("*")):
                raise AuthorityError("recorder source contains a symlink")
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(source, target)
            with self.activated():
                _load_recorder(
                    PaseoCli(require_bridge_runtime(self.config)),
                    target,
                    self.fixture.root / "recorder-install.json",
                )
            self.batch_host.recorder_installed = True
        self.host_event_paths = (self.paseo_home / "agent-eval/host-events.jsonl",)
        self.turn_ends_path = self.paseo_home / "agents-remember/turn-ends.jsonl"

    def cleanup(self) -> None:
        """Release only this fresh case; the batch owner stops the shared host once."""
        release_fixture(self.fixture)


def _git_baseline(path: Path, environment: dict[str, str]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if (path / ".git").exists():
        raise AuthorityError(f"case input supplied Git administration at {path}; refuse it")
    commands = (
        ["init", "--initial-branch=eval-fixture"],
        ["add", "--all"],
        [
            "-c",
            "user.name=Curator eval fixture",
            "-c",
            "user.email=eval@invalid",
            "commit",
            "--allow-empty",
            "-m",
            "D637 disposable case material",
        ],
    )
    for arguments in commands:
        done = subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", *arguments],
            cwd=path,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        if done.returncode:
            raise AuthorityError(f"fixture Git baseline failed at {path}: {done.stderr.strip()}")


def _settings(fixture: TrialFixture, project: Path, batch: BatchHost) -> dict[str, Any]:
    document = read_object(batch.settings_path)
    document.update(
        coordinationRoot=str(project / "coordination"),
        workspaceRoot=str(project / "projects"),
        transcriptRoot=str(fixture.root / "tool-logs"),
        repositories={REPOSITORY_ID: {}},
    )
    return document


def _candidate_identity(info: dict[str, Any], document: dict[str, Any], candidate: Path) -> str:
    for key in ("coordinationRoot", "workspaceRoot"):
        if info.get(key) != document[key]:
            raise AuthorityError(f"candidate trial server resolved a foreign {key}")
    serving = info.get("servingBuild")
    if (
        not isinstance(serving, dict)
        or serving.get("packageRoot") != str(candidate / "mcp/src/agents_remember")
        or info.get("allowedRepoIds") != [REPOSITORY_ID]
    ):
        raise AuthorityError("trial server is not the selected candidate/repository binding")
    digest = serving.get("sourceDigest")
    if not isinstance(digest, str) or not digest:
        raise AuthorityError("candidate trial server returned no actual sourceDigest")
    return digest


def _place_native_memory(project: Path) -> tuple[Path, Path]:
    material = project / "projects/memory"
    memory = project / "coordination/memory-repos/ar-code"
    memory.parent.mkdir(parents=True, exist_ok=True)
    # Native memory operations resolve the physical tree before admitting its topology.
    # Keep one material copy at the official location; immutable case paths address it
    # through this single, explicitly captured alias.
    if material.exists():
        material.rename(memory)
    else:
        memory.mkdir()
    material.symlink_to("../coordination/memory-repos/ar-code", target_is_directory=True)
    return memory, material


def _place_trial_repositories(case: CaseBundle, fixture: TrialFixture) -> tuple[Path, Path, Path]:
    leaf_roots = place_native_leaf_repositories(case, fixture)
    if leaf_roots is not None:
        return leaf_roots
    memory, material = _place_native_memory(fixture.actor_root)
    return fixture.actor_root / "projects/code", memory, material


def _bind_hermes_route(
    fixture: TrialFixture,
    environment: dict[str, str],
    receipt: dict[str, Any],
    owner: BatchHost,
    profile: LaunchProfile,
) -> None:
    if owner.hermes_runtime is None:
        return
    owner.hermes_runtime.verify()
    receipt["runtime"] = owner.hermes_runtime.receipt
    probe_environment = {
        **owner.environment_values,
        **{key: value for key, value in environment.items() if key in TOOL_ENVIRONMENT},
    }
    if profile == HERMES_GPT_ACTOR:
        probe_environment["CODEX_HOME"] = environment["CODEX_HOME"]
    receipt["resolvedDestination"] = resolve_hermes_destination(
        owner.hermes_runtime,
        probe_environment,
        receipt_path=owner.sandbox_home
        / "agent-eval/provider-destinations"
        / (fixture.identity + ".json"),
        profile=profile,
    )
    receipt["resolvedAuxiliaryTasks"] = resolve_hermes_auxiliary(
        owner.hermes_runtime,
        probe_environment,
        receipt_root=owner.sandbox_home / "agent-eval/auxiliary-destinations" / fixture.identity,
    )
    receipt["window"] = resolve_hermes_window(
        owner.hermes_runtime,
        probe_environment,
        profile,
        receipt_path=owner.sandbox_home
        / "agent-eval/window-bindings"
        / (fixture.identity + ".json"),
    )
    receipt["compaction"] = {
        **bind_compaction(Path(environment["HERMES_HOME"]), receipt["window"]["resolvedWindow"]),
        "windowSource": receipt["window"]["windowSource"],
    }
    receipt["seatSettings"]["compressionThreshold"] = receipt["compaction"]["fraction"]
    receipt["seatSettings"]["declaredFacts"]["compaction"] = {
        **receipt["compaction"],
        "nativeBindingPending": False,
        "basis": "native get_model_context_length result before actor creation",
    }
    if (declaration := effort_declaration(profile)) is not None:
        receipt["effortEvidence"] = declaration


def _prepare_access_pool(
    profile: LaunchProfile, source: Path | None, expected_end: float | None, home: Path
) -> dict[str, Any] | None:
    """Only the GPT actor accepts an explicit access-only credential binding."""
    if profile == HERMES_GPT_ACTOR:
        if source is None or expected_end is None:
            raise AuthorityError("Hermes GPT access pool binding requires source and expected end")
        return install_access_pool(source, home, expected_end)
    if source is not None or expected_end is not None:
        raise AuthorityError("Codex access pool is only admitted for the Hermes GPT actor")
    return None


def provision_trial(
    case: CaseBundle,
    trials_root: Path,
    candidate_code_root: Path,
    *,
    batch_host: BatchHost,
    launch: TrialLaunch = _DEFAULT_TRIAL_LAUNCH,
) -> ProvisionedTrial:
    """Reset immutable case inputs, bind the real candidate server, and leave host stopped.

    D647 Hermes seat settings minus MCP are private to its fresh home. Only Hermes GPT gets
    an empty Codex home; the other actors retain their selector. The serving build is copied.
    """
    profile = launch.profile
    selected = candidate_code_root.resolve(strict=True)
    python = selected / "mcp/.venv/bin/python"
    if not python.is_file():
        raise AuthorityError(f"prepare the candidate Python environment first: {python}")
    fixture = reset_case(case, trials_root)
    try:
        candidate = private_build(selected, fixture.root / "serving-build")
        python = candidate / "mcp/.venv/bin/python"
        project = fixture.actor_root
        (project / "reports/scratch").mkdir(parents=True, exist_ok=True)
        code, memory, material = _place_trial_repositories(case, fixture)
        environment, provider_receipt = trial_environment(
            fixture,
            batch_host.environment_values,
            batch_host.sandbox_home,
            profile=profile,
        )
        access_pool = _prepare_access_pool(
            profile, launch.codex_access, launch.expected_end, Path(environment["HERMES_HOME"])
        )
        if access_pool is not None:
            provider_receipt["accessOnlyPool"] = access_pool
        _bind_hermes_route(fixture, environment, provider_receipt, batch_host, profile)
        memory.mkdir(parents=True, exist_ok=True)
        layout = memory / "knowledge/layout.json"
        if not layout.exists():
            layout.parent.mkdir(parents=True, exist_ok=True)
            layout.write_bytes(NATIVE_LAYOUT_BYTES)
        _git_baseline(code, environment)
        _git_baseline(memory, environment)
        native_setup = {
            "worklist": import_native_sides(case, fixture, environment),
            "leaf": prepare_native_leaf(case, fixture, code, memory, environment),
        }
        snapshot_path = fixture.root / "path-snapshot.json"
        write_object(snapshot_path, path_snapshot(environment, project))
        settings_path = fixture.root / "settings/agents-remember-settings.json"
        document = _settings(fixture, project, batch_host)
        coordination = project / "coordination"
        for name in ("system", "skills", "tasks", "memory-repos"):
            (coordination / name).mkdir(parents=True, exist_ok=True)
        (coordination / "system/settings.md").write_text(
            "# D637 disposable Curator trial\n", encoding="utf-8"
        )
        write_object(settings_path, document)
        write_object(
            Path(environment["XDG_CONFIG_HOME"]) / "agents-remember/settings.json",
            {"coordinationRoot": str(coordination)},
        )
        definition = {
            "type": "stdio",
            "command": str(python),
            "args": ["-P", "-m", "agents_remember.mcp", "--config", str(settings_path)],
            # Native auth stays in the host's process environment. MCP configuration is an
            # evidence file, so only explicit routing variables, never keys, are serialized.
            "env": {key: value for key, value in environment.items() if key in TOOL_ENVIRONMENT},
        }
        tool_config = fixture.root / "settings/mcp.json"
        write_object(tool_config, {"mcpServers": {"agents-remember-task": definition}})
        info = probe_tool_server(definition)
        build = _candidate_identity(info, document, candidate)
        write_object(fixture.root / "server-info.json", info)
        config = config_from_mapping(document, settings_path)
        homes = {"codex": Path(environment["CODEX_HOME"]).expanduser().resolve()}
        homes["hermes"] = Path(environment["HERMES_HOME"])
        provisioned = ProvisionedTrial(
            fixture,
            candidate,
            project,
            code,
            memory,
            batch_host.sandbox_home,
            settings_path,
            tool_config,
            config,
            definition,
            build,
            homes,
            batch_host,
            environment,
            profile=profile,
            turn_ends_path=batch_host.sandbox_home / "agents-remember/turn-ends.jsonl",
            native_setup=native_setup,
        )
        write_object(
            fixture.root / "native-binding.json",
            {
                "identity": fixture.identity,
                "case": fixture.case_id,
                "projectRoot": str(project),
                "codeRoot": str(code),
                "memoryRoot": str(memory),
                "memoryAlias": str(material),
                "repoId": REPOSITORY_ID,
                "wave": f"{case.case_id}-{fixture.identity}",
                "settings": str(settings_path),
                "toolBuild": build,
                "nativeHomes": {name: str(path) for name, path in homes.items()},
                "pathSnapshot": {"path": str(snapshot_path), "sha256": sha256_file(snapshot_path)},
                "providerIsolation": provider_receipt,
                "hermesRuntime": (
                    batch_host.hermes_runtime.receipt
                    if batch_host.hermes_runtime is not None
                    else None
                ),
                "nativeSetup": native_setup,
                "servingCopy": str(candidate),
            },
        )
        return provisioned
    except Exception:
        release_fixture(fixture)
        raise
