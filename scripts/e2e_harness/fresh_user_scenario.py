"""The fresh-user end-to-end acceptance: one clean-room repository's first hour, per step.

The packet's flow, in order, against the fixtures ``fresh_user_fixture`` creates:

    install runtime -> memory_init (with the spear-branch choice) -> thin bootstrap (c-03)
    -> exclusion review -> baseline adoption -> first worktree task (light leaf)
    -> citation_fix + memory_quality_check + closeout validation

Every step runs the product's own entry point, records the exact call it made and the real
result it got, and never summarises: a sentence like "the bootstrap succeeded" is not evidence,
the transcript is. A step that cannot run in this environment is recorded as ``blocked`` with
the exact reason and with what evidence would have been required.

WHAT THIS SCENARIO CERTIFIES
----------------------------
It certifies the *chain* over disposable fixtures created by the harness: two repositories from
nothing, the register persisted, the baseline adopted, the first worktree task started by the
product's own activation, and the four chain invariants asserted on both fixtures.

It also runs the **free agent**, the seat the developer ruled has no task document at all.
``free_agent_acceptance`` opens it through the product's live route -- ``POST /api/terminal/{session}``
with ``role='bootstrap'`` and no ``taskDocumentRef`` -- and reads the capsule back **out of the
launch token the session was actually started with**, parsed by the product's own
``parse_runner_config``. The capsule it receives is the ``(bootstrap, orientation)`` compilation:
the route publishes its semantic digest, and the run asserts that the digest the route published is
the digest on the token, and that the instruction text equals the compiler's own result for the same
admitted seat through the same application entry point. The step reports ``completed`` or ``failed``,
never ``blocked``: a free agent that opens without its instructions is a failure, not a deferral.

The standing rule this shape exists to satisfy: a delivery claim is closed by a production path from
producer to consumer, with the artifact read from the consumer's side -- never a seam plus a
caller-supplied value.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

MCP_SRC = Path(__file__).resolve().parents[2] / "mcp" / "src"
# A STRING, never the Path: ``sys.path`` entries that are not ``str`` are silently ignored by
# the import machinery, so inserting the Path object executes the interpreter's editable install
# -- the primary checkout -- while every line of this module still looks right. The transcript
# records ``packageSource`` per step so that failure cannot recur unnoticed.
if MCP_SRC.as_posix() not in sys.path:
    sys.path.insert(0, MCP_SRC.as_posix())

import agents_remember  # noqa: E402
from agents_remember.application.memory_tools import (  # noqa: E402
    _baseline_request,
    citation_fix_tool,
)
from agents_remember.application.role_capsules.launch import (  # noqa: E402
    LaunchCapsuleRequest,
    compile_launch_capsule,
)
from agents_remember.application.worktree_tool_requests import (  # noqa: E402
    StartExecution,
    TaskBases,
    TaskIdentity,
)
from agents_remember.application.worktree_tools import worktree_start_tool  # noqa: E402
from agents_remember.cli.dashboard import serving_collaborators  # noqa: E402
from agents_remember.install.runtime import (  # noqa: E402
    RuntimeInstallRequest,
    install_runtime_from_config,
)
from agents_remember.kernel.agentic_settings import agentic_settings_path  # noqa: E402
from agents_remember.kernel.authority import require_repo  # noqa: E402
from agents_remember.kernel.memory_init import initialize_memory  # noqa: E402
from agents_remember.kernel.primitives import checkout_coordination  # noqa: E402
from agents_remember.kernel.primitives.runtime_config import load_config  # noqa: E402
from agents_remember.memory import baseline  # noqa: E402
from agents_remember.memory_quality.check import (  # noqa: E402
    BEFORE_METADATA_REFRESH_CHECKS,
    DriftCheckContext,
    run_memory_quality_check,
)
from agents_remember.models.task_document_ref import TaskDocumentRef  # noqa: E402
from agents_remember.observer import reset_ambient  # noqa: E402
from agents_remember.serving.app import create_app  # noqa: E402
from agents_remember.serving.harness_control_runner import parse_runner_config  # noqa: E402
from agents_remember.serving.projector import ProjectionCadence  # noqa: E402
from agents_remember.serving.terminal import TerminalSessionBinding  # noqa: E402
from agents_remember.serving.terminal_catalog import TerminalCatalog  # noqa: E402
from agents_remember.tasks import TaskDocument, write_task_doc  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from fresh_user_fixture import (  # noqa: E402  (the harness's flat module convention)
    FreshUserFixture,
    create_fresh_user_fixture,
    git,
    write_exclusion_register,
    write_thin_bootstrap,
)
from reporting import CheckpointDefinition, CheckpointRecorder  # noqa: E402

STEP_INSTALL = "install-runtime"
STEP_MEMORY_INIT = "memory-init"
STEP_BOOTSTRAP = "thin-bootstrap-c03"
STEP_EXCLUSION = "exclusion-review"
STEP_BASELINE = "baseline-adoption"
STEP_WORKTREE = "first-worktree-task"
STEP_CITATION = "citation-fix"
STEP_QUALITY = "memory-quality-check"
STEP_CLOSEOUT = "closeout-validation"
STEP_FREE_AGENT = "free-agent-bootstrap-seat"

INVARIANTS = (
    CheckpointDefinition(
        checkpoint_id="invariant-1-no-bootstrap-content-in-first-memory-commit",
        requirement="CAPS-R14@v1 B5",
        expected="the first memory commit contains no bootstrap/ scaffolding",
        owner="260915-CAPS-L14",
    ),
    CheckpointDefinition(
        checkpoint_id="invariant-2-no-confidence-tags-in-durable-onboarding",
        requirement="CAPS-R14@v1 B5",
        expected="no [HIGH]/[MEDIUM]/[LOW] tag in a durable onboarding document",
        owner="260915-CAPS-L14",
    ),
    CheckpointDefinition(
        checkpoint_id="invariant-3-first-ledger-row",
        requirement="CAPS-R14@v1 B5",
        expected="the memory ledger carries a row naming the adopted code commit",
        owner="260915-CAPS-L14",
    ),
    CheckpointDefinition(
        checkpoint_id="invariant-4-first-worktree-task",
        requirement="CAPS-R14@v1 B5",
        expected="a light leaf's code worktree exists on its own work branch",
        owner="260915-CAPS-L14",
    ),
)
STEP_DEFINITIONS = (
    CheckpointDefinition(
        checkpoint_id="step-install-runtime",
        requirement="CAPS-R14@v1 B4",
        expected="runtime install completes over the disposable coordination root",
        owner="260915-CAPS-L14",
    ),
    CheckpointDefinition(
        checkpoint_id="step-memory-init-spear-branch",
        requirement="CAPS-R14@v1 B4",
        expected="memory_init creates the external memory repo on the chosen spear branch",
        owner="260915-CAPS-L14",
    ),
    CheckpointDefinition(
        checkpoint_id="step-exclusion-review-persisted",
        requirement="CAPS-R14@v1 B1",
        expected="the register is persisted to settings.json before memory closeout",
        owner="260915-CAPS-L14",
    ),
    CheckpointDefinition(
        checkpoint_id="step-baseline-adopted",
        requirement="CAPS-R14@v1 B4",
        expected="the first attributed memory baseline is adopted",
        owner="260915-CAPS-L14",
    ),
    CheckpointDefinition(
        checkpoint_id="step-first-worktree-task",
        requirement="CAPS-R14@v1 B4",
        expected="a light leaf's worktree is created on its own branch",
        owner="260915-CAPS-L14",
    ),
    CheckpointDefinition(
        checkpoint_id="step-citation-checks-run",
        requirement="CAPS-R14@v1 B3",
        expected="the citation checks run over the fixture and report their register",
        owner="260915-CAPS-L14",
    ),
)
CONFIDENCE_TAGS = ("[HIGH]", "[MEDIUM]", "[LOW]")


@dataclass
class StepRecord:
    """One step's real command, real status, and verbatim result."""

    step: str
    fixture: str
    call: str
    status: str
    result: dict[str, Any] = field(default_factory=dict)
    blocked_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "step": self.step,
            "fixture": self.fixture,
            "call": self.call,
            "status": self.status,
            "packageSource": package_source(),
            "result": self.result,
        }
        if self.blocked_reason:
            record["blockedReason"] = self.blocked_reason
        return record


def package_source() -> str:
    """The ``agents_remember`` package this transcript was actually produced by.

    Recorded on every step and in the environment block, because the interpreter's editable
    install points at the PRIMARY checkout while the candidate lives in a worktree: a run whose
    ``sys.path`` lost the worktree would execute the wrong code and still look green.
    """
    return Path(agents_remember.__file__).resolve().parents[1].as_posix()


def _assert_candidate_source() -> str:
    """Refuse to produce a transcript from anything but this candidate's own package."""
    source = package_source()
    expected = MCP_SRC.as_posix()
    if source != expected:
        raise AssertionError(
            f"agents_remember resolved to {source}, not this candidate's {expected}; the run "
            "would execute another checkout's code"
        )
    return source


def _config(fixture: FreshUserFixture):
    """The fixture's own authority file, through the product's loader.

    The declaration is mandatory and it is the product's own: an UNDECLARED process whose
    ``agents_remember`` package resolves to a linked worktree is confined by
    ``checkout_coordination`` to one disposable coordination root inside that worktree, and the
    loader then *ignores the path it was handed*. That is exactly the failure this scenario must
    not have -- it would report a fixture run while driving the worktree's own root -- so
    ``declare_execution_mode("test")`` runs first, which is the mode the product documents for an
    explicit test process and the one ``mcp/conftest.py`` uses for the suite.
    """
    _assert_candidate_source()
    checkout_coordination.declare_execution_mode("test")
    config = load_config(fixture.authority_path)
    if config.config_path.resolve() != fixture.authority_path.resolve():
        raise AssertionError(
            f"the loader did not honour the fixture authority {fixture.authority_path}; it used "
            f"{config.config_path}. Every step below would have driven another coordination root"
        )
    return config


def _bounded(value: object, limit: int = 4000) -> dict[str, Any]:
    """A result small enough to keep the transcript readable without changing its meaning."""
    text = json.dumps(value, sort_keys=True, default=str)
    if len(text) <= limit:
        return value if isinstance(value, dict) else {"value": value}
    return {"truncated": True, "characters": len(text), "head": text[:limit]}


def _record_check(
    recorder: CheckpointRecorder,
    definition: CheckpointDefinition,
    *,
    fixture: str,
    actual: object,
    passed: bool,
) -> None:
    """Record one checkpoint without aborting the run.

    ``CheckpointRecorder.check`` raises on the first failure, which would end the transcript
    before the later steps ran. The packet is explicit that a failing scenario is a real result
    to report with the failing step and its output, never a scenario removed to make the run
    green -- so every step runs, every checkpoint is recorded, and the exit status carries the
    verdict.
    """
    recorder.checkpoints.append(
        {
            "id": definition.checkpoint_id,
            "fixture": fixture,
            "requirement": definition.requirement,
            "expected": definition.expected,
            "actual": actual,
            "owner": definition.owner,
            "status": "passed" if passed else "failed",
        }
    )


def _run(
    records: list[StepRecord],
    fixture: FreshUserFixture,
    step: str,
    call: str,
    action,
) -> dict[str, Any]:
    """Run one product entry point, recording either its result or its failure verbatim."""
    try:
        result = action()
    except Exception as error:  # a step failure is a RESULT to report, not a crash
        records.append(
            StepRecord(
                step=step,
                fixture=fixture.name,
                call=call,
                status="failed",
                result={"error": f"{type(error).__name__}: {error}"},
            )
        )
        return {"ok": False, "error": f"{type(error).__name__}: {error}"}
    payload = result if isinstance(result, dict) else {"value": result}
    records.append(
        StepRecord(
            step=step,
            fixture=fixture.name,
            call=call,
            status="completed" if payload.get("ok", True) else "refused",
            result=_bounded(payload),
        )
    )
    return payload


def _install_runtime(fixture: FreshUserFixture) -> dict[str, Any]:
    return install_runtime_from_config(
        _config(fixture),
        RuntimeInstallRequest(install_provider_deps=False, include_benchmarks=False),
    )


def _memory_init(fixture: FreshUserFixture) -> dict[str, Any]:
    return initialize_memory(
        _config(fixture), repo_id=fixture.repo_id, initial_branch=fixture.spear_branch
    )


def _baseline_adopt(fixture: FreshUserFixture) -> dict[str, Any]:
    config = _config(fixture)
    code, payload = baseline.baseline_adopt(
        _baseline_request(config, require_repo(config, fixture.repo_id)),
        accept_drift=True,
        source_branch=fixture.spear_branch,
    )
    return {"ok": code == 0, "returncode": code, **payload}


def _worktree_topology(fixture: FreshUserFixture) -> dict[str, str]:
    """The master/leaf documents a light leaf needs, written where worktree_start looks."""
    task_root = fixture.coordination_root / "tasks" / fixture.repo_id
    common = {
        "repo": fixture.repo_id,
        "type": "feature",
        "createdAt": "2026-09-17T00:00",
    }

    def document(**values: object) -> TaskDocument:
        return TaskDocument(**{**common, **values})  # type: ignore[arg-type]

    write_task_doc(
        task_root / "fresh-user-sprint",
        document(
            id="FRESH-USER-SPRINT",
            slug="fresh-user-sprint",
            title="Fresh user acceptance",
            kind="master",
            status="inProgress",
            orchestrates=["fresh-user-master"],
            integrationBranch=fixture.sprint_branch,
            executionGraph={
                "nodes": [{"repository": fixture.repo_id, "path": "fresh-user-master/task.json"}],
                "edges": [],
            },
        ),
    )
    write_task_doc(
        task_root / "fresh-user-master",
        document(
            id="FRESH-USER-MASTER",
            slug="fresh-user-master",
            title="Fresh user master",
            kind="master",
            status="inProgress",
            executionNature="atomic",
            subTasks=[
                {
                    # The row is matched by CHILD DOCUMENT ID (``require_leaf_parent_row`` selects
                    # rows whose ``number == leaf_id``), and its ``file`` cell must name the rendered
                    # Markdown child source -- a row naming the JSON refuses the start entirely (D21).
                    "number": "FRESH-USER-LEAF-1",
                    "name": "Fresh user leaf",
                    "file": "leaf-1.md",
                    "status": "inProgress",
                }
            ],
        ),
    )
    write_task_doc(
        task_root / "fresh-user-master",
        document(
            id="FRESH-USER-LEAF-1",
            slug="leaf-1",
            title="Fresh user leaf",
            kind="subTask",
            status="inProgress",
            master="task.md",
        ),
    )
    return {
        "sprint": TaskDocumentRef(
            repository=fixture.repo_id, path="fresh-user-sprint/task.json"
        ).path,
        "master": TaskDocumentRef(
            repository=fixture.repo_id, path="fresh-user-master/task.json"
        ).path,
        "leaf": "FRESH-USER-LEAF-1",
        "worktreeName": "leaf-1",
    }


def _start_leaf_worktree(fixture: FreshUserFixture) -> dict[str, Any]:
    """The product's real first-hour start: one ATOMIC LEAF start, with no ``source_branch``.

    `start_contract.py::_parent_series_contract` mints the master's series contract and its
    integration branch on the first atomic leaf start, and `_start_source_branch` then derives the
    leaf's expected source from that minted branch. A caller-supplied ``source_branch`` therefore
    contradicts the branch the activation is about to create -- so the scenario supplies none, and
    every value it later reports is read back from the product's own artifacts.
    """
    _worktree_topology(fixture)
    return worktree_start_tool(
        _config(fixture),
        TaskIdentity(
            repo_id=fixture.repo_id,
            task_name="fresh-user-master",
            worktree_name="leaf-1",
            # The leaf is addressed by its DOC ID and its master folder, which is the
            # ``<repo>/<master-folder>/<doc-id>`` form the resolver names as expected.
            leaf_id="FRESH-USER-LEAF-1",
            parent_task="fresh-user-master",
            workflow_kind="light-task",
        ),
        bases=TaskBases(
            work_branch="ar/leaf-1",
            memory_mode="external",
            stale_base_choice="proceed-stale",
        ),
        execution=StartExecution(skip_provider_setup=True),
    )


def _branches(repo, pattern: str) -> tuple[str, ...]:
    listing = subprocess.run(
        ["git", "branch", "--list", "--format=%(refname:short)", pattern],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    ).stdout
    return tuple(line.strip() for line in listing.splitlines() if line.strip())


def _series_branch(master: dict[str, Any], fixture: FreshUserFixture) -> str:
    """The branch the master's own start produced, read from its result or from the repository."""
    for key in ("workBranch", "work_branch", "seriesBranch", "sourceBranch"):
        value = master.get(key)
        if isinstance(value, str) and value:
            return value
    for candidate in (
        fixture.master_branch,
        *sorted(_branches(fixture.code_repo, "ar/*")),
        fixture.sprint_branch,
    ):
        exists = subprocess.run(
            ["git", "rev-parse", "--verify", candidate],
            cwd=fixture.code_repo,
            text=True,
            capture_output=True,
            check=False,
        )
        if exists.returncode == 0:
            return candidate
    return fixture.master_branch


def _declare_memory_series_branch(fixture: FreshUserFixture) -> StepRecord:
    """Put the sprint's integration branch in the memory repo, where the series must find it.

    Declared as FIXTURE SETUP, not as product behaviour: in a real first hour that branch is
    created by AR's own master-series machinery, which a clean-room fixture has no reason to
    drive. What is *not* fixture-supplied is everything the next step asserts -- the worktree,
    the branch cut from it, and the contract the product writes for it.
    """
    branch = fixture.sprint_branch
    head = _try_git(fixture.memory_root, "rev-parse", "HEAD")
    created: list[str] = []
    if head:
        for args in (
            ("branch", "--quiet", branch),
            ("update-ref", f"refs/remotes/origin/{branch}", head),
        ):
            try:
                git(fixture.memory_root, *args)
                created.append(" ".join(args))
            except AssertionError as error:
                created.append(f"failed: {error}")
    return StepRecord(
        step="fixture-memory-series-branch",
        fixture=fixture.name,
        call=f"git branch {branch}  # fixture setup, in the disposable memory repo",
        status="completed" if head else "failed",
        result={"branch": branch, "head": head, "created": created},
    )


def _citation_checks(fixture: FreshUserFixture) -> dict[str, Any]:
    """The citation checks over the fixture, through the function the closeout gate calls."""
    return run_memory_quality_check(
        fixture.memory_root / "onboarding",
        checks=list(BEFORE_METADATA_REFRESH_CHECKS),
        drift_context=DriftCheckContext(
            fixture.code_repo,
            context=None,
            detail_limit=10,
            write_report=False,
        ),
    )


def _try_git(root: Path, *args: str) -> str:
    """A Git fact that may legitimately be absent; absence is a value, not an exception."""
    result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def _first_memory_commit(fixture: FreshUserFixture) -> str:
    commits = _try_git(fixture.memory_root, "rev-list", "--max-parents=0", "HEAD")
    return commits.splitlines()[-1] if commits else ""


def _memory_commit_paths(fixture: FreshUserFixture, commit: str) -> tuple[str, ...]:
    if not commit:
        return ()
    listing = _try_git(fixture.memory_root, "ls-tree", "-r", "--name-only", commit)
    return tuple(line for line in listing.splitlines() if line)


def _bootstrap_paths(paths: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(one for one in paths if one.startswith("bootstrap/") or "/bootstrap/" in one)


def _confidence_tag_offenders(fixture: FreshUserFixture) -> tuple[str, ...]:
    offenders: list[str] = []
    onboarding = fixture.memory_root / "onboarding"
    for document in sorted(onboarding.rglob("*.md")):
        text = document.read_text(encoding="utf-8", errors="replace")
        if any(tag in text for tag in CONFIDENCE_TAGS):
            offenders.append(document.relative_to(onboarding).as_posix())
    return tuple(offenders)


def _ledger_facts(adopted: dict[str, Any]) -> dict[str, Any]:
    """What the adoption itself reported about the ledger, plus the file it wrote.

    The expected side is the product's own ledger status (``ledger.lastVerifiedCodeCommit``);
    the observed side is the ledger file on disk. Two artifacts, not one value compared to
    itself.
    """
    reported = adopted.get("ledger") if isinstance(adopted.get("ledger"), dict) else {}
    path = adopted.get("ledger_path") or adopted.get("ledgerPath") or ""
    file = Path(str(path)) if path else Path()
    facts: dict[str, Any] = {
        "reportedPath": path,
        "reported": reported,
        "exists": bool(path) and file.exists(),
        "keys": [],
        "rowCount": None,
    }
    if facts["exists"]:
        try:
            payload = json.loads(file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            facts["malformed"] = str(error)
            return facts
        if isinstance(payload, dict):
            facts["keys"] = sorted(payload)
            rows = payload.get("rows")
            facts["rowCount"] = len(rows) if isinstance(rows, list) else None
    return facts


def run_fixture_scenario(
    fixture: FreshUserFixture,
    records: list[StepRecord],
    recorder: CheckpointRecorder,
) -> dict[str, Any]:
    """The ordered flow for one fixture. Returns the chain facts the invariants are read from."""
    install = _run(
        records,
        fixture,
        STEP_INSTALL,
        "install_runtime_from_config(config, RuntimeInstallRequest(install_provider_deps=False))",
        lambda: _install_runtime(fixture),
    )
    _record_check(
        recorder,
        STEP_DEFINITIONS[0],
        fixture=fixture.name,
        actual={"ok": install.get("ok", install.get("state")), "error": install.get("error")},
        passed=bool(install.get("ok", True)) and "error" not in install,
    )

    memory_init = _run(
        records,
        fixture,
        STEP_MEMORY_INIT,
        f"initialize_memory(config, repo_id={fixture.repo_id!r}, "
        f"initial_branch={fixture.spear_branch!r})",
        lambda: _memory_init(fixture),
    )
    _record_check(
        recorder,
        STEP_DEFINITIONS[1],
        fixture=fixture.name,
        actual={
            "ok": memory_init.get("ok"),
            "state": memory_init.get("state"),
            "initialBranch": memory_init.get("initialBranch"),
            "initialBranchSource": memory_init.get("initialBranchSource"),
            "error": memory_init.get("error"),
        },
        passed=bool(memory_init.get("ok"))
        and memory_init.get("initialBranch") == fixture.spear_branch,
    )

    overview = write_thin_bootstrap(fixture)
    records.append(
        StepRecord(
            step=STEP_BOOTSTRAP,
            fixture=fixture.name,
            call="write_thin_bootstrap(fixture)  # c-03 has no runtime entry point",
            status="emulated-outcome",
            result={
                "artifact": overview.as_posix(),
                "bytes": overview.stat().st_size,
                "note": (
                    "c-03-repo-bootstrap is an LLM-driven procedure: it has no MCP tool, no CLI "
                    "subcommand and no operation in the capsule vocabulary. A script can only "
                    "produce its documented minimum artifact, which is this file; the procedure "
                    "itself is not an executable surface at this base."
                ),
            },
        )
    )

    settings = write_exclusion_register(
        fixture,
        excludes=("vendor/**", ".gitignore"),
        citation_index={"maxSourceBytes": 8 * 1024 * 1024},
    )
    records.append(
        StepRecord(
            step=STEP_EXCLUSION,
            fixture=fixture.name,
            call="write_exclusion_register(fixture, excludes=('vendor/**', '.gitignore'), "
            "citation_index={'maxSourceBytes': 8388608})",
            status="persisted",
            result={
                "settings": settings.as_posix(),
                "bytes": settings.read_text(encoding="utf-8"),
                "note": (
                    "the judgement-first review (developer ruling 2026-08-21) produces this "
                    "file; this step is its persistence, not a substitute for the review"
                ),
            },
        )
    )
    persisted = json.loads(settings.read_text(encoding="utf-8"))["onboarding"]
    _record_check(
        recorder,
        STEP_DEFINITIONS[2],
        fixture=fixture.name,
        actual={
            "pathRules": persisted["pathRules"],
            "citationIndex": persisted.get("citationIndex"),
        },
        passed="vendor/**" in persisted["pathRules"]["exclude"]["paths"],
    )

    adopted = _run(
        records,
        fixture,
        STEP_BASELINE,
        f"baseline_adopt(request, accept_drift=True, source_branch={fixture.spear_branch!r})",
        lambda: _baseline_adopt(fixture),
    )
    _record_check(
        recorder,
        STEP_DEFINITIONS[3],
        fixture=fixture.name,
        actual={
            "ok": adopted.get("ok"),
            "state": adopted.get("state"),
            "returncode": adopted.get("returncode"),
            "ledger": adopted.get("ledger"),
        },
        passed=bool(adopted.get("ok")) and adopted.get("state") in {"adopted", "already-adopted"},
    )

    series = _declare_memory_series_branch(fixture)
    records.append(series)
    started = _run(
        records,
        fixture,
        STEP_WORKTREE,
        "worktree_start_tool(config, TaskIdentity(task_name='fresh-user-master', "
        "leaf_id='FRESH-USER-LEAF-1', worktree_name='leaf-1', parent_task='fresh-user-master', "
        "workflow_kind='light-task'), bases=TaskBases(work_branch='ar/leaf-1', "
        "memory_mode='external', stale_base_choice='proceed-stale'), "
        "execution=StartExecution(skip_provider_setup=True))",
        lambda: _start_leaf_worktree(fixture),
    )

    work_branch = "ar/leaf-1"
    worktree_exists = (
        subprocess.run(
            ["git", "rev-parse", "--verify", work_branch],
            cwd=fixture.code_repo,
            capture_output=True,
            text=True,
            check=False,
        ).returncode
        == 0
    )
    _record_check(
        recorder,
        STEP_DEFINITIONS[4],
        fixture=fixture.name,
        actual={
            "state": started.get("state"),
            "branchExists": worktree_exists,
            "nextStep": started.get("nextStep") or started.get("error"),
            "sprintIntegrationBranch": fixture.sprint_branch,
            "contractPath": started.get("contractPath") or started.get("contract_path"),
            "worktreePath": started.get("worktreePath") or started.get("worktree_path"),
        },
        passed=worktree_exists,
    )

    contract = _contract_path(fixture, started)
    if contract is None:
        records.append(citation_fix_blocked(fixture, started))
        citation: dict[str, Any] = {"reachable": False}
    else:
        citation = _run(
            records,
            fixture,
            STEP_CITATION,
            f"citation_fix_payload(config, {fixture.repo_id!r}, contract_path=<leaf contract>, "
            "dry_run=True)",
            lambda: _citation_fix(fixture, contract),
        )
    quality = _run(
        records,
        fixture,
        STEP_QUALITY,
        "run_memory_quality_check(onboarding, checks=BEFORE_METADATA_REFRESH_CHECKS, "
        "drift_context=DriftCheckContext(code_repo))",
        lambda: _citation_checks(fixture),
    )
    checks = quality.get("checks", {}) if isinstance(quality, dict) else {}
    range_result = (
        checks.get("style.citations.range_resolution", {}) if isinstance(checks, dict) else {}
    )
    _record_check(
        recorder,
        STEP_DEFINITIONS[5],
        fixture=fixture.name,
        actual={
            "status": range_result.get("status"),
            "resolvedCitations": range_result.get("resolvedCitations"),
            "boundsStatus": (range_result.get("sourceIndex") or {}).get("bounds", {}).get("status"),
            "skippedCount": (range_result.get("sourceIndex") or {})
            .get("bounds", {})
            .get("skippedCount"),
        },
        passed=range_result.get("status") == "checked",
    )
    records.append(
        StepRecord(
            step=STEP_CLOSEOUT,
            fixture=fixture.name,
            call="run_memory_quality_check(onboarding, checks=BEFORE_METADATA_REFRESH_CHECKS, ...)",
            status="completed",
            result={
                "note": (
                    "the closeout gate's citation checks are exercised through the function the "
                    "gate calls (application/worktree_services.py MemoryQualityAdapter.run_check "
                    "-> memory_quality/check.py run_memory_quality_check). The full closeout "
                    "TRANSACTION is not exercised here: it requires a selected four-terminal "
                    "code prefix and current curator coherence, which a fresh user's first hour "
                    "does not have."
                ),
                "rangeResolution": _bounded(range_result),
                "citationFixReachability": citation,
            },
        )
    )
    first_commit = _first_memory_commit(fixture)
    chain = {
        "firstMemoryCommit": first_commit,
        "firstMemoryCommitPaths": _memory_commit_paths(fixture, first_commit),
        "confidenceTagOffenders": _confidence_tag_offenders(fixture),
        "ledger": _ledger_facts(adopted),
        "workBranch": work_branch,
        "workBranchExists": worktree_exists,
        "worktreeState": started.get("state"),
        "worktreeError": started.get("nextStep") or started.get("error"),
    }
    return chain


def _contract_path(fixture: FreshUserFixture, started: dict[str, Any]) -> str | None:
    """The leaf contract the start wrote, or ``None`` when the start did not get that far."""
    for key in ("contractPath", "contract_path"):
        value = started.get(key)
        if isinstance(value, str) and value:
            return value
    roots = (
        fixture.coordination_root / "worktrees" / fixture.repo_id,
        fixture.coordination_root / "worktrees",
    )
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*series-contract.md")):
            return path.as_posix()
    return None


def _citation_fix(fixture: FreshUserFixture, contract_path: str) -> dict[str, Any]:
    """The real per-run citation fix call, preview only: it writes nothing.

    Tree-wide with ``dry_run=True`` is the reachable form here (a per-document call also needs a
    published snapshot id), it publishes the generation the document-scoped form then needs, and
    it returns the work order. The result is read back and recorded; nothing is rewritten.
    """
    return citation_fix_tool(
        _config(fixture),
        repo_id=fixture.repo_id,
        contract_path=contract_path,
        dry_run=True,
    )


def citation_fix_blocked(fixture: FreshUserFixture, started: dict[str, Any]) -> StepRecord:
    """The citation step when no enclosure contract exists: blocked BY NAME, never completed."""
    return StepRecord(
        step=STEP_CITATION,
        fixture=fixture.name,
        call=("citation_fix_payload(config, repo_id, contract_path=<leaf contract>, dry_run=True)"),
        status="blocked",
        result={
            "reachable": False,
            "worktreeState": started.get("state"),
            "wouldHaveRequired": (
                "a leaf enclosure contract, which only a successful worktree start writes: "
                "citation_fix writes into a leaf's memory worktree and refuses anything else"
            ),
            "owner": "260915-CAPS-L14 (the ordered starts) then this step",
        },
        blocked_reason=(
            "no leaf enclosure contract exists at this point in the run, so the citation fix "
            "surface is unreachable and the step is recorded blocked rather than completed"
        ),
    )


class _RecordingTerminalHost:
    """The tmux boundary, doubled for the OPEN path only.

    Declared as a boundary double, not as a value: it records the session specification the product
    produced (cwd, argv, env) and never supplies anything the capsule is compared against. The
    capsule itself is compiled by the product and read back out of the launched token.
    """

    def __init__(self) -> None:
        self.ensured: list[Any] = []
        self.known: set[str] = set()

    def has_session(self, tmux_name: str) -> bool:
        return tmux_name in self.known

    def shutdown(self) -> None:
        return None

    def ensure(self, sid: str, spec: Any) -> Any:
        self.ensured.append(spec)
        tmux_name = spec.tmux_name_for(sid)
        self.known.add(tmux_name)
        return TerminalSessionBinding(
            sid=sid,
            tmux_name=tmux_name,
            cwd=spec.cwd,
            command=spec.command,
            lifecycle_id=spec.lifecycle_id,
            suspend_unsafe=spec.suspend_unsafe,
        )

    def last_spec(self) -> Any:
        if not self.ensured:
            raise AssertionError("the launch route ensured no session")
        return self.ensured[-1]


def _write_role_settings(fixture: FreshUserFixture, harness: str) -> Path:
    """Point the bootstrap role at one harness: the settings-owned launch selection."""
    path = agentic_settings_path(fixture.coordination_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "orchestration": {
                    "roles": {
                        "bootstrap": {
                            "harness": harness,
                            "model": "gpt-5.6-sol",
                            "effort": "medium",
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return path


def free_agent_acceptance(fixture: FreshUserFixture) -> StepRecord:
    """L14R-2: a FREE agent's compiled capsule, read back from its own first prompt.

    The seat is opened the way a user opens it -- the product's registered route,
    ``POST /api/terminal/{session}`` with ``role='bootstrap'`` and **no task document** -- and the
    capsule is read back from the artifact that launch produced: the encoded launch token the child
    process is started with, parsed by the product's own ``parse_runner_config``. The expected side
    is the same application entry point the route itself calls, so neither side is hand-supplied.
    """
    _write_role_settings(fixture, "codex")
    reset_ambient()
    config = _config(fixture)
    host = _RecordingTerminalHost()
    catalog_path = fixture.coordination_root / "logs" / "dashboard" / "terminal-sessions.json"
    collaborators = replace(
        serving_collaborators(config),
        terminal_host=host,
        terminal_catalog=TerminalCatalog(catalog_path),
    )
    app = create_app(config, cadence=ProjectionCadence(interval=100), collaborators=collaborators)
    session = "l14-free-bootstrap"
    with TestClient(app) as client:
        response = client.post(
            f"/api/terminal/{session}",
            json={
                "kind": "harness",
                "harness": "codex",
                "role": "bootstrap",
                "label": "L14 free agent",
            },
        )
    body = (
        response.json()
        if response.headers.get("content-type", "").startswith("application/json")
        else {}
    )
    if response.status_code != 200:
        return StepRecord(
            step=STEP_FREE_AGENT,
            fixture=fixture.name,
            call=f"POST /api/terminal/{session} (role=bootstrap, no taskDocumentRef)",
            status="failed",
            result={"statusCode": response.status_code, "body": response.text[:600]},
        )

    spec = host.last_spec()
    token = spec.command[-1]
    parsed = parse_runner_config(token)
    delivery = parsed.capsule_delivery
    launched = compile_launch_capsule(
        config,
        LaunchCapsuleRequest(
            role="bootstrap", workspace_root=config.workspace_root, harness="codex"
        ),
    )
    received = "" if delivery is None else delivery.trusted_instructions
    encoded = received.encode("utf-8")
    expected = (
        "" if launched.codex_delivery is None else launched.codex_delivery.trusted_instructions
    )
    instruction_mode = body.get("instructionMode") or {}
    matches_expected = received == expected and bool(expected)
    digest_matches = (
        delivery is not None
        and bool(instruction_mode)
        and instruction_mode.get("semanticDigest") == delivery.semantic_digest
    )
    passed = (
        instruction_mode.get("mode") == "capsule"
        and body.get("taskDocumentRef") is None
        and matches_expected
        and digest_matches
    )
    return StepRecord(
        step=STEP_FREE_AGENT,
        fixture=fixture.name,
        call=(
            f"POST /api/terminal/{session} {{'kind':'harness','harness':'codex',"
            "'role':'bootstrap','label':'L14 free agent'}}  # no taskDocumentRef"
        ),
        status="completed" if passed else "failed",
        result={
            "routeStatus": response.status_code,
            "taskDocumentRef": body.get("taskDocumentRef"),
            "instructionMode": instruction_mode,
            "sessionReceived": {
                "characters": len(received),
                "bytes": len(encoded),
                "sha256": hashlib.sha256(encoded).hexdigest(),
                "semanticDigest": None if delivery is None else delivery.semantic_digest,
                "firstLine": received.splitlines()[0] if received else "",
            },
            "compiledByProduct": {
                "mode": launched.mode.value,
                "semanticDigest": None
                if launched.codex_delivery is None
                else launched.codex_delivery.semantic_digest,
                "instructionBytes": launched.report.get("instructionBytes"),
            },
            "tokenCharacters": len(token),
            "equalsCompilerResult": matches_expected,
            "routeDigestEqualsSessionDigest": digest_matches,
        },
    )


def first_worktree_task_blocked(records: list[StepRecord], chains: dict[str, Any]) -> StepRecord:
    """The step this run could not complete, built FROM the run's own step records.

    Nothing here is asserted about attempts the transcript does not carry: the refusals below are
    read out of the step records the run already wrote, so this entry and the record can only
    agree. Every refused call keeps its own verbatim ``StepRecord`` in ``steps``; this entry names
    them and adds nothing to them.

    Two earlier scenario shapes are recorded verbatim in the round-1 transcript artifact
    ``260915-CAPS-L14-evidence-fresh-user-acceptance.json``: ``leaf-ref-not-found`` (4 occurrences,
    the master-altitude start that is not a route) and ``leaf source branch does not match its
    task-derived organizational or atomic parent`` (10 occurrences, the explicit sprint
    ``source_branch`` that contradicted the branch the activation mints). **The other two shapes this
    leaf met -- ``integration-branch authority collision`` and ``declared memory series source branch
    does not exist`` -- were never written to a transcript**: those runs were not kept, and the texts
    survive only as quotes in the reviewer's verdicts and in this leaf's round-3 report. They are
    named here as quotes, not as a pointer to an artifact that does not carry them.
    """
    refused = [
        {
            "fixture": one.fixture,
            "step": one.step,
            "status": one.status,
            "state": one.result.get("state"),
            "refusal": one.result.get("nextStep") or one.result.get("error"),
        }
        for one in records
        if one.step == STEP_WORKTREE and one.status in {"failed", "refused"}
    ]
    return StepRecord(
        step=STEP_WORKTREE,
        fixture="both",
        call=(
            "worktree_start_tool(config, TaskIdentity(task_name='fresh-user-master', "
            "leaf_id='FRESH-USER-LEAF-1', worktree_name='leaf-1', workflow_kind='light-task'), "
            "bases=TaskBases(work_branch='ar/leaf-1', memory_mode='external'), "
            "execution=StartExecution(skip_provider_setup=True))"
        ),
        status="blocked",
        result={
            "refusals": refused,
            "wouldHaveRequired": (
                "a leaf start the product accepts: the activation mints the master's series "
                "contract on the first atomic leaf start, and a caller-supplied source_branch or a "
                "missing leaf-parent row both refuse before it gets that far"
            ),
            "owner": "260915-CAPS-L14 successor or 260915-CAPS-L11, as a bounded re-run",
        },
        blocked_reason=(
            "the first worktree task refused on both fixtures. Every refused call this run made is "
            "recorded verbatim as its own step in `steps`; `result.refusals` below reads those same "
            "records rather than restating them, and this run's own chain facts carry "
            f"{len(chains)} fixture(s)."
        ),
    )


def run_fresh_user_acceptance(run_root: Path) -> dict[str, Any]:
    """Both fixtures, the ordered flow, and the four chain invariants each asserted."""
    recorder = CheckpointRecorder(scenario="fresh-user-acceptance")
    records: list[StepRecord] = []
    chains: dict[str, Any] = {}
    # The harness owns exactly these two directories under the run root, so a re-run over the
    # same root starts from nothing rather than on top of the previous run's repositories.
    for name in ("spear-not-main", "plain-main"):
        shutil.rmtree(run_root / name, ignore_errors=True)
    fixtures = {
        "spear-not-main": create_fresh_user_fixture(
            run_root / "spear-not-main",
            name="spear-not-main",
            spear_branch="dev",
            over_cap_source=True,
            vendored_tree=True,
        ),
        "plain-main": create_fresh_user_fixture(
            run_root / "plain-main",
            name="plain-main",
            spear_branch="main",
            over_cap_source=False,
            vendored_tree=False,
        ),
    }
    for name, fixture in fixtures.items():
        chains[name] = run_fixture_scenario(fixture, records, recorder)

    invariant_results: list[dict[str, Any]] = []
    for name, chain in chains.items():
        found = _bootstrap_paths(tuple(chain["firstMemoryCommitPaths"]))
        invariant_results.append(
            _invariant(
                INVARIANTS[0],
                fixture=name,
                actual={"firstMemoryCommit": chain["firstMemoryCommit"], "bootstrapPaths": found},
                passed=not found,
            )
        )
        invariant_results.append(
            _invariant(
                INVARIANTS[1],
                fixture=name,
                actual={"offenders": chain["confidenceTagOffenders"]},
                passed=not chain["confidenceTagOffenders"],
            )
        )
        invariant_results.append(
            _invariant(
                INVARIANTS[2],
                fixture=name,
                actual=chain["ledger"],
                passed=bool(chain["ledger"].get("exists")),
            )
        )
        invariant_results.append(
            _invariant(
                INVARIANTS[3],
                fixture=name,
                actual={
                    "workBranch": chain["workBranch"],
                    "exists": chain["workBranchExists"],
                    "worktreeState": chain["worktreeState"],
                },
                passed=bool(chain["workBranchExists"]),
            )
        )

    for fixture in fixtures.values():
        records.append(free_agent_acceptance(fixture))
    if not all(chain.get("workBranchExists") for chain in chains.values()):
        records.append(first_worktree_task_blocked(records, chains))
    blocked = [
        {"step": one.step, "reason": one.blocked_reason}
        for one in records
        if one.status == "blocked"
    ]
    report = recorder.report(
        runRoot=run_root.as_posix(),
        steps=[one.to_dict() for one in records],
        invariants=invariant_results,
        chains=chains,
        blocked=blocked,
    )
    return report


def _invariant(
    definition: CheckpointDefinition,
    *,
    fixture: str,
    actual: object,
    passed: bool,
) -> dict[str, Any]:
    return {
        "id": definition.checkpoint_id,
        "fixture": fixture,
        "requirement": definition.requirement,
        "expected": definition.expected,
        "actual": actual,
        "status": "passed" if passed else "failed",
    }


def environment_facts() -> dict[str, Any]:
    """The interpreter and environment the transcript was produced with."""
    return {
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "cwd": Path.cwd().as_posix(),
        "pid": os.getpid(),
        "packageSource": package_source(),
    }
