"""Pointer withdrawals regenerate wording and refusals expose their working remedy."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from agents_remember.errors import CapsuleManifestError
from agents_remember_test_support.testing.instruction_renewal import renewal_module

MODULE = renewal_module("pointers")
LIFECYCLE = Path("skills/l-01-agent-lifecycles")
ROOT = Path(__file__).resolve().parents[2]


def _withdrawal(root: Path) -> tuple[Path, Path]:
    folder = root / LIFECYCLE
    (folder / "roles").mkdir(parents=True)
    source = folder / "roles/worker.md"
    source.write_text(
        "Before drafting, read [policy](../reference/policy.md#rules).\n"
        "Before publishing, read [policy](../reference/policy.md#rules).\n"
    )
    policy = folder / "pointer-actions.json"
    policy.write_text(
        json.dumps({"actionSources": {"reference/policy.md": {"draft": ["roles/worker.md"]}}})
    )
    (folder / "pointer-actions-derived.json").write_text(
        json.dumps(
            {
                "actions": {
                    action: [{"source": "roles/worker.md", "fragment": "rules", "wording": wording}]
                    for action, wording in (
                        ("draft", "before drafting"),
                        ("publish", "before publishing"),
                    )
                }
            }
        )
    )
    return source, policy


def test_withdrawn_pointer_id_is_removed_without_hand_editing_derived(tmp_path: Path) -> None:
    source, policy = _withdrawal(tmp_path)
    source.write_text(source.read_text().splitlines(keepends=True)[0])
    before = policy.read_bytes()
    result, _notices = MODULE.plan(tmp_path, ())
    assert set(result["actions"]) == {"draft"}
    assert result["actions"]["draft"][0]["wording"] == "before drafting"
    assert policy.read_bytes() == before


def test_withdrawn_admission_with_remaining_source_pointer_names_working_repair(
    tmp_path: Path,
) -> None:
    source, _policy = _withdrawal(tmp_path)
    with pytest.raises(ValueError, match="Pointer binding unresolved") as caught:
        MODULE.plan(tmp_path, ())
    assert "remove its source pointer" in str(caught.value)
    assert "pointer-actions.json" in str(caught.value)
    source.write_text(source.read_text().splitlines(keepends=True)[0])
    assert set(MODULE.plan(tmp_path, ())[0]["actions"]) == {"draft"}


def test_fresh_command_refuses_dangling_policy_even_after_wording_was_pruned(
    tmp_path: Path,
) -> None:
    for relative in ("scripts", "skills", "mcp/src", "mcp/test_support"):
        shutil.copytree(
            ROOT / relative, tmp_path / relative, ignore=shutil.ignore_patterns("__pycache__")
        )
    folder = tmp_path / LIFECYCLE
    identity = "every-role-before-proposing-an-input-limit-or-a-new-guard-370ccadee2"
    policy = folder / "pointer-actions.json"
    document = json.loads(policy.read_text())
    del document["actionSources"]["core/every-role.md"][identity]
    policy.write_text(json.dumps(document))
    derived = folder / "pointer-actions-derived.json"
    document = json.loads(derived.read_text())
    del document["actions"][identity]
    derived.write_text(json.dumps(document))
    before = (policy.read_bytes(), derived.read_bytes())
    for arguments in ([], ["--check"]):
        result = subprocess.run(
            [sys.executable, "scripts/renew-instructions.py", *arguments],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 1
        assert "Traceback" not in result.stderr
        assert identity in result.stderr and "distinctActions" in result.stderr
        assert "actionSources admission" in result.stderr
        assert (policy.read_bytes(), derived.read_bytes()) == before


def test_cli_prints_manifest_error_next_action(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = importlib.util.spec_from_file_location(
        "renewal_cli", ROOT / "scripts/renew-instructions.py"
    )
    assert spec is not None and spec.loader is not None
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)

    def refuse(_request: object) -> None:
        raise CapsuleManifestError(
            "manifest-invalid",
            "references.example has no read action",
            next_action="Add its source pointer.",
        )

    monkeypatch.setattr(cli, "plan", refuse)
    monkeypatch.setattr("sys.argv", ["renew-instructions.py", "--check"])
    assert cli.main() == 1
    output = capsys.readouterr().err
    assert "references.example" in output
    assert "Add its source pointer." in output
    assert "Traceback" not in output


def test_withdrawal_refuses_every_retained_policy_use_before_pruning(tmp_path: Path) -> None:
    source, policy = _withdrawal(tmp_path)
    source.write_text(source.read_text().splitlines(keepends=True)[0])
    document = json.loads(policy.read_text())
    document.update(
        distinctActions={"reference/policy.md": {"actions": ["draft", "publish"]}},
        multiLinkSentences={
            "reference/policy.md": [{"source": "roles/worker.md", "action": "publish"}]
        },
        exclusive=[["draft", "publish"], ["publish"]],
    )
    policy.write_text(json.dumps(document))
    held = tmp_path / LIFECYCLE / "pointer-actions-derived.json"
    for stored in (json.loads(held.read_text()), {"actions": {"draft": []}}):
        held.write_text(json.dumps(stored))
        before = (policy.read_bytes(), held.read_bytes())
        for _attempt in range(2):
            with pytest.raises(ValueError, match="publish") as caught:
                MODULE.plan(tmp_path, ())
            message = str(caught.value)
            for place in (
                'distinctActions["reference/policy.md"].actions[1]',
                'multiLinkSentences["reference/policy.md"][0].action',
                "exclusive[0][1]",
                "exclusive[1][0]",
            ):
                assert place in message
            assert "actionSources" in message and "authored" in message
            assert (policy.read_bytes(), held.read_bytes()) == before
    document["distinctActions"]["reference/policy.md"]["actions"].remove("publish")
    document["multiLinkSentences"] = {}
    document["exclusive"] = [["draft"]]
    policy.write_text(json.dumps(document))
    held.write_text(
        json.dumps(
            {
                "actions": {
                    "draft": [
                        {
                            "source": "roles/worker.md",
                            "fragment": "rules",
                            "wording": "before drafting",
                        }
                    ]
                }
            }
        )
    )
    assert set(MODULE.plan(tmp_path, ())[0]["actions"]) == {"draft"}
