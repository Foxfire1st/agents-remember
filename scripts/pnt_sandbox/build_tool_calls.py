#!/usr/bin/env python3
"""Call the tool server of the PNT build under test, over stdio, in the sandbox's environment.

Run with the build's own interpreter (``<checkout>/mcp/.venv/bin/python``), so the server it
starts is ``agents_remember.mcp`` from that checkout. The request file names the sandbox the
request belongs to, the settings file, the roots the server must report before anything is
written, and the calls to make. A call with an ``unless`` probe is skipped when the probe
succeeds, which is what makes a rebuild leave existing task documents alone.

The tool server is started with the sandbox's own environment, whatever the caller's holds, and
the request is refused before the first call unless the Paseo host the build resolves there is
the sandbox's own. A helper run from an agent's shell therefore addresses the sandbox and no
other Paseo runtime.

Prints one JSON document: ``{"ok", "packageRoot", "results": [{"name", "tool", "action",
"ok", "detail"}], "error", "host"}``.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib
import json
import os
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from mcp.client.stdio import stdio_client

from mcp import ClientSession, StdioServerParameters

_DETAIL_LIMIT = 600


class Refused(Exception):
    """A request refused before any tool call; the message names the fault and the remedy."""


def _tooling(name: str) -> Any:
    """One module of the sandbox tooling this helper belongs to.

    The helper runs from its own directory inside the build's Python environment, so the
    tooling package is importable only once the directory above it is on the path;
    ``scripts/pnt-sandbox.py`` puts it there the same way for the commands.
    """
    scripts = Path(__file__).resolve().parents[1]
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    return importlib.import_module(f"pnt_sandbox.{name}")


def _payload(result: Any) -> tuple[bool, Any]:
    """Whether a tool call succeeded, and what it returned."""
    raw = result.model_dump(mode="json", by_alias=True, exclude_none=True)
    payload: Any = raw.get("structuredContent")
    if not isinstance(payload, dict):
        text = "\n".join(
            block.get("text", "")
            for block in raw.get("content", [])
            if isinstance(block, dict) and block.get("type") == "text"
        )
        try:
            payload = json.loads(text)
        except ValueError:
            payload = {"text": text}
    failed = bool(raw.get("isError")) or (isinstance(payload, dict) and payload.get("ok") is False)
    return not failed, payload


def _admission_error(info: Any, expect: dict[str, Any]) -> str | None:
    """Refuse a server whose roots are not exactly the sandbox's."""
    if not isinstance(info, dict):
        return "server_info returned no object"
    for key in ("coordinationRoot", "workspaceRoot"):
        reported = info.get(key)
        if not isinstance(reported, str) or Path(reported).resolve() != Path(expect[key]).resolve():
            return f"the tool server reports {key}={reported!r}, expected {expect[key]!r}"
    if info.get("allowedRepoIds") != expect["allowedRepoIds"]:
        return f"the tool server admits repositories {info.get('allowedRepoIds')!r}"
    # The providers the settings file still configures; ``server_info`` names them under the one
    # field it sends for them, and a sandbox configures none.
    if info.get("unusedProviderKeys"):
        return f"the tool server has providers configured: {info.get('unusedProviderKeys')!r}"
    expected_package = expect.get("packageRoot")
    actual_package = (info.get("servingBuild") or {}).get("packageRoot")
    if expected_package is not None and actual_package != expected_package:
        return f"the tool server package is {actual_package!r}, expected {expected_package!r}"
    return None


def _sandbox(request: dict[str, Any]) -> Any:
    """The sandbox the request belongs to; a request that names none is refused."""
    named = request.get("sandbox")
    if not isinstance(named, str) or not named.strip():
        raise Refused(
            'the request names no sandbox: add "sandbox": "<sandbox directory>" to the request '
            "file, because a tool call is admitted only for the sandbox it belongs to"
        )
    root = Path(named)
    if not root.is_absolute():
        raise Refused(f'the request\'s "sandbox" is not an absolute path: {named!r}')
    return _tooling("layout").SandboxLayout(root, paseo_port=_host_port(request))


def _host_port(request: dict[str, Any]) -> int:
    """The sandbox's own Paseo port: the endpoint the request says that sandbox listens on."""
    layout = _tooling("layout")
    named = request.get("hostPort", layout.PASEO_PORT)
    if (
        not isinstance(named, int)
        or isinstance(named, bool)
        or not 1 <= named <= 65535
        or named in layout.LIVE_PORTS
    ):
        raise Refused(
            "the request's \"hostPort\" must be the sandbox's Paseo port as an integer from 1 "
            "to 65535, other than the live ports 9785 and 9786"
        )
    return named


def _server_environment(layout: Any) -> dict[str, str]:
    """The tool server's environment: the sandbox's, whatever the caller's holds."""
    return _tooling("environment").sandbox_environment(os.environ, layout)


def _require_sandbox_environment(environment: Mapping[str, str], layout: Any) -> None:
    """Refuse an environment that still selects a home or an address of its own.

    ``sandbox_environment`` removes every selector and sets the sandbox's own variables; this
    reads the result back through the tooling's own judgment of a running process
    (``environment.foreign_variables``) instead of trusting that the two agree. A default home
    under ``HOME`` is only reachable through such a selector, so this is where "no call reaches
    a home outside the sandbox" is checked before the server exists.
    """
    foreign = _tooling("environment").foreign_variables(environment, layout)
    if foreign:
        raise Refused(
            "the tool server's environment is not the sandbox's: it still carries "
            f"{', '.join(foreign)}; no call was made"
        )


def _host_block(raw: object, path: Path) -> tuple[str, str] | None:
    """The host one ``paseoRuntime`` block selects; ``None`` when the block is absent."""
    if raw is None:
        return None
    from agents_remember.kernel.primitives.paseo_runtime_settings import (  # noqa: PLC0415
        parse_paseo_runtime_settings,
    )

    try:
        settings = parse_paseo_runtime_settings(raw)
    except ValueError as error:
        raise Refused(
            f"the sandbox's own settings carry no usable paseoRuntime: {path}: {error}"
        ) from error
    if settings is None:
        return None
    return settings.home.as_posix(), settings.listen


def _sandbox_host(layout: Any) -> tuple[str, str] | None:
    """The Paseo home and listen address the sandbox's own shared settings record.

    ``None`` until a fresh build writes its ``paseoRuntime`` block: the asset bootstrap runs
    before the sandbox supplies its host, and the sandbox records none while it runs.
    """
    path = layout.user_settings_file
    if not path.exists():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise Refused(
            f"cannot read the sandbox's own settings {path}: {error}; build the sandbox with "
            "scripts/pnt-sandbox.py build before driving it"
        ) from error
    if not isinstance(document, dict):
        raise Refused(f"the sandbox's own settings are not a JSON object: {path}")
    return _host_block(document.get("paseoRuntime"), path)


@contextlib.contextmanager
def _environment_in_force(environment: Mapping[str, str]) -> Iterator[None]:
    """Read the build's own settings resolution under the server's environment."""
    caller = dict(os.environ)
    os.environ.clear()
    os.environ.update(environment)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(caller)


def _resolved_host(
    environment: Mapping[str, str], coordination_root: Path
) -> tuple[str, str] | None:
    """The Paseo home and listen address the build's own code selects under ``environment``."""
    from agents_remember.kernel.primitives.paseo_authority import (  # noqa: PLC0415
        load_shared_paseo_runtime,
    )

    with _environment_in_force(environment):
        settings = load_shared_paseo_runtime(coordination_root)
    return None if settings is None else (settings.home.as_posix(), settings.listen)


def _admitted(request: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    """The server's environment and the sandbox host it addresses, or the refusal that ends it."""
    layout = _sandbox(request)
    environment = _server_environment(layout)
    _require_sandbox_environment(environment, layout)
    # The sandbox's host is what the layout says it is, never what a settings file inside the
    # sandbox claims: a request that names a sandbox with its own record pointed elsewhere, or
    # whose port is not the sandbox port it names, must not pass as that sandbox.
    expected = (layout.paseo_home.as_posix(), layout.paseo_listen)
    recorded = _sandbox_host(layout)
    try:
        resolved = _resolved_host(environment, layout.coordination)
    except (OSError, ValueError) as error:
        raise Refused(
            "the build cannot read the Paseo host the sandbox environment selects: "
            f"{error}; check the sandbox's user settings file"
        ) from error
    if recorded is not None and recorded != expected:
        raise Refused(
            f"the sandbox {layout.root} records Paseo home {recorded[0]} on {recorded[1]}, not "
            f'its own {expected[0]} on {expected[1]}; name its "hostPort" or rebuild it with '
            "scripts/pnt-sandbox.py build"
        )
    # A fresh build records no host while it bootstraps its assets, before the build supplies
    # one; a server that resolves no host either reaches no runtime at all, so only a host that
    # exists and is not the sandbox's ends the request.
    if resolved is None:
        if recorded is None:
            # The fresh build's own phase: no Paseo runtime is configured anywhere yet, and the
            # server it starts can name none, so the request is admitted and its report says so.
            return environment, {"state": "no host configured"}
        raise Refused(
            "the build selects no Paseo host under the sandbox environment, where the sandbox "
            f"records {expected[0]} on {expected[1]}"
        )
    if resolved != expected:
        raise Refused(
            f"the tool server would address Paseo home {resolved[0]} on {resolved[1]}, not the "
            f"sandbox's {expected[0]} on {expected[1]}; no call was made"
        )
    return environment, {"state": "sandbox", "home": expected[0], "listen": expected[1]}


def _answer(
    *,
    ok: bool,
    host: dict[str, str] | None,
    package_root: str | None = None,
    results: list[dict[str, Any]] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """One report: the four facts every request answers with, and the host it addressed."""
    return {
        "ok": ok,
        "packageRoot": package_root,
        "results": [] if results is None else results,
        "error": error,
        "host": host,
    }


async def _run(request: dict[str, Any]) -> dict[str, Any]:
    try:
        environment, host = _admitted(request)
    except Refused as refusal:
        return _answer(ok=False, host=None, error=str(refusal))
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "agents_remember.mcp", "--config", request["config"]],
        env=environment,
        cwd=request["cwd"],
    )
    results: list[dict[str, Any]] = []
    with open(request["serverLog"], "a", encoding="utf-8") as server_log:
        return await _call_all(server, server_log, request, results, host)


async def _call_all(
    server: StdioServerParameters,
    server_log: Any,
    request: dict[str, Any],
    results: list[dict[str, Any]],
    host: dict[str, str],
) -> dict[str, Any]:
    async with (
        stdio_client(server, errlog=server_log) as (reader, writer),
        ClientSession(reader, writer) as session,
    ):
        await session.initialize()
        ok, info = _payload(await session.call_tool("server_info", {}))
        refusal = None if ok else "server_info failed"
        refusal = refusal or _admission_error(info, request["expect"])
        package_root = (info.get("servingBuild") or {}).get("packageRoot") if ok else None
        if refusal is not None:
            return _answer(ok=False, host=host, package_root=package_root, error=refusal)
        for call in request["calls"]:
            probe = call.get("unless")
            if probe is not None:
                present, _existing = _payload(
                    await session.call_tool(probe["tool"], probe["arguments"])
                )
                if present:
                    results.append(
                        {
                            "name": call["name"],
                            "tool": call["tool"],
                            "action": "present",
                            "ok": True,
                        }
                    )
                    continue
            ok, payload = _payload(await session.call_tool(call["tool"], call["arguments"]))
            result = {"name": call["name"], "tool": call["tool"], "action": "called", "ok": ok}
            if call["tool"] == "runtime_install":
                result["payload"] = payload
            if not ok:
                result["detail"] = json.dumps(payload)[:_DETAIL_LIMIT]
            results.append(result)
            if not ok:
                return _answer(
                    ok=False,
                    host=host,
                    package_root=package_root,
                    results=results,
                    error=f"{call['name']}: {call['tool']} failed",
                )
    return _answer(ok=True, host=host, package_root=package_root, results=results)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, help="JSON file describing the calls.")
    args = parser.parse_args()
    request = json.loads(Path(args.request).read_text(encoding="utf-8"))
    try:
        report = asyncio.run(_run(request))
    except Exception as error:  # the report is the contract; a traceback is not
        report = _answer(ok=False, host=None, error=repr(error))
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
