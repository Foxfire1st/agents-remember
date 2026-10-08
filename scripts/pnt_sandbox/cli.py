"""Command line of the PNT sandbox: build, start, stop, reset, check.

Exit status: 0 when the command did what it reports; 1 when a step failed or the safety check
did not pass; 2 when the command refused. A start that refuses has started nothing, though it may
have built the sandbox and prepared the checkout before it refused.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import builder, commands
from .layout import (
    DASHBOARD_PORT,
    PASEO_PORT,
    SandboxLayout,
    SandboxRefusal,
    default_eve_project,
    default_sandbox_root,
    read_marker,
)
from .operations import TOOLING_CHECKOUT, Operations, StepFailed, require_checkout


def _port(value: str) -> int:
    port = int(value)
    if not 1 <= port <= 65535 or port in {9785, 9786}:
        raise argparse.ArgumentTypeError(
            "use a port from 1 to 65535 other than live ports 9785/9786"
        )
    return port


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pnt-sandbox",
        description="Run a PNT build of Agents Remember as a disposable, self-contained sandbox.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def command(name: str, help_text: str) -> argparse.ArgumentParser:
        added = sub.add_parser(name, help=help_text)
        added.add_argument(
            "--sandbox",
            type=Path,
            default=default_sandbox_root(),
            help="The sandbox directory (default: %(default)s).",
        )
        added.set_defaults(host_port=PASEO_PORT, dashboard_port=DASHBOARD_PORT)
        if name in {"build", "check", "start", "stop"}:
            added.add_argument("--host-port", type=_port, default=PASEO_PORT)
            added.add_argument("--dashboard-port", type=_port, default=DASHBOARD_PORT)
        return added

    def with_build_inputs(added: argparse.ArgumentParser) -> None:
        added.add_argument(
            "--eve-project",
            type=Path,
            default=default_eve_project(),
            help="The developer's Eve project the sandbox's Eve application is made from "
            "(default: %(default)s); without one the Eve provider entry is omitted.",
        )

    def with_checkout_option(added: argparse.ArgumentParser) -> None:
        added.add_argument(
            "--checkout",
            type=Path,
            default=TOOLING_CHECKOUT,
            help="The PNT build checkout whose code is used (default: the one holding this tool).",
        )

    build = command("build", "Create the sandbox; safe to repeat.")
    with_checkout_option(build)
    with_build_inputs(build)
    start = command("start", "Start the Paseo runtime and the dashboard of a PNT build checkout.")
    start.add_argument("checkout", type=Path, help="The PNT build checkout to run.")
    with_build_inputs(start)
    command("stop", "Stop the processes the sandbox started.")
    command("reset", "Stop the sandbox's processes and delete its directory.")
    check = command("check", "Check that every root the build would use lies inside the sandbox.")
    with_checkout_option(check)
    return parser


def _run(args: argparse.Namespace) -> int:
    if args.host_port == args.dashboard_port:
        raise SandboxRefusal("host and dashboard ports must differ; nothing was started")
    layout = SandboxLayout(args.sandbox, args.host_port, args.dashboard_port)
    ops = Operations(layout)
    if args.command == "build":
        builder.build(layout, require_checkout(args.checkout), ops, print, args.eve_project)
        return 0
    if args.command == "start":
        return commands.start(layout, args.checkout, ops, print, args.eve_project)
    if args.command == "stop":
        return commands.stop(layout, ops, print)
    if args.command == "reset":
        return commands.reset(layout, ops, print)
    if read_marker(layout) is None:
        raise SandboxRefusal(f"no sandbox at {layout.root}; run 'build' first")
    return 0 if commands.check(layout, require_checkout(args.checkout), ops, print) else 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _run(args)
    except SandboxRefusal as refusal:
        print(f"refused: {refusal}", file=sys.stderr)
        return 2
    except StepFailed as failure:
        print(f"failed at step '{failure.step}': {failure}", file=sys.stderr)
        if failure.log is not None:
            print(f"log file: {failure.log}", file=sys.stderr)
        return 1
