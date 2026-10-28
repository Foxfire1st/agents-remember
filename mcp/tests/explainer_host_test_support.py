"""Shared host fixture identities and workspace construction."""

from __future__ import annotations

from pathlib import Path

from agents_remember.cli.paseo_explainer_host import PaseoExplainerHost
from canvas_host_test_support import REPO, TASK, config

SERVER_NAME = "agents-remember-task"

LEAF = "260928-MIK-L183"
CAPSULE = "You are this leaf's review explainer.\n"
NOW = "2026-10-22T08:00:00+00:00"


def _text_of(result: object) -> str:
    """One ``call_tool`` answer's text content, for the JSON bodies these tests assert on."""

    if isinstance(result, tuple):
        result = result[0]
    blocks = result if isinstance(result, list) else getattr(result, "content", None)
    if isinstance(blocks, list):
        for block in blocks:
            text = getattr(block, "text", None)
            if isinstance(text, str):
                return text
    return str(result)


def _workspace(tmp_path: Path) -> Path:
    """A path of the shape the server reads its coordination root back from."""

    workspace = tmp_path / "explainer" / REPO / TASK / LEAF
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def _host(tmp_path: Path, *, bridge) -> PaseoExplainerHost:
    runtime = config(tmp_path)
    return PaseoExplainerHost(runtime)
