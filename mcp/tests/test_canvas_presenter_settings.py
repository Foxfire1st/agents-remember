"""L104-B17: the presenter's launch choices come from the landed settings owner (MIK-R105 rule 3).

The cases here exercise the real two-layer agentic settings loader over temporary settings files
-- the user's own file, resolved the way the product resolves it, and an optional repository-local
file -- so a launch is refused or applied by the landed owner's own rules rather than by a parser
of the presenter's own:

1. the user file's block is applied, and provenance names that exact file and each applied choice;
2. a repository-local row that sets permission authority is refused, naming the repository file,
   and the launch refuses instead of quietly ignoring it;
3. an unknown ``orchestration`` key fails loud naming the offending file;
4. a block that is absent, disabled, or missing a harness or model refuses by name;
5. a choice this capability cannot apply refuses by name instead of being dropped;
6. the same-request retry keeps the choices the first start applied, even after the file moved.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from _user_settings_guard import user_settings_path_for_write
from agents_remember.canvas.presenter import Presenter, presenter_launch
from agents_remember.canvas.presenter_evidence import source as read_source
from agents_remember.canvas.presenter_inputs import APPLIED_CHOICES, SETTINGS_AUTHORITY
from agents_remember.kernel.agentic_settings import AgenticSettingsError, load_agentic_settings
from agents_remember.models.knowledge.review_trees import ReviewTreeSide
from canvas_host_test_support import (
    AT,
    FRAME,
    LEAF,
    REPO,
    TASK,
    FakeRunner,
    config,
    packet,
    record,
    task,
)

KEY = "orchestration.roles.review-presenter"
LAUNCH = {"harness": "hermes", "model": "the-presenter-model", "effort": "high", "mode": "auto"}


def _user_settings_path() -> Path:
    """The user's settings file, derived exactly as the product derives it."""

    return user_settings_path_for_write()


def _write(document: dict[str, Any]) -> Path:
    path = _user_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _write_block(block: dict[str, Any]) -> Path:
    return _write({"orchestration": {"roles": {"review-presenter": block}}})


def _repository_settings(settings: Any, document: dict[str, Any]) -> Path:
    root = Path(str(settings.repositories[REPO].path)) / "system"
    root.mkdir(parents=True, exist_ok=True)
    path = root / "settings.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_the_user_file_supplies_the_launch_and_provenance_names_it(tmp_path: Path) -> None:
    """The applied values and their exact file, including each choice's own source and key."""

    settings = config(tmp_path)
    path = _write_block(LAUNCH)

    launch = presenter_launch(settings)

    assert launch.state == "ok"
    assert (launch.harness, launch.model, launch.effort, launch.mode) == (
        "hermes",
        "the-presenter-model",
        "high",
        "auto",
    )
    provenance = launch.provenance
    assert provenance["key"] == KEY
    assert provenance["authority"] == SETTINGS_AUTHORITY
    assert provenance["sources"] == [path.as_posix()]
    assert provenance["source"] == path.as_posix()
    assert provenance["value"] == dict(
        zip(APPLIED_CHOICES, ("hermes", "the-presenter-model", "high", "auto"), strict=True)
    )
    for choice in APPLIED_CHOICES:
        assert provenance["fields"][choice]["source"] == path.as_posix()
        assert provenance["fields"][choice]["key"] == f"{KEY}.{choice}"

    # The MCP authority file is not a launch-choice source: the same block there is not read.
    Path(settings.config_path).write_text(
        json.dumps({"orchestration": {"roles": {"review-presenter": dict(LAUNCH)}}}),
        encoding="utf-8",
    )
    path.unlink()
    ignored = presenter_launch(settings)
    assert ignored.state == "ok"
    assert ignored.provenance["stage"] == "requested"
    assert ignored.model is None


def test_a_repository_row_may_not_set_permission_authority(tmp_path: Path) -> None:
    """The loader's authority rule refuses the repository file; the launch refuses with it."""

    settings = config(tmp_path)
    user = _write_block(LAUNCH)
    local = _repository_settings(
        settings, {"orchestration": {"roles": {"review-presenter": {"mode": "auto"}}}}
    )

    with pytest.raises(AgenticSettingsError) as authority:
        load_agentic_settings(
            settings.coordination_root, repo_root=Path(settings.repositories[REPO].path)
        )
    assert str(local) in str(authority.value)
    assert "user-layer only" in str(authority.value)

    launch = presenter_launch(settings, repository_id=REPO)
    assert launch.state == "refused"
    assert launch.provenance["stage"] == "settings-invalid"
    assert str(local) in launch.detail
    assert launch.provenance["consulted"] == [user.as_posix(), local.as_posix()]
    assert launch.provenance["sources"] == []
    assert launch.model is None


def test_a_repository_row_may_supply_a_plain_choice_and_is_named(tmp_path: Path) -> None:
    """A repo-local harness/model is not permission authority; the reader sees which file won."""

    settings = config(tmp_path)
    user = _write_block(LAUNCH)
    local = _repository_settings(
        settings, {"orchestration": {"roles": {"review-presenter": {"model": "repository-model"}}}}
    )

    launch = presenter_launch(settings, repository_id=REPO)

    assert launch.state == "ok"
    assert launch.model == "repository-model"
    assert launch.provenance["sources"] == [user.as_posix(), local.as_posix()]
    assert launch.provenance["fields"]["model"]["source"] == local.as_posix()
    assert launch.provenance["fields"]["harness"]["source"] == user.as_posix()
    assert (
        launch.provenance["fields"]["model"]["precedence"]
        > launch.provenance["fields"]["harness"]["precedence"]
    )


def test_an_unknown_orchestration_key_fails_loud_naming_the_file(tmp_path: Path) -> None:
    """The landed owner's fail-loud rule, surfaced as this launch's refusal rather than a crash."""

    settings = config(tmp_path)
    path = _write({"orchestration": {"presenter": {"model": "m"}}})

    launch = presenter_launch(settings)

    assert launch.state == "refused"
    assert launch.provenance["stage"] == "settings-invalid"
    assert "presenter" in launch.detail
    assert str(path) in launch.detail


def test_absent_choices_defer_to_host_and_invalid_disabled_refuses(tmp_path: Path) -> None:
    """No block, and a block that asks to be off, both refuse with the key and file named."""

    settings = config(tmp_path)

    absent = presenter_launch(settings)
    assert absent.state == "ok"
    assert absent.provenance["stage"] == "requested"
    assert absent.model is None

    _write({"orchestration": {"roles": {}}})
    empty = presenter_launch(settings)
    assert empty.state == "ok"
    assert empty.model is None

    path = _write_block({"disabled": True, "harness": "hermes", "model": "m"})
    disabled = presenter_launch(settings)
    assert disabled.state == "refused"
    assert disabled.provenance["stage"] == "settings-invalid"
    assert "disabled" in disabled.detail
    assert str(path) in disabled.detail
    assert disabled.model is None


def test_partial_choices_are_requests_until_host_qualification(tmp_path: Path) -> None:
    settings = config(tmp_path)
    _write_block({"model": "the-presenter-model"})
    requested = presenter_launch(settings)
    assert requested.state == "ok"
    assert requested.model == "the-presenter-model"
    assert requested.provenance["stage"] == "requested"
    _write_block({"harness": "hermes"})
    assert presenter_launch(settings).model is None


def test_unowned_step_up_refuses_instead_of_being_dropped(tmp_path: Path) -> None:
    settings = config(tmp_path)
    _write_block({**LAUNCH, "stepUp": {"model": "the-other-model"}})
    launch = presenter_launch(settings)
    assert launch.state == "refused"
    assert launch.provenance["stage"] == "unapplied"
    assert "stepUp" in launch.detail


def test_the_same_request_retry_keeps_the_applied_choices(tmp_path: Path) -> None:
    """Two reads are identical, and a retried request keeps the model its first start applied."""

    settings = config(tmp_path)
    _write_block(LAUNCH)

    first = presenter_launch(settings)
    second = presenter_launch(settings)
    assert first == second
    assert first.provenance == second.provenance

    root = task(settings)
    presenter = Presenter(
        settings,
        repository_id=REPO,
        task=TASK,
        leaf_id=LEAF,
        frame_id=FRAME,
        runner=FakeRunner(),
        clock=lambda: AT,
    )
    started = presenter.start(
        caller="dashboard",
        request_id="req-b17-retry",
        activation="explicit",
        evidence=packet(record()),
        focus="the change to src/app.py",
    )
    assert started.model is not None

    moved = _write_block({**LAUNCH, "model": "the-moved-model"})
    assert presenter_launch(settings).model == "the-moved-model"

    retried = presenter.start(
        caller="dashboard",
        request_id="req-b17-retry",
        activation="explicit",
        evidence=packet(record()),
        focus="the change to src/app.py",
    )
    assert retried.attempt_id == started.attempt_id
    assert retried.model == started.model
    assert [attempt.attempt_id for attempt in presenter.attempts()] == [started.attempt_id]
    assert moved.is_file()
    assert root.is_dir()


def test_source_grant_reads_the_packet_tree_and_never_the_working_file(tmp_path: Path) -> None:
    repository = tmp_path / "exact-source"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    path = repository / "source.txt"
    path.write_text("the admitted historical bytes\n")
    subprocess.run(["git", "-C", str(repository), "add", "source.txt"], check=True)
    tree = subprocess.check_output(["git", "-C", str(repository), "write-tree"], text=True).strip()
    comparison = record().model_copy(
        update={"code_candidate": ReviewTreeSide(repository=str(repository), tree=tree)}
    )
    admitted = packet(comparison, claims=())
    path.write_text("CURRENT WORKTREE MUST NOT BE READ\n")
    receipt = read_source(admitted, "code-candidate", "source.txt", start=1, end=1)
    assert receipt["state"] == "present", receipt
    assert receipt["text"] == "the admitted historical bytes"
    assert receipt["tree"] == tree
    for side, target in (
        ("HEAD", "source.txt"),
        ("code-candidate", "../source.txt"),
        ("code-candidate", "/etc/hosts"),
        ("code-candidate", ":(glob)*"),
    ):
        refused = read_source(admitted, side, target, start=1, end=10)
        assert refused["state"] == "refused"
        assert not refused["text"]
