#!/usr/bin/env python3
"""Run the environment-gated integration paths, one command per path.

``pyproject.toml`` registers eight markers for suites that skip unless an ``AR_*``
variable opts them in. Registering them made them nameable; until this script and the
``integration-gated`` workflow landed, nothing ran any of them, so fifteen tests over
the Pi RPC transport, the Codex app-server, the Claude stream-json transport, the L3
control routes, the production evidence seam and the real MCP stdio server were never
executed by anything.

Two of the eight need no vendor account and run in CI on every push:

``ar-run-pi-rpc-smoke``
    Installs ``@earendil-works/pi-coding-agent`` into its own temp prefix and drives it
    against a fake OpenAI endpoint on ``127.0.0.1`` with ``--offline``. Needs node, npm
    and reachability of the npm registry. Nothing else.
``agents-remember-real-mcp-config``
    Spawns *this* repository's MCP server over stdio against a generated settings file.
    ``--dry-run-only`` selects the planning test, which needs nothing but the file;
    without it the live grepai search runs too and needs the self-hosted docker stack up
    and indexed (``ar-grepai-postgres`` / ``ar-grepai-ollama`` / ``ar-grepai-watcher``).

Six need something a CI runner cannot hold, and each says which:

``ar-codex-app-server-live-smoke``
    An installed, signed-in Codex whose live ``model/list`` advertises the requested
    model. Sends no prompt and persists no thread, so it bills nothing -- but the
    handshake is against the vendor with the operator's own credentials.
``ar-codex-app-server-live-conformance``
    The same install, and two real turns. This one bills.
``ar-claude-stream-smoke``
    An installed, signed-in Claude Code. The prompt is the local ``/cost`` command
    rather than a model turn, so the spend is ~nothing, but the handshake needs a
    logged-in CLI.
``ar-run-control-plane-installed``, ``ar-run-control-installed``,
``ar-run-evidence-installed``
    Exactly ``codex 0.144.5`` and ``pi 0.80.7`` on PATH -- each class skips itself when
    the version does not match -- plus real billed turns, including a 600-word essay
    prompt and image uploads. ``ar-run-evidence-installed`` additionally writes a
    persisted thread into the operator's real ``CODEX_HOME``.

``list`` prints the same table with each path's current readiness on this machine.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

REPO_ROOT = Path(__file__).resolve().parents[1]

CI_SAFE = "no vendor account"
LOCAL_ONLY = "local only"


@dataclass(frozen=True)
class GatedPath:
    """One registered marker, its opt-in variable, and what it actually needs."""

    name: str
    marker: str
    environment: dict[str, str]
    category: str
    requires: str
    binaries: tuple[str, ...] = ()


PATHS: tuple[GatedPath, ...] = (
    GatedPath(
        name="ar-run-pi-rpc-smoke",
        marker="ar_run_pi_rpc_smoke",
        environment={"AR_RUN_PI_RPC_SMOKE": "1"},
        category=CI_SAFE,
        requires=(
            "node + npm, and the npm registry reachable. The suite installs "
            "@earendil-works/pi-coding-agent@0.80.7 into a temp prefix itself and drives it "
            "offline against a fake OpenAI endpoint on 127.0.0.1. The second test uses "
            "whatever `pi` is on PATH and skips when there is none."
        ),
        binaries=("node", "npm"),
    ),
    GatedPath(
        name="agents-remember-real-mcp-config",
        marker="agents_remember_real_mcp_config",
        environment={},
        category=CI_SAFE,
        requires=(
            "a settings file, which this script generates. The planning test needs "
            "nothing else; the live search test needs the self-hosted grepai docker stack "
            "running and indexed. Use --dry-run-only to run just the planning test."
        ),
    ),
    GatedPath(
        name="ar-codex-app-server-live-smoke",
        marker="ar_codex_app_server_live_smoke",
        environment={"AR_CODEX_APP_SERVER_LIVE_SMOKE": "1"},
        category=LOCAL_ONLY,
        requires=(
            "an installed, signed-in Codex whose live model catalogue advertises "
            "AR_CODEX_SMOKE_MODEL (default gpt-5.6-sol) at AR_CODEX_SMOKE_EFFORT (default "
            "xhigh). Sends no prompt and persists no thread, so it bills nothing, but the "
            "handshake goes to the vendor under your credentials."
        ),
        binaries=("codex",),
    ),
    GatedPath(
        name="ar-codex-app-server-live-conformance",
        marker="ar_codex_app_server_live_conformance",
        environment={"AR_CODEX_APP_SERVER_LIVE_CONFORMANCE": "1"},
        category=LOCAL_ONLY,
        requires=(
            "the same install, a catalogue advertising at least two selectable models, "
            "and two real turns. THIS ONE BILLS."
        ),
        binaries=("codex",),
    ),
    GatedPath(
        name="ar-claude-stream-smoke",
        marker="ar_claude_stream_smoke",
        environment={"AR_CLAUDE_STREAM_SMOKE": "1"},
        category=LOCAL_ONLY,
        requires=(
            "an installed, signed-in Claude Code (or AR_CLAUDE_STREAM_BINARY pointing at "
            "one). The prompt is the local /cost slash command rather than a model turn, so "
            "spend is negligible, but the stream-json handshake needs a logged-in CLI. Runs "
            "the binary with this repository as its cwd."
        ),
        binaries=("claude",),
    ),
    GatedPath(
        name="ar-run-control-plane-installed",
        marker="ar_run_control_plane_installed",
        environment={"AR_RUN_CONTROL_PLANE_INSTALLED": "1"},
        category=LOCAL_ONLY,
        requires=(
            "exactly codex 0.144.5 and pi 0.80.7 on PATH (each class skips itself on a "
            "version mismatch), signed in, plus real billed turns including a 600-word "
            "essay prompt and an image upload. THIS ONE BILLS."
        ),
        binaries=("codex", "pi"),
    ),
    GatedPath(
        name="ar-run-control-installed",
        marker="ar_run_control_installed",
        environment={"AR_RUN_CONTROL_INSTALLED": "1"},
        category=LOCAL_ONLY,
        requires=(
            "exactly codex 0.144.5 and pi 0.80.7 on PATH, signed in, plus the essay "
            "prompt, two further settled turns and an uploaded PNG. THIS ONE BILLS."
        ),
        binaries=("codex", "pi"),
    ),
    GatedPath(
        name="ar-run-evidence-installed",
        marker="ar_run_evidence_installed",
        environment={"AR_RUN_EVIDENCE_INSTALLED": "1"},
        category=LOCAL_ONLY,
        requires=(
            "exactly codex 0.144.5 and pi 0.80.7 on PATH, signed in. The prompts are tiny "
            '("Reply with exactly the word OK"), but it persists a real thread into your '
            "CODEX_HOME and resumes it in a second process. THIS ONE BILLS."
        ),
        binaries=("codex", "pi"),
    ),
)

BY_NAME = {path.name: path for path in PATHS}

# The planning test of the real-MCP class: it asserts the command the server would run
# and executes nothing, so it needs the settings file and no infrastructure at all. Its
# sibling in the same class performs a live grepai search and needs the docker stack.
DRY_RUN_NODE = (
    "mcp/tests/test_tools.py::RealMcpIntegrationTests::"
    "test_real_mcp_grepai_search_dry_run_uses_workspace_scope"
)


def settings_document(root: Path) -> dict[str, object]:
    """A settings file for the real MCP server, holding no credential of any kind.

    The provider entries name this repository's own self-hosted stacks. Nothing here
    reaches a vendor, which is why the planning half of that suite runs in CI.
    """
    return {
        "version": 1,
        "coordinationRoot": str(root / "ar-coordination"),
        "workspaceRoot": str(root / "workspace"),
        "repositories": {"agents-remember": {}},
        "providers": {"codegraphcontext-code": {}, "grepai-memory": {}},
        "timeoutCaps": {"toolSeconds": 30, "providerSetupSeconds": 1800},
        "benchmarksEnabled": True,
    }


def write_settings(root: Path) -> Path:
    """Write the settings file and the tree it names.

    The provider layer resolves each repository's memory root under
    ``<coordinationRoot>/memory-repos/ar-<repo id>`` and refuses to plan a search
    against a path that does not exist, so the directories are created here rather than
    left for the suite to trip over.
    """
    document = settings_document(root)
    coordination = Path(str(document["coordinationRoot"]))
    for key in ("coordinationRoot", "workspaceRoot"):
        Path(str(document[key])).mkdir(parents=True, exist_ok=True)
    repositories = document["repositories"]
    if isinstance(repositories, dict):
        for repo_id in repositories:
            (coordination / "memory-repos" / f"ar-{repo_id}").mkdir(parents=True, exist_ok=True)
    settings = root / "agents-remember-settings.json"
    settings.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return settings


@dataclass(frozen=True)
class RunRequest:
    """How one invocation differs from the plain "run this marker" default."""

    dry_run_only: bool = False
    require_passed: int | None = None
    extra: tuple[str, ...] = ()


def pytest_command(
    path: GatedPath, request: RunRequest, *, node: str | None, report: Path | None
) -> list[str]:
    selection = [node] if node else ["-m", path.marker]
    report_arguments = ["--junit-xml", str(report)] if report is not None else []
    return [sys.executable, "-m", "pytest", *selection, "-v", *report_arguments, *request.extra]


def child_environment(path: GatedPath, settings: Path | None) -> dict[str, str]:
    env = dict(os.environ)
    env.update(path.environment)
    source_root = REPO_ROOT / "mcp" / "src"
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = f"{source_root}{os.pathsep}{existing}" if existing else str(source_root)
    if settings is not None:
        env["AGENTS_REMEMBER_REAL_MCP_CONFIG"] = str(settings)
    return env


def run_path(path: GatedPath, request: RunRequest) -> int:
    """Run one gated path, generating whatever the suite needs it to be given."""
    real_mcp = path.marker == "agents_remember_real_mcp_config"
    node = DRY_RUN_NODE if request.dry_run_only and real_mcp else None
    with tempfile.TemporaryDirectory(prefix="ar-gated-") as tmp:
        settings = write_settings(Path(tmp)) if real_mcp else None
        return invoke(path, request, settings=settings, node=node, workspace=Path(tmp))


def invoke(
    path: GatedPath,
    request: RunRequest,
    *,
    settings: Path | None,
    node: str | None,
    workspace: Path,
) -> int:
    report = workspace / "report.xml" if request.require_passed is not None else None
    command = pytest_command(path, request, node=node, report=report)
    print(f"== {path.name} ({path.category})", flush=True)
    print(f"   requires: {path.requires}", flush=True)
    print(f"   $ {' '.join(command)}", flush=True)
    completed = subprocess.run(
        command, cwd=REPO_ROOT, env=child_environment(path, settings), check=False
    )
    if completed.returncode:
        return completed.returncode
    return 0 if report is None else verify_passed(report, request.require_passed or 0)


def verify_passed(report: Path, expected: int) -> int:
    """Fail unless exactly ``expected`` tests actually ran and passed.

    A skipped test exits pytest 0, so a runner that only checks the exit code reports
    success for a job that ran nothing. Several of these suites skip themselves when a
    binary is missing from PATH, which is precisely the state this workflow exists to
    end, so the count is read from pytest's own JUnit report rather than inferred.
    """
    if not report.is_file():
        print(f"pytest wrote no JUnit report at {report}", flush=True)
        return 1
    suite = ElementTree.parse(report).getroot().find("testsuite")
    if suite is None:
        print(f"JUnit report has no testsuite element: {report}", flush=True)
        return 1
    counts = {key: int(suite.get(key, "0")) for key in ("tests", "skipped", "errors", "failures")}
    passed = counts["tests"] - counts["skipped"] - counts["errors"] - counts["failures"]
    if passed == expected:
        return 0
    print(
        f"expected {expected} test(s) to run and pass, got {passed} "
        f"(collected {counts['tests']}, skipped {counts['skipped']}, "
        f"errors {counts['errors']}, failures {counts['failures']})",
        flush=True,
    )
    return 1


def readiness(path: GatedPath) -> str:
    missing = [binary for binary in path.binaries if shutil.which(binary) is None]
    if missing:
        return f"missing on PATH: {', '.join(missing)}"
    return "binaries present" if path.binaries else "no binary needed"


def print_listing() -> int:
    for path in PATHS:
        print(f"{path.name}")
        print(f"  marker    {path.marker}")
        print(f"  category  {path.category}")
        print(f"  local     {readiness(path)}")
        print(f"  requires  {path.requires}")
        print()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run one of the eight environment-gated integration paths. Two need no "
            "vendor account and run in CI; the other six need an installed, signed-in "
            "vendor CLI and mostly bill for real turns, so they run here."
        )
    )
    parser.add_argument(
        "path",
        nargs="?",
        choices=[*sorted(BY_NAME), "list", "ci-safe"],
        default="list",
        help="A path name, 'list' for the table, or 'ci-safe' for both credential-free paths.",
    )
    parser.add_argument(
        "--dry-run-only",
        action="store_true",
        help=(
            "For agents-remember-real-mcp-config, run only the planning test, which "
            "needs no docker stack."
        ),
    )
    parser.add_argument(
        "--require-passed",
        type=int,
        help=(
            "Fail unless exactly this many tests ran and passed. A skipped test exits "
            "pytest 0, so CI uses this to tell 'ran' from 'was skipped'."
        ),
    )
    return parser


def selected(name: str) -> list[GatedPath]:
    if name == "ci-safe":
        return [path for path in PATHS if path.category == CI_SAFE]
    return [BY_NAME[name]]


def main(argv: list[str] | None = None) -> int:
    """Parse this script's own flags and hand everything else to pytest.

    ``parse_known_args`` rather than an ``argparse.REMAINDER`` positional: REMAINDER
    swallows every token after the path name, so ``... real-mcp-config --dry-run-only``
    would forward this script's own flag to pytest and fail there.
    """
    args, extra = build_parser().parse_known_args(argv)
    if args.path == "list":
        return print_listing()
    request = RunRequest(
        dry_run_only=args.dry_run_only,
        require_passed=args.require_passed,
        extra=tuple(extra),
    )
    failures = sum(1 for path in selected(args.path) if run_path(path, request))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
