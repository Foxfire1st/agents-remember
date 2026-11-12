"""GT3: judge admitted current routes without requiring historical build reconstruction."""

from __future__ import annotations

import posixpath
import shlex
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit

from ..adapters.models import NativeCapture
from .models import Grade, GradeStatus, native_reference
from .trajectory import arguments


@dataclass(frozen=True)
class RouteAuthority:
    """Compare a selected current build; historical assessment may omit that comparison."""

    expected_build: str | None
    serving_route_required: bool
    evidence_ref: str
    executable_builds: dict[str, str] = field(default_factory=dict)


def command_heads(command: str) -> list[list[str]]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
    lexer.whitespace_split = True
    segments: list[list[str]] = [[]]
    for token in lexer:
        if token in {";", "&&", "||", "|", "&"}:
            segments.append([])
        else:
            segments[-1].append(token)
    return [segment for segment in segments if segment]


def _native_cwd(cwd: object) -> str:
    if not isinstance(cwd, str):
        raise ValueError("Native command has no cwd")
    if cwd.startswith("file:"):
        uri = urlsplit(cwd)
        if uri.netloc or uri.query or uri.fragment:
            raise ValueError("Native command cwd is not a local file URI")
        cwd = unquote(uri.path)
    if not PurePosixPath(cwd).is_absolute():
        raise ValueError("Native command cwd is not absolute")
    return cwd


def _native_heads(params: dict[str, Any]) -> list[list[str]]:
    argv = params.get("command")
    cwd = _native_cwd(params.get("cwd"))
    if (
        not isinstance(argv, list)
        or not argv
        or not all(isinstance(part, str) and part for part in argv)
    ):
        raise ValueError("Native command has no supported argv")
    executable = PurePosixPath(argv[0]).name
    if executable in {"sh", "bash", "dash", "zsh", "fish"}:
        if (
            executable not in {"sh", "bash", "dash"}
            or len(argv) != 3
            or argv[1] not in {"-c", "-lc"}
        ):
            raise ValueError("Native shell argv shape is unsupported")
        heads = command_heads(argv[2])
        if any(head[0] == "cd" for head in heads):
            raise ValueError("Native shell changes cwd before command execution")
    else:
        heads = [argv.copy()]
    for head in heads:
        if "/" in head[0] and not PurePosixPath(head[0]).is_absolute():
            head[0] = posixpath.normpath(posixpath.join(cwd, head[0]))
    return heads


def _event_heads(params: dict[str, Any], name: object) -> list[list[str]]:
    if name == "CommandExecution":
        return _native_heads(params)
    command = params.get("cmd", params.get("command"))
    if isinstance(command, str):
        return command_heads(command)
    if "cmd" in params or "command" in params:
        raise ValueError("Command argument shape is unsupported")
    return []


def grade_routes(capture: NativeCapture, authority: RouteAuthority) -> Grade:
    evidence: list[str] = [authority.evidence_ref]
    violations: list[str] = []
    uncertainties: list[str] = list(capture.unknowns)
    unknown = bool(capture.unknowns)
    for event in capture.events:
        if event.kind != "tool_call":
            continue
        params = arguments(event)
        if params is None:
            unknown = True
            evidence.append(native_reference(event))
            uncertainties.append("Native tool arguments could not be decoded")
            continue
        try:
            heads = _event_heads(params, event.payload.get("name"))
        except ValueError as error:
            unknown = True
            evidence.append(native_reference(event))
            uncertainties.append(str(error))
            continue
        for head in heads:
            if not any(part.startswith("knowledge-") for part in head[1:]):
                continue
            is_cli = PurePosixPath(head[0]).name == "agents-remember" or head[1:3] in (
                ["-m", "agents_remember"],
                ["-m", "agents_remember.cli"],
            )
            if not is_cli:
                continue
            evidence.append(native_reference(event))
            outcome, reason = _classify_route(head[0], authority)
            violations.extend([reason] if outcome == "fail" else [])
            unknown |= outcome == "unknown"
    status: GradeStatus = "fail" if violations else "unknown" if unknown else "pass"
    return Grade.produced(
        __file__, "GT3", status, tuple(evidence), tuple(violations + uncertainties)
    )


def _classify_route(executable: str, authority: RouteAuthority) -> tuple[GradeStatus, str]:
    if executable == "agents-remember" and authority.serving_route_required:
        return "fail", "Knowledge command uses bare PATH instead of the admitted serving route"
    if not authority.expected_build:
        return (
            ("unknown", "Admitted current serving build was not supplied")
            if authority.serving_route_required
            else ("pass", "")
        )
    observed_build = authority.executable_builds.get(executable)
    if observed_build is None:
        return "unknown", "Executable build was not observed"
    if observed_build != authority.expected_build:
        return "fail", "Executed knowledge route reports a different current source build"
    return "pass", ""
