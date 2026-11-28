"""Authored reference changes renew routing without editing generated facts."""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from agents_remember.errors import CapsuleManifestError
from agents_remember.models.role_capsules.manifest_derived import compose_manifest
from agents_remember_test_support.testing.instruction_renewal import RENEW_COMMAND, renewal_module

MODULE = renewal_module("manifest")
LIFECYCLE = Path("skills/l-01-agent-lifecycles")
ROOT = Path(__file__).resolve().parents[2]


def _command():
    spec = importlib.util.spec_from_file_location(
        "_manifest_renewal_cli", ROOT / "scripts/renew-instructions.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _world(root: Path, entry: dict, generated: dict) -> dict:
    lifecycle = root / LIFECYCLE
    (lifecycle / "roles").mkdir(parents=True)
    (lifecycle / "reference").mkdir()
    (lifecycle / "roles/worker.md").write_text(
        "Before editing source, read [the detail](../reference/detail.md) for its rules.\n"
    )
    (lifecycle / "reference/detail.md").write_text("# Detail\n\nKeep the rule.\n")
    document = {
        "role_order": ["worker", "manager"],
        "roles": {"worker": {"file": "roles/worker.md"}},
        "operations": {},
        "core": {},
        "references": {"detail": entry},
        "derived": {"references": generated},
    }
    (lifecycle / "composition-manifest.json").write_text(json.dumps(document))
    return document


@pytest.mark.parametrize("generated", [{}, {"detail": {"roles": ["worker"]}}])
def test_new_reference_and_removed_no_step_mark_derive_from_authored_source(
    tmp_path: Path, generated: dict
) -> None:
    document = _world(
        tmp_path,
        {"source": "reference/detail.md", "step_detail": True},
        generated,
    )
    before = (tmp_path / LIFECYCLE / "composition-manifest.json").read_bytes()
    values = MODULE.plan(tmp_path)
    assert values["references"]["detail"] == {
        "roles": ["worker"],
        "step": "before editing source",
    }
    assert compose_manifest({**document, "derived": values})["references"]["detail"]["step"]
    assert (tmp_path / LIFECYCLE / "composition-manifest.json").read_bytes() == before


def test_unpointed_reference_stops_naming_the_authored_repair(tmp_path: Path) -> None:
    _world(tmp_path, {"source": "reference/detail.md", "step_detail": True}, {})
    (tmp_path / LIFECYCLE / "roles/worker.md").write_text("# Worker\n")
    with pytest.raises(CapsuleManifestError, match=r"references\.detail") as caught:
        MODULE.plan(tmp_path)
    assert "source pointer" in caught.value.next_action
    assert "step:null" in caught.value.next_action


def test_composing_reader_refuses_neither_mark_nor_step() -> None:
    with pytest.raises(CapsuleManifestError, match=r"references\.detail"):
        compose_manifest(
            {
                "references": {"detail": {"source": "reference/detail.md"}},
                "derived": {"references": {"detail": {}}},
            }
        )


def test_routing_exception_is_authored_without_derived_line_numbers() -> None:
    document = json.loads((ROOT / LIFECYCLE / "composition-manifest.json").read_text())
    reason = document["references"]["digest-map"]["rolesReason"]
    assert all(re.search(rf"\b{role}\b", reason, re.I) for role in document["role_order"])
    assert all(
        not re.search(r"\.md:\d+", entry.get("rolesReason", ""))
        for entry in document["references"].values()
    )


def test_digest_map_audience_follows_its_authored_reason(tmp_path: Path) -> None:
    document = _world(
        tmp_path,
        {"source": "core/digest-map.json", "step_detail": False, "step": None},
        {"detail": {}},
    )
    path = tmp_path / LIFECYCLE / "composition-manifest.json"
    assert MODULE.plan(tmp_path)["references"]["detail"] == {}
    document["references"]["detail"]["rolesReason"] = (
        "The Worker and Manager read these dispositions."
    )
    path.write_text(json.dumps(document))
    assert MODULE.plan(tmp_path)["references"]["detail"] == {"roles": ["worker", "manager"]}


@pytest.mark.parametrize("fault", ["neither", "detail-mark"])
@pytest.mark.parametrize("arguments", [[], ["--check"]])
def test_command_and_check_name_invalid_no_step_decisions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    fault: str,
    arguments: list[str],
) -> None:
    entry: dict[str, object] = {
        "source": "reference/detail.md",
        "step_detail": fault == "detail-mark",
    }
    generated = {}
    if fault != "neither":
        entry["step"] = None
    document = _world(tmp_path, entry, generated)
    if fault == "neither":
        (tmp_path / LIFECYCLE / "roles/worker.md").write_text("# Worker\n")
    cli = _command()
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["renew-instructions.py", *arguments])
    assert cli.main() == 1
    diagnostic = capsys.readouterr().err
    assert "references.detail" in diagnostic
    assert RENEW_COMMAND in diagnostic
    if fault == "detail-mark":
        assert "step:null mark" in diagnostic
    with pytest.raises(CapsuleManifestError, match=r"references\.detail") as caught:
        compose_manifest(document)
    assert RENEW_COMMAND in caught.value.next_action


def test_ordinary_no_step_decision_wins_over_pointer_actions(tmp_path: Path) -> None:
    _world(
        tmp_path,
        {"source": "reference/detail.md", "step_detail": False, "step": None},
        {"detail": {}},
    )
    assert MODULE.plan(tmp_path)["references"]["detail"] == {"roles": ["worker"]}


@pytest.mark.parametrize(
    "derived",
    [None, {"references": None}, {"references": {"detail": {"step": "old action"}}}],
)
def test_renewal_ignores_stored_derived_state_for_an_ordinary_no_step_mark(
    tmp_path: Path, derived: object
) -> None:
    document = _world(
        tmp_path,
        {"source": "reference/detail.md", "step_detail": False, "step": None},
        {},
    )
    document["derived"] = derived
    (tmp_path / LIFECYCLE / "composition-manifest.json").write_text(json.dumps(document))
    assert MODULE.plan(tmp_path)["references"]["detail"] == {"roles": ["worker"]}


def _public_command_tree(root: Path) -> Path:
    for relative in ("scripts", "skills", "mcp/src", "mcp/test_support"):
        shutil.copytree(
            ROOT / relative, root / relative, ignore=shutil.ignore_patterns("__pycache__")
        )
    for relative in (
        "generated-copies.json",
        "mcp/certification-profile-v1.json",
        "docs/tool-details.md",
    ):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    return root / LIFECYCLE / "composition-manifest.json"


def _public_run(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "scripts/renew-instructions.py", *arguments],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )


def _ordinary_reference_with_step(root: Path) -> Path:
    path = _public_command_tree(root)
    document = json.loads(path.read_text())
    document["references"]["ordinary-trial"] = {
        "source": "reference/ordinary-trial.md",
        "description": "An ordinary reference with an authored no-step decision.",
        "injected": False,
        "step_detail": False,
    }
    path.write_text(json.dumps(document, indent=2) + "\n")
    (root / LIFECYCLE / "reference/ordinary-trial.md").write_text("# Ordinary reference\n")
    worker = root / LIFECYCLE / "roles/worker.md"
    pointer = "Before planning the trial, read [the reference](../reference/ordinary-trial.md).\n"
    worker.write_text(worker.read_text() + "\n" + pointer)
    renewed = _public_run(root)
    assert renewed.returncode == 0, renewed.stdout + renewed.stderr
    document = json.loads(path.read_text())
    assert (
        document["derived"]["references"]["ordinary-trial"]["step"] == "before planning the trial"
    )
    worker.write_text(worker.read_text().removesuffix("\n" + pointer))
    document["references"]["ordinary-trial"]["step"] = None
    path.write_text(json.dumps(document, indent=2) + "\n")
    return path


def test_public_command_replaces_a_stored_step_with_an_authored_no_step_mark(
    tmp_path: Path,
) -> None:
    path = _ordinary_reference_with_step(tmp_path)
    document = json.loads(path.read_text())
    with pytest.raises(CapsuleManifestError, match="stale") as caught:
        compose_manifest(document)
    assert "derived.references.ordinary-trial.step" in caught.value.detail
    assert RENEW_COMMAND in caught.value.next_action
    assert "remove" not in caught.value.next_action
    before = {item: item.read_bytes() for item in tmp_path.rglob("*") if item.is_file()}
    checked = _public_run(tmp_path, "--check")
    assert checked.returncode == 1, checked.stdout + checked.stderr
    assert "$.derived.references.ordinary-trial.step" in checked.stdout
    assert RENEW_COMMAND in checked.stderr
    assert before == {item: item.read_bytes() for item in before}
    renewed = _public_run(tmp_path)
    assert renewed.returncode == 0, renewed.stdout + renewed.stderr
    document = json.loads(path.read_text())
    assert document["references"]["ordinary-trial"]["step"] is None
    assert "step" not in document["derived"]["references"]["ordinary-trial"]
    assert "step" not in compose_manifest(document)["references"]["ordinary-trial"]
    checked = _public_run(tmp_path, "--check")
    assert checked.returncode == 0, checked.stdout + checked.stderr


@pytest.mark.parametrize("change", ["new-reference", "removed-mark"])
def test_public_command_renews_authored_reference_changes_without_typing_derived(
    tmp_path: Path, change: str
) -> None:
    path = _public_command_tree(tmp_path)
    document = json.loads(path.read_text())
    before = json.dumps(document["derived"], sort_keys=True)
    if change == "new-reference":
        document["references"]["repository-research"] = {
            "source": "reference/repository-research.md",
            "description": "Repository research instructions.",
            "injected": False,
            "step_detail": True,
        }
        key = "repository-research"
    else:
        del document["references"]["master-task"]["step"]
        key = "master-task"
    assert json.dumps(document["derived"], sort_keys=True) == before
    path.write_text(json.dumps(document, indent=2) + "\n")
    command = [sys.executable, "scripts/renew-instructions.py"]
    renewed = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, check=False)
    assert renewed.returncode == 0, renewed.stdout + renewed.stderr
    generated = json.loads(path.read_text())["derived"]["references"][key]
    assert generated["roles"]
    assert generated["step"]
    before_check = {item: item.read_bytes() for item in tmp_path.rglob("*") if item.is_file()}
    checked = subprocess.run(
        [*command, "--check"], cwd=tmp_path, capture_output=True, text=True, check=False
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert "Instruction facts and package copies are current." in checked.stdout
    assert before_check == {item: item.read_bytes() for item in before_check}
