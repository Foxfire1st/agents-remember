#!/usr/bin/env python3
"""Prepare a PNT sandbox's settings and prove they hold only the sandbox's own values.

The sandbox is a disposable installation of this checkout, so its settings file is one real
installation's settings file: the same coordination root, the same transcript folder, the same
dashboard port and the same one repository. This module is the sandbox's owner of that file and
of the receipt that proves what it wrote.

It owns the *sandbox's* values only. The block a cold host needs and the one user settings
location are product behaviour, so this module asks the product for them instead of spelling
its own: :func:`agents_remember.kernel.primitives.fresh_host.fresh_host_block` supplies the
``paseoRuntime`` block and :func:`agents_remember.kernel.primitives.user_settings_location.
require_user_settings_path` resolves the shared file the whole host reads. A sandbox that
spelled either one itself could drift from the build under test and still report success.

Two properties are checked here, and both are about the sandbox being ordinary rather than
special:

* the settings file differs from the product's own default only in the recorded root, the
  transcript folder and the dashboard port; an existing native authority block is preserved,
  and only an absent block receives the product's fresh defaults;
* an existing shared host file is an input, byte for byte. The sandbox never rewrites the
  developer's configured file, and its receipt records the same digest before and after.

The receipt is inert data: digests, the two documents with provider entries omitted, and
difference keys with their reasons. No provider value is printed or copied into it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from agents_remember.kernel.primitives.fresh_host import fresh_host_block
from agents_remember.kernel.primitives.paseo_runtime_settings import (
    PaseoRuntimeSettingsError,
    parse_paseo_runtime_settings,
)
from agents_remember.kernel.primitives.user_settings_location import require_user_settings_path

#: Settings keys the sandbox may differ on, and the reason each difference is ordinary.
SETTINGS_DIFFERENCE_REASONS = {
    "coordinationRoot": "the sandbox's own disposable coordination root",
    "transcriptRoot": "the sandbox's own transcript folder under that root",
    "dashboard": "the sandbox's recorded reserved dashboard port",
}

#: Host-block keys the sandbox may differ on, and the reason each difference is ordinary.
HOST_DIFFERENCE_REASONS = {
    "installPrefix": "native sandbox-owned install prefix",
    "home": "native sandbox-owned daemon home",
    "listen": "recorded reserved host port",
    "embed": "recorded host/dashboard ports and both loopbacks",
    "providers": "native guarded harness provider entries",
}

#: The dashboard port a settings template names before an installation records its own.
DEFAULT_DASHBOARD_PORT = 8765

#: Words a repository id may not contain: it names one folder under the workspace root.
_FOLDER_SEPARATORS = ("/", "\\")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest_if_present(path: Path) -> str | None:
    """The file's digest, or ``None`` when it does not exist (an input the sandbox never creates)."""
    return digest(path) if path.exists() else None


def repository_ids(workspace_root: Path, repos: list[str]) -> list[str]:
    """The repository folder names to configure, each of which must exist under the root."""
    if not repos:
        raise ValueError("pass at least one --repo <repository-folder-name>")
    ordered: list[str] = []
    for repo_id in repos:
        if not repo_id or any(separator in repo_id for separator in _FOLDER_SEPARATORS):
            raise ValueError(
                f"repository id must be a folder name under workspace root: {repo_id!r}"
            )
        repo_root = workspace_root / repo_id
        if not repo_root.is_dir():
            raise ValueError(f"repository root does not exist for {repo_id!r}: {repo_root}")
        if repo_id not in ordered:
            ordered.append(repo_id)
    return ordered


def settings_payload(
    data: dict[str, Any],
    workspace_root: Path,
    repos: list[str],
    coordination_root: Path,
    dashboard_port: int,
) -> dict[str, Any]:
    """Only the recorded coordination root, transcript folder and dashboard port change."""
    if not coordination_root.is_absolute() or not 0 < dashboard_port < 65536:
        raise ValueError(
            "coordination root must be absolute and the dashboard port must be 1..65535"
        )
    coordination = coordination_root.resolve()
    return {
        **data,
        "coordinationRoot": coordination.as_posix(),
        "workspaceRoot": workspace_root.as_posix(),
        "transcriptRoot": (coordination / "logs/mcp").as_posix(),
        "dashboard": {**data.get("dashboard", {}), "port": dashboard_port},
        "repositories": {repo: {} for repo in repos},
    }


def difference_keys(default: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    return [
        key for key in sorted(set(default) | set(actual)) if default.get(key) != actual.get(key)
    ]


def unapproved(differences: list[str], approved: dict[str, str], subject: str) -> None:
    refused = [key for key in differences if key not in approved]
    if refused:
        raise ValueError(f"unapproved {subject} differences: {refused}")


def host_authority(before: dict[str, Any], default: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Validate existing authority before writes; absence alone selects fresh defaults."""
    if "paseoRuntime" not in before:
        return default, False
    block = before["paseoRuntime"]
    try:
        parsed = parse_paseo_runtime_settings(block)
    except PaseoRuntimeSettingsError as error:
        raise ValueError(f"existing paseoRuntime is invalid: {error}") from error
    if parsed is None:
        raise ValueError("existing paseoRuntime is null; it was not replaced")
    return block, True


def receipt_settings(data: dict[str, Any]) -> dict[str, Any]:
    """Keep settings provenance without copying either provider map into the receipt."""
    result = {key: value for key, value in data.items() if key != "providers"}
    if isinstance(result.get("paseoRuntime"), dict):
        result["paseoRuntime"] = {
            key: value for key, value in result["paseoRuntime"].items() if key != "providers"
        }
    return result


def check(
    settings: Path,
    workspace: Path,
    coordination: Path,
    dashboard_port: int,
    repo: str,
) -> dict[str, Any]:
    """Prepare ``settings`` and return the receipt that proves what the sandbox holds."""
    repos = repository_ids(workspace, [repo])
    input_hash = digest(settings)
    before = json.loads(settings.read_text(encoding="utf-8"))
    default_host = fresh_host_block(dashboard_port)
    block, existing = host_authority(before, default_host)
    # MIK-R196 rule 3: the sandbox's own settings file is the host authority, so the block lives
    # in it. The developer's user settings file is an input the sandbox never writes -- and never
    # creates: an absent one stays absent.
    shared = require_user_settings_path(coordination)
    shared_before = digest_if_present(shared)
    prepared = {
        **settings_payload(before, workspace, repos, coordination, dashboard_port),
        "paseoRuntime": block,
    }
    # The template's own default: the workspace's coordination root and the port it names.
    default_settings = settings_payload(
        before, workspace, repos, workspace / "ar-coordination", DEFAULT_DASHBOARD_PORT
    )
    # The host block is the product's own fresh block, compared below; it is not a settings
    # difference the template has to approve.
    settings_differences = [
        key for key in difference_keys(default_settings, prepared) if key != "paseoRuntime"
    ]
    unapproved(settings_differences, SETTINGS_DIFFERENCE_REASONS, "settings")
    differences = difference_keys(default_host, block)
    if not existing:
        unapproved(differences, HOST_DIFFERENCE_REASONS, "host")
    if digest_if_present(shared) != shared_before:
        raise ValueError("preparing the sandbox settings changed the developer's own settings file")
    settings.write_text(json.dumps(prepared, indent=2) + "\n", encoding="utf-8", newline="\n")
    actual = json.loads(settings.read_text(encoding="utf-8"))
    return {
        "settingsOwner": str(Path(__file__).resolve()),
        "settingsOwnerSha256": digest(Path(__file__).resolve()),
        "productOwners": [
            "agents_remember/kernel/primitives/fresh_host.py",
            "agents_remember/kernel/primitives/user_settings_location.py",
        ],
        "inputSettingsPath": str(settings),
        "inputSettingsSha256": input_hash,
        "preparedSettingsPath": str(settings),
        "preparedSettingsSha256": digest(settings),
        "sharedInputPath": str(shared),
        "sharedBeforeSha256": shared_before,
        "sharedAfterSha256": digest_if_present(shared),
        "invocations": 1,
        "arguments": {
            "coordinationRoot": str(coordination),
            "dashboardPort": dashboard_port,
            "repository": repo,
        },
        "defaultSettings": receipt_settings(default_settings),
        "preparedSettings": receipt_settings(actual),
        "settingsDifferenceKeys": settings_differences,
        "hostDifferenceKeys": differences,
        "providerIds": sorted(block["providers"]),
        "hostDifferences": [
            {
                "key": key,
                "reason": (
                    "existing native authority, preserved unchanged"
                    if existing
                    else HOST_DIFFERENCE_REASONS[key]
                ),
            }
            for key in differences
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("settings", "workspace", "coordination"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--dashboard-port", type=int, required=True)
    parser.add_argument("--repo", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            check(args.settings, args.workspace, args.coordination, args.dashboard_port, args.repo)
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
