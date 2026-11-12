"""Census receipts identify the captured bytes actually parsed and resolved."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

import pytest
from agents_remember.application import census_artifacts, memory_tools
from agents_remember.memory_quality import reference_anchors
from agents_remember.memory_quality.knowledge_validator.trees import (
    KnowledgeTreeReadError,
    code_tree_from_git,
    knowledge_tree_from_git,
)
from agents_remember.memory_quality.reference_state import git_blob_id, located
from agents_remember.models.knowledge_files.anchor_content import content_identity
from knowledge_validator_test_support import encode, tree

SOURCE = "pkg/module.py"
ORIGINAL = b"def alpha():\n    return 1\n"
CHANGED = b"def alpha():\n    return 2\n"


def _world(root: Path, git: bool) -> tuple[Path, Path, Path]:
    code, memory = root / "code", root / "memory"
    source = code / SOURCE
    source.parent.mkdir(parents=True)
    source.write_bytes(ORIGINAL)
    found = located(SOURCE, ORIGINAL, {"kind": "symbol", "name": "alpha"})
    assert found is not None
    document = {
        "schema": "ar-onboarding-file/v1",
        "path": SOURCE,
        "references": {
            "1": {
                "note": "alpha returns one",
                "targets": [
                    {
                        "kind": "code",
                        "anchor": {
                            "blob": git_blob_id(ORIGINAL),
                            "content": content_identity(found),
                            "locator": {"kind": "symbol", "name": "alpha"},
                        },
                    }
                ],
            }
        },
        "realizes": [],
    }
    files = {
        "knowledge/layout.json": encode({"schema": "ar-memory-layout/v2", "conversion": "1"}),
        f"onboarding/{SOURCE}.json": encode(document),
    }
    for path, data in tree(files).files.items():
        destination = memory / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    if git:
        for repository in (code, memory):
            subprocess.run(["git", "init", "-q", str(repository)], check=True)
            subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repository),
                    "-c",
                    "user.name=Fixture",
                    "-c",
                    "user.email=f@x",
                    "commit",
                    "-qm",
                    "original pair",
                ],
                check=True,
            )
    return code, memory, memory / f"onboarding/{SOURCE}.json"


@pytest.mark.parametrize("git", [False, True], ids=["directory", "git"])
@pytest.mark.parametrize("restore", [False, True], ids=["changed", "returned-to-before"])
def test_live_changes_cannot_rebind_the_captured_census(
    tmp_path: Path,
    git: bool,
    restore: bool,
) -> None:
    code, memory, sidecar = _world(tmp_path, git)
    before = census_artifacts.pair_identities(memory, code)
    sidecar_before = sidecar.read_bytes()
    original_census = reference_anchors.census

    def change_during_read(*args: Any, **kwargs: Any) -> dict[str, Any]:
        (code / SOURCE).write_bytes(CHANGED)
        changed = json.loads(sidecar_before)
        changed["references"]["1"]["note"] = "alpha returns two"
        sidecar.write_bytes(encode(changed))
        result = original_census(*args, **kwargs)
        if restore:
            (code / SOURCE).write_bytes(ORIGINAL)
            sidecar.write_bytes(sidecar_before)
        return result

    scope = SimpleNamespace(
        repo_id="demo",
        onboarding_root=memory / "onboarding",
        code_root=code,
    )
    config = cast(Any, SimpleNamespace(coordination_root=tmp_path / "coord"))
    with (
        mock.patch.object(memory_tools, "_memory_scope", return_value=scope),
        mock.patch.object(reference_anchors, "census", side_effect=change_during_read),
    ):
        answer = memory_tools.citation_census_tool(config, repo_id="demo", contract_path="leaf.md")

    artifact = Path(answer["artifactPath"])
    complete = json.loads(artifact.read_bytes())
    assert answer["artifactSha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert answer["artifactInputIdentities"] == before
    assert answer["artifactPairStableDuringRead"] is restore
    assert complete["targets"][0]["actualBlob"] == git_blob_id(ORIGINAL)
    assert complete["ok"] is True and complete["denominator"] == 1
    assert complete["inputIdentities"] == before
    if git:
        replay = original_census(
            knowledge_tree_from_git(memory, before["memory"]["worktreeTree"]),
            code_tree_from_git(code, before["code"]["worktreeTree"]),
        )
        assert replay["targets"] == complete["targets"]


def test_a_quiet_captured_pair_has_reproducible_input_identities(tmp_path: Path) -> None:
    code, memory, _ = _world(tmp_path, True)
    from agents_remember.application.census_inputs import measured_census  # noqa: PLC0415

    full, inputs = measured_census(memory, code)
    assert full["ok"] is True
    assert inputs.stable is True
    assert (
        full["inputIdentities"] == inputs.identity == census_artifacts.pair_identities(memory, code)
    )
    # Failed capture must not turn a partial scratch index into the measured tree.
    with (
        mock.patch.object(
            census_artifacts,
            "run_git_with_index",
            return_value=subprocess.CompletedProcess([], 1, "", "capture failed"),
        ),
        pytest.raises(KnowledgeTreeReadError, match="cannot capture"),
    ):
        measured_census(memory, code)
