"""Resolve the AR-owned eve runtime application and its one launch environment.

The adapter never shells out to a user-supplied command and never reads ambient credentials:
every value that reaches the runtime is derived here from the adapter's own launch spec, the
resolved settings selection, and an explicitly named runtime root.

Root resolution order is fixed so a source checkout and an installed wheel both work without a
second code path:

1. ``AR_EVE_RUNTIME_ROOT`` when the operator sets it (the development escape hatch);
2. the runtime application shipped inside this package's own data
   (``package_data/runtime/eve-agent``), materialized to a temporary directory when the package
   is installed as a zip;
3. the ``eve_runtime`` directory beside the repository root, for a checkout that has not synced
   package data yet.

A root that does not contain a compiled-ready application is refused with the exact paths tried,
never silently replaced by a different application.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import socket
import subprocess
import tempfile
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

from agents_remember.errors import HarnessControlError
from agents_remember.models.conversations.control_wire import LaunchSpec
from agents_remember.serving.eve_protocol import EveRuntimeLaunch
from agents_remember.serving.harness_capabilities import LaunchKnobs

RUNTIME_ROOT_ENV = "AR_EVE_RUNTIME_ROOT"
NODE_EXECUTABLE_ENV = "AR_EVE_NODE"
PACKAGED_RUNTIME_PATH = ("runtime", "eve-agent")
DEFAULT_NODE_EXECUTABLE = "node"
MINIMUM_NODE_MAJOR = 24
"""The oldest Node major the pinned eve release will start under; it refuses below this."""
EVE_PINNED_VERSION = "0.56.0"
DEFAULT_RUNTIME_PORT = 0
"""Zero means "choose a free loopback port at launch"; a fixed port collides across sessions."""


def choose_runtime_port(host: str = "127.0.0.1") -> int:
    """Reserve one free loopback port for an eve runtime about to start.

    The socket is closed before the child binds it: the window is a local port reservation, and a
    collision fails the launch loudly rather than silently serving on a port the adapter cannot
    address.
    """

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((host, DEFAULT_RUNTIME_PORT))
        return int(probe.getsockname()[1])


OWNED_ENV_PREFIX = "AR_EVE_"
"""Variables this adapter owns on the launch line; free-form launch args must not redeclare them."""

PROVIDER_BASE_URL_ENV = "AR_EVE_PROVIDER_BASE_URL"
PROVIDER_API_KEY_ENV = "AR_EVE_PROVIDER_API_KEY"
PROVIDER_NAME_ENV = "AR_EVE_PROVIDER_NAME"
MODEL_ENV = "AR_EVE_MODEL"
EFFORT_ENV = "AR_EVE_EFFORT"
CONTEXT_WINDOW_ENV = "AR_EVE_CONTEXT_WINDOW_TOKENS"
BINDING_REF_ENV = "AR_BINDING_REF"
CAPSULE_DIGEST_ENV = "AR_CAPSULE_DIGEST"
WORKSPACE_ROOT_ENV = "AR_WORKSPACE_ROOT"
STATE_ROOT_ENV = "AR_EVE_STATE_ROOT"
"""Where each epoch's staged application directory is created.

eve keeps one development server per application root, so two live runtimes of the same authored
application need separate roots. The default derives from the admitted workspace, which is the
surface this adapter already owns and is allowed to write; an operator may override it.
"""

DEFAULT_CONTEXT_WINDOW_TOKENS = 200_000

RUNTIME_ENTRYPOINT = ("node_modules", "eve", "bin", "eve.js")


@dataclass(frozen=True)
class EveLaunchSelection:
    """The model/effort/provider selection one eve runtime is started with."""

    model_key: str
    effort: str
    provider_base_url: str | None = None
    provider_api_key: str | None = None
    provider_name: str = "ar-eve"
    context_window_tokens: int = DEFAULT_CONTEXT_WINDOW_TOKENS

    def __post_init__(self) -> None:
        for label, value in (("model", self.model_key), ("effort", self.effort)):
            if not value or value != value.strip():
                raise HarnessControlError(
                    f"eve launch {label} must be non-empty with no outer whitespace"
                )
        if self.context_window_tokens < 1:
            raise HarnessControlError("eve launch context window must be positive")


@dataclass(frozen=True)
class EveWorkspaceBinding:
    """The admitted workspace and AR binding a run is launched against.

    The capsule/workspace leaf owns where these values come from. This adapter only carries them:
    it applies the binding before the first model call and confines the runtime's file tools to
    the named root.
    """

    workspace_root: Path
    binding_ref: str | None = None
    capsule_digest: str | None = None


@dataclass(frozen=True)
class EveRuntimeSpec:
    """A fully resolved launch: process, environment, and the paths to report back as evidence.

    ``node_executable`` is ``None`` until a real process is started. Resolving an interpreter is a
    process-launch concern and belongs to the transport that spawns it, not to a spec that a
    deterministic test transport also consumes without ever executing a binary.
    """

    root: Path
    launch: EveRuntimeLaunch
    port: int
    env: Mapping[str, str] = field(default_factory=dict)
    node_executable: str | None = None


def resolve_runtime_root(
    *,
    env: Mapping[str, str] | None = None,
    repo_root: Path | None = None,
) -> Path:
    """Find the AR-owned eve application; refuse rather than substitute a different one."""

    environ = dict(env if env is not None else os.environ)
    override = environ.get(RUNTIME_ROOT_ENV)
    if override:
        candidate = Path(override).expanduser()
        _require_application(candidate, tried=(candidate,))
        return candidate
    tried: list[Path] = []
    with contextlib.suppress(FileNotFoundError, ModuleNotFoundError):
        packaged = resources.files("agents_remember").joinpath(
            "package_data", *PACKAGED_RUNTIME_PATH
        )
        if isinstance(packaged, Path):
            tried.append(packaged)
            if _is_application(packaged):
                return packaged
    root = repo_root if repo_root is not None else _checkout_root()
    if root is not None:
        candidate = root / "eve_runtime"
        tried.append(candidate)
        if _is_application(candidate):
            return candidate
    raise HarnessControlError(
        "the AR-owned eve runtime application was not found; tried "
        + ", ".join(str(path) for path in tried)
        + f" (set {RUNTIME_ROOT_ENV} to name one explicitly)"
    )


def materialize_runtime_root(
    *, env: Mapping[str, str] | None = None
) -> contextlib.AbstractContextManager[Path]:
    """Yield the runtime root, unpacking the packaged copy only when it is not a real directory."""

    environ = dict(env if env is not None else os.environ)
    if environ.get(RUNTIME_ROOT_ENV) or _checkout_application() is not None:
        return contextlib.nullcontext(resolve_runtime_root(env=environ))
    return packaged_runtime_root()


@contextlib.contextmanager
def packaged_runtime_root() -> Iterator[Path]:
    """Yield a filesystem path for the packaged application, unpacking archives when needed."""

    root = resources.files("agents_remember").joinpath("package_data", *PACKAGED_RUNTIME_PATH)
    if isinstance(root, Path):
        yield root
        return
    with tempfile.TemporaryDirectory(prefix="ar-eve-runtime-") as temp_dir:
        destination = Path(temp_dir) / "eve-agent"
        _copy_tree(root, destination)
        yield destination


def stage_runtime_root(source: Path, destination: Path) -> Path:
    """Give one epoch its own application directory, linked to the shared dependency install.

    eve resolves the application root from the process working directory and keeps one development
    server per resolved root, so two live runtimes of the same authored application collide unless
    each owns a directory. The authored surface is copied and the installed dependencies are
    symlinked, so only eve's own generated state is per-epoch.

    Staging the same destination twice is idempotent, which is what lets an epoch restart without
    rebuilding its tree.
    """

    if (destination / "agent" / "agent.ts").is_file():
        return destination
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("package.json", "package-lock.json", "agent", "tsconfig.json"):
        origin = source / name
        if not origin.exists():
            continue
        target = destination / name
        if target.exists() or target.is_symlink():
            continue
        if origin.is_dir():
            shutil.copytree(origin, target)
        else:
            shutil.copy2(origin, target)
    modules = destination / "node_modules"
    if not modules.exists() and not modules.is_symlink():
        installed = source / "node_modules"
        if not installed.is_dir():
            raise HarnessControlError(
                f"the eve application at {source} has no installed dependencies; run its "
                "documented install before staging a runtime"
            )
        with contextlib.suppress(FileExistsError):
            # Another epoch staged the same destination concurrently; the link it created is the
            # one this caller wanted, so the stage is complete rather than failed.
            modules.symlink_to(installed)
    return destination


def resolve_runtime_spec(
    *,
    selection: EveLaunchSelection,
    binding: EveWorkspaceBinding,
    env: Mapping[str, str] | None = None,
    port: int = DEFAULT_RUNTIME_PORT,
    state_root: Path | None = None,
) -> EveRuntimeSpec:
    """Build the complete launch for one bridge epoch.

    ``state_root`` stages a per-epoch application directory under it; without one the runtime uses
    the resolved application in place, which is right when only one runs at a time.
    """

    environ = dict(env if env is not None else os.environ)
    source = resolve_runtime_root(env=environ)
    root = stage_runtime_root(source, state_root) if state_root is not None else source
    bound_port = port or choose_runtime_port()
    runtime_env = build_runtime_env(selection=selection, binding=binding, base=environ)
    return EveRuntimeSpec(
        root=root,
        port=bound_port,
        env=runtime_env,
        launch=EveRuntimeLaunch(
            runtime_root=str(root),
            # The caller's interpreter choice is carried verbatim so a bad path fails at spawn with
            # the path it named. Everything else about the executable -- the search, the minimum
            # version, the refusal that advertises both -- belongs to the transport that spawns it.
            node_executable=environ.get(NODE_EXECUTABLE_ENV),
            port=bound_port,
            env=runtime_env,
        ),
    )


def build_runtime_env(
    *,
    selection: EveLaunchSelection,
    binding: EveWorkspaceBinding,
    base: Mapping[str, str],
) -> dict[str, str]:
    """Compose the child environment: the operator's base plus this adapter's owned values.

    Owned values always win over an inherited value of the same name, so an ambient
    ``AR_EVE_MODEL`` can never silently redirect a launch the settings selection already fixed.
    """

    child = dict(base)
    # The two values the adapter owns as launch selectors are resolved into the launch itself
    # before this point, so the child must not re-read them: a stale ambient root or interpreter
    # would otherwise point the process at another application.
    child.pop(RUNTIME_ROOT_ENV, None)
    child.pop(NODE_EXECUTABLE_ENV, None)
    child[MODEL_ENV] = selection.model_key
    child[EFFORT_ENV] = selection.effort
    child[PROVIDER_NAME_ENV] = selection.provider_name
    child[CONTEXT_WINDOW_ENV] = str(selection.context_window_tokens)
    child[WORKSPACE_ROOT_ENV] = str(binding.workspace_root)
    if selection.provider_base_url is not None:
        child[PROVIDER_BASE_URL_ENV] = selection.provider_base_url
    if selection.provider_api_key is not None:
        child[PROVIDER_API_KEY_ENV] = selection.provider_api_key
    if binding.binding_ref is not None:
        child[BINDING_REF_ENV] = binding.binding_ref
    if binding.capsule_digest is not None:
        child[CAPSULE_DIGEST_ENV] = binding.capsule_digest
    return child


def eve_launch_knobs(*, model_key: str, effort: str | None) -> LaunchKnobs:
    """How eve spells its model/effort selection: entirely through the adapter-owned environment.

    eve has no argv vocabulary for a model or an effort -- its model is a compiled application
    value -- so the selection rides the environment the adapter composes, and no free-form launch
    argument may redeclare one of those names.
    """

    if not model_key or model_key != model_key.strip():
        raise HarnessControlError("eve launch model must be non-empty with no outer whitespace")
    return LaunchKnobs(
        argv=(),
        env={MODEL_ENV: model_key, EFFORT_ENV: effort or "provider-default"},
        owned_argv_options=(),
        owned_config_keys=(),
    )


def launch_spec_selection(launch: LaunchSpec) -> EveLaunchSelection:
    """Read the selection the runner already applied to one launch spec.

    The runner applies :func:`eve_launch_knobs` before the process exists, so the launch spec is
    the authority here -- not ambient process state.
    """

    model = launch.env.get(MODEL_ENV)
    if not model:
        raise HarnessControlError(f"eve launch requires the settings-resolved model in {MODEL_ENV}")
    effort = launch.env.get(EFFORT_ENV) or "provider-default"
    context_window = launch.env.get(CONTEXT_WINDOW_ENV)
    try:
        window = int(context_window) if context_window else DEFAULT_CONTEXT_WINDOW_TOKENS
    except ValueError as exc:
        raise HarnessControlError(f"eve launch {CONTEXT_WINDOW_ENV} must be an integer") from exc
    return EveLaunchSelection(
        model_key=model,
        effort=effort,
        provider_base_url=launch.env.get(PROVIDER_BASE_URL_ENV),
        provider_api_key=launch.env.get(PROVIDER_API_KEY_ENV),
        provider_name=launch.env.get(PROVIDER_NAME_ENV) or "ar-eve",
        context_window_tokens=window,
    )


def launch_spec_state_root(launch: LaunchSpec) -> Path:
    """Where to stage this epoch's application directory."""

    explicit = launch.env.get(STATE_ROOT_ENV)
    if explicit:
        return Path(explicit).expanduser()
    return launch.cwd / ".ar-eve-runtime"


def launch_spec_binding(launch: LaunchSpec) -> EveWorkspaceBinding:
    """Read the admitted workspace and binding one launch spec carries."""

    return EveWorkspaceBinding(
        workspace_root=launch.cwd,
        binding_ref=launch.env.get(BINDING_REF_ENV),
        capsule_digest=launch.env.get(CAPSULE_DIGEST_ENV),
    )


def _require_application(root: Path, *, tried: tuple[Path, ...]) -> None:
    if not _is_application(root):
        raise HarnessControlError(
            "named eve runtime root is not a complete application (needs package.json and "
            f"agent/agent.ts): tried {', '.join(str(path) for path in tried)}"
        )


def _is_application(root: Path) -> bool:
    return (root / "package.json").is_file() and (root / "agent" / "agent.ts").is_file()


def _checkout_root() -> Path | None:
    """The repository root of this module, when it is running from a source checkout.

    Found by walking up for the sibling ``eve_runtime`` directory rather than by counting path
    levels, so moving this module inside the package cannot silently repoint the lookup.
    """

    for parent in Path(__file__).resolve().parents:
        if (parent / "eve_runtime").is_dir():
            return parent
    return None


def _checkout_application() -> Path | None:
    root = _checkout_root()
    if root is None:
        return None
    candidate = root / "eve_runtime"
    return candidate if _is_application(candidate) else None


def resolve_node_executable(env: Mapping[str, str] | None = None) -> str:
    """Find a node new enough to run the pinned eve release.

    eve 0.56.0 refuses to start below Node 24, so a first-on-PATH older node is a launch failure
    with an advertised remedy rather than a confusing runtime error: the candidates are tried in
    order, and a too-old PATH node raises with the exact requirement named.
    """

    environ = dict(env if env is not None else os.environ)
    override = environ.get(NODE_EXECUTABLE_ENV)
    if override:
        return override
    # Newest-major candidates first: a machine whose PATH node is an older release than an
    # installed nvm runtime must still find the one eve will start under.
    candidates = [*_nvm_node_candidates(), *(shutil.which(name) or "" for name in ("node",))]
    versions: list[str] = []
    for candidate in candidates:
        if not candidate:
            continue
        major = _node_major_version(candidate)
        if major is None:
            continue
        versions.append(f"{candidate} (v{major})")
        if major >= MINIMUM_NODE_MAJOR:
            return candidate
    advertised = ", ".join(versions) or "none found"
    raise HarnessControlError(
        f"the eve runtime needs Node.js >= {MINIMUM_NODE_MAJOR} (eve {EVE_PINNED_VERSION} "
        f"refuses to start below it); candidates tried: {advertised}. Set {NODE_EXECUTABLE_ENV} "
        "to a compatible node executable."
    )


def _nvm_node_candidates() -> tuple[str, ...]:
    """Node versions installed through nvm, newest major first."""

    root = Path.home() / ".nvm" / "versions" / "node"
    if not root.is_dir():
        return ()
    found = sorted(
        (entry / "bin" / "node" for entry in root.iterdir() if entry.is_dir()),
        key=lambda path: path.parent.parent.name,
        reverse=True,
    )
    return tuple(str(path) for path in found if path.is_file())


def _node_major_version(executable: str) -> int | None:
    try:
        completed = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    text = completed.stdout.strip().lstrip("v")
    major = text.split(".", 1)[0]
    return int(major) if major.isdigit() else None


def _copy_tree(source: Traversable, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for child in source.iterdir():
        target = destination / child.name
        if child.is_dir():
            _copy_tree(child, target)
        elif child.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(child.read_bytes())
