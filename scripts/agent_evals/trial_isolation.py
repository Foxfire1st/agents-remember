"""D641 trial-local provider state, serving build bytes and executable observations."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from agents_remember.errors import AuthorityError

from .adapters.execution import HERMES_ACTOR, HERMES_GPT_ACTOR, LaunchProfile
from .artifacts import json_digest, write_object
from .fixtures import TrialFixture
from .hermes_profiles import hermes_auxiliary_tasks, hermes_route
from .trial_skill_policy import install_skill_policy

# Provider parsing uses the system's prepared PyYAML, independently of actor PATH
# and the serving venv (which need not contain PyYAML). No interpreter fallback.
_PROVIDER_PYTHON = "/usr/bin/python3"
_PROVIDER_PARSER = r"""
import json,sys,yaml
from pathlib import Path
try:
    raw=yaml.safe_load(Path(sys.argv[1]).read_text()) or {}
    if not isinstance(raw,dict): raise ValueError('provider config is not a mapping')
except (yaml.YAMLError, UnicodeError, OSError, ValueError) as error:
    # YAML diagnostics can quote credentials, including on their last line.
    print(type(error).__name__ + ': provider config rejected', file=sys.stderr)
    sys.exit(1)
# D647: migrate the seat mapping, rather than maintain a provider-only projection.
# Captured stdout is private in-memory input, never a report or config digest.
source_keys=sorted(raw)
out=raw
out.pop('mcp_servers',None)
for key in ('model','terminal','agent','compression','platform_toolsets'):
    if key in out and not isinstance(out[key],dict):
        print('ValueError: provider config rejected',file=sys.stderr)
        sys.exit(1)
platforms=raw.get('platform_toolsets')
if isinstance(platforms,dict) and 'acp' in platforms:
    acp=platforms['acp']
    if not isinstance(acp,list) or not all(isinstance(name,str) for name in acp):
        print('ValueError: provider config rejected',file=sys.stderr)
        sys.exit(1)
print(json.dumps({'config':out,'sourceKeys':source_keys,'parserVersion':yaml.__version__}))
"""


def _provider_stderr(stderr: str) -> str:
    """Retain only known safe last-line diagnostics, never YAML source excerpts."""
    lines = stderr.strip().splitlines()
    last = lines[-1] if lines else ""
    if last == "ModuleNotFoundError: No module named 'yaml'":
        return last
    kind, separator, detail = last.partition(": ")
    if (
        separator
        and kind.isidentifier()
        and len(kind) <= 64
        and detail == "provider config rejected"
    ):
        return last
    return "stderr detail withheld" if last else "no stderr diagnostic"


def _declared_facts(document: dict[str, Any], route: dict[str, Any]) -> dict[str, Any]:
    """Keep native provider-default policy distinct from an explicit effort proof."""
    if route["effortPolicy"] == "max":
        basis = "D647 model-free native source and resolved-toolset proofs; not a live request observation"
        effort = {
            "configurationKey": "agent.reasoning_effort",
            "outboundField": "reasoning_effort",
            "value": "max",
            "scope": "initial OpenCodeGoProfile DeepSeek V4 chat-completions request; per-model overrides remain in force",
        }
        compaction = {
            "fraction": 0.40,
            "sourceDefaultWindow": 1_000_000,
            "sourceInitialTriggerTokens": 400_000,
            "scope": "cold private-home metadata cache, no context or model-threshold override, no output reservation; later auxiliary feasibility may lower the trigger",
            "runtimeWindowObserved": False,
        }
    else:
        if route["effortPolicy"] == "high":
            basis = (
                "D650 explicit Hermes GPT configuration; native outgoing proof recorded separately"
            )
            effort = {
                "configurationKey": "agent.reasoning_overrides." + route["model"],
                "value": "high",
                "runtimeRequestObserved": False,
            }
        else:
            basis = "D649 installed native Go profile source; not a request or window observation"
            effort = {
                "configurationKeysCleared": ["agent.reasoning_effort", "agent.reasoning_overrides"],
                "policy": "provider-default",
                "mechanism": "native Go Kimi K3 profile emits no effort field",
                "parity": "unresolved",
                "runtimeRequestObserved": False,
            }
        compaction = {
            "fraction": 0.40,
            "absoluteTargetTokens": 400_000,
            "nativeBindingPending": True,
            "scope": "initial fraction is provisional until native preflight derives the fraction for absolute 400000 from its resolved window",
            "runtimeWindowObserved": False,
        }
    facts = {
        "basis": basis,
        "effort": effort,
        "maxTurns": document["agent"].get("max_turns"),
        "compaction": compaction,
        "personality": {
            "resolved": None,
            "scope": "ACP _make_agent passes no ephemeral_system_prompt and calls no personality resolver; definitions are not injected",
        },
        "delegateTask": {
            "absent": True,
            "scope": "D647 native resolved-toolset check for the copied seat ACP toolsets; not inferred from the delegation key",
        },
    }
    return facts


def _seat_seed_names(
    document: dict[str, Any], route: dict[str, Any]
) -> tuple[list[str], list[str]]:
    """Seed only the actor and the three configured DeepSeek seat tasks."""
    tasks = hermes_auxiliary_tasks(document)
    names = list(route["seedNames"])
    for task in tasks:
        for name in task["seedNames"]:
            if name not in names:
                names.append(name)
    return names, [task["task"] for task in tasks]


def _set_actor_effort(agent: dict[str, Any], route: dict[str, Any]) -> None:
    """Bind explicit effort or preserve the provider-default no-field policy."""
    if route["effortPolicy"] == "max":
        agent["reasoning_effort"] = "max"
    elif route["effortPolicy"] == "high":
        agent["reasoning_effort"] = "high"
        overrides = agent.setdefault("reasoning_overrides", {})
        if not isinstance(overrides, dict):
            raise AuthorityError("Hermes GPT reasoning overrides must be a mapping")
        overrides[route["model"]] = "high"
    else:
        # Overrides precede the global setting in Hermes' resolver.
        agent.pop("reasoning_effort", None)
        agent.pop("reasoning_overrides", None)


def hermes_home(
    source: Path, target: Path, project: Path, *, profile: LaunchProfile = HERMES_ACTOR
) -> dict[str, Any]:
    """Seed D647 seat settings privately, excluding MCP; never copy skills or sessions."""
    route = hermes_route(profile)
    target.mkdir(parents=True, exist_ok=False)
    target.chmod(0o700)
    document: dict[str, Any] = {}
    parser: dict[str, str] = {}
    source_keys: list[str] = []
    config = source / "config.yaml"
    if config.is_file():
        executable = _PROVIDER_PYTHON
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(target),
            "HERMES_HOME": str(target),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        # Verify the dependency before giving the interpreter any provider file.
        for stage, script, arguments in (
            ("verification", "import yaml", []),
            ("parsing", _PROVIDER_PARSER, [str(config)]),
        ):
            try:
                done = subprocess.run(
                    [executable, "-I", "-B", "-c", script, *arguments],
                    env=env,
                    cwd=target,
                    capture_output=True,
                    text=True,
                    check=False,
                )
            except OSError as error:
                raise AuthorityError(
                    f"Hermes provider YAML {stage} via {executable} failed: "
                    f"{type(error).__name__} (errno {error.errno})"
                ) from None
            if done.returncode:
                raise AuthorityError(
                    f"Hermes provider YAML {stage} via {executable} failed: "
                    + _provider_stderr(done.stderr)
                )
        parsed = json.loads(done.stdout)
        document = parsed["config"]
        source_keys = parsed["sourceKeys"]
        parser = {"path": str(Path(executable).resolve()), "yamlVersion": parsed["parserVersion"]}
    # ACP constructs its initial agent from model.default. An empty initial model
    # can advertise the picker's first choice as current, causing Paseo to skip
    # its matching model override. Bind the admitted profile before ACP starts.
    # Rebuild the provider-bound block. Updating only provider/default makes
    # Hermes' matching-provider URL guard accept the previous provider's URL.
    document["model"] = {"provider": route["provider"], "default": route["model"]}
    model_binding = {
        "key": "model.default",
        "value": route["model"],
        "provider": route["provider"],
        "selectedModel": profile.model,
        "origin": route["origin"],
    }
    document.setdefault("terminal", {})["cwd"] = str(project)
    agent = document.setdefault("agent", {})
    _set_actor_effort(agent, route)
    document.setdefault("compression", {})["threshold"] = 0.40
    # A fixture-owned denial removes skill management from both trial arms.
    skill_policy = install_skill_policy(target, document)
    write_object(target / "config.yaml", document)
    (target / "config.yaml").chmod(0o600)
    files = ["config.yaml"]
    seed_names, auxiliary_tasks = _seat_seed_names(document, route)
    copied_names: set[str] = set()
    dotenv = source / ".env"
    if dotenv.is_file():
        selected = []
        for line in dotenv.read_text().splitlines():
            stripped = line.strip().removeprefix("export ")
            name = stripped.partition("=")[0].strip()
            if "=" in stripped and name in seed_names:
                selected.append(stripped)
                copied_names.add(name)
        if selected:
            (target / ".env").write_text("\n".join(selected) + "\n")
            (target / ".env").chmod(0o600)
            files.append(".env")
    # Only the fixture-owned hook is hashed. The secret-bearing config is
    # compared in memory by trial_skill_policy, never digested into evidence.
    return {
        "provider": route["provider"],
        "files": files,
        "parser": parser,
        "modelBinding": model_binding,
        "credentialSeed": {
            "configuredAuxiliaryTasks": auxiliary_tasks,
            "requiredNames": seed_names,
            "copiedNames": [name for name in seed_names if name in copied_names],
            "boundary": "provider selection, not file scope; selected dotenv lines are visible to the entire trial home",
        },
        "acpToolsets": {
            "seedConfigException": 2,
            "authority": "D646",
            "name": "source ACP platform toolsets only",
            "key": "platform_toolsets.acp",
            "sourceConfig": str(config),
            "copied": "platform_toolsets" in document,
            "value": document.get("platform_toolsets", {}).get("acp"),
            "scope": "D647: source seat settings in both arms; no MCP servers",
        },
        "seatSettings": {
            "authority": "D647",
            "sourceConfig": str(config),
            "topLevelKeys": sorted(document),
            "removedTopLevelKeys": [key for key in source_keys if key not in document],
            "overriddenTopLevelKeys": [
                "model",
                "terminal",
                "agent",
                "compression",
                "hooks",
                "hooks_auto_accept",
            ],
            "overriddenPaths": [
                "model.provider",
                "model.default",
                "terminal.cwd",
                *(["agent.reasoning_effort"] if route["effortPolicy"] in ("max", "high") else []),
                *(
                    ["agent.reasoning_overrides." + route["model"]]
                    if route["effortPolicy"] == "high"
                    else []
                ),
                "compression.threshold",
                "hooks",
                "hooks_auto_accept",
            ],
            "maxTurns": document["agent"].get("max_turns"),
            "reasoningEffort": route["effortPolicy"],
            "compressionThreshold": 0.40,
            "declaredFacts": _declared_facts(document, route),
            "configEvidence": "secret-bearing values stay private; no config copies or digests in evidence",
            "privateConfigMode": "0600",
        },
        "skillPolicy": skill_policy,
    }


CREDENTIAL_NAMES = (".env", "auth.json", "auth.lock", "config.yaml")


def _credential_scope(path: Path) -> None:
    if not path.is_absolute() or ".." in path.parts:
        raise AuthorityError(f"Credential cleanup requires an absolute scoped path: {path}")


def _credential_directory(path: Path, stack: ExitStack) -> int | None:
    """Open every component without following links; never read file contents."""
    _credential_scope(path)
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    stack.callback(os.close, descriptor)
    for component in path.parts[1:]:
        try:
            entry = os.stat(component, dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(entry.st_mode):
            raise AuthorityError(f"Credential cleanup rejects symlink traversal: {path}")
        descriptor = os.open(
            component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
        )
        stack.callback(os.close, descriptor)
    return descriptor


def _credential_entries(home: Path, descriptor: int) -> list[str]:
    present = []
    for name in CREDENTIAL_NAMES:
        try:
            entry = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(entry.st_mode):
            raise AuthorityError(f"Credential cleanup rejects symlink: {home / name}")
        if not stat.S_ISREG(entry.st_mode):
            raise AuthorityError(f"Credential cleanup requires a regular file: {home / name}")
        present.append(name)
    return present


def _purge_credential_homes(homes: list[Path], stack: ExitStack) -> dict[str, list[str]]:
    opened = []
    for home in homes:
        descriptor = _credential_directory(home, stack)
        if descriptor is not None:
            opened.append((home, descriptor, _credential_entries(home, descriptor)))
    # Validate the entire sweep before removing any file. Directory descriptors
    # keep each unlink in its opened home even if a path is subsequently moved.
    removed = []
    for home, descriptor, names in opened:
        for name in names:
            os.unlink(name, dir_fd=descriptor)
            removed.append(str(home / name))
    remaining = [
        str(home / name)
        for home, descriptor, _ in opened
        for name in _credential_entries(home, descriptor)
    ]
    return {
        "visitedHomes": [str(home) for home in homes],
        "removedPaths": removed,
        "remainingPaths": remaining,
    }


def purge_hermes_credentials(home: Path, *, trials_root: Path) -> dict[str, list[str]]:
    """Remove credentials and private config from one explicitly scoped trial home."""
    _credential_scope(trials_root)
    _credential_scope(home)
    if home.parent.parent != trials_root or home.name != "hermes":
        raise AuthorityError(f"Credential home is outside the trial cleanup scope: {home}")
    try:
        with ExitStack() as stack:
            return _purge_credential_homes([home], stack)
    except OSError as error:
        raise AuthorityError(
            f"Credential cleanup failed for {home}: {type(error).__name__}"
        ) from None


def sweep_hermes_credentials(trials_root: Path) -> dict[str, list[str]]:
    """Purge only immediate trials/*/hermes homes, without following symlinks."""
    _credential_scope(trials_root)
    try:
        with ExitStack() as stack:
            descriptor = _credential_directory(trials_root, stack)
            homes = []
            if descriptor is not None:
                for name in sorted(os.listdir(descriptor)):
                    trial = trials_root / name
                    entry = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                    if stat.S_ISLNK(entry.st_mode):
                        raise AuthorityError(f"Credential sweep rejects symlink: {trial}")
                    if stat.S_ISDIR(entry.st_mode):
                        home = trial / "hermes"
                        if _credential_directory(home, stack) is not None:
                            homes.append(home)
            return _purge_credential_homes(homes, stack)
    except OSError as error:
        raise AuthorityError(
            f"Credential sweep failed for {trials_root}: {type(error).__name__}"
        ) from None


def private_build(source: Path, target: Path) -> Path:
    """Copy independent bytes, including the prepared interpreter environment."""
    ignored = shutil.ignore_patterns(
        ".git",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        "node_modules",
        ".agents",
        ".codex",
        ".aws",
        ".env",
    )
    shutil.copytree(source, target, ignore=ignored, symlinks=False)
    for path in (target / "mcp/.venv").rglob("*.pth"):
        data = path.read_text()
        if str(source) in data:
            path.write_text(data.replace(str(source), str(target)))
    for path in (target / "mcp/.venv/bin").iterdir():
        if path.is_file():
            data = path.read_bytes()
            if data.startswith(b"#!") and str(source).encode() in data.split(b"\n", 1)[0]:
                path.write_bytes(data.replace(str(source).encode(), str(target).encode()))
    python = target / "mcp/.venv/bin/python"
    if not python.is_file():
        raise AuthorityError("Trial serving copy has no prepared interpreter")
    return target


def path_snapshot(environment: dict[str, str], project: Path) -> dict[str, Any]:
    """Observe resolution from the exact future actor PATH, without running commands."""
    executable_map = {}
    for entry in environment.get("PATH", "").split(os.pathsep):
        directory = Path(entry) if entry else project
        if not directory.is_absolute():
            directory = project / directory
        try:
            children = sorted(directory.iterdir())
        except (FileNotFoundError, NotADirectoryError, PermissionError):
            continue
        for child in children:
            if child.name not in executable_map and child.is_file() and os.access(child, os.X_OK):
                executable_map[child.name] = {
                    "path": str(child.absolute()),
                    "realPath": str(child.resolve()),
                }
    payload = {
        "schema": "ar-eval-path-snapshot/v1",
        "path": environment.get("PATH", ""),
        "executables": executable_map,
    }
    return {**payload, "sha256": json_digest(payload)}


def source_hermes_home() -> Path:
    """The explicit developer credential source shared by setup and observation."""
    return Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))).expanduser().resolve()


def credential_borrowing(source: Path, *, profile: LaunchProfile = HERMES_ACTOR) -> dict[str, str]:
    """Declare the existing provider seed without emitting secret values or hashes."""
    return {
        "access": "read-only",
        "sourceHome": str(source),
        "provider": hermes_route(profile)["provider"],
        "mechanism": "selected actor and configured seat-task dotenv copied into private HERMES_HOME; bounded by provider selection, not file scope",
    }


def trial_environment(
    fixture: TrialFixture,
    batch_environment: dict[str, str],
    sandbox_home: Path,
    *,
    profile: LaunchProfile = HERMES_ACTOR,
) -> tuple[dict[str, str], dict[str, Any]]:
    """Bind a fresh Hermes home and the batch runtime without serializing credentials."""
    project = fixture.actor_root
    environment = fixture.environment(dict(os.environ))
    environment = {
        key: value for key, value in environment.items() if not key.startswith("HERMES_")
    }
    source_home = source_hermes_home()
    provider_home = fixture.root / "hermes"
    seed_profile = profile if profile.harness == "hermes" else HERMES_ACTOR
    provider_receipt = hermes_home(source_home, provider_home, project, profile=seed_profile)
    provider_receipt["credentialBorrowing"] = credential_borrowing(
        source_home, profile=seed_profile
    )
    codex_home = os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))
    if profile == HERMES_GPT_ACTOR:
        private_codex = fixture.root / "code-home"
        private_codex.mkdir(mode=0o700)
        private_codex.chmod(0o700)
        codex_home = str(private_codex)
    environment.update(
        HERMES_HOME=str(provider_home),
        HOME=str(fixture.root / "home"),
        CODEX_HOME=codex_home,
        CLAUDE_CONFIG_DIR=os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude")),
    )
    Path(environment["HOME"]).mkdir()
    environment.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL="/dev/null",
        PASEO_HOME=str(sandbox_home),
        XDG_DATA_HOME=batch_environment["XDG_DATA_HOME"],
        XDG_CACHE_HOME=batch_environment["XDG_CACHE_HOME"],
        TMPDIR=str(fixture.root / "tmp"),
        NPM_CONFIG_USERCONFIG=batch_environment["NPM_CONFIG_USERCONFIG"],
        NPM_CONFIG_GLOBALCONFIG=batch_environment["NPM_CONFIG_GLOBALCONFIG"],
        NPM_CONFIG_CACHE=batch_environment["NPM_CONFIG_CACHE"],
    )
    Path(environment["TMPDIR"]).mkdir(exist_ok=True)
    for key in (
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
        "XDG_CACHE_HOME",
        "TMUX_TMPDIR",
    ):
        Path(environment[key]).mkdir(parents=True, exist_ok=True)
    Path(environment["TMUX_TMPDIR"]).chmod(0o700)
    return environment, provider_receipt
