"""Derive Hermes' prepared runtime from its actual ambient published launcher.

Only the observed shell wrappers and literal top-level sys.path.insert are
parsed. The owner's public committed_venv/venv_python API reads selection;
runtime_command builds the launch. Neither probe boots application code.
Actor -B and disable-lazy-installs are explicit MIK Manager 064848 approvals.
"""

from __future__ import annotations

import ast
import json
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents_remember.errors import AuthorityError

from .artifacts import sha256_file

_SOURCE_FILES = (
    "hermes_bootstrap.py",
    "hermes_cli/_launchers.py",
    "hermes_cli/venv_sync.py",
    "pm/environments.py",
    "hermes_cli/version_info.py",
    "hermes_cli/__init__.py",
    "hermes_cli/_early_recovery.py",
    "hermes_cli/update_lock.py",
    "pyproject.toml",
)
_DISCOVER = """
import json,sys
from pathlib import Path
root=Path(sys.argv[1]);sys.path.insert(0,str(root))
import pm.environments as native
from hermes_cli.version_info import get_version_info
from hermes_cli import __release_date__
from hermes_cli._early_recovery import interrupted_pull_marker
from hermes_cli.update_lock import update_marker_path
markers=(interrupted_pull_marker(root),update_marker_path())
for marker in markers:
 if marker.is_file(): raise RuntimeError('Hermes runtime repair/update pending: '+str(marker))
selected=native.committed_venv(root)
if selected is None: raise RuntimeError('Hermes has no committed dependency environment')
info=get_version_info()
print(json.dumps({'preparedPython':str(native.venv_python(selected)),
 'environment':str(selected),'apiSource':native.__file__,
 'version':info.derived_version,'versionSource':info.source,
 'versionString':f'Hermes Agent v{info.derived_version} ({__release_date__})',
 'checkedRepairMarkers':[str(marker) for marker in markers]}))
"""
_COMMAND = """
import json,sys
from pathlib import Path
root=Path(sys.argv[1]);sys.path.insert(0,str(root))
import hermes_cli._launchers as native
from hermes_cli.version_info import get_version_info
import openai
print(json.dumps({'command':native.runtime_command(root,('acp',),python=Path(sys.argv[2])),
 'python':sys.executable,'prefix':sys.prefix,'basePrefix':sys.base_prefix,
 'launcherSource':native.__file__,'openaiVersion':openai.__version__,
 'openaiOrigin':openai.__file__,'version':get_version_info().derived_version}))
"""


def _identity(path: Path) -> tuple[int, int, int, int, int]:
    value = path.stat()
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


@dataclass(frozen=True)
class _RuntimeFile:
    path: Path
    resolved: Path
    sha256: str
    identity: tuple[int, int, int, int, int]

    @classmethod
    def capture(cls, path: Path) -> _RuntimeFile:
        before = _identity(path)
        digest = sha256_file(path)
        if before != _identity(path):
            raise AuthorityError(f"Hermes runtime changed while reading: {path}")
        return cls(path, path.resolve(strict=True), digest, before)

    def verify(self) -> None:
        if self.path.resolve(strict=True) != self.resolved or _identity(self.path) != self.identity:
            raise AuthorityError(f"Hermes runtime changed after binding: {self.path}")

    def receipt(self) -> dict[str, str]:
        return {"path": str(self.path), "resolvedPath": str(self.resolved), "sha256": self.sha256}


@dataclass(frozen=True)
class HermesRuntimeBinding:
    command: tuple[str, ...]
    source_root: Path
    python: Path
    files: tuple[_RuntimeFile, ...]
    probe: dict[str, Any]
    ambient: dict[str, Any]
    official_command: tuple[str, ...]

    @property
    def environment(self) -> dict[str, str]:
        return {"HERMES_DISABLE_LAZY_INSTALLS": "true"}

    @property
    def receipt(self) -> dict[str, Any]:
        return {
            "schema": "ar-hermes-runtime-binding/v1",
            "sourceRoot": str(self.source_root),
            "preparedPython": str(self.python),
            "command": list(self.command),
            "officialCommand": list(self.official_command),
            "environment": self.environment,
            "files": [value.receipt() for value in self.files],
            "probe": {key: value for key, value in self.probe.items() if key != "command"},
            "ambient": dict(self.ambient),
            "approvedDifferences": [
                {
                    "kind": "bootstrap",
                    "value": "official runtime_command",
                    "authority": "20261030T064848Z",
                },
                {"kind": "argv", "value": "-B", "authority": "20261030T064848Z"},
                {"kind": "environment", "value": self.environment, "authority": "20261030T064848Z"},
            ],
        }

    def verify(self) -> None:
        try:
            for value in self.files:
                value.verify()
            for marker in self.ambient["checkedRepairMarkers"]:
                if Path(marker).is_file():
                    raise AuthorityError(f"Hermes runtime repair/update pending: {marker}")
        except OSError as error:
            raise AuthorityError(f"Hermes runtime became unavailable: {error}") from error


def _wrapper(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("#!/bin/sh\n"):
        raise AuthorityError(
            f"unsupported Hermes launcher kind=shell; path={path}; sha256={sha256_file(path)}"
        )
    return shlex.split(text, comments=True)


def _source_root(vendor: Path, words: list[str]) -> Path:
    if (
        len(words) != 6
        or words[0] != "exec"
        or words[2:4] != ["-I", "-c"]
        or words[-1] != "$@"
        or not Path(words[1]).is_absolute()
    ):
        raise AuthorityError(
            f"unsupported Hermes vendor argv shape; path={vendor}; sha256={sha256_file(vendor)}"
        )
    candidates = []
    try:
        tree = ast.parse(words[4])
    except SyntaxError as error:
        raise AuthorityError(
            f"unsupported Hermes vendor Python AST; path={vendor}; sha256={sha256_file(vendor)}"
        ) from error
    for statement in tree.body:
        if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Call):
            continue
        call = statement.value
        if ast.unparse(call.func) == "sys.path.insert":
            if (
                len(call.args) != 2
                or call.keywords
                or not isinstance(call.args[0], ast.Constant)
                or call.args[0].value != 0
                or not isinstance(call.args[1], ast.Constant)
                or not isinstance(call.args[1].value, str)
            ):
                raise AuthorityError(
                    f"unsupported Hermes source-root AST; path={vendor}; sha256={sha256_file(vendor)}"
                )
            candidates.append(Path(call.args[1].value))
    if len(candidates) != 1 or not candidates[0].is_absolute():
        raise AuthorityError(
            f"Hermes launcher has no unique literal source root; path={vendor}; sha256={sha256_file(vendor)}"
        )
    source = candidates[0].resolve(strict=True)
    if vendor.resolve(strict=True) != source / ".hermes/bin/hermes":
        raise AuthorityError("Hermes source root and actual vendor launcher disagree")
    return source


def _probe(
    python: Path, source: Path, root: Path, *, script: str, source_home: Path
) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    environment = {"PATH": os.defpath, "LANG": "C.UTF-8", "HERMES_HOME": str(source_home)}
    for key, name in (
        ("HOME", "home"),
        ("TMPDIR", "tmp"),
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_CACHE_HOME", "cache"),
        ("XDG_STATE_HOME", "state"),
    ):
        directory = root / name
        directory.mkdir(exist_ok=True)
        environment[key] = str(directory)
    done = subprocess.run(
        [str(python), "-I", "-B", "-c", script, str(source), str(python)],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if done.returncode:
        raise AuthorityError(
            "Hermes native runtime/launcher/OpenAI dependency unavailable: " + done.stderr[-4000:]
        )
    result = json.loads(done.stdout)
    if not isinstance(result, dict):
        raise AuthorityError("Hermes native runtime returned no binding object")
    return result


def _command(probe: dict[str, Any], source: Path, python: Path) -> tuple[str, ...]:
    fields = (
        "python",
        "prefix",
        "basePrefix",
        "launcherSource",
        "openaiVersion",
        "openaiOrigin",
        "version",
    )
    if not all(isinstance(probe.get(key), str) and probe[key] for key in fields):
        raise AuthorityError("Hermes native runtime ownership/dependency fields are absent")
    command = probe.get("command")
    if (
        not isinstance(command, list)
        or len(command) != 5
        or not all(isinstance(item, str) and item for item in command)
        or command[0] != str(python)
        or command[1:3] != ["-I", "-c"]
        or command[-1] != "acp"
        or probe["python"] != str(python)
        or Path(probe["prefix"]).resolve() != python.parent.parent.resolve()
        or probe["prefix"] == probe["basePrefix"]
        or Path(probe["launcherSource"]).resolve() != source / "hermes_cli/_launchers.py"
    ):
        raise AuthorityError("Hermes native command does not bind its actual prepared venv/owner")
    return tuple(command)


def derive_hermes_runtime(
    *, ambient_path: str, source_home: Path, probe_root: Path
) -> HermesRuntimeBinding:
    """Derive the default; an unrecognized/missing native owner fails, never falls back."""
    try:
        published = shutil.which("hermes", path=ambient_path)
        if published is None:
            raise AuthorityError("ambient PATH has no published Hermes launcher")
        outer = _RuntimeFile.capture(Path(published).absolute())
        outer_words = _wrapper(outer.path)
        if (
            len(outer_words) != 3
            or outer_words[0] != "exec"
            or outer_words[-1] != "$@"
            or not Path(outer_words[1]).is_absolute()
        ):
            raise AuthorityError(
                f"unsupported Hermes outer-wrapper shape; path={outer.path}; sha256={outer.sha256}"
            )
        vendor = _RuntimeFile.capture(Path(outer_words[1]))
        words = _wrapper(vendor.path)
        source = _source_root(vendor.path, words)
        files = [outer, vendor, _RuntimeFile.capture(Path(words[1]))]
        files.extend(_RuntimeFile.capture(source / name) for name in _SOURCE_FILES)
        stamp = source / "install-stamp.json"
        if stamp.is_file():
            files.append(_RuntimeFile.capture(stamp))
        discovered = _probe(
            Path(words[1]), source, probe_root / "owner", script=_DISCOVER, source_home=source_home
        )
        python = Path(discovered["preparedPython"])
        if (
            not python.is_absolute()
            or not python.is_file()
            or not os.access(python, os.X_OK)
            or not (python.parent.parent / "pyvenv.cfg").is_file()
        ):
            raise AuthorityError(
                "ambient Hermes has no usable committed dependency-venv interpreter"
            )
        if Path(discovered["apiSource"]).resolve() != source / "pm/environments.py":
            raise AuthorityError("Hermes dependency discovery used another owner's API")
        files.extend(
            (
                _RuntimeFile.capture(python),
                _RuntimeFile.capture(python.parent.parent / "pyvenv.cfg"),
            )
        )
        probe = _probe(
            python, source, probe_root / "command", script=_COMMAND, source_home=source_home
        )
        official = _command(probe, source, python)
        if (
            probe["version"] != discovered["version"]
            or discovered.get("versionSource") == "unknown"
        ):
            raise AuthorityError("prepared Hermes code/version differs from ambient owner")
        files.append(_RuntimeFile.capture(Path(probe["openaiOrigin"])))
        command = (*official[:2], "-B", *official[2:])
        ambient = {
            **discovered,
            "publishedLauncher": outer.receipt(),
            "vendorLauncher": vendor.receipt(),
            "argv": [str(outer.path), "acp"],
            "expandedArgv": [*words[1:-1], "acp"],
            "sourceRoot": str(source),
            "versionEqualsPrepared": True,
        }
        binding = HermesRuntimeBinding(
            command, source, python, tuple(files), probe, ambient, official
        )
        binding.verify()
        return binding
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        raise AuthorityError(f"default prepared Hermes runtime is unavailable: {error}") from error
