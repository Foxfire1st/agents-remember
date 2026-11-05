"""F10 trial-home policy controls; native dispatch checks start no model or actor."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from agent_evals.trial_isolation import hermes_home


def _seed(root: Path, name: str = "hermes") -> tuple[Path, dict]:
    source = root / "source"
    source.mkdir(exist_ok=True)
    (source / "config.yaml").write_text(
        json.dumps({"platform_toolsets": {"acp": ["terminal", "file", "skills"]}})
    )
    project = root / "project"
    project.mkdir(exist_ok=True)
    home = root / name
    return home, hermes_home(source, home, project)


def test_trial_seed_installs_fixed_skill_manage_deny_hook(tmp_path: Path) -> None:
    home, receipt = _seed(tmp_path)
    config = json.loads((home / "config.yaml").read_text())
    assert config["hooks_auto_accept"] is True
    hook = config["hooks"]["pre_tool_call"]
    assert len(hook) == 1
    assert hook[0]["matcher"] == "^skill_manage$"
    assert hook[0]["timeout"] == 10
    assert hook[0]["fail_closed"] is True
    assert (home / "hooks/deny-skill-manage.py").is_file()
    assert receipt["skillPolicy"]["authority"] == "20261030T155305Z"
    assert receipt["skillPolicy"]["seedConfigException"] == 1
    assert receipt["acpToolsets"]["seedConfigException"] == 2
    assert "disabled_toolsets" not in config.get("agent", {})


import os
import shlex
import subprocess

import pytest
from agent_evals.trial_skill_policy import (
    HOOK_PATH,
    POLICY_MESSAGE,
    check_skill_policy_integrity,
    skill_policy_snapshot,
)
from agents_remember.errors import AuthorityError
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS


def test_fixed_command_binds_home_with_spaces_and_returns_native_block(tmp_path: Path) -> None:
    home, _ = _seed(tmp_path, "private Hermes home")
    command = json.loads((home / "config.yaml").read_text())["hooks"]["pre_tool_call"][0]["command"]
    assert shlex.split(command) == ["/usr/bin/python3", "-I", "-B", str(home / HOOK_PATH)]
    before = skill_policy_snapshot(home)
    done = subprocess.run(
        command,
        shell=True,
        input='{"args":{"content":"unlogged-control"}}',
        text=True,
        capture_output=True,
        check=False,
        timeout=HANG_GUARD_SECONDS,
    )
    assert done.returncode == 0
    assert json.loads(done.stdout) == {"action": "block", "message": POLICY_MESSAGE}
    assert done.stderr == ""
    assert check_skill_policy_integrity(home, before)["unchanged"] is True


def test_both_arms_have_same_semantic_policy_and_hook_bytes(tmp_path: Path) -> None:
    left, left_receipt = _seed(tmp_path, "old-text")
    right, right_receipt = _seed(tmp_path, "candidate-text")
    assert left_receipt["skillPolicy"] == right_receipt["skillPolicy"]
    assert (left / HOOK_PATH).read_bytes() == (right / HOOK_PATH).read_bytes()
    assert left_receipt["acpToolsets"] == right_receipt["acpToolsets"]
    left_config = (left / "config.yaml").read_text().replace(str(left), "<TRIAL_HOME>")
    right_config = (right / "config.yaml").read_text().replace(str(right), "<TRIAL_HOME>")
    assert left_config == right_config


def test_snapshot_binds_only_policy_files_and_ignores_credential_changes(tmp_path: Path) -> None:
    home, _ = _seed(tmp_path)
    (home / ".env").write_text("CONTROL_KEY=before\n")
    (home / "auth.json").write_text('{"control":"before"}')
    before = skill_policy_snapshot(home)
    assert [row["path"] for row in before["files"]] == ["config.yaml", HOOK_PATH]
    (home / ".env").write_text("CONTROL_KEY=after\n")
    (home / "auth.json").unlink()
    checked = check_skill_policy_integrity(home, before)
    assert checked["unchanged"] is True
    assert checked["cause"] is None
    assert checked["outcome"] is None
    assert "CONTROL_KEY" not in json.dumps(checked)


@pytest.mark.parametrize("relative", ["config.yaml", HOOK_PATH])
@pytest.mark.parametrize("mutation", ["changed", "missing", "symlink", "hardlink"])
def test_policy_file_mutation_is_instrument_altered(
    tmp_path: Path, relative: str, mutation: str
) -> None:
    home, _ = _seed(tmp_path)
    before = skill_policy_snapshot(home)
    target = home / relative
    if mutation == "changed":
        target.write_bytes(target.read_bytes() + b"\n")
    elif mutation == "missing":
        target.unlink()
    else:
        saved = tmp_path / "saved-policy-file"
        target.rename(saved)
        if mutation == "symlink":
            target.symlink_to(saved)
        else:
            os.link(saved, target)
    checked = check_skill_policy_integrity(home, before)
    assert checked["unchanged"] is False
    assert checked["cause"] == "instrument-altered"
    assert checked["outcome"] == "unknown"
    assert relative in [row["path"] for row in checked["changes"]]


@pytest.mark.parametrize("relative", [".", "hooks"])
def test_linked_policy_directory_is_not_followed(tmp_path: Path, relative: str) -> None:
    home, _ = _seed(tmp_path)
    before = skill_policy_snapshot(home)
    target = home if relative == "." else home / relative
    saved = tmp_path / "saved-directory"
    target.rename(saved)
    target.symlink_to(saved, target_is_directory=True)
    checked = check_skill_policy_integrity(home, before)
    assert checked["cause"] == "instrument-altered"
    changed = {row["path"]: row["after"] for row in checked["changes"]}
    assert HOOK_PATH in changed
    assert "sha256" not in changed[HOOK_PATH]
    with pytest.raises(AuthorityError, match="missing, linked, or unreadable"):
        skill_policy_snapshot(home)


def test_missing_or_unapproved_policy_cannot_be_a_baseline(tmp_path: Path) -> None:
    home, _ = _seed(tmp_path)
    config = json.loads((home / "config.yaml").read_text())
    del config["hooks"]
    (home / "config.yaml").write_text(json.dumps(config))
    with pytest.raises(AuthorityError, match="approved fixed denial"):
        skill_policy_snapshot(home)


def test_foreign_snapshot_is_rejected(tmp_path: Path) -> None:
    left, _ = _seed(tmp_path, "left")
    right, _ = _seed(tmp_path, "right")
    with pytest.raises(AuthorityError, match="exact pre-run home snapshot"):
        check_skill_policy_integrity(right, skill_policy_snapshot(left))


def test_linked_home_ancestor_is_not_followed(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    home, _ = _seed(parent)
    before = skill_policy_snapshot(home)
    saved = tmp_path / "saved-parent"
    parent.rename(saved)
    parent.symlink_to(saved, target_is_directory=True)
    result = check_skill_policy_integrity(home, before)
    assert result["cause"] == "instrument-altered"
    assert all("sha256" not in row["after"] for row in result["changes"])


def test_replaced_hook_fifo_does_not_block_integrity_check(tmp_path: Path) -> None:
    home, _ = _seed(tmp_path)
    before = skill_policy_snapshot(home)
    hook = home / HOOK_PATH
    hook.unlink()
    os.mkfifo(hook)
    result = check_skill_policy_integrity(home, before)
    assert result["cause"] == "instrument-altered"
    assert result["changes"][0]["after"]["state"] == "not-single-regular-file"


def test_config_comparison_has_no_digest_or_values_and_names_changed_keys(tmp_path: Path) -> None:
    home, _ = _seed(tmp_path)
    path = home / "config.yaml"
    document = json.loads(path.read_text())
    document["auxiliary"] = {"syntheticSecret": "PRIVATE-CONTROL-BEFORE"}
    path.write_text(json.dumps(document))
    before = skill_policy_snapshot(home)
    assert "sha256" not in before["files"][0]
    assert "sizeBytes" not in before["files"][0]
    equal = check_skill_policy_integrity(home, before)
    assert equal["configComparison"] == {
        "result": "equal",
        "differingTopLevelKeys": [],
        "method": "compared-without-digest",
        "reason": "configuration may contain secrets; retain bytes only in process",
    }
    document["auxiliary"] = {"syntheticSecret": "PRIVATE-CONTROL-AFTER"}
    document["added"] = "PRIVATE-CONTROL-ADDED"
    del document["platform_toolsets"]
    path.write_text(json.dumps(document))
    changed = check_skill_policy_integrity(home, before)
    assert changed["configComparison"]["result"] == "differs"
    assert changed["configComparison"]["differingTopLevelKeys"] == [
        "added",
        "auxiliary",
        "platform_toolsets",
    ]
    assert changed["cause"] == "instrument-altered"
    assert changed["outcome"] == "unknown"
    for report in (before, equal, changed):
        encoded = json.dumps(report)
        assert "PRIVATE-CONTROL" not in encoded
        assert all(
            "sha256" not in row for row in report.get("files", []) if row["path"] == "config.yaml"
        )


def test_persisted_snapshot_cannot_restore_private_config_baseline(tmp_path: Path) -> None:
    home, _ = _seed(tmp_path)
    before = skill_policy_snapshot(home)
    restored = json.loads(json.dumps(before))
    with pytest.raises(AuthorityError, match="in-process config baseline"):
        check_skill_policy_integrity(home, restored)
