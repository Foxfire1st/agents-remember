"""The host part of runtime_install, including a strictly read-only preview."""

from __future__ import annotations

from typing import Any

from agents_remember.errors import PaseoRuntimeFailure
from agents_remember.kernel.primitives.paseo_authority import paseo_runtime_path
from agents_remember.kernel.primitives.paseo_node_paths import product_node
from agents_remember.kernel.primitives.paseo_runtime_settings import PaseoRuntimeSettings
from agents_remember.kernel.primitives.runtime_config import (
    ConfigError,
    McpRuntimeConfig,
    load_paseo_runtime_settings,
)
from agents_remember.serving.paseo.paseo_command import CommandRunner, PaseoCli, run_command
from agents_remember.serving.paseo.paseo_node import node_executable_valid
from agents_remember.serving.paseo.paseo_packages import installed_lock_matches
from agents_remember.serving.paseo.paseo_plugin_files import (
    installed_plugin_path,
    plugin_source_root,
    read_embed,
    tree_digest,
)
from agents_remember.serving.paseo.paseo_process_record import (
    ProcessReader,
    inspect_record,
    read_process,
)
from agents_remember.serving.paseo.paseo_provision import (
    bind_error,
    provision_for_install,
)
from agents_remember.serving.paseo.paseo_run import (
    ProvisionIntent,
    _check_install_restart,
    _Run,
)
from agents_remember.serving.paseo.paseo_settings import pending_settings


def install_host(config: McpRuntimeConfig, dry_run: bool) -> dict[str, Any]:
    source = paseo_runtime_path(config.coordination_root)
    try:
        settings = load_paseo_runtime_settings(config.config_path)
        if settings is None:
            return {
                "ok": True,
                "changed": False,
                "configured": False,
                "settingsPath": source.as_posix(),
                "node": None,
                "message": f"No host configured: {source} has no paseoRuntime block; "
                "complete the shared host settings in the install setup.",
            }
        source = settings.source_path or source
        report = preview_host(settings) if dry_run else provision_for_install(settings)
        report["configured"] = True
        report["settingsPath"] = source.as_posix()
        if report.get("restartRequired"):
            report["message"] = (
                f"Host kept running; restart required: {', '.join(report['restartRequired'])}. "
                f"Run agents-remember paseo provision --config {config.config_path} from a "
                "terminal outside the host; that restart ends agent sessions and running turns."
            )
        if settings.version_notice:
            report["notice"] = f"{settings.version_notice}; settings: {source}"
        return report
    except (ConfigError, PaseoRuntimeFailure, OSError, UnicodeDecodeError) as error:
        failure = (
            error
            if isinstance(error, PaseoRuntimeFailure)
            else PaseoRuntimeFailure("settings_invalid", "host", str(error))
        )
        return {
            "ok": False,
            "changed": False,
            "settingsPath": source.as_posix(),
            "error": failure.as_payload(),
        }


def preview_host(
    settings: PaseoRuntimeSettings,
    *,
    runner: CommandRunner = run_command,
    reader: ProcessReader = read_process,
) -> dict[str, Any]:
    """Observe without a lockfile, download, npm install, setting write or daemon start."""
    node = product_node()
    cli = PaseoCli(settings, runner)
    run = _Run(settings, cli, plugin_source_root(), bind_error, reader, ProvisionIntent.INSTALL)
    _check_install_restart(run)
    record = inspect_record(settings.home, "install", reader)
    node_present = node_executable_valid(node, runner) and node.npm.is_file()
    if node.root.exists() and not node_present:
        raise PaseoRuntimeFailure(
            "node_target_invalid",
            "node",
            f"Node {node.version} ({node.platform}) at {node.root}, from {node.url}, "
            "is invalid; it would not be overwritten.",
        )
    installed = cli.installed_version()
    install = installed != settings.version or not installed_lock_matches(settings.install_prefix)
    pending = pending_settings(cli) if installed is not None and node_present else []
    home_files = read_embed(settings.home) != settings.embed_payload() or tree_digest(
        installed_plugin_path(settings.home)
    ) != tree_digest(plugin_source_root())
    held = bool(run.restart_reasons)
    changed = not held and (
        not node_present or install or bool(pending) or home_files or record.kind != "own"
    )
    return {
        "ok": True,
        "changed": changed,
        "home": settings.home.as_posix(),
        "installPrefix": settings.install_prefix.as_posix(),
        "version": settings.version,
        "listen": settings.listen,
        "node": {**node.payload(), "wouldInstall": not held and not node_present},
        "wouldInstallHost": not held and install,
        "wouldStart": not held and record.kind != "own",
        "restartRequired": run.restart_reasons,
        "daemon": {"action": "untouched", "reasons": run.restart_reasons},
        "changes": [],
        "error": None,
    }
