"""The memory repository is founded on a chosen code branch, not on ``main``.

The absorbed 260820 runtime-correctness scope demands one property end to end: the memory
repository's initial branch is the branch the developer names, and every seam that later
compares a branch against "the memory repository's default" reads that recorded name
instead of a hard-coded one. Four seams carry it, and each one is a refusal if it is wrong:

* ``memory_init`` mints the branch and records it as ``agents-remember.defaultBranch``;
* its repair path accepts the recorded branch instead of demanding ``refs/heads/main``;
* ``memory._baseline_default_branch`` follows the recorded branch, so the first baseline
  can be adopted on a repository founded on anything;
* ``memory_repository_default_branch`` validates the recorded name against the refs that
  exist, rather than against the literal ``main``.

Every case drives the real public operation on a real Git repository, and every comparison
takes its two sides from different artifacts: the recorded config, the actual ref, the
returned payload, and — where a refusal is the behaviour — the refusal's own text.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

from agents_remember.application.memory_tools import (
    MemoryBranches,
    memory_baseline_adopt_tool,
    memory_init_tool,
)
from agents_remember.kernel._agentic_settings_core import KNOWN_ROLES
from agents_remember.kernel.memory_init import DEFAULT_BRANCH_CONFIG_KEY
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.memory.baseline import _baseline_default_branch
from agents_remember.models.memory_content_excludes import MEMORY_CONTENT_EXCLUDES
from agents_remember.models.role_capsules.vocabulary import CAPSULE_ROLES
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.serving import task_binding
from agents_remember.serving.seat_binding import PIPELINE_SEAT_ROLES
from agents_remember.serving.task_binding import (
    TASKLESS_SEAT_ROLES,
    TaskBindingRequest,
    TaskDocumentResolutionFailure,
    resolve_task_binding,
)
from agents_remember.tasks import document as document_module
from agents_remember.tasks.document import LEAF_ROLES, MASTER_ROLES, SPRINT_ROLES
from agents_remember.worktrees.integration.integration_branch_repository import (
    memory_repository_default_branch,
)
from agents_remember.worktrees.modules.git import commit_if_dirty
from test_worktree_support import git, init_repo

REPO_ID = "repo"


def _run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=False)


def _world(tmp_path: Path) -> tuple[Path, Path]:
    """A code repository, a coordination root, and a settings file outside both."""

    root = tmp_path / "world"
    # The resolver finds the code repository as ``workspace_root / repo_id``.
    code = root / REPO_ID
    init_repo(code)
    (root / "coordination").mkdir(parents=True, exist_ok=True)
    path = root / "settings.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "coordinationRoot": (root / "coordination").as_posix(),
                "workspaceRoot": root.as_posix(),
                "repositories": {REPO_ID: {}},
            }
        ),
        encoding="utf-8",
    )
    return code, path


def _memory_root(config) -> Path:
    """The configured memory root, proved present rather than assumed.

    The runtime config models it as optional because a ``disabled`` repository has none; every
    case in this module configures an external one, so the absence is a fixture defect and the
    assertion is what narrows the type.
    """

    root = config.repositories[REPO_ID].memory_root
    assert root is not None, "the fixture configures an external memory root"
    return root


def _recorded_branch(memory: Path) -> str:
    return git(memory, "config", "--get", DEFAULT_BRANCH_CONFIG_KEY)


def test_memory_init_mints_and_records_the_named_initial_branch(tmp_path: Path) -> None:
    """An explicit branch becomes the ref HEAD points at, and the recorded default."""

    code, path = _world(tmp_path)
    config = load_config(path)

    result = memory_init_tool(config, repo_id=REPO_ID, initial_branch="spear/2026-q3")

    assert result["ok"] is True, result
    memory = Path(str(result["memoryRoot"]))
    assert result["initialBranch"] == "spear/2026-q3"
    assert result["initialBranchSource"] == "explicit"
    assert git(memory, "symbolic-ref", "HEAD") == "refs/heads/spear/2026-q3"
    assert _recorded_branch(memory) == "spear/2026-q3"
    # The code repository is untouched: memory_init never moves a code branch.
    assert git(code, "symbolic-ref", "HEAD") == "refs/heads/main"


def test_memory_init_inherits_the_code_repositorys_current_branch_by_default(
    tmp_path: Path,
) -> None:
    """Omitted, the branch comes from the code checkout -- not from a built-in name."""

    code, path = _world(tmp_path)
    git(code, "checkout", "-q", "-b", "foundation-line")
    config = load_config(path)
    assert git(code, "symbolic-ref", "--short", "HEAD") == "foundation-line"

    result = memory_init_tool(config, repo_id=REPO_ID)

    assert result["initialBranch"] == "foundation-line", result
    assert result["initialBranchSource"] == "code-repository-current-branch"
    memory = Path(str(result["memoryRoot"]))
    assert _recorded_branch(memory) == "foundation-line"
    assert git(memory, "symbolic-ref", "HEAD") == "refs/heads/foundation-line"


def test_memory_init_refuses_rather_than_inventing_a_branch_when_it_has_no_answer(
    tmp_path: Path,
) -> None:
    """A detached code checkout is not a licence to record ``main``."""

    code, path = _world(tmp_path)
    git(code, "checkout", "-q", "--detach", "HEAD")
    config = load_config(path)

    result = memory_init_tool(config, repo_id=REPO_ID)

    assert result["ok"] is False, result
    assert result["initialBranch"] is None
    assert result["initialBranchSource"] == "unresolved"
    assert "initial_branch" in str(result["git"]["stderr"])
    # Nothing was created: a refusal must not leave half a memory root behind.
    memory = Path(str(result["memoryRoot"]))
    assert not (memory / ".git").exists()


def test_the_unborn_repair_path_accepts_the_configured_branch_instead_of_main(
    tmp_path: Path,
) -> None:
    """A half-created empty repository is repaired onto the branch that was asked for."""

    _code, path = _world(tmp_path)
    config = load_config(path)
    memory = _memory_root(config)
    memory.mkdir(parents=True)
    assert _run("git", "init", "-q", cwd=memory).returncode == 0
    assert git(memory, "symbolic-ref", "HEAD") == "refs/heads/master"

    result = memory_init_tool(config, repo_id=REPO_ID, initial_branch="spear/2026-q3")

    assert result["ok"] is True, result
    assert result["git"]["repairAttempted"] is True
    assert result["git"]["headRepointed"] == "refs/heads/master -> refs/heads/spear/2026-q3"
    assert git(memory, "symbolic-ref", "HEAD") == "refs/heads/spear/2026-q3"
    assert _recorded_branch(memory) == "spear/2026-q3"


def test_the_unborn_repair_path_still_refuses_a_repository_that_already_has_refs(
    tmp_path: Path,
) -> None:
    """Repair is for the unborn state; a real history is never repointed."""

    _code, path = _world(tmp_path)
    config = load_config(path)
    memory = _memory_root(config)
    init_repo(memory, "main")
    head = git(memory, "rev-parse", "HEAD")

    result = memory_init_tool(config, repo_id=REPO_ID, initial_branch="spear/2026-q3")

    assert result["ok"] is True, result
    assert "repairAttempted" not in result["git"]
    assert git(memory, "symbolic-ref", "HEAD") == "refs/heads/main"
    assert git(memory, "rev-parse", "HEAD") == head


def test_an_initial_branch_that_is_not_one_local_branch_name_is_refused(tmp_path: Path) -> None:
    """The value becomes a Git ref, so it is narrowed before it reaches Git."""

    _code, path = _world(tmp_path)
    config = load_config(path)

    for value in ("refs/", "  ", "-x", "two words"):
        with pytest.raises(ValueError, match="one local branch"):
            memory_init_tool(config, repo_id=REPO_ID, initial_branch=value)


def test_the_recorded_refs_heads_spelling_is_accepted_and_normalized(tmp_path: Path) -> None:
    """The recorded authority is spelled ``refs/heads/x`` elsewhere; both spellings work."""

    _code, path = _world(tmp_path)
    config = load_config(path)

    result = memory_init_tool(config, repo_id=REPO_ID, initial_branch="refs/heads/team-memory")

    assert result["initialBranch"] == "team-memory", result
    memory = Path(str(result["memoryRoot"]))
    assert git(memory, "symbolic-ref", "HEAD") == "refs/heads/team-memory"


def test_baseline_adoption_follows_the_configured_branch(tmp_path: Path) -> None:
    """The first baseline is adopted on a repository founded on a non-``main`` branch.

    This is the end-to-end property: ``memory_init`` records ``spear/2026-q3``, the unborn
    branch of that exact name exists, and adoption proceeds. Before the fix, adoption
    refused here because ``_baseline_default_branch`` compared the recorded name to the
    literal ``main``.
    """

    code, path = _world(tmp_path)
    config = load_config(path)
    memory_init_tool(config, repo_id=REPO_ID, initial_branch="spear/2026-q3")
    memory = _memory_root(config)
    # The foundation is the code branch the memory is verified against, so it exists.
    git(code, "checkout", "-q", "-b", "spear/2026-q3")
    (memory / "onboarding").mkdir(parents=True, exist_ok=True)
    (memory / "onboarding" / "overview.md").write_text("# Overview\n", encoding="utf-8")

    assert _baseline_default_branch(memory) == "spear/2026-q3"

    adopted = memory_baseline_adopt_tool(
        config,
        repo_id=REPO_ID,
        accept_drift=True,
        branches=MemoryBranches(source_branch="main", work_branch="spear/2026-q3"),
    )

    assert adopted["ok"] is True, adopted
    assert adopted["state"] == "adopted", adopted
    assert git(memory, "rev-parse", "--abbrev-ref", "HEAD") == "spear/2026-q3"
    assert git(memory, "rev-list", "--count", "HEAD") == "1"


def test_baseline_adoption_refuses_a_branch_the_memory_repository_does_not_record(
    tmp_path: Path,
) -> None:
    """The recorded name is authority: a mismatch is refused, not silently honoured."""

    code, path = _world(tmp_path)
    config = load_config(path)
    memory_init_tool(config, repo_id=REPO_ID, initial_branch="spear/2026-q3")
    memory = _memory_root(config)
    git(code, "checkout", "-q", "-b", "spear/2026-q3")
    (memory / "onboarding").mkdir(parents=True, exist_ok=True)
    (memory / "onboarding" / "overview.md").write_text("# Overview\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="bootstrap-only exception"):
        memory_baseline_adopt_tool(
            config,
            repo_id=REPO_ID,
            accept_drift=True,
            branches=MemoryBranches(source_branch="main", work_branch="main"),
        )


def test_the_memory_default_branch_validates_the_recorded_name_against_its_refs(
    tmp_path: Path,
) -> None:
    """No remote: the recorded local name is validated for existence, not for being ``main``."""

    _code, path = _world(tmp_path)
    config = load_config(path)
    memory = _memory_root(config)
    init_repo(memory, "main")
    git(memory, "branch", "-m", "main", "spear/2026-q3")
    git(memory, "config", "--local", DEFAULT_BRANCH_CONFIG_KEY, "spear/2026-q3")
    # ``init_repo`` installs an origin/HEAD; without this the remote would win by design.
    git(memory, "symbolic-ref", "--delete", "refs/remotes/origin/HEAD")

    assert memory_repository_default_branch(memory) == "spear/2026-q3"


def test_the_memory_default_branch_still_refuses_a_recorded_name_with_no_ref(
    tmp_path: Path,
) -> None:
    """Dropping the ``main`` comparison must not drop the existence check with it."""

    _code, path = _world(tmp_path)
    config = load_config(path)
    memory = _memory_root(config)
    init_repo(memory, "main")
    git(memory, "config", "--local", DEFAULT_BRANCH_CONFIG_KEY, "spear/absent")
    git(memory, "symbolic-ref", "--delete", "refs/remotes/origin/HEAD")

    with pytest.raises(RuntimeError, match="authority target does not exist"):
        memory_repository_default_branch(memory)


def test_memory_init_refuses_a_coordination_root_that_does_not_exist(tmp_path: Path) -> None:
    """Git creates missing parents, so an absent scaffold must be refused, not obeyed.

    Without this check ``git init`` would build the whole path and report success for a
    memory repository the rest of the product would never look at.
    """

    _code, path = _world(tmp_path)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["coordinationRoot"] = (tmp_path / "world" / "never-created").as_posix()
    path.write_text(json.dumps(document), encoding="utf-8")
    config = load_config(path)

    with pytest.raises(ValueError, match=r"coordination root .* does not exist"):
        memory_init_tool(config, repo_id=REPO_ID, initial_branch="spear/2026-q3")

    assert not (tmp_path / "world" / "never-created").exists()


# --------------------------------------------------------------------------------------
# The free agent's shape.
#
# Developer ruling 2026-09-16: *"It is not a task related agent. Can't be. It needs to be
# free agent. All what it needs is that can 'call'."* These cases hold that shape, so a
# later reader cannot "fix" the seat by giving it a task altitude it must not have.
# --------------------------------------------------------------------------------------


def test_the_bootstrap_role_is_a_free_agent_and_has_no_task_altitude() -> None:
    """``bootstrap`` is deliberately outside every structural role set.

    If a later change adds it to one of these, the free agent silently becomes a task
    seat, which is the shape the developer ruled out. The assertion names both sides:
    the role IS published by the capsule vocabulary, and it is NOT in the task layer's
    altitude sets or in the serving seat-role list.
    """

    assert "bootstrap" in CAPSULE_ROLES, "the role itself must stay published"
    assert "bootstrap" not in SPRINT_ROLES
    assert "bootstrap" not in MASTER_ROLES
    assert "bootstrap" not in LEAF_ROLES
    # The settings surface still knows the role, so its knobs are configurable.
    assert "bootstrap" in KNOWN_ROLES
    # ``PIPELINE_SEAT_ROLES`` is the "which roles run the task pipeline" list; the free
    # agent is not one of them, and adding it there would be the same mistake.
    assert "bootstrap" not in PIPELINE_SEAT_ROLES


def test_without_a_task_document_only_the_taskless_seat_roles_are_admitted() -> None:
    """The gate, named by its refusal: taskless roles open, every other role refuses.

    Developer ruling 2026-09-16 admits ``bootstrap`` here: *"It is not a task related agent.
    Can't be. It needs to be free agent. All what it needs is that can 'call'."* The
    assertions name the observed outcome of both halves, so a later change either way fails
    with the role it changed rather than with a count.
    """

    def refusal_for(seat_role: str):
        return resolve_task_binding(
            Path("/nonexistent-coordination-root"),
            TaskBindingRequest(
                task_document_ref=None,
                replacement_for_task_document_ref=None,
                seat_role=seat_role,
            ),
        ).refusal

    # The free agent and the two pre-existing taskless roles are admitted.
    for taskless in ("bootstrap", "chat", "terminal"):
        assert refusal_for(taskless) is None, f"{taskless} must open without a task document"

    # Every task-bound role still refuses, with the same status as before this change.
    for bound in ("worker", "architect", "agent", "manager", "curator", "reviewer"):
        refusal = refusal_for(bound)
        assert refusal is not None, f"{bound} must still need a task document"
        assert refusal.status == "task-binding-required", f"{bound}: {refusal.status}"


def test_the_free_agent_set_is_named_and_readable_not_a_bare_literal() -> None:
    """The exemption is a named policy set the ruling is legible in — and it is exported.

    If a future edit drops a free role from that set, this case fails on the role name.
    The second assertion is the independent side: the gate is expressed in terms of the
    named set, not a literal that could drift away from it.
    """

    assert frozenset({"chat", "terminal", "bootstrap"}) == TASKLESS_SEAT_ROLES, (
        "the taskless set changed; if that is intended, change this case deliberately"
    )
    assert "bootstrap" in TASKLESS_SEAT_ROLES, "bootstrap is a free agent by developer ruling"
    source = Path(task_binding_module_path()).read_text(encoding="utf-8")
    assert "not in TASKLESS_SEAT_ROLES" in source, (
        "the refusal must consult the named set, not a second literal"
    )
    assert 'not in {"chat", "terminal"}' not in source, "a stale literal survives beside the set"


def task_binding_module_path() -> str:
    """The binding-policy module, resolved from the package rather than hard-coded."""

    return str(task_binding.__file__)


def test_no_task_altitude_set_in_the_source_names_bootstrap() -> None:
    """The source-level companion to the import-level shape case.

    The import-level case reads the loaded frozensets; this one reads the TEXT that declares
    them, so a later edit that adds ``bootstrap`` to an altitude set fails on the line the
    developer's ruling forbids rather than only on the derived value. Both sides come from
    different artifacts: this test reads the source file, and the vocabulary it must agree
    with comes from the imported registry.
    """

    assert "bootstrap" in CAPSULE_ROLES, "the role is published, so this check is not vacuous"
    source = Path(document_module_path()).read_text(encoding="utf-8")
    for name in ("SPRINT_ROLES", "MASTER_ROLES", "LEAF_ROLES"):
        start = source.index(f"{name} = frozenset(")
        block = source[start : source.index(")", start)]
        assert "bootstrap" not in block, (
            f"{name} names bootstrap; under the free-agent ruling a task altitude is the wrong "
            "shape for this agent and must not be added"
        )


def document_module_path() -> str:
    """The task layer's document module, resolved from the package rather than hard-coded."""

    return str(document_module.__file__)


# --------------------------------------------------------------------------------------
# The reviewed round-2 findings, pinned.
# --------------------------------------------------------------------------------------


def _adoptable_memory(config, *, bootstrap_file: str = "bootstrap/state.md") -> Path:
    """A memory root prepared for adoption: memory_init, onboarding content, scaffolding."""

    # Adoption is the bootstrap exception on the configured branch, which must also be the
    # branch the code repository has checked out and the one the spear names.
    git(_memory_root(config).parents[2] / REPO_ID, "checkout", "-q", "-b", "spear/2026-q3")
    memory_init_tool(config, repo_id=REPO_ID, initial_branch="spear/2026-q3")
    memory = _memory_root(config)
    (memory / "onboarding").mkdir(parents=True, exist_ok=True)
    (memory / "onboarding" / "overview.md").write_text("# Overview\n", encoding="utf-8")
    target = memory / bootstrap_file
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("transient scaffolding\n", encoding="utf-8")
    return memory


def test_the_first_baseline_never_commits_bootstrap_scaffolding(tmp_path: Path) -> None:
    """``bootstrap/`` is absent from the commit, and still on disk afterwards.

    This is the case the baseline round demanded. The behaviour it protects was measured as
    broken: the exclusion was applied to a bare ``git add`` that ``commit_if_dirty`` then
    superseded by re-staging the whole worktree, so ``bootstrap/state.md`` landed in the
    baseline commit. The assertion is on ``git ls-files`` — the committed tree — not on any
    comment, and the second half proves the exclusion does not delete the developer's work.
    """

    _code, path = _world(tmp_path)
    config = load_config(path)
    memory = _adoptable_memory(config)

    adopted = memory_baseline_adopt_tool(config, repo_id=REPO_ID, accept_drift=True)

    assert adopted["ok"] is True, adopted
    assert adopted["state"] == "adopted", adopted
    assert git(memory, "ls-files", "--", "bootstrap") == ""
    assert git(memory, "ls-files", "--", "onboarding") == "onboarding/overview.md"
    # Excluded from the commit, NOT removed from the worktree.
    assert (memory / "bootstrap" / "state.md").is_file()
    # And it is left as unstaged content, which is what "transient" means here.
    assert "bootstrap/" in git(memory, "status", "--porcelain")


def test_no_memory_content_commit_stages_bootstrap_scaffolding(tmp_path: Path) -> None:
    """The same exclusion on the helper later commits use, with content ready to stage.

    Covers the obligation's "nor any later one" half without driving a full closeout: a
    staged tree plus ``commit_if_dirty`` is exactly the seam a later memory commit calls.
    """

    _code, path = _world(tmp_path)
    config = load_config(path)
    memory = _adoptable_memory(config)

    commit = commit_if_dirty(memory, "later memory content", exclude_paths=MEMORY_CONTENT_EXCLUDES)

    assert commit
    assert git(memory, "ls-files", "--", "bootstrap") == ""
    assert git(memory, "ls-files", "--", "onboarding") == "onboarding/overview.md"
    assert (memory / "bootstrap" / "state.md").is_file()


def test_a_document_supplied_to_a_taskless_role_still_resolves_but_skips_the_altitude_check(
    tmp_path: Path,
) -> None:
    """The measured document-supplied path, pinned so the comment cannot drift from it.

    ``bootstrap`` with no document opens (its own case above). This one supplies a document
    and asserts what actually happens: the reference is resolved, the altitude check is
    skipped for every taskless role — not only ``terminal`` — and a role outside the set is
    still refused. The baseline round measured this path as UNCOVERED and the shipped comment
    as false; both are fixed here.
    """

    coordination = tmp_path / "coordination"
    master = coordination / "tasks" / "repo" / "master.json"
    master.parent.mkdir(parents=True, exist_ok=True)
    master.write_text(
        json.dumps(
            {
                "schema": "ar-task-document/v1",
                "id": "260916-REV-M1",
                "slug": "master",
                "title": "A master",
                "kind": "master",
                "repo": "repo",
                "type": "Feature",
                "createdAt": "2026-09-16T00:00+00:00",
                "objective": "objective",
                "requirements": [],
                "subTasks": [],
                "sections": [],
            }
        ),
        encoding="utf-8",
    )
    document = TaskDocumentRef(repository="repo", path="master.json")

    def refusal_for(seat_role: str):
        return resolve_task_binding(
            coordination,
            TaskBindingRequest(
                task_document_ref=document,
                replacement_for_task_document_ref=None,
                seat_role=seat_role,
            ),
        ).refusal

    # The three roles are spelled here rather than iterated from the set under test: a loop over
    # TASKLESS_SEAT_ROLES can only show that the set's members behave alike, so it cannot see a
    # membership change. Membership is caught by the two cases above (`…only_the_taskless_seat_roles…`
    # and `…named_and_readable_not_a_bare_literal`); this line makes the loop's own expectation
    # independent of the constant it tests, so the two failure modes are separately attributable.
    assert sorted(TASKLESS_SEAT_ROLES) == ["bootstrap", "chat", "terminal"], (
        "the taskless set changed; that is a policy change, and the two membership cases must be "
        "updated deliberately if it is intended"
    )
    # Taskless roles: the document resolves and admission is accepted, altitude check skipped.
    for taskless in ("bootstrap", "chat", "terminal"):
        assert refusal_for(taskless) is None, f"{taskless} with a document must be admitted"

    # A role outside the set still takes the structural path and is refused on its altitude.
    outsider = refusal_for("notarole")
    assert outsider is not None
    assert outsider.status == "task-binding-invalid"
    assert "no structural task altitude" in (outsider.detail or "")

    # Resolution is NOT skipped for a taskless role: a bad reference still fails loudly,
    # with the same resolution failure a task-bound role would get.
    with pytest.raises(TaskDocumentResolutionFailure, match="does not exist"):
        resolve_task_binding(
            coordination,
            TaskBindingRequest(
                task_document_ref=TaskDocumentRef(repository="repo", path="absent.json"),
                replacement_for_task_document_ref=None,
                seat_role="bootstrap",
            ),
        )


def test_the_manifest_declares_no_task_altitude_for_the_free_agent() -> None:
    """The registry the compiler reads must not give ``bootstrap`` a task altitude.

    The baseline round measured this as load-bearing: perturbing only
    ``roles.bootstrap.altitude`` changed the shipped capsule digest. The nine pre-existing
    roles declare exactly their task altitude here, so a task value for this role would be
    the same claim the developer ruling forbids — arriving through the manifest instead of the
    task layer, which is why the altitude-set cases above cannot see it.
    """

    manifest = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "skills"
            / "l-01-agent-lifecycles"
            / "composition-manifest.json"
        ).read_text(encoding="utf-8")
    )

    altitude = manifest["roles"]["bootstrap"]["altitude"]
    assert altitude not in {"sprint", "master", "leaf"}, (
        f"the free agent declares the task altitude {altitude!r}; it has no task altitude"
    )
    assert altitude.strip(), "the manifest field is required and must stay non-blank"
    # The nine task-bound roles keep declaring theirs, so this is a scoped exception.
    assert manifest["roles"]["worker"]["altitude"] == "leaf"
    assert manifest["roles"]["manager"]["altitude"] == "master"
