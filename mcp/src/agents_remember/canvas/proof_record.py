"""Mandatory served-policy, effective-source and artifact observations of a browser record.

A check table alone is not a browser witness. Missing or weakened observations keep execution off.
The constructor's explicit harness switch is used only by the browser proof's isolated fixture.
"""

from __future__ import annotations

import hashlib
import importlib
import re
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

from agents_remember.canvas import policy

_HEX = re.compile(r"[0-9a-f]{64}\Z")


def source_bindings() -> dict[str, Any]:
    modules = {
        name: hashlib.sha256(
            Path(cast(str, importlib.import_module(name).__file__)).read_bytes()
        ).hexdigest()
        for name in policy.ENFORCEMENT_SOURCES
    }
    dashboard = Path(__file__).resolve().parent.parent / "package_data" / "dashboard"
    files = sorted(path for path in dashboard.rglob("*") if path.is_file())
    frontend = (
        hashlib.sha256(
            b"\0".join(
                path.relative_to(dashboard).as_posix().encode() + b"\0" + path.read_bytes()
                for path in files
            )
        ).hexdigest()
        if files
        else "absent"
    )
    return {"modules": modules, "frontend": frontend}


def _headers_gaps(payload: dict) -> list[str]:
    headers = payload.get("servedPolicy")
    if not isinstance(headers, dict):
        return ["no served policy observations"]
    origin, session = headers.get("origin"), headers.get("session")
    if not isinstance(origin, str) or not isinstance(session, str):
        return ["served policy names no origin/session"]
    try:
        expected = policy.frame_csp(origin, session=session, scripts=True)
    except ValueError:
        return ["served policy names an invalid session"]
    fields = {
        "csp": expected,
        "permissionsPolicy": policy.FRAME_PERMISSIONS_POLICY,
        "openerPolicy": "same-origin",
        "frameCorp": "same-origin",
        "assetCorp": "cross-origin",
        "assetCors": "*",
    }
    return [
        f"served policy {name} is missing or differs"
        for name, value in fields.items()
        if headers.get(name) != value
    ]


def _artifact_gaps(payload: dict) -> list[str]:
    artifact = payload.get("artifacts")
    if not isinstance(artifact, dict):
        return ["no artifact observations"]
    assets = artifact.get("assets")
    if not isinstance(assets, list) or not assets:
        return ["no observed artifact assets"]
    entry = artifact.get("entrypoint")
    if not _HEX.fullmatch(str(artifact.get("artifactDigest", ""))):
        return ["no observed artifact digest"]
    names = []
    for asset in assets:
        if (
            not isinstance(asset, dict)
            or not isinstance(asset.get("path"), str)
            or not _HEX.fullmatch(str(asset.get("digest", "")))
            or not isinstance(asset.get("bytes"), int)
            or asset["bytes"] <= 0
        ):
            return ["malformed observed artifact asset"]
        names.append(asset["path"])
    return (
        [] if entry in names and len(names) == len(set(names)) else ["unbound artifact entrypoint"]
    )


def _matrix_gaps(payload: dict) -> list[str]:
    assets = payload.get("assets")
    if not isinstance(assets, dict) or assets.get("matrixComplete") is not True:
        return ["no completed asset matrix"]
    matrix, targets, requests = (assets.get(key) for key in ("matrix", "targets", "requests"))
    if (
        not isinstance(matrix, dict)
        or not isinstance(targets, dict)
        or not isinstance(requests, dict)
    ):
        return ["no asset matrix targets/results/request ledger"]
    own = {f"{kind}:own" for kind in ("script", "style", "image")}
    foreign = {
        f"{kind}:{target}"
        for kind in ("script", "style", "image", "font", "media", "iframe", "worker", "fetch")
        for target in ("revision", "frame", "task", "session")
    }
    if set(matrix) != own | foreign or set(targets) != own | foreign:
        return ["asset matrix does not name all 35 cases"]
    gaps = []
    for key in own | foreign:
        expected = "loaded" if key in own else "blocked:"
        if not isinstance(matrix[key], str) or not matrix[key].startswith(expected):
            gaps.append(f"asset matrix {key} is not observed as {expected}")
    ledger = requests.get("host")
    if (
        not isinstance(ledger, list)
        or not ledger
        or not all(isinstance(row, str) for row in ledger)
    ):
        return [*gaps, "no asset matrix host request ledger"]
    for key in foreign:
        if not isinstance(targets[key], str) or any(
            f" {urlsplit(targets[key]).path} -> 200" in row for row in ledger
        ):
            gaps.append(f"foreign asset {key} is unbound or answered 200")
    if requests.get("external") != []:
        gaps.append("asset matrix reached an external sink or names no sink observation")
    return gaps


def _request_gaps(payload: dict) -> list[str]:
    requests = payload.get("requests")
    if not isinstance(requests, dict):
        return ["no request observations"]
    return [
        f"request observation {key} is missing or differs"
        for key, expected in (
            ("hostApiRequests", 0),
            ("externalSinkRequests", 0),
            ("externalSink", []),
        )
        if requests.get(key) != expected
    ]


def record_gaps(payload: dict) -> tuple[str, ...]:
    gaps = (
        _headers_gaps(payload)
        + _artifact_gaps(payload)
        + _matrix_gaps(payload)
        + _request_gaps(payload)
    )
    source = payload.get("source")
    if not isinstance(source, dict):
        gaps.append("no effective-source observations")
    else:
        for key, expected in source_bindings().items():
            if expected == "absent" or source.get(key) != expected:
                gaps.append(f"effective-source {key} is missing or differs")
        for key in ("harness", "proofServer", "panelBundle"):
            if not _HEX.fullmatch(str(source.get(key, ""))):
                gaps.append(f"no {key} source digest")
    return tuple(gaps)
