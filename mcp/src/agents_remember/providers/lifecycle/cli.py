"""Command-line parser and entrypoint for provider lifecycle."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from agents_remember.providers.cgc.lifecycle import (
    cgc_apply_settings,
    cgc_backend_start,
    cgc_backend_status,
    cgc_doctor,
    cgc_init_layout,
    cgc_install,
    cgc_install_all,
    cgc_patch,
    cgc_refresh,
    cgc_refresh_all,
    cgc_run,
    cgc_start,
    cgc_start_all,
    cgc_status,
    cgc_stop,
    cgc_stop_all,
    cgc_visualize,
)
from agents_remember.providers.context import ContextProviderError, stable_provider_id
from agents_remember.providers.grepai.lifecycle import grepai_run
from agents_remember.providers.lifecycle.result_rendering import (
    render,
    render_cgc_run_result,
    render_grepai_run_result,
)
from agents_remember.providers.lifecycle.runtime_environment import (
    configure_utf8_stdio,
    default_coordination_root,
)
from agents_remember.providers.lifecycle.watchers import watchers_run


def add_common_provider_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--coordination-root",
        type=Path,
        default=default_coordination_root(),
        help="Coordination root. Defaults to the installed script's parent directory.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--timeout", type=int, default=60)


def add_cgc_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--coordination-root", type=Path, default=argparse.SUPPRESS)
    parser.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS)
    parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    parser.add_argument("--timeout", type=int, default=argparse.SUPPRESS)
    parser.add_argument(
        "--from-settings",
        type=Path,
        default=argparse.SUPPRESS,
        help="Coordinator settings.json containing codegraphcontext-code.",
    )
    parser.add_argument(
        "--repo-id", default=argparse.SUPPRESS, help="Stable provider id for this code repository."
    )
    parser.add_argument("--code-repo-root", type=Path, default=argparse.SUPPRESS)


def add_cgc_action_parser(
    subparsers: Any, action: str, *, help_text: str
) -> argparse.ArgumentParser:
    action_parser = subparsers.add_parser(action, help=help_text)
    add_cgc_common_args(action_parser)
    return action_parser


def normalize_cgc_args(args: argparse.Namespace) -> None:
    for key, value in {
        "dry_run": False,
        "json": False,
        "timeout": 60,
        "from_settings": None,
        "repo_id": None,
        "code_repo_root": None,
    }.items():
        if not hasattr(args, key):
            setattr(args, key, value)
    if not getattr(args, "coordination_root", None):
        args.coordination_root = default_coordination_root()


def add_grepai_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--coordination-root", type=Path, default=argparse.SUPPRESS)
    parser.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS)
    parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    parser.add_argument("--timeout", type=int, default=argparse.SUPPRESS)
    parser.add_argument(
        "--from-settings",
        type=Path,
        default=argparse.SUPPRESS,
        help="Coordinator settings.json containing grepai-memory.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Reinstall even when the pinned version is already present.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=argparse.SUPPRESS,
        help="Indexed memory root. Defaults to <coordination-root>/memory-repos.",
    )
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=argparse.SUPPRESS,
        help="Provider runtime root. Defaults to <coordination-root>/providers/runners/grepai.",
    )


def add_grepai_action_parser(
    subparsers: Any, action: str, *, help_text: str
) -> argparse.ArgumentParser:
    action_parser = subparsers.add_parser(action, help=help_text)
    add_grepai_common_args(action_parser)
    return action_parser


def normalize_grepai_args(args: argparse.Namespace) -> None:
    for key, value in {
        "dry_run": False,
        "json": False,
        "timeout": 60,
        "from_settings": None,
        "force": False,
        "root": None,
        "runtime_root": None,
    }.items():
        if not hasattr(args, key):
            setattr(args, key, value)
    if not getattr(args, "coordination_root", None):
        args.coordination_root = default_coordination_root()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    providers = parser.add_subparsers(dest="provider", required=True)

    cgc = providers.add_parser(
        "cgc", help="Manage CodeGraphContext code provider settings and instances."
    )
    add_cgc_common_args(cgc)
    cgc_actions = cgc.add_subparsers(dest="action", required=True)
    for action, help_text in [
        ("apply-settings", "Apply configured CGC provider settings."),
        ("backend-start", "Start the shared FalkorDB backend."),
        ("backend-status", "Inspect the shared FalkorDB backend."),
        ("status", "Inspect one managed CGC repository runtime."),
        ("install", "Install CGC for one managed repository runtime."),
        ("install-all", "Install CGC for every configured repository runtime."),
        ("init-layout", "Create the managed CGC runtime layout."),
        ("patch", "Apply managed CGC containment patches."),
        ("doctor", "Run CGC provider health checks."),
        ("start", "Start managed CGC watchers."),
        ("start-all", "Start every configured CGC watcher."),
        ("stop", "Stop managed CGC watchers."),
        ("stop-all", "Stop every configured CGC watcher."),
        ("shutdown-all", "Stop every configured CGC watcher."),
        ("refresh", "Reindex one managed CGC repository."),
        ("refresh-all", "Reindex every configured CGC repository."),
    ]:
        add_cgc_action_parser(cgc_actions, action, help_text=help_text)
    run_parser = add_cgc_action_parser(
        cgc_actions,
        "run",
        help_text="Run a bounded native CGC command in the managed environment.",
    )
    run_parser.add_argument(
        "--lifecycle-json",
        action="store_true",
        help="Render lifecycle command metadata instead of the native provider output.",
    )
    run_parser.add_argument(
        "native_args",
        nargs=argparse.REMAINDER,
        help="Native CGC arguments. Use -- before native args.",
    )
    visualize_parser = add_cgc_action_parser(
        cgc_actions,
        "visualize",
        help_text="Launch the long-running CGC visualizer server.",
    )
    visualize_parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Visualizer server port.",
    )
    visualize_parser.add_argument(
        "--context",
        help="Specific CGC context to visualize.",
    )

    grepai = providers.add_parser("grepai", help="Manage a GrepAI memory provider instance.")
    add_grepai_common_args(grepai)
    grepai_actions = grepai.add_subparsers(dest="action", required=True)
    for action, help_text in [
        ("status", "Inspect the managed GrepAI workspace."),
        ("install", "Install the managed GrepAI runtime."),
        ("backend-start", "Start the shared GrepAI backend."),
        ("backend-status", "Inspect the shared GrepAI backend."),
        ("start", "Start the managed GrepAI watcher."),
        ("stop", "Stop the managed GrepAI watcher."),
        ("refresh", "Restart the managed GrepAI watcher."),
    ]:
        add_grepai_action_parser(grepai_actions, action, help_text=help_text)
    grepai_run_parser = add_grepai_action_parser(
        grepai_actions,
        "run",
        help_text="Run a bounded GrepAI CLI command inside the managed Docker runner.",
    )
    grepai_run_parser.add_argument(
        "--lifecycle-json",
        action="store_true",
        help="Render lifecycle command metadata instead of the provider output.",
    )
    grepai_run_parser.add_argument(
        "native_args",
        nargs=argparse.REMAINDER,
        help="GrepAI CLI arguments. Use -- before CLI args.",
    )

    watchers = providers.add_parser(
        "watchers", help="Start, stop, or check every enabled provider watcher."
    )
    watchers.add_argument("action", choices=["status", "start", "stop", "shutdown-all"])
    add_common_provider_args(watchers)
    watchers.add_argument(
        "--from-settings", type=Path, help="Debug override for coordinator settings.json."
    )
    return parser


def normalize_provider_args(args: argparse.Namespace) -> None:
    normalize_provider_specific_args(args)
    args.coordination_root = args.coordination_root.resolve()
    normalize_optional_path_args(args)
    normalize_optional_repo_id(args)


def normalize_provider_specific_args(args: argparse.Namespace) -> None:
    normalizers = {"cgc": normalize_cgc_args, "grepai": normalize_grepai_args}
    normalizer = normalizers.get(args.provider)
    if normalizer:
        normalizer(args)


def normalize_optional_path_args(args: argparse.Namespace) -> None:
    if getattr(args, "from_settings", None):
        args.from_settings = args.from_settings.resolve()
    if hasattr(args, "code_repo_root") and args.code_repo_root is not None:
        args.code_repo_root = args.code_repo_root.resolve()


def normalize_optional_repo_id(args: argparse.Namespace) -> None:
    if hasattr(args, "repo_id") and args.repo_id is not None:
        args.repo_id = stable_provider_id(args.repo_id)


def cgc_cli_handlers() -> dict[str, Any]:
    return {
        "apply-settings": cgc_apply_settings,
        "backend-start": cgc_backend_start,
        "backend-status": cgc_backend_status,
        "status": cgc_status,
        "install": cgc_install,
        "install-all": cgc_install_all,
        "init-layout": cgc_init_layout,
        "patch": cgc_patch,
        "doctor": cgc_doctor,
        "start": cgc_start,
        "start-all": cgc_start_all,
        "stop": cgc_stop,
        "stop-all": cgc_stop_all,
        "shutdown-all": cgc_stop_all,
        "refresh": cgc_refresh,
        "refresh-all": cgc_refresh_all,
        "run": cgc_run,
        "visualize": cgc_visualize,
    }


def run_cli_action(parser: argparse.ArgumentParser, args: argparse.Namespace) -> dict[str, Any]:
    if args.provider == "cgc":
        return cgc_cli_handlers()[args.action](args)
    if args.provider == "grepai":
        return grepai_run(args, args.action)
    if args.provider == "watchers":
        return watchers_run(args, args.action)
    parser.error(f"unsupported provider: {args.provider}")
    return {"provider": args.provider, "action": args.action, "ok": False}


def cli_error_result(args: argparse.Namespace, error: BaseException) -> dict[str, Any]:
    return {"provider": args.provider, "action": args.action, "ok": False, "error": str(error)}


def render_cli_result(data: dict[str, Any], args: argparse.Namespace) -> None:
    renderers = {
        ("cgc", "run"): render_cgc_run_result,
        ("grepai", "run"): render_grepai_run_result,
    }
    renderer = renderers.get((args.provider, args.action))
    rendered = renderer(data, args) if renderer else False
    if not rendered:
        render(data, as_json=args.json)


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        normalize_provider_args(args)
        data = run_cli_action(parser, args)
    except (
        ContextProviderError,
        subprocess.TimeoutExpired,
        OSError,
        json.JSONDecodeError,
    ) as error:
        data = cli_error_result(args, error)
    render_cli_result(data, args)
    return 0 if data.get("ok") else 1
