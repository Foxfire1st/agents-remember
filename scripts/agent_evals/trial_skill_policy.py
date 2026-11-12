"""A fixed skill-management denial confined to each private Hermes trial home."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import shlex
import stat
from contextlib import ExitStack
from pathlib import Path
from typing import Any, BinaryIO

from agents_remember.errors import AuthorityError

from .artifacts import json_digest

AUTHORITY = "20261030T155305Z"
POLICY_MESSAGE = "skill_manage disabled by the trial-home F10 policy"
HOOK_PATH = "hooks/deny-skill-manage.py"
POLICY_FILES = ("config.yaml", HOOK_PATH)
_SNAPSHOT_SCHEMA = "ar-hermes-trial-skill-policy-snapshot/v1"
_HOOK = (
    '"""Skill-management denial: stdlib only; no arguments, network, or file writes."""\n'
    "import json\n"
    f"print(json.dumps({{'action': 'block', 'message': {POLICY_MESSAGE!r}}}))\n"
).encode()
_HOOK_SHA256 = hashlib.sha256(_HOOK).hexdigest()
_RULE = {
    "authority": AUTHORITY,
    "matcher": "^skill_manage$",
    "python": "/usr/bin/python3",
    "pythonArguments": ["-I", "-B"],
    "hookPath": HOOK_PATH,
    "hookSha256": _HOOK_SHA256,
    "timeout": 10,
    "fail_closed": True,
    "hooks_auto_accept": True,
}
POLICY_ID = "sha256:" + json_digest(_RULE)
_CONFIG_COMPARISON = {
    "method": "compared-without-digest",
    "reason": "configuration may contain secrets; retain bytes only in process",
}


class _PolicySnapshot(dict[str, Any]):
    """Serialize public policy bindings, retaining config bytes only in memory."""

    def __init__(self, public: dict[str, Any], config: bytes) -> None:
        super().__init__(public)
        self._config = config
        self._public = json.dumps(public, sort_keys=True)

    def config_baseline(self) -> bytes:
        if json.dumps(self, sort_keys=True) != self._public:
            raise AuthorityError("F10 policy integrity rejects an altered pre-run file binding")
        return self._config


def _home(home: Path) -> Path:
    # Do not resolve links: an actor-replaced home must be reported, not followed.
    return home.absolute()


def _directory(home: Path, stack: ExitStack) -> int:
    """Walk every directory component without following ancestor or home links."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(home.anchor, flags)
    stack.callback(os.close, descriptor)
    for component in home.parts[1:]:
        descriptor = os.open(component, flags, dir_fd=descriptor)
        stack.callback(os.close, descriptor)
    return descriptor


def _hooks(home: Path) -> dict[str, list[dict[str, Any]]]:
    return {
        "pre_tool_call": [
            {
                "matcher": _RULE["matcher"],
                "command": shlex.join(["/usr/bin/python3", "-I", "-B", str(home / HOOK_PATH)]),
                "timeout": 10,
                "fail_closed": True,
            }
        ]
    }


def install_skill_policy(home: Path, document: dict[str, Any]) -> dict[str, Any]:
    """Add the two admitted config keys and fixed hook; never inherit other hooks."""
    home = _home(home)
    try:
        with ExitStack() as stack:
            root = _directory(home, stack)
            os.mkdir("hooks", mode=0o700, dir_fd=root)
            parent = os.open("hooks", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
            stack.callback(os.close, parent)
            descriptor = os.open(
                "deny-skill-manage.py",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent,
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(_HOOK)
    except OSError as error:
        raise AuthorityError(
            f"F10 policy could not create its fresh fixed hook: errno {error.errno}"
        ) from None
    document.update(hooks_auto_accept=True, hooks=_hooks(home))
    return {
        "schema": "ar-hermes-trial-skill-policy/v1",
        "seedConfigException": 1,
        "name": "F10 fixed skill-management denial",
        "authority": AUTHORITY,
        "policyId": POLICY_ID,
        "rule": dict(_RULE),
        "hook": {"path": HOOK_PATH, "sha256": _HOOK_SHA256},
        "scope": "both text arms; private trial home only",
    }


def _identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _file_contents(
    stream: BinaryIO, relative: str, *, include_content: bool
) -> tuple[dict[str, Any], bytes | None]:
    digest = hashlib.sha256() if relative == HOOK_PATH else None
    chunks = [] if include_content else None
    size = 0
    while chunk := stream.read(65536):
        if digest is not None:
            digest.update(chunk)
        size += len(chunk)
        if chunks is not None:
            chunks.append(chunk)
    metadata = {"sizeBytes": size, "sha256": digest.hexdigest()} if digest is not None else {}
    return metadata, b"".join(chunks) if chunks is not None else None


def _read_file(
    home: Path, relative: str, *, include_content: bool
) -> tuple[dict[str, Any], bytes | None]:
    """Read only the two fixed files through non-following directory descriptors."""
    result: dict[str, Any] = {"path": relative}
    try:
        with ExitStack() as stack:
            root = _directory(home, stack)
            parent, name = root, relative
            if relative == HOOK_PATH:
                parent = os.open("hooks", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
                stack.callback(os.close, parent)
                name = "deny-skill-manage.py"
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            stream = stack.enter_context(os.fdopen(descriptor, "rb"))
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                return {**result, "state": "not-single-regular-file"}, None
            metadata, content = _file_contents(stream, relative, include_content=include_content)
            if _identity(before) != _identity(os.fstat(stream.fileno())):
                return {**result, "state": "changed-during-read"}, None
            if _identity(os.fstat(root)) != _identity(home.stat(follow_symlinks=False)):
                return {**result, "state": "home-replaced-during-read"}, None
            if relative == HOOK_PATH and _identity(os.fstat(parent)) != _identity(
                os.stat("hooks", dir_fd=root, follow_symlinks=False)
            ):
                return {**result, "state": "hook-parent-replaced-during-read"}, None
            result["state"] = "regular"
            return {**result, **metadata}, content
    except OSError as error:
        state = "missing" if error.errno == errno.ENOENT else "unreadable-or-linked"
        return {**result, "state": state, "errorNumber": error.errno}, None


def _snapshot(
    home: Path, *, include_content: bool = False
) -> tuple[dict[str, Any], dict[str, bytes | None]]:
    files, contents = [], {}
    for relative in POLICY_FILES:
        record, data = _read_file(home, relative, include_content=include_content)
        files.append(record)
        contents[relative] = data
    return {
        "schema": _SNAPSHOT_SCHEMA,
        "home": str(home),
        "policyId": POLICY_ID,
        "files": files,
        "configComparisonPolicy": dict(_CONFIG_COMPARISON),
    }, contents


def skill_policy_snapshot(home: Path) -> dict[str, Any]:
    """Keep config in process and freeze hook binding; never digest config or read auth."""
    home = _home(home)
    snapshot, contents = _snapshot(home, include_content=True)
    if any(row["state"] != "regular" for row in snapshot["files"]):
        raise AuthorityError("F10 policy baseline is missing, linked, or unreadable")
    try:
        document = json.loads(contents["config.yaml"] or b"")
    except (ValueError, UnicodeError):
        raise AuthorityError(
            "F10 policy baseline config is not the generated JSON mapping"
        ) from None
    if (
        not isinstance(document, dict)
        or document.get("hooks_auto_accept") is not True
        or document.get("hooks") != _hooks(home)
        or contents[HOOK_PATH] != _HOOK
    ):
        raise AuthorityError("F10 policy baseline does not contain the approved fixed denial")
    return _PolicySnapshot(snapshot, contents["config.yaml"] or b"")


def _differing_config_keys(before: bytes, after: bytes | None) -> list[str]:
    """Compare decoded top-level values in memory; expose names, never values."""
    try:
        left, right = json.loads(before), json.loads(after or b"")
    except (ValueError, UnicodeError):
        return []
    if not isinstance(left, dict) or not isinstance(right, dict):
        return []
    return sorted(
        key
        for key in left.keys() | right.keys()
        if key not in left
        or key not in right
        or json.dumps(left[key], sort_keys=True) != json.dumps(right[key], sort_keys=True)
    )


def check_skill_policy_integrity(home: Path, before: dict[str, Any]) -> dict[str, Any]:
    """Compare config in memory and hook digest; any change means instrument-altered."""
    home = _home(home)
    rows = before.get("files")
    if (
        before.get("schema") != _SNAPSHOT_SCHEMA
        or before.get("home") != str(home)
        or before.get("policyId") != POLICY_ID
        or not isinstance(rows, list)
        or len(rows) != len(POLICY_FILES)
    ):
        raise AuthorityError("F10 policy integrity requires its exact pre-run home snapshot")
    if not isinstance(before, _PolicySnapshot):
        raise AuthorityError("F10 policy integrity requires its in-process config baseline")
    config_before = before.config_baseline()
    for relative, row in zip(POLICY_FILES, rows, strict=True):
        if (
            not isinstance(row, dict)
            or row.get("path") != relative
            or row.get("state") != "regular"
        ):
            raise AuthorityError("F10 policy integrity rejects an altered pre-run file binding")
        if relative == HOOK_PATH and (
            not isinstance(row.get("sha256"), str)
            or re.fullmatch(r"[0-9a-f]{64}", row["sha256"]) is None
            or type(row.get("sizeBytes")) is not int
            or row["sizeBytes"] < 0
        ):
            raise AuthorityError("F10 policy integrity rejects an altered pre-run file binding")
    after, contents = _snapshot(home, include_content=True)
    config_after = contents["config.yaml"]
    config_changed = config_before != config_after
    comparison = {
        "result": "differs" if config_changed else "equal",
        "differingTopLevelKeys": _differing_config_keys(config_before, config_after),
        **_CONFIG_COMPARISON,
    }
    changes = [
        {"path": old["path"], "before": old, "after": new}
        for old, new in zip(rows, after["files"], strict=True)
        if old != new or (old["path"] == "config.yaml" and config_changed)
    ]
    return {
        "before": before,
        "after": after,
        "changes": changes,
        "configComparison": comparison,
        "unchanged": not changes,
        "cause": "instrument-altered" if changes else None,
        "outcome": "unknown" if changes else None,
    }
