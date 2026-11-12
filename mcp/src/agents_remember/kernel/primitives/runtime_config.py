"""Runtime configuration loading for the Agents Remember server.

The runtime configuration record and parser live in kernel, which every layer
may import without reversing the package dependency direction.
"""

from __future__ import annotations

import json
import logging
import sys
import warnings
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any

from agents_remember.errors import AgentsRememberError
from agents_remember.kernel.agentic_settings import (
    AgenticSettingsError,
    load_agentic_settings,
    parse_gate_delegation,
    user_agentic_settings_path,
)
from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.memory_mode import (
    legacy_internal_memory_root,
    refuse_removed_memory_mode,
)
from agents_remember.kernel.primitives import checkout_coordination
from agents_remember.kernel.primitives.gate_policy import DEFAULT_GATE_POLICY, GatePolicy
from agents_remember.kernel.primitives.identity import (
    explicit_provider_instance_id,
    provider_instance_id,
)
from agents_remember.kernel.primitives.paseo_authority import (
    HostAuthorityRefused,
    load_authority_paseo_runtime,
)
from agents_remember.kernel.primitives.paseo_runtime_settings import (
    PaseoRuntimeSettings,
    PaseoRuntimeSettingsError,
)
from agents_remember.kernel.primitives.provider_degradation_settings import (
    ProviderDegradationSettings,
    ProviderDegradationSettingsError,
    parse_provider_degradation_settings,
)

DEFAULT_PROVIDER_SETUP_SECONDS = 1800
DEFAULT_DOCKER_CONTROL_SECONDS = 120

_logger = logging.getLogger(__name__)

#: Authority-file paths (with shadowed state) that already emitted the
#: one-cycle gateDelegation migration warning in this process. The warning
#: is a boot warning; per-call settings re-resolution must not re-emit it
#: (pytest escalates warnings to errors inside tool calls).
_LEGACY_GATE_DELEGATION_WARNED: set[tuple[str, bool]] = set()

# The documented timeout caps (see examples/mcp/settings.example.json and
# docs/reference/settings-json.md). Unknown keys are rejected so a typo
# (e.g. "providerSetupSecond") fails loudly instead of being silently stored and
# ignored -- matching how the providers section rejects unknown fields. Only
# providerSetupSeconds is currently consumed by the runtime; toolSeconds is a
# documented, reserved cap.
KNOWN_TIMEOUT_CAPS = frozenset({"providerSetupSeconds", "toolSeconds"})

DEFAULT_DASHBOARD_PORT = 8765

# Same fail-loud discipline as timeoutCaps: a typo ("autostart") must surface at
# boot, not silently leave the daemon unsupervised.
KNOWN_DASHBOARD_FIELDS = frozenset({"autoStart", "port"})
# MIK-R113 rule 4: top-level sections an earlier build read and this one does not. A file that
# holds one loads; nothing in it is read or checked, and its keys are named once as no longer
# used (``McpRuntimeConfig.unused_settings_keys``).
UNUSED_SETTINGS_SECTIONS = ("retirement",)
# MIK-R108 rule 3: the tool-call log keeping. Unknown keys fail loud, like
# every other settings family.
KNOWN_TOOL_LOG_FIELDS = frozenset({"keepDays", "maxBytes", "enclosureMaxBytes"})
# The agentic orchestration family lives in the user's settings
# file (260703-L13); the authority file keeps ONLY the one-cycle gateDelegation
# legacy fallback. loops/roles/concurrency/spawn here fail loud, pointing at
# the new home (roles/concurrency were previously reserved-and-silently-dropped
# -- that trap is closed).
KNOWN_AUTHORITY_ORCHESTRATION_FIELDS = frozenset({"gateDelegation"})


class ConfigError(AgentsRememberError):
    """Raised when MCP authority settings are missing or unsafe."""


@dataclass(frozen=True)
class RepositoryScope:
    repo_id: str
    path: Path
    memory_root: Path | None = None
    contract_path: Path | None = None
    certification_profile: Path | None = None


@dataclass(frozen=True)
class ProviderScope:
    provider_id: str
    runtime_root: Path
    log_root: Path
    instance_id: str
    scope: str = "workspace"


@dataclass(frozen=True)
class DashboardSettings:
    """The optional ``dashboard`` settings object (defaults keep it fully off)."""

    auto_start: bool = False
    port: int = DEFAULT_DASHBOARD_PORT


@dataclass(frozen=True)
class OrchestrationSettings:
    """The optional ``orchestration`` settings object."""

    gate_policy: GatePolicy = DEFAULT_GATE_POLICY
    require_reviewer_verdict_at_seams: bool = False


# MIK-R108 rule 3: the tool-call log keeping. 14 days when absent.
TOOL_LOG_KEEP_DAYS_DEFAULT = 14
# MIK-R108 rule 3: the log-folder budgets in bytes (D1-D4). 256 MiB for the
# transcript root, 64 MiB for the logs/ of one enclosure, when absent.
TOOL_LOG_MAX_BYTES_DEFAULT = 268435456
TOOL_LOG_ENCLOSURE_MAX_BYTES_DEFAULT = 67108864


@dataclass(frozen=True)
class ToolLogSettings:
    """The optional ``toolLog`` settings object (MIK-R108 rule 3).

    ``keepDays`` bounds the coordination-root tool log: the first call of a
    day removes the log files and detail folders older than it (14 when
    absent). In an enclosure, no age-based removal happens before cleanup.
    ``maxBytes`` bounds the transcript-root log folder in bytes (256 MiB
    when absent); ``enclosureMaxBytes`` bounds the ``logs/`` of one
    enclosure (64 MiB when absent). In an enclosure the budget is the only
    removal before cleanup.
    """

    keep_days: int = TOOL_LOG_KEEP_DAYS_DEFAULT
    max_bytes: int = TOOL_LOG_MAX_BYTES_DEFAULT
    enclosure_max_bytes: int = TOOL_LOG_ENCLOSURE_MAX_BYTES_DEFAULT


@dataclass(frozen=True)
class CheckoutBinding:
    """What an undeclared linked-checkout process is bound to (MIK-R83 rule 7).

    A process that loads this package from a linked worktree without declaring
    an execution mode is bound to that worktree's disposable coordination root,
    not the root its settings name. The same three facts are printed by the
    command line and carried as a field by ``server_info`` and the dashboard's
    served state, so no surface has to be asked separately.
    """

    worktree: str
    disposable_root: str
    configured_root: str | None

    def as_payload(self) -> dict[str, Any]:
        return {
            "state": "disposable-coordination-root",
            "worktree": self.worktree,
            "coordinationRoot": self.disposable_root,
            "configuredRoot": self.configured_root,
            "remedy": (
                "run the installed command for the configured root, or declare the test "
                "mode for scratch work; --config does not change the binding"
            ),
        }

    def sentence(self) -> str:
        configured = self.configured_root or "none named by the given settings"
        return (
            f"agents-remember is loaded from the linked worktree {self.worktree} and bound "
            f"to its disposable coordination root {self.disposable_root}; the configured "
            f"root {configured} is not used, and --config does not change the binding. "
            "Run the installed command for the configured root, or declare the test mode "
            "for scratch work."
        )


@dataclass(frozen=True)
class McpRuntimeConfig:
    config_path: Path
    coordination_root: Path
    workspace_root: Path
    transcript_root: Path
    repositories: dict[str, RepositoryScope] = field(default_factory=dict)
    providers: dict[str, ProviderScope] = field(default_factory=dict)
    timeout_caps: dict[str, int] = field(default_factory=dict)
    benchmarks_enabled: bool = False
    dashboard: DashboardSettings = field(default_factory=DashboardSettings)
    orchestration: OrchestrationSettings = field(default_factory=OrchestrationSettings)
    provider_degradation: ProviderDegradationSettings = field(
        default_factory=ProviderDegradationSettings
    )
    tool_log: ToolLogSettings = field(default_factory=ToolLogSettings)
    # MIK-R113 rule 4: the keys this file holds that are no longer read, as dotted names.
    unused_settings_keys: tuple[str, ...] = ()
    # MIK-R83 rule 7: the disposable coordination root an undeclared linked
    # checkout is bound to, with the configured root it did not use.
    checkout_binding: CheckoutBinding | None = None

    @property
    def paseo_runtime(self) -> PaseoRuntimeSettings | None:
        """Host authority is per-use, so adding its block never requires a server restart."""
        settings = _paseo_runtime_block(self.config_path)
        return None if settings is None else replace(settings, command_config_path=self.config_path)

    @property
    def allowed_repo_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self.repositories))

    @property
    def allowed_provider_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self.providers))


def load_config(config_path: str | Path) -> McpRuntimeConfig:
    """Load trusted authority or the leaf-local synthetic checkout config."""
    # Checkout selection precedes reads so a live authority path supplied by unpublished
    # worktree code cannot leak into the candidate writer.
    candidate = _config_for_execution(config_path)
    if isinstance(candidate, McpRuntimeConfig):
        return candidate
    path = candidate
    return config_from_mapping(_read_config_mapping(path), path)


@dataclass(frozen=True)
class ProviderAuthority:
    """The providers map re-read from the on-disk authority settings.

    Containment R1 (260707-HFX-L1): the boot-snapshot config is NOT launch
    authority. A server process loads its config once and closes over it, so
    editing the authority file to ``"providers": {}`` — the operator's only
    fleet-wide kill-switch — previously changed nothing until every running
    server restarted. Launch-capable operations re-read the file through this
    type instead. ``error`` carries the fail-closed reason when the file could
    not be read or parsed: callers must treat that as "no launch authority",
    never fall back to the snapshot. Stopping and cleanup are never gated.
    """

    providers: dict[str, ProviderScope]
    source_path: Path
    error: str | None = None

    def apply(self, config: McpRuntimeConfig) -> McpRuntimeConfig:
        """The boot config with the live providers map swapped in."""
        return replace(config, providers=dict(self.providers))


def reload_provider_authority(config: McpRuntimeConfig) -> ProviderAuthority:
    """Re-read only the providers map from the authority settings file."""
    path = config.config_path
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return ProviderAuthority(
            providers={},
            source_path=path,
            error=f"cannot re-read MCP authority settings: {path}: {error}",
        )
    if not isinstance(data, dict):
        return ProviderAuthority(
            providers={},
            source_path=path,
            error=f"MCP authority settings must be a JSON object: {path}",
        )
    try:
        providers = parse_providers(
            data.get("providers", {}),
            config.coordination_root,
            config.workspace_root,
        )
    except ConfigError as error:
        return ProviderAuthority(providers={}, source_path=path, error=str(error))
    return ProviderAuthority(providers=providers, source_path=path)


def require_provider_launch_authority(
    config: McpRuntimeConfig, *, operation: str
) -> McpRuntimeConfig:
    """Gate a provider-launching operation on the on-disk authority (fail-closed).

    Returns the boot config with the live providers map applied; raises
    ``ConfigError`` when the authority file disables providers or cannot be
    read. Stop/status/cleanup paths must not call this — stopping is always
    legal.
    """
    authority = reload_provider_authority(config)
    if authority.error is not None:
        raise ConfigError(f"{operation} refused (containment R1, fail-closed): {authority.error}")
    if not authority.providers:
        raise ConfigError(
            f"{operation} refused: providers are disabled in the on-disk authority settings "
            f"({authority.source_path}); the boot snapshot ({sorted(config.providers)}) is not "
            "launch authority (containment R1). stop/status/cleanup remain available."
        )
    return authority.apply(config)


def require_config_path(config_path: str | Path) -> Path:
    if not config_path:
        raise ConfigError("--config is required")

    path = Path(config_path)
    if not path.is_absolute():
        raise ConfigError("--config must be an absolute path")
    path = path.resolve()
    if path == user_agentic_settings_path().resolve():
        raise ConfigError(f"user agentic settings are not an MCP authority settings file: {path}")
    if path.name == "settings.json" and path.parent.name == "system":
        raise ConfigError("coordinator system/settings.json is not an MCP authority settings file")
    if not path.exists():
        raise ConfigError(f"MCP settings file does not exist: {path}")
    if path.is_dir():
        raise ConfigError(f"MCP settings path must be a file: {path}")
    return path


def config_from_mapping(data: dict[str, Any], config_path: Path) -> McpRuntimeConfig:
    coordination_root = required_absolute_path(data, "coordinationRoot")
    workspace_root = required_absolute_path(data, "workspaceRoot")
    transcript_root = optional_absolute_path(data, "transcriptRoot", owner="MCP settings")
    if transcript_root is None:
        transcript_root = coordination_root / "logs" / "mcp"

    if path_is_relative_to(config_path, coordination_root):
        raise ConfigError("MCP settings must not live inside the coordinator root")

    repositories = parse_repositories(
        data.get("repositories", {}),
        coordination_root,
        workspace_root,
    )
    _require_unique_repository_git_identities(repositories)
    providers = parse_providers(data.get("providers", {}), coordination_root, workspace_root)
    timeout_caps = parse_timeout_caps(data.get("timeoutCaps", {}))
    benchmarks_enabled = parse_benchmarks_enabled(data.get("benchmarksEnabled", False))
    warn_retired_direct_execution_key(data, config_path)
    dashboard = parse_dashboard_settings(data.get("dashboard"))
    try:
        provider_degradation = parse_provider_degradation_settings(data.get("providerDegradation"))
    except ProviderDegradationSettingsError as error:
        raise ConfigError(str(error)) from error
    orchestration = parse_orchestration_settings(
        data.get("orchestration"),
        coordination_root=coordination_root,
        config_path=config_path,
    )
    tool_log = parse_tool_log_settings(data.get("toolLog"))
    # MIK-R196 rule 3: the authority file now holds the host block it establishes itself, so that
    # block is read authority, not a stray key and not a per-harness override to migrate.

    return McpRuntimeConfig(
        config_path=config_path,
        coordination_root=coordination_root,
        workspace_root=workspace_root,
        transcript_root=transcript_root,
        repositories=repositories,
        providers=providers,
        timeout_caps=timeout_caps,
        benchmarks_enabled=benchmarks_enabled,
        dashboard=dashboard,
        orchestration=orchestration,
        provider_degradation=provider_degradation,
        tool_log=tool_log,
        unused_settings_keys=unused_settings_keys(data),
    )


def load_paseo_runtime_settings(config_path: str | Path) -> PaseoRuntimeSettings | None:
    """Read the ``paseoRuntime`` block of the authority file this call names.

    MIK-R196 rule 3: the host authority is the product's own settings file, so the block a cold
    first run writes and the block every later call reads are the same bytes in the same file.
    """
    # A cold root has no authority file yet: that is "no host configured", not an unreadable
    # settings file, and MIK-R196 rule 3 has the launch create the block before the host stage.
    candidate = Path(config_path).expanduser()
    if not candidate.exists() and not candidate.is_symlink():
        return None
    path = require_config_path(candidate)
    settings = _paseo_runtime_block(path)
    return None if settings is None else replace(settings, command_config_path=path)


def _paseo_runtime_block(config_path: Path) -> PaseoRuntimeSettings | None:
    # An unreadable or malformed authority file is a settings refusal with a recovery action, not
    # an exception that escapes the model: every caller answers `settings_invalid` (L188-B07).
    try:
        return load_authority_paseo_runtime(config_path)
    except (PaseoRuntimeSettingsError, HostAuthorityRefused) as error:
        raise ConfigError(str(error)) from error


def _parse_repository_entry(
    repo_id: str,
    value: object,
    coordination_root: Path,
    workspace_root: Path,
) -> RepositoryScope:
    if not isinstance(value, dict):
        raise ConfigError(f"repository settings for {repo_id!r} must be an object")

    repo_path = workspace_root / repo_id
    memory_root = default_memory_root(repo_path, coordination_root, repo_id)
    contract_path = optional_coordination_path(
        value,
        "contractPath",
        owner=f"repository {repo_id}",
        coordination_root=coordination_root,
    )
    if contract_path is not None and not path_is_relative_to(contract_path, coordination_root):
        raise ConfigError(f"repository {repo_id} contractPath must be inside the coordinator root")
    certification_profile = _optional_repository_profile_reference(repo_id, value)
    # memorySettingsIncludes was dead plumbing (parsed, never consumed); it was
    # removed with 260703-L13. A leftover key in an existing settings file is
    # tolerated-ignored like any other unknown repository field.
    return RepositoryScope(
        repo_id=repo_id,
        path=repo_path,
        memory_root=memory_root,
        contract_path=contract_path,
        certification_profile=certification_profile,
    )


def _optional_repository_profile_reference(
    repo_id: str,
    value: dict[str, Any],
) -> Path | None:
    raw = value.get("certificationProfile")
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw:
        raise ConfigError(
            f"repository {repo_id} certificationProfile must be one non-empty relative path"
        )
    path = PurePosixPath(raw)
    if (
        path.is_absolute()
        or not path.parts
        or "\\" in raw
        or (path.parts and len(path.parts[0]) >= 2 and path.parts[0][1] == ":")
        or path.as_posix() != raw
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ConfigError(
            f"repository {repo_id} certificationProfile must be a canonical, "
            "traversal-free repository-relative POSIX path"
        )
    return Path(*path.parts)


def parse_repositories(
    raw: object,
    coordination_root: Path,
    workspace_root: Path,
) -> dict[str, RepositoryScope]:
    if not isinstance(raw, dict):
        raise ConfigError("repositories must be an object keyed by repo id")

    repositories: dict[str, RepositoryScope] = {}
    for repo_id, value in raw.items():
        if not isinstance(repo_id, str) or not repo_id:
            raise ConfigError("repository ids must be non-empty strings")
        repositories[repo_id] = _parse_repository_entry(
            repo_id, value, coordination_root, workspace_root
        )
    return repositories


def _require_unique_repository_git_identities(
    repositories: dict[str, RepositoryScope],
) -> None:
    """Refuse two configured repository ids that own one physical Git repository."""

    owners: dict[Path, tuple[str, str]] = {}
    for repo_id, repository in repositories.items():
        code_identity = _configured_git_identity(repository.path)
        if code_identity is not None:
            _claim_configured_git_identity(owners, code_identity, repo_id, "code")
        memory_identity = (
            _configured_git_identity(repository.memory_root)
            if repository.memory_root is not None
            else None
        )
        # A memory root inside the code repository would not be a second authority edge, but
        # no supported mode has one: `default_memory_root` refuses that layout before this
        # check, so a memory identity here is always a real external edge.
        if memory_identity is not None and memory_identity != code_identity:
            _claim_configured_git_identity(owners, memory_identity, repo_id, "memory")


def _configured_git_identity(path: Path) -> Path | None:
    if not path.exists():
        return None
    result = run_git(path, ["rev-parse", "--path-format=absolute", "--git-common-dir"])
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return Path(value).resolve() if value else None


def _claim_configured_git_identity(
    owners: dict[Path, tuple[str, str]],
    identity: Path,
    repo_id: str,
    side: str,
) -> None:
    previous = owners.get(identity)
    if previous is not None and previous[0] != repo_id:
        raise ConfigError(
            f"repository ids {previous[0]!r} ({previous[1]}) and {repo_id!r} ({side}) "
            f"share Git common-dir {identity.as_posix()}; one physical repository must "
            "have exactly one configured owner"
        )
    owners[identity] = (repo_id, side)


def default_memory_root(repo_path: Path, coordination_root: Path, repo_id: str) -> Path:
    """The configured repository's memory root: the external one, always.

    A repo-sidecar ``<repo>/ar-memory`` root used to win here silently. It is now reported
    instead: a repository still carrying one is in the removed layout, and answering with its
    path would re-admit the mode through the configuration reader.
    """
    legacy_root = legacy_internal_memory_root(repo_path)
    if legacy_root.exists():
        refuse_removed_memory_mode("internal", artifact=legacy_root.as_posix())
    return coordination_root / "memory-repos" / f"ar-{repo_id}"


def parse_providers(
    raw: object,
    coordination_root: Path,
    workspace_root: Path,
) -> dict[str, ProviderScope]:
    if not isinstance(raw, dict):
        raise ConfigError("providers must be an object keyed by provider id")

    providers: dict[str, ProviderScope] = {}
    for provider_id, value in raw.items():
        if not isinstance(provider_id, str) or not provider_id:
            raise ConfigError("provider ids must be non-empty strings")
        if not isinstance(value, dict):
            raise ConfigError(f"provider settings for {provider_id!r} must be an object")

        provider_config = parse_provider_config(value, workspace_root)
        unsupported = sorted(set(value) - {"instanceId", "scope"})
        if unsupported:
            unsupported_text = ", ".join(unsupported)
            raise ConfigError(
                f"provider {provider_id} settings are derived by the server; "
                f"remove unsupported fields: {unsupported_text}"
            )
        provider_name = provider_runtime_name(provider_id)

        providers[provider_id] = ProviderScope(
            provider_id=provider_id,
            runtime_root=(
                coordination_root
                / "providers"
                / "runners"
                / provider_name
                / provider_config["instance_id"]
            ),
            log_root=(
                coordination_root
                / "logs"
                / "providers"
                / provider_name
                / provider_config["instance_id"]
            ),
            instance_id=provider_config["instance_id"],
            scope=provider_config["scope"],
        )
    return providers


def parse_provider_config(
    value: dict[str, Any],
    workspace_root: Path,
) -> dict[str, str]:
    scope = value.get("scope", "workspace")
    if not isinstance(scope, str) or not scope:
        raise ConfigError("provider scope must be a non-empty string")
    # scope is an open set (workspace/worktree/benchmark + custom benchmark scopes);
    # provider_instance_id slugifies it internally, so do not pre-mangle it here.
    raw_instance = value.get("instanceId")
    if raw_instance is None:
        instance_id = provider_instance_id(scope, workspace_root)
    elif not isinstance(raw_instance, str) or not raw_instance:
        raise ConfigError("provider instanceId must be a non-empty string")
    else:
        instance_id = explicit_provider_instance_id(raw_instance)
    return {"scope": scope, "instance_id": instance_id}


def provider_runtime_name(provider_id: str) -> str:
    names = {
        "codegraphcontext-code": "codegraphcontext",
        "grepai-memory": "grepai",
    }
    try:
        return names[provider_id]
    except KeyError as error:
        raise ConfigError(f"unsupported provider id: {provider_id}") from error


def warn_retired_direct_execution_key(data: dict[str, Any], config_path: Path) -> None:
    """The retired switch loads with one notice per load: a grant per task decides now.

    Logged, not warned: the warnings policy suppresses a repeated notice for
    the same file in one process, while the packet requires one notice per
    load under the ordinary default configuration (F11).
    """
    if "directExecutionEnabled" in data:
        _logger.warning(
            "directExecutionEnabled in %s is retired and ignored; "
            "direct execution is decided by a direct-execution grant per task now "
            "(given by the developer on the task in the dashboard, or by an "
            "architect with direct_execution_grant). Nothing is refused because of the key.",
            config_path,
        )


def parse_benchmarks_enabled(raw: object) -> bool:
    if not isinstance(raw, bool):
        raise ConfigError("benchmarksEnabled must be a boolean")
    return raw


def parse_dashboard_settings(raw: object) -> DashboardSettings:
    if raw is None:
        return DashboardSettings()
    if not isinstance(raw, dict):
        raise ConfigError("dashboard settings must be an object")
    unknown = sorted(set(raw) - KNOWN_DASHBOARD_FIELDS)
    if unknown:
        allowed = ", ".join(sorted(KNOWN_DASHBOARD_FIELDS))
        unknown_text = ", ".join(unknown)
        raise ConfigError(f"unsupported dashboard setting(s): {unknown_text}; allowed: {allowed}")
    auto_start = raw.get("autoStart", False)
    if not isinstance(auto_start, bool):
        raise ConfigError("dashboard.autoStart must be a boolean")
    port = raw.get("port", DEFAULT_DASHBOARD_PORT)
    if isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536:
        raise ConfigError("dashboard.port must be an integer in 1..65535")
    return DashboardSettings(auto_start=auto_start, port=port)


def unused_settings_keys(data: dict[str, Any]) -> tuple[str, ...]:
    """The keys of the sections that are no longer read, as dotted names (MIK-R113 rule 4)."""
    names: list[str] = []
    for section in UNUSED_SETTINGS_SECTIONS:
        if section not in data:
            continue
        held = data[section]
        if isinstance(held, dict) and held:
            names.extend(f"{section}.{key}" for key in sorted(held))
        else:
            names.append(section)
    return tuple(names)


def parse_tool_log_settings(raw: object) -> ToolLogSettings:
    if raw is None:
        return ToolLogSettings()
    if not isinstance(raw, dict):
        raise ConfigError("toolLog settings must be an object")
    unknown = sorted(set(raw) - KNOWN_TOOL_LOG_FIELDS)
    if unknown:
        allowed = ", ".join(sorted(KNOWN_TOOL_LOG_FIELDS))
        unknown_text = ", ".join(unknown)
        raise ConfigError(f"unsupported toolLog setting(s): {unknown_text}; allowed: {allowed}")
    keep_days = raw.get("keepDays", TOOL_LOG_KEEP_DAYS_DEFAULT)
    if isinstance(keep_days, bool) or not isinstance(keep_days, int) or keep_days < 1:
        raise ConfigError("toolLog.keepDays must be an integer of at least 1")
    max_bytes = raw.get("maxBytes", TOOL_LOG_MAX_BYTES_DEFAULT)
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
        raise ConfigError("toolLog.maxBytes must be an integer of at least 1")
    enclosure_max_bytes = raw.get("enclosureMaxBytes", TOOL_LOG_ENCLOSURE_MAX_BYTES_DEFAULT)
    if (
        isinstance(enclosure_max_bytes, bool)
        or not isinstance(enclosure_max_bytes, int)
        or enclosure_max_bytes < 1
    ):
        raise ConfigError("toolLog.enclosureMaxBytes must be an integer of at least 1")
    return ToolLogSettings(
        keep_days=keep_days, max_bytes=max_bytes, enclosure_max_bytes=enclosure_max_bytes
    )


def parse_orchestration_settings(
    raw: object,
    *,
    coordination_root: Path,
    config_path: Path,
) -> OrchestrationSettings:
    """Resolve the boot-snapshot orchestration settings (260703-L13, GQ1).

    ``orchestration.gateDelegation``'s home is the GLOBAL agentic settings file
    (``~/.config/agents-remember/settings.json``), read once at boot through the
    kernel agentic-settings loader -- a change still needs a restart (documented
    boot-snapshot semantics; the downstream ``gate_policy`` wiring is
    unchanged). An authority-file value is honored as a one-cycle legacy
    fallback WITH a boot warning naming the new home; when the global file also
    sets the key, the global value wins and the shadowed authority value warns.
    Any other ``orchestration.*`` key in the authority file fails loud -- those
    knobs never belonged here (loops) or moved home (roles/concurrency/spawn).
    """
    legacy = _parse_legacy_authority_gate_delegation(raw, config_path)
    try:
        agentic = load_agentic_settings(coordination_root)
    except AgenticSettingsError as error:
        raise ConfigError(str(error)) from error
    if agentic.gate_delegation_configured:
        if legacy is not None:
            _warn_legacy_gate_delegation(config_path, shadowed=True)
        return OrchestrationSettings(
            gate_policy=agentic.gate_policy,
            require_reviewer_verdict_at_seams=agentic.require_reviewer_verdict_at_seams,
        )
    if legacy is not None:
        _warn_legacy_gate_delegation(config_path, shadowed=False)
        return legacy
    return OrchestrationSettings()


def _parse_legacy_authority_gate_delegation(
    raw: object,
    config_path: Path,
) -> OrchestrationSettings | None:
    """The authority file's ``orchestration`` block: gateDelegation-only, legacy."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigError("orchestration settings must be an object")
    unknown = sorted(set(raw) - KNOWN_AUTHORITY_ORCHESTRATION_FIELDS)
    if unknown:
        unknown_text = ", ".join(unknown)
        raise ConfigError(
            f"unsupported orchestration setting(s): {unknown_text}; the MCP "
            "authority file only reads the legacy gateDelegation fallback -- "
            "agentic orchestration settings (gateDelegation, loops, roles, "
            "rolesPerLevel, concurrency, spawn, harnesses) live in "
            f"{user_agentic_settings_path()}"
        )
    gate_raw = raw.get("gateDelegation")
    if gate_raw is None:
        return None
    try:
        gate_policy, require_verdict_at_seams = parse_gate_delegation(
            gate_raw, source=str(config_path)
        )
    except AgenticSettingsError as error:
        raise ConfigError(str(error)) from error
    return OrchestrationSettings(
        gate_policy=gate_policy,
        require_reviewer_verdict_at_seams=require_verdict_at_seams,
    )


def _warn_legacy_gate_delegation(
    config_path: Path,
    *,
    shadowed: bool,
) -> None:
    """One-cycle migration warning (GQ1): the authority-file key must move home."""
    key = (str(config_path), shadowed)
    if key in _LEGACY_GATE_DELEGATION_WARNED:
        return
    _LEGACY_GATE_DELEGATION_WARNED.add(key)
    new_home = user_agentic_settings_path()
    if shadowed:
        message = (
            f"orchestration.gateDelegation in the MCP authority file ({config_path}) "
            f"is IGNORED: the global agentic settings file ({new_home}) already "
            "defines it -- remove the authority-file copy"
        )
    else:
        message = (
            "orchestration.gateDelegation moved to the global agentic settings "
            f"file ({new_home}); the MCP authority file value ({config_path}) is "
            "honored as a one-cycle legacy fallback -- move it now"
        )
    warnings.warn(message, stacklevel=2)


def parse_timeout_caps(raw: object) -> dict[str, int]:
    if not isinstance(raw, dict):
        raise ConfigError("timeoutCaps must be an object")
    if "providerSeconds" in raw:
        raise ConfigError(
            "timeoutCaps.providerSeconds was renamed to providerSetupSeconds; "
            "indexing and seed are now always uncapped"
        )

    parsed: dict[str, int] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key:
            raise ConfigError("timeout cap names must be non-empty strings")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ConfigError(f"timeout cap {key!r} must be a non-negative integer")
        if key not in KNOWN_TIMEOUT_CAPS:
            allowed = ", ".join(sorted(KNOWN_TIMEOUT_CAPS))
            raise ConfigError(f"unsupported timeout cap {key!r}; allowed: {allowed}")
        parsed[key] = value
    return parsed


def required_absolute_path(data: dict[str, Any], key: str, *, owner: str = "MCP settings") -> Path:
    if key not in data:
        raise ConfigError(f"{owner} must define {key}")
    return require_absolute_json_path(data[key], f"{owner}.{key}")


def optional_absolute_path(data: dict[str, Any], key: str, *, owner: str) -> Path | None:
    if key not in data or data[key] is None:
        return None
    return require_absolute_json_path(data[key], f"{owner}.{key}")


def optional_coordination_path(
    data: dict[str, Any],
    key: str,
    *,
    owner: str,
    coordination_root: Path,
) -> Path | None:
    if key not in data or data[key] is None:
        return None
    value = data[key]
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{owner}.{key} must be a non-empty string")
    path = Path(value)
    resolved = path.resolve() if path.is_absolute() else (coordination_root / path).resolve()
    if not path_is_relative_to(resolved, coordination_root):
        raise ConfigError(f"{owner} {key} must be inside the coordinator root")
    return resolved


def require_absolute_json_path(value: object, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{label} must be a non-empty string")
    path = Path(value)
    if not path.is_absolute():
        raise ConfigError(f"{label} must be an absolute path")
    return path.resolve()


def path_is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _config_for_execution(config_path: str | Path) -> Path | McpRuntimeConfig:
    try:
        checkout = checkout_coordination.checkout_cli_location()
    except checkout_coordination.CheckoutCoordinationError as error:
        raise ConfigError(str(error)) from error
    if checkout is None:
        return require_config_path(config_path)
    binding = CheckoutBinding(
        worktree=checkout.checkout_root.as_posix(),
        disposable_root=checkout.coordination_root.as_posix(),
        configured_root=_configured_coordination_root(config_path),
    )
    # MIK-R83 rule 7: one line, on stderr, before the run answers from the
    # disposable root. A silent empty world was the debt this ends.
    print(binding.sentence(), file=sys.stderr)
    return _checkout_runtime_config(
        checkout_root=checkout.checkout_root,
        worktree_group=checkout.worktree_group,
        coordination_root=checkout.coordination_root,
        synthetic_config_path=checkout.synthetic_config_path,
        binding=binding,
    )


def _configured_coordination_root(config_path: str | Path) -> str | None:
    """The root the given settings name, named but not used by a bound process.

    It is only read for the sentence: a settings file that is absent, not JSON
    or names no root contributes nothing, and the sentence says so rather than
    inventing a root the caller never wrote.
    """

    if not config_path:
        return None
    path = Path(config_path)
    try:
        if not path.is_absolute() or not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    root = data.get("coordinationRoot") if isinstance(data, dict) else None
    return str(root) if isinstance(root, str) and root else None


def _read_config_mapping(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ConfigError(f"cannot parse MCP settings JSON: {path}: {error}") from error
    if not isinstance(data, dict):
        raise ConfigError(f"MCP settings must be a JSON object: {path}")
    return data


def _checkout_runtime_config(
    *,
    checkout_root: Path,
    worktree_group: Path,
    coordination_root: Path,
    synthetic_config_path: Path,
    binding: CheckoutBinding,
) -> McpRuntimeConfig:
    """Build the minimal non-authority config for an unpublished linked checkout."""
    repository = RepositoryScope(
        repo_id="agents-remember",
        path=checkout_root,
        memory_root=coordination_root / "memory-repos" / "ar-agents-remember",
    )
    return McpRuntimeConfig(
        config_path=synthetic_config_path,
        coordination_root=coordination_root,
        workspace_root=worktree_group,
        transcript_root=coordination_root / "logs" / "mcp",
        repositories={"agents-remember": repository},
        providers={},
        benchmarks_enabled=False,
        dashboard=DashboardSettings(auto_start=False),
        orchestration=OrchestrationSettings(),
        checkout_binding=binding,
    )
