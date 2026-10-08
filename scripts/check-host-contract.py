#!/usr/bin/env python3
"""Release concordance for the shipped host pin, plugin SDK and complete npm lock."""

from __future__ import annotations

import json
import re
from pathlib import Path


def check(root: Path) -> list[str]:
    data = root / "mcp/src/agents_remember/package_data"
    host = data / "paseo_host"
    contract = json.loads((host / "contract.json").read_text())
    manifest = json.loads((host / "package.json").read_text())
    lock = json.loads((host / "package-lock.json").read_text())
    plugin = json.loads((data / "paseo_plugin/package.json").read_text())
    errors = []
    expected = {contract["package"]: contract["version"]}
    if (
        manifest.get("dependencies") != expected
        or lock["packages"][""].get("dependencies") != expected
    ):
        errors.append("host manifest/lock root does not match the build contract")
    if plugin["devDependencies"]["@getpaseo/plugin"] != contract["version"]:
        errors.append("plugin development SDK version does not match the build host")
    package_entry = lock["packages"].get("node_modules/" + contract["package"], {})
    if package_entry.get("version") != contract["version"]:
        errors.append("installed host lock entry does not match the build contract")
    for path, package in lock["packages"].items():
        if path and (
            not package.get("version")
            or not package.get("integrity")
            or not str(package.get("resolved", "")).startswith("https://")
        ):
            errors.append(f"unlocked or unverified package: {path}")
    archives = contract["node"]["archives"]
    if set(archives) != {"linux-x64"}:
        errors.append("host Node contract must claim Linux x86_64 only")
    for platform, archive in archives.items():
        version = contract["node"]["version"]
        expected_url = f"https://nodejs.org/dist/v{version}/node-v{version}-{platform}.tar.xz"
        if archive["url"] != expected_url or not re.fullmatch("[0-9a-f]{64}", archive["sha256"]):
            errors.append(f"invalid official Node archive pin: {platform}")
    return errors


if __name__ == "__main__":
    findings = check(Path(__file__).resolve().parents[1])
    print("\n".join(findings) if findings else "host contract, plugin SDK and whole npm lock agree")
    raise SystemExit(bool(findings))
