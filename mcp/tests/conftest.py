"""Local, isolated pytest; delivery certification is an explicit Dagger-only option."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from unittest import mock

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(REPOSITORY_ROOT / "mcp" / "src"),
    str(REPOSITORY_ROOT / "mcp" / "test_support"),
]

from agents_remember.worktrees import outside_scratch
from agents_remember_test_support.testing.catalog_canonical import lane_files, read_catalog
from agents_remember_test_support.testing.hermetic_bootstrap import (
    activate_current_pytest_environment,
    candidate_test_process,
)
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS

# This is an actual pytest process, using the existing test isolation owner. No daemon,
# lifecycle-worker identity, or Dagger capability is supplied to the ordinary test loop.
_ENVIRONMENT_LEASE = activate_current_pytest_environment(
    candidate_test_process(REPOSITORY_ROOT), os.environ
)
_TEMPORARY = tempfile.TemporaryDirectory(prefix="ar-pytest-", dir="/tmp")
_ISOLATED_ROOT = Path(_TEMPORARY.name)
_ISOLATED_ENVIRONMENT = mock.patch.dict(
    os.environ,
    {
        "HOME": str(_ISOLATED_ROOT / "home"),
        "XDG_CONFIG_HOME": str(_ISOLATED_ROOT / "config"),
        "XDG_DATA_HOME": str(_ISOLATED_ROOT / "data"),
        "XDG_CACHE_HOME": str(_ISOLATED_ROOT / "cache"),
        "CODEX_HOME": str(_ISOLATED_ROOT / "codex"),
        "TMUX_TMPDIR": str(_ISOLATED_ROOT / "tmux"),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    },
)
_ISOLATED_ENVIRONMENT.start()
# Every outside scratch root a test allocates lives under this process's own isolated root, so a
# worker never shares the product parent with a sibling and never has to sweep a shared one
# (MIK-R207): the parent is per process, and the process removes exactly its own at the end.
_OUTSIDE_SCRATCH_PARENT = _ISOLATED_ROOT / "outside-scratch"
_OUTSIDE_SCRATCH_PARENT.mkdir(mode=0o700)
for directory in ("home", "config", "data", "cache", "codex", "tmux"):
    (_ISOLATED_ROOT / directory).mkdir()
# Inherited live opt-ins and credentials must never make an ordinary run contact a service.
# Tests that exercise these inputs construct their own local environment explicitly.
for name in tuple(os.environ):
    if (
        name.startswith(("AR_RUN_", "AR_SPAWN_", "AR_HOSTED_"))
        or name.endswith(("_API_KEY", "_ACCESS_TOKEN", "_AUTH_TOKEN"))
        or name
        in {
            "AGENTS_REMEMBER_REAL_MCP_CONFIG",
            "AR_CLAUDE_STREAM_SMOKE",
            "AR_CODEX_APP_SERVER_LIVE_SMOKE",
            "AR_CODEX_APP_SERVER_LIVE_CONFORMANCE",
            "SSH_AUTH_SOCK",
            "TMUX",
            "TMUX_PANE",
            "GITHUB_TOKEN",
            "GH_TOKEN",
            "AWS_PROFILE",
        }
    ):
        os.environ.pop(name, None)

# The lane manifest is the registry every module must appear in, and `evidence_lanes`
# carries the hook that enforces it: `pytest_collection_modifyitems` calls
# `load_lane_manifest` and raises `pytest.UsageError` when it refuses. That hook was
# defined but never registered, so no run on this repository had ever asked the loader
# its verdict -- the suite passed while the manifest refused (T48/T49). Registering it
# here is what makes the registry load-bearing; `test_evidence_lanes.py` asserts the
# registration so it cannot be dropped again silently.
#
# Consequence, accepted: with this hook armed, collection requires a Git checkout -- the loader
# enumerates the population through `git ls-files`, so an exported (`git archive`/tarball) tree
# fails collection with `ScopeError ... fatal: not a git repository` rather than running. Git is
# already a prerequisite of the delivery path (`code_quality/scope.py` scopes by index and diff,
# `quality_plan.py` runs in a worktree), and `git init && git add -A` restores an exported tree.
#
# `database_retirement_guard` watches every test of every worker process: production code outside
# its named modules that opens a SQLite connection, or writes, copies or moves a database file,
# fails that test at teardown. `test_knowledge_database_retired.py` asserts the registration.
pytest_plugins = (
    "agents_remember_test_support.testing.pytest_bootstrap",
    "agents_remember_test_support.testing.evidence_lanes",
    "agents_remember_test_support.testing.database_retirement_guard",
)
_INTEGRATION_FILES = pytest.StashKey[frozenset[Path]]()


def _discard_owned(base: Path, parent: Path) -> None:
    """Remove only the roots whose marker names a coordination root under this run's own base.

    Ownership is the coordination root the marker carries, resolved under this invocation's
    temporary base -- never a name shape, a shared parent, or a root being merely absent, because
    a parallel worker shares the parent and its roots must survive this worker's teardown.
    """

    owned = Path(base).resolve()
    for item in outside_scratch.list_roots(parent):
        marker = item.get("marker")
        if not isinstance(marker, dict):
            continue
        coordination = str(marker.get("coordinationRoot") or "")
        if not coordination:
            continue
        try:
            named = Path(coordination).resolve()
        except OSError:  # pragma: no cover - an unresolvable path is not proof of ownership
            continue
        if named != owned and owned not in named.parents:
            continue
        record = outside_scratch.record_from_marker(marker)
        if record is not None:
            outside_scratch.remove_root(record, parent=parent)


@pytest.fixture(scope="session", autouse=True)
def _isolated_outside_scratch_parent(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[None]:
    """Point the product's outside-scratch parent at this process's isolated root.

    The product parent is a short shared path under `/var/tmp`; a test tree under it would be
    shared with every other worker, and any teardown that swept it could delete a root a sibling
    was still using. The parent is therefore per process, exactly as the environment above is,
    and the socket budget is raised because an isolated root is deeper than the real parent --
    the rule itself is measured in `test_outside_scratch_root.py` against the real limit.
    """

    parent = outside_scratch.OUTSIDE_SCRATCH_PARENT
    limit = outside_scratch.SOCKET_PATH_LIMIT
    outside_scratch.OUTSIDE_SCRATCH_PARENT = _OUTSIDE_SCRATCH_PARENT
    outside_scratch.SOCKET_PATH_LIMIT = 4096
    try:
        yield
    finally:
        base = tmp_path_factory.getbasetemp()
        # A test that serves the product from a separate process allocates in the product parent,
        # because that process never loads this fixture. Its marker still names a coordination
        # root under this worker's own base, so the same ownership proof reaches it -- and only it.
        _discard_owned(base, outside_scratch.OUTSIDE_SCRATCH_PARENT)
        _discard_owned(base, parent)
        outside_scratch.OUTSIDE_SCRATCH_PARENT = parent
        outside_scratch.SOCKET_PATH_LIMIT = limit
        shutil.rmtree(_OUTSIDE_SCRATCH_PARENT, ignore_errors=True)


def pytest_addoption(parser: pytest.Parser) -> None:
    # These two declarations REGISTER the option names; they do not state a rail. The selected-case
    # rails live once, in the repository-root `pyproject.toml` under `[tool.pytest.ini_options]`,
    # which is the inifile pytest actually reads -- `mcp/pyproject.toml` declares no
    # `[tool.pytest.ini_options]`, so a `default=` here would never be in effect and would put a
    # second, contradictory number in the tree for a terminal reader to find (D-20). With no
    # `default` the type default is `0`, which `pytest_collection_finish` refuses by name rather
    # than running an unbounded population.
    parser.addini("unit_case_budget", "maximum selected unit cases", type="int")
    parser.addini("integration_case_budget", "maximum selected integration cases", type="int")
    parser.addoption(
        "--certify",
        action="store_true",
        help="load delivery evidence services after genuine Dagger admission",
    )


def pytest_configure(config: pytest.Config) -> None:
    # Read existing file membership once; no source census, dependency graph, or collection probe.
    manifest = REPOSITORY_ROOT / "mcp/tests/test-evidence-lanes.toml"
    _, document = read_catalog("evidence lane manifest", manifest, pytest.UsageError)
    files = lane_files(document, manifest, pytest.UsageError)
    try:
        config.stash[_INTEGRATION_FILES] = frozenset(
            REPOSITORY_ROOT / path
            for category in ("integration", "stress-durability")
            for path in files[category]
        )
    except (KeyError, TypeError) as error:
        raise pytest.UsageError(
            f"evidence lane manifest {manifest}: the [files] table must hold the lanes "
            f"integration and stress-durability as lists of paths ({error!r})"
        ) from error
    if config.getoption("certify"):
        from agents_remember_test_support.testing.certifying_bootstrap import (  # noqa: PLC0415
            prepare_certifying_pytest_bootstrap,
        )
        from agents_remember_test_support.testing.dagger_admission import (  # noqa: PLC0415
            DaggerAdmissionError,
        )

        try:
            prepare_certifying_pytest_bootstrap(REPOSITORY_ROOT)
        except DaggerAdmissionError as error:
            raise pytest.UsageError(str(error)) from error
        config.pluginmanager.import_plugin(
            "agents_remember_test_support.pytest_certifying_bootstrap"
        )


def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool | None:
    # Pure unit invocations do not import application/transaction test modules at all.
    if (
        config.option.markexpr == "not integration"
        and collection_path in config.stash[_INTEGRATION_FILES]
    ):
        return True
    return None


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        if item.path in config.stash[_INTEGRATION_FILES]:
            item.add_marker(pytest.mark.integration)


def pytest_collection_finish(session: pytest.Session) -> None:
    """Enforce maintenance budgets on collected parametrized cases, without another scan."""
    integration = sum(item.get_closest_marker("integration") is not None for item in session.items)
    populations = {"unit": len(session.items) - integration, "integration": integration}
    for lane, count in populations.items():
        budget = session.config.getini(f"{lane}_case_budget")
        if budget < 1 or count > budget:
            raise pytest.UsageError(
                f"{lane} suite has {count} cases; budget is {budget}. "
                "Consolidate overlapping protection first. Budget growth requires an explicit "
                "behavior, case-count, size and runtime tradeoff in the change description."
            )


@pytest.fixture(autouse=True)
def _user_settings_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test has its own user settings; no process-wide settings leak between cases."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "user-config"))


@pytest.fixture(autouse=True)
def _user_settings_env_intact(tmp_path: Path) -> Iterator[None]:
    """MIK-R137 rule 2: a test that ends outside the run's temporary root fails.

    Compares the derived settings path at teardown, not the variable: fixture
    teardown order cannot be relied on to still hold the per-test folder, so
    the check allows the test's own root and the process-wide isolated root
    and fails — naming the test — on anything else. A test that needs the
    environment cleared for what it asserts clears it and restores it; the
    guard, not the leftover environment, carries the protection while it is
    cleared.
    """
    yield
    from _user_settings_guard import (  # noqa: PLC0415
        UserSettingsWriteRefused,
        user_settings_path_for_write,
    )

    try:
        candidate = user_settings_path_for_write()
    except UserSettingsWriteRefused as error:
        raise AssertionError(
            "the test ended with the user's settings path at the account's "
            f"real file: {error}; restore what the test clears so no later "
            "write can reach the user's real settings file"
        ) from error
    allowed = (tmp_path, _ISOLATED_ROOT)
    if not any(candidate == root or root in candidate.parents for root in allowed):
        raise AssertionError(
            "the test ended with the user's settings path outside the run's "
            f"temporary roots: {candidate}; restore what the test clears so no "
            "later write can reach the user's real settings file"
        )


@pytest.fixture(autouse=True)
def _integration_composition(request: pytest.FixtureRequest) -> Iterator[None]:
    if request.node.get_closest_marker("integration") is not None and not request.config.getoption(
        "certify"
    ):
        request.getfixturevalue("worktree_services")
    yield


@pytest.fixture
def worktree_services() -> Iterator[None]:
    """Explicit application composition for boundary tests, never an autouse unit fixture."""
    from agents_remember.application.worktree_services import (  # noqa: PLC0415
        bind_worktree_services,
        build_default_worktree_services,
    )
    from agents_remember.worktrees.services import reset_worktree_services  # noqa: PLC0415

    bind_worktree_services(build_default_worktree_services())
    try:
        yield
    finally:
        reset_worktree_services()


def pytest_unconfigure(config: pytest.Config) -> None:
    del config
    try:
        if shutil.which("tmux") is not None:
            subprocess.run(
                ["tmux", "kill-server"],
                capture_output=True,
                check=False,
                timeout=HANG_GUARD_SECONDS,
            )
    finally:
        _ISOLATED_ENVIRONMENT.stop()
        _ENVIRONMENT_LEASE.close()
        _TEMPORARY.cleanup()
