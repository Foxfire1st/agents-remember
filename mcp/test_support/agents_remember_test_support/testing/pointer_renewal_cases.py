"""Focused examples of pointer identity binding and unchanged admission policy."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from agents_remember_test_support.testing.instruction_renewal import RENEW_COMMAND, renewal_module

_LIFECYCLE = Path("skills/l-01-agent-lifecycles")
_FIRST = "Before drafting, read [policy](../reference/policy.md#rules).\n"
_SECOND = "Before publishing, read [policy](../reference/policy.md#rules).\n"


def _refused(call: Callable[[], object]) -> str:
    try:
        call()
    except ValueError as failure:
        return str(failure)
    raise AssertionError("Ambiguous or unadmitted pointer binding was accepted.")


def assert_pointer_wording_is_current() -> None:
    root = Path(__file__).resolve().parents[4]
    held = json.loads((root / _LIFECYCLE / "pointer-actions-derived.json").read_text())
    current, _notices = renewal_module("pointers").plan(root, ())
    assert current == held, f"Stale pointer wording; run {RENEW_COMMAND}."


def _world(root: Path) -> tuple[Path, Path, dict]:
    folder = root / _LIFECYCLE
    (folder / "roles").mkdir(parents=True)
    source = folder / "roles/worker.md"
    source.write_text(_FIRST + _SECOND)
    policy = {
        "distinctActions": {
            "reference/policy.md": {"actions": ["draft", "publish"], "reason": "Different acts."}
        },
        "actionSources": {
            "reference/policy.md": {"draft": ["roles/worker.md"], "publish": ["roles/worker.md"]}
        },
        "multiLinkSentences": {},
        "exclusive": [],
    }
    held = {
        "actions": {
            action: [{"source": "roles/worker.md", "fragment": "rules", "wording": wording}]
            for action, wording in (("draft", "before drafting"), ("publish", "before publishing"))
        }
    }
    path = folder / "pointer-actions.json"
    path.write_text(json.dumps(policy))
    (folder / "pointer-actions-derived.json").write_text(json.dumps(held))
    return source, path, held


def assert_pointer_binding_uses_identity(root: Path) -> None:
    source, policy, held = _world(root)
    before = policy.read_bytes()
    planner = renewal_module("pointers")
    assert planner.plan(root, ()) == (held, [])
    source.write_text(_SECOND + _FIRST)
    assert planner.plan(root, ()) == (held, [])
    source.write_text(_SECOND.replace("publishing", "publishing the final report") + _FIRST)
    renewed, notices = planner.plan(root, ())
    assert renewed["actions"]["draft"] == held["actions"]["draft"]
    assert renewed["actions"]["publish"][0]["wording"] == "before publishing the final report"
    assert len(notices) == 1 and "publish" in notices[0]
    assert policy.read_bytes() == before


def assert_pointer_ambiguity_requires_explicit_bindings(root: Path) -> None:
    source, policy, _held = _world(root)
    before = policy.read_bytes()
    source.write_text(
        _SECOND.replace("publishing", "releasing") + _FIRST.replace("drafting", "writing")
    )
    planner = renewal_module("pointers")
    diagnostic = _refused(lambda: planner.plan(root, ()))
    for expected in ("draft", "publish", "Before releasing", "Before writing", "--bind-pointer"):
        assert expected in diagnostic
    renewed, _notices = planner.plan(
        root,
        (
            "draft=skills/l-01-agent-lifecycles/roles/worker.md:2",
            "publish=skills/l-01-agent-lifecycles/roles/worker.md:1",
        ),
    )
    assert renewed["actions"]["draft"][0]["wording"] == "before writing"
    assert renewed["actions"]["publish"][0]["wording"] == "before releasing"
    assert policy.read_bytes() == before


def assert_pointer_changes_do_not_learn_admissions(root: Path) -> None:
    source, policy, _held = _world(root)
    before = policy.read_bytes()
    planner = renewal_module("pointers")
    for text in (_FIRST, _FIRST + _SECOND + _SECOND.replace("publishing", "checking")):
        source.write_text(text)
        diagnostic = _refused(lambda: planner.plan(root, ()))
        assert "IDs:" in diagnostic and ("draft" in diagnostic or "publish" in diagnostic)
    source.write_text(_FIRST + _SECOND)
    for binding in (
        "unknown=roles/worker.md:1",
        "draft=roles/reviewer.md:1",
    ):
        assert _refused(lambda binding=binding: planner.plan(root, (binding,)))
    assert "Pointer binding unresolved" in _refused(
        lambda: planner.plan(root, ("draft=roles/worker.md:1", "publish=roles/worker.md:1"))
    )
    assert policy.read_bytes() == before
