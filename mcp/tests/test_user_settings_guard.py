"""The test-support guard never writes the user's real settings file (MIK-R135).

On 2026-10-11 a Worker-built script constructed ``EntryPointWorld`` outside
pytest and overwrote the developer's real ``settings.json``, refusing every
role message and start for 11 minutes. These cases pin the repair: one guard
refuses the account's real settings path before anything is written, every
support module reaches that path only through the guard, and the world stays
hermetic as its own module text claims.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import pwd
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

import _user_settings_guard as guard_module
import conftest
import pytest
from _user_settings_guard import UserSettingsWriteRefused, user_settings_path_for_write
from paseo_launch_test_support import prepare_host
from paseo_runtime_test_support import runtime_settings, write_shared_runtime

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TESTS_ROOT = REPOSITORY_ROOT / "mcp" / "tests"
TEST_SUPPORT_ROOT = REPOSITORY_ROOT / "mcp" / "test_support"
GUARD_MODULE = "_user_settings_guard.py"
GUARD_PATH = TESTS_ROOT / GUARD_MODULE
# Every write of the user's settings file flows through one of these product
# functions, so a support module that never calls them — directly, through an
# import alias, or by rebuilding the same literal path — can never address
# that file. The guard module itself is the single admitted caller.
RAW_USER_SETTINGS_FUNCTIONS = frozenset({"user_agentic_settings_path", "user_settings_path"})
GUARDED_PATH_FUNCTION = "user_settings_path_for_write"
# The three segments that together name the user's settings file. One
# expression joining all three rebuilds the real path without calling the
# product resolver.
PATH_MARKERS = frozenset({".config", "agents-remember", "settings.json"})


def _fake_account(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    """Replace the account database answer and HOME with one made-up account."""
    monkeypatch.setattr(pwd, "getpwuid", lambda _uid: SimpleNamespace(pw_dir=str(home)))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)


def test_the_guard_refuses_the_made_up_real_path_and_creates_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Evidence 1: with a made-up account and no XDG_CONFIG_HOME, the guard raises."""
    home = tmp_path / "home"
    _fake_account(monkeypatch, home)
    with pytest.raises(UserSettingsWriteRefused) as caught:
        user_settings_path_for_write()
    message = str(caught.value)
    assert "XDG_CONFIG_HOME" in message
    assert "temporary" in message
    assert not home.exists(), "the guard must raise before a folder or a file is made"


def test_the_guard_allows_a_moved_configuration_folder_and_creates_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inside pytest XDG_CONFIG_HOME points at a temporary folder: the guard returns it."""
    moved = tmp_path / "config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(moved))
    path = user_settings_path_for_write()
    assert path == moved / "agents-remember" / "settings.json"
    assert not moved.exists(), "the guard itself creates nothing"


def test_the_guard_without_an_account_database_compares_and_refuses_a_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fallback compares against the earliest observed home and says so.

    With the account database unreadable and no moved configuration folder,
    the candidate matches the fallback path, so the guard refuses — naming
    the database failure and the home it actually compared with — and creates
    nothing.
    """
    observed = guard_module._EARLIEST_OBSERVED_HOME

    def _no_account(_uid: int) -> SimpleNamespace:
        raise KeyError("no such user")

    monkeypatch.setattr(pwd, "getpwuid", _no_account)
    monkeypatch.setenv("HOME", str(observed))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    with pytest.raises(UserSettingsWriteRefused) as caught:
        user_settings_path_for_write()
    message = str(caught.value)
    assert "XDG_CONFIG_HOME" in message
    assert "account database" in message
    assert str(observed) in message
    assert not (observed / ".config").exists(), "a matched fallback never falls back to writing"


def test_the_guard_without_an_account_database_allows_a_moved_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the database unreadable but XDG moved, the moved path is returned.

    The moved candidate provably differs from the fallback comparison, so
    returning it writes nothing real; the guard itself creates nothing.
    """
    moved = tmp_path / "config"

    def _no_account(_uid: int) -> SimpleNamespace:
        raise KeyError("no such user")

    monkeypatch.setattr(pwd, "getpwuid", _no_account)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(moved))
    path = user_settings_path_for_write()
    assert path == moved / "agents-remember" / "settings.json"
    assert not moved.exists(), "the guard itself creates nothing"


def test_the_guard_reports_the_home_it_actually_observed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Provenance is truthful: the refusal names the observed home, not a proven start.

    A home mutated before the guard is first imported is unobservable
    in-process; the guard reports the earliest home it could observe and never
    claims it as a proven process-start home.
    """
    later = tmp_path / "import-home"

    def _no_account(_uid: int) -> SimpleNamespace:
        raise KeyError("no such user")

    monkeypatch.setattr(pwd, "getpwuid", _no_account)
    monkeypatch.setenv("HOME", str(later))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    name = "guard_reloaded_for_provenance"
    spec = importlib.util.spec_from_file_location(name, GUARD_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
        with pytest.raises(module.UserSettingsWriteRefused) as caught:
            module.user_settings_path_for_write()
    finally:
        sys.modules.pop(name, None)
    message = str(caught.value)
    assert str(later) in message
    assert "account database" in message
    assert "process started with" not in message
    assert not later.exists(), "the refused fallback creates nothing"


def test_building_the_world_with_the_users_own_environment_is_refused(
    tmp_path: Path,
) -> None:
    """Evidence 2: the world built in a child process with a made-up account fails.

    The child carries no XDG_CONFIG_HOME and a faked account database, the way
    the 01:45 script ran with the developer's own environment. It must fail
    with the guard's error while the made-up configuration folder stays empty.
    """
    home = tmp_path / "home"
    script = (
        "import os, sys, json\n"
        f"home = {str(home)!r}\n"
        "os.environ.pop('XDG_CONFIG_HOME', None)\n"
        "os.environ['HOME'] = home\n"
        "import pwd\n"
        "from types import SimpleNamespace\n"
        "pwd.getpwuid = lambda _uid: SimpleNamespace(pw_dir=home)\n"
        "import tool_entry_point_world as world_module\n"
        "print(json.dumps({'world_source': world_module.__file__}))\n"
        "from tool_entry_point_world import EntryPointWorld\n"
        "EntryPointWorld()\n"
    )
    # A script outside pytest sets PYTHONPATH to its own worktree explicitly —
    # tests dir, product source, test support — never inheriting an ambient one.
    environment = dict(os.environ)
    environment.pop("XDG_CONFIG_HOME", None)
    environment["HOME"] = str(home)
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(TESTS_ROOT), str(REPOSITORY_ROOT / "mcp" / "src"), str(TEST_SUPPORT_ROOT)]
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        check=False,
        text=True,
        timeout=300,
        cwd=str(REPOSITORY_ROOT),
        env=environment,
    )
    assert completed.returncode != 0, (
        "the world must not build with the user's own environment\n"
        f"stdout: {completed.stdout}\nstderr: {completed.stderr}"
    )
    assert str(TESTS_ROOT / "tool_entry_point_world.py") in completed.stdout, (
        "the child must show which worktree source it ran\n"
        f"stdout: {completed.stdout}\nstderr: {completed.stderr}"
    )
    assert "XDG_CONFIG_HOME" in completed.stderr, (
        "the child must fail with the guard's error\n"
        f"stdout: {completed.stdout}\nstderr: {completed.stderr}"
    )
    assert list(home.rglob("*")) == [], "the made-up configuration folder must stay empty"


def _bound_callable_names(tree: ast.AST) -> tuple[set[str], set[str]]:
    """Local names bound to the raw resolvers and to the guard, aliases included."""
    raw = set(RAW_USER_SETTINGS_FUNCTIONS)
    guarded = {GUARDED_PATH_FUNCTION}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        for alias in node.names:
            local = alias.asname or alias.name
            if alias.name in RAW_USER_SETTINGS_FUNCTIONS:
                raw.add(local)
            if alias.name == GUARDED_PATH_FUNCTION:
                guarded.add(local)
    return raw, guarded


def _expression_strings(node: ast.AST) -> set[str]:
    return {
        child.value
        for child in ast.walk(node)
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
    }


def _literal_settings_path_lines(tree: ast.AST) -> set[int]:
    """Lines joining every marker segment into one path without the resolver."""
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            if _expression_strings(node) >= PATH_MARKERS:
                lines.add(node.lineno)
        elif isinstance(node, ast.Call):
            function = node.func
            name = (
                function.attr
                if isinstance(function, ast.Attribute)
                else function.id
                if isinstance(function, ast.Name)
                else ""
            )
            if name in ("joinpath", "join") and _expression_strings(node) >= PATH_MARKERS:
                lines.add(node.lineno)
    return lines


def _direct_user_settings_calls(path: Path) -> list[str]:
    """Unsafe spellings that reach the user's settings path outside the guard.

    Catches direct and import-aliased calls of the raw product resolvers, and
    literal rebuilds of the settings path (``/`` chains and join calls joining
    every marker segment). Guarded calls stay clean.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    raw, guarded = _bound_callable_names(tree)
    calls: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if isinstance(function, ast.Name):
            if function.id in guarded:
                continue
            if function.id in raw:
                calls.append(f"{path.name}:{node.lineno}: {function.id}()")
        elif isinstance(function, ast.Attribute):
            if function.attr in RAW_USER_SETTINGS_FUNCTIONS:
                calls.append(f"{path.name}:{node.lineno}: {function.attr}()")
    for lineno in _literal_settings_path_lines(tree):
        calls.append(f"{path.name}:{lineno}: literal user settings path")
    return sorted(set(calls))


def _python_files(root: Path) -> list[Path]:
    """Every Python file below ``root``, however deep, bytecode caches left out."""
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def _is_test_file(path: Path) -> bool:
    return path.name.startswith("test_")


def _support_modules(
    tests_root: Path = TESTS_ROOT, support_root: Path = TEST_SUPPORT_ROOT
) -> list[Path]:
    """Every support module rule 2 binds, without listing any of them by name.

    Recursive over both support roots, so a future nested module is covered
    without being listed; only the guard itself may call the product resolvers.
    Below the tests root these are the files ``_test_modules`` does not hold,
    so the two populations together leave out the guard alone.
    """
    guard = tests_root / GUARD_MODULE
    modules = [
        path for path in _python_files(tests_root) if not _is_test_file(path) and path != guard
    ]
    modules.extend(_python_files(support_root))
    return modules


def test_every_support_write_of_the_user_settings_goes_through_the_guard() -> None:
    """Rule 4: a support module that reaches the settings path fails this test.

    The population is derived recursively from the tree, so a new or nested
    support module is covered without being listed; only the guard itself may
    call the product resolvers.
    """
    violations = {
        call for module in _support_modules() for call in _direct_user_settings_calls(module)
    }
    assert not violations, (
        "support modules must reach the user's settings path only through "
        "user_settings_path_for_write:\n" + "\n".join(sorted(violations))
    )


def test_the_check_fails_a_support_module_that_writes_directly(tmp_path: Path) -> None:
    """Evidence 3: the rule-4 check reddens on a module that writes directly."""
    direct = tmp_path / "direct_support.py"
    direct.write_text(
        "from agents_remember.kernel.agentic_settings import user_agentic_settings_path\n"
        "user_agentic_settings_path().write_text('{}', encoding='utf-8')\n",
        encoding="utf-8",
    )
    guarded = tmp_path / "guarded_support.py"
    guarded.write_text(
        "from _user_settings_guard import user_settings_path_for_write\n"
        "user_settings_path_for_write().write_text('{}', encoding='utf-8')\n",
        encoding="utf-8",
    )
    assert _direct_user_settings_calls(direct), "the check must catch a direct write"
    assert _direct_user_settings_calls(guarded) == [], "the check must pass a guarded write"


def test_the_check_fails_aliased_and_literal_bypasses(tmp_path: Path) -> None:
    """Evidence 3: imported aliases and literal path rebuilds redden too."""
    aliased = tmp_path / "aliased_support.py"
    aliased.write_text(
        "from agents_remember.kernel.agentic_settings import "
        "user_agentic_settings_path as target\n"
        "target().parent.mkdir(parents=True, exist_ok=True)\n"
        "target().write_text('{}')\n",
        encoding="utf-8",
    )
    literal = tmp_path / "literal_support.py"
    literal.write_text(
        "from pathlib import Path\n"
        "path = Path.home() / '.config' / 'agents-remember' / 'settings.json'\n"
        "path.parent.mkdir(parents=True, exist_ok=True)\n"
        "path.write_text('{}')\n",
        encoding="utf-8",
    )
    guard_aliased = tmp_path / "guard_aliased_support.py"
    guard_aliased.write_text(
        "from _user_settings_guard import user_settings_path_for_write as guarded_path\n"
        "guarded_path().write_text('{}', encoding='utf-8')\n",
        encoding="utf-8",
    )
    assert _direct_user_settings_calls(aliased), "the check must catch an aliased write"
    assert _direct_user_settings_calls(literal), "the check must catch a literal rebuild"
    assert _direct_user_settings_calls(guard_aliased) == [], (
        "the check must pass a guarded write under an alias"
    )


def _test_modules(tests_root: Path = TESTS_ROOT) -> list[Path]:
    """Every test file rule 3 binds, however deep, without listing any of them by name.

    Reads go through the guard exactly like writes: under pytest the moved
    configuration folder answers identically, while a cleared environment
    refuses instead of silently addressing the account's real file.
    """
    return [path for path in _python_files(tests_root) if _is_test_file(path)]


def _assert_test_files_reach_only_the_guard(tests_root: Path = TESTS_ROOT) -> None:
    """The whole rule-3 check over one tests root, failing on every unsafe spelling."""
    violations = {
        call for module in _test_modules(tests_root) for call in _direct_user_settings_calls(module)
    }
    assert not violations, (
        "test files must reach the user's settings path only through "
        "user_settings_path_for_write:\n" + "\n".join(sorted(violations))
    )


def test_every_test_file_reaches_the_user_settings_only_through_the_guard() -> None:
    """MIK-R137 rule 3: a test file that reaches the settings path fails this test.

    The population is derived recursively from the tree, so a new or nested
    test file is covered without being listed; the guard module and this
    check's own sample texts hold no such call.
    """
    _assert_test_files_reach_only_the_guard()


def test_the_rule3_check_fails_a_test_file_that_writes_directly(tmp_path: Path) -> None:
    """Evidence 3 for rule 3: the check reddens on a directly writing test file."""
    direct = tmp_path / "test_direct.py"
    direct.write_text(
        "from agents_remember.kernel.agentic_settings import user_agentic_settings_path\n"
        "def test_direct_write():\n"
        "    user_agentic_settings_path().write_text('{}', encoding='utf-8')\n",
        encoding="utf-8",
    )
    guarded = tmp_path / "test_guarded.py"
    guarded.write_text(
        "from _user_settings_guard import user_settings_path_for_write\n"
        "def test_guarded_write():\n"
        "    user_settings_path_for_write().write_text('{}', encoding='utf-8')\n",
        encoding="utf-8",
    )
    assert _direct_user_settings_calls(direct), "the check must catch a direct test write"
    assert _direct_user_settings_calls(guarded) == [], "the check must pass a guarded test write"


def test_the_rule3_check_fails_a_nested_test_file_that_writes_directly(tmp_path: Path) -> None:
    """Evidence 3 for rule 3: the whole check reddens on a nested writing test file.

    The samples are text below a made-up tests root, one and two folders
    deep; the check reads them and nothing imports, collects or runs them.
    The whole check finds both and fails naming the one that writes directly.
    """
    tests_root = tmp_path / "tests"
    direct = tests_root / "nested" / "deeper" / "test_nested_direct.py"
    direct.parent.mkdir(parents=True)
    direct.write_text(
        "from agents_remember.kernel.agentic_settings import user_agentic_settings_path\n"
        "def test_nested_direct_write():\n"
        "    user_agentic_settings_path().write_text('{}', encoding='utf-8')\n",
        encoding="utf-8",
    )
    guarded = tests_root / "nested" / "test_nested_guarded.py"
    guarded.write_text(
        "from _user_settings_guard import user_settings_path_for_write\n"
        "def test_nested_guarded_write():\n"
        "    user_settings_path_for_write().write_text('{}', encoding='utf-8')\n",
        encoding="utf-8",
    )
    assert _test_modules(tests_root) == [direct, guarded], "nested test files must be found"
    with pytest.raises(AssertionError) as failed:
        _assert_test_files_reach_only_the_guard(tests_root)
    message = str(failed.value)
    assert "test_nested_direct.py:3: user_agentic_settings_path()" in message
    assert "test_nested_guarded.py" not in message, "the check must pass a guarded nested write"


def _every_test_support_writer(root: Path) -> list[tuple[str, Callable[[], None]]]:
    """Every writer of the user's settings path the tests hold, read from the code.

    ``write_shared_runtime`` is the fixture authority every paseo test calls;
    ``prepare_host`` is the launch-fixture route onto it. Both reach the path
    through the guard.
    """
    settings = runtime_settings(root)
    return [
        ("write_shared_runtime", lambda: write_shared_runtime(root, settings)),
        ("prepare_host", lambda: prepare_host(root, configured=True)),
    ]


def test_cleared_environment_and_stopped_patches_still_refuse_every_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MIK-R137 rule 2, evidence 2: cleared env and stopped patches still refuse.

    On a made-up account with no moved configuration folder, every writer the
    tests hold raises the guard's error — inside a cleared environment, and
    again after a started patcher is stopped and the variable is deleted, so
    the protection never depends on the environment a test left behind. The
    made-up folder is unchanged.
    """
    home = tmp_path / "made-up-home"
    _fake_account(monkeypatch, home)
    roots = tmp_path / "roots"
    roots.mkdir()
    writers = _every_test_support_writer(roots)
    with mock.patch.dict(os.environ, {}, clear=True):
        os.environ["HOME"] = str(home)
        for _name, call in writers:
            with pytest.raises(UserSettingsWriteRefused):
                call()
    keeper = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(tmp_path / "xdg")})
    keeper.start()
    try:
        keeper.stop()
    finally:
        os.environ.pop("XDG_CONFIG_HOME", None)
    with mock.patch.dict(os.environ, {}, clear=True):
        os.environ["HOME"] = str(home)
        for _name, call in writers:
            with pytest.raises(UserSettingsWriteRefused):
                call()
    assert list(home.rglob("*")) == [], "no writer may create anything in the made-up home"


# Where each of those writers is defined, as a traceback through it names it.
WRITER_SOURCES = {
    "write_shared_runtime": TESTS_ROOT / "paseo_runtime_test_support.py",
    "prepare_host": TESTS_ROOT / "paseo_launch_test_support.py",
}
GUARD_ERROR = f"{UserSettingsWriteRefused.__module__}.{UserSettingsWriteRefused.__qualname__}"


def _child_writer_program(home: Path, roots: Path) -> str:
    """A program for a child that calls every writer on a made-up account.

    The child clears the configuration folder and fakes the account database
    to the made-up home before it imports a writer. For each writer that an
    error stops it prints one JSON line: the error's class and every frame
    the error passed through. It then ends with the last of those errors.
    """
    return (
        "import json, os, traceback\n"
        f"home = {str(home)!r}\n"
        "os.environ.pop('XDG_CONFIG_HOME', None)\n"
        "os.environ['HOME'] = home\n"
        "import pwd\n"
        "from types import SimpleNamespace\n"
        "pwd.getpwuid = lambda _uid: SimpleNamespace(pw_dir=home)\n"
        "from pathlib import Path\n"
        "from paseo_launch_test_support import prepare_host\n"
        "from paseo_runtime_test_support import runtime_settings, write_shared_runtime\n"
        f"root = Path({str(roots)!r})\n"
        "writers = {\n"
        "    'write_shared_runtime': lambda: write_shared_runtime(root, runtime_settings(root)),\n"
        "    'prepare_host': lambda: prepare_host(root, configured=True),\n"
        "}\n"
        "stopped = None\n"
        "for name, call in writers.items():\n"
        "    try:\n"
        "        call()\n"
        "    except Exception as error:\n"
        "        stopped = error\n"
        "        kind = f'{type(error).__module__}.{type(error).__qualname__}'\n"
        "        frames = traceback.extract_tb(error.__traceback__)\n"
        "        reached = [[frame.filename, frame.name] for frame in frames]\n"
        "        print(json.dumps({'writer': name, 'error': kind, 'frames': reached}))\n"
        "if stopped is not None:\n"
        "    raise stopped\n"
    )


def _run_outside_pytest(program: str, home: Path) -> subprocess.CompletedProcess[str]:
    """Run a program the way a script outside pytest runs, on a made-up account."""
    # A script outside pytest sets PYTHONPATH to its own worktree explicitly —
    # tests dir, product source, test support — never inheriting an ambient one.
    environment = dict(os.environ)
    environment.pop("XDG_CONFIG_HOME", None)
    environment["HOME"] = str(home)
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(TESTS_ROOT), str(REPOSITORY_ROOT / "mcp" / "src"), str(TEST_SUPPORT_ROOT)]
    )
    return subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        check=False,
        text=True,
        timeout=300,
        cwd=str(REPOSITORY_ROOT),
        env=environment,
    )


def _writers_the_guard_refused(completed: subprocess.CompletedProcess[str]) -> list[str]:
    """The writers a child reached and the guard refused, read from the child's own report.

    A writer counts when the child reported the guard's error class for it,
    raised from the guard's own function, with the writer's frame on the way.
    The error text of the child proves neither: a child that fails before it
    reaches a writer echoes whatever its program names, and another error
    may name the same variable.
    """
    refused: list[str] = []
    for line in completed.stdout.splitlines():
        report = json.loads(line)
        writer = report["writer"]
        frames = report["frames"]
        if (
            report["error"] == GUARD_ERROR
            and [str(WRITER_SOURCES[writer]), writer] in frames
            and frames[-1:] == [[str(GUARD_PATH), GUARDED_PATH_FUNCTION]]
        ):
            refused.append(writer)
    return refused


def test_writers_started_in_a_child_without_the_moved_folder_refuse(
    tmp_path: Path,
) -> None:
    """MIK-R137 rule 2: a child that starts without the moved folder refuses.

    The child clears the configuration folder, fakes the account database to
    a made-up home, and calls every writer. Each writer is reached and is
    stopped by the guard's own error, the child ends with that error, and
    the made-up folder stays empty.
    """
    home = tmp_path / "home"
    roots = tmp_path / "roots"
    roots.mkdir()
    completed = _run_outside_pytest(_child_writer_program(home, roots), home)
    assert completed.returncode != 0, (
        "the writers must not run with the user's own environment\n"
        f"stdout: {completed.stdout}\nstderr: {completed.stderr}"
    )
    assert "XDG_CONFIG_HOME" in completed.stderr, (
        "the child must fail with the guard's error\n"
        f"stdout: {completed.stdout}\nstderr: {completed.stderr}"
    )
    writers = [name for name, _call in _every_test_support_writer(roots)]
    assert _writers_the_guard_refused(completed) == writers, (
        "every writer must be reached and stopped by the guard's own error\n"
        f"stdout: {completed.stdout}\nstderr: {completed.stderr}"
    )
    assert list(home.rglob("*")) == [], "the made-up configuration folder must stay empty"
    assert list(roots.rglob("*")) == [], "no writer may leave anything behind"


@pytest.mark.parametrize(
    ("written", "spoiled", "error"),
    [
        ("\n", "\\n", "SyntaxError"),
        (
            "os.environ.pop('XDG_CONFIG_HOME', None)",
            "os.environ['XDG_CONFIG_HOME'] = 'relative'",
            "AgenticSettingsError",
        ),
    ],
    ids=["does-not-compile", "another-error"],
)
def test_a_child_the_guard_did_not_stop_proves_no_refusal(
    tmp_path: Path, written: str, spoiled: str, error: str
) -> None:
    """A child that ends in another error is not read as a guarded refusal.

    Both children end as a refused one does, with a failing status and an
    error text that names XDG_CONFIG_HOME. One does not compile and reaches
    no writer; the other reaches every writer and is stopped by the
    product's own error for a relative folder. Neither proves a refusal.
    """
    home = tmp_path / "home"
    program = _child_writer_program(home, tmp_path / "roots")
    assert written in program
    completed = _run_outside_pytest(program.replace(written, spoiled), home)
    assert completed.returncode != 0
    assert error in completed.stderr
    assert "XDG_CONFIG_HOME" in completed.stderr
    assert _writers_the_guard_refused(completed) == []
    assert list(home.rglob("*")) == [], "the made-up configuration folder must stay empty"


def _intact_teardown(tmp_path: Path) -> Any:
    """Drive the ``_user_settings_env_intact`` fixture's teardown once."""
    return cast(Any, conftest._user_settings_env_intact).__wrapped__(tmp_path)


def test_the_intact_fixture_fails_a_leaked_environment(tmp_path: Path) -> None:
    """The teardown comparison names the test that leaked the settings folder.

    Drives the ``_user_settings_env_intact`` fixture itself: a clean teardown
    passes, and a teardown whose derived path lies outside both temporary
    roots fails naming that path.
    """
    run = _intact_teardown(tmp_path)
    next(run)
    try:
        next(run)
    except StopIteration:
        pass
    else:
        raise AssertionError("a clean teardown must pass")
    saved_xdg = os.environ.get("XDG_CONFIG_HOME")
    saved_home = os.environ.get("HOME")
    foreign = Path(tempfile.mkdtemp(prefix="r137-foreign-"))
    try:
        os.environ["XDG_CONFIG_HOME"] = str(foreign)
        run = _intact_teardown(tmp_path)
        next(run)
        try:
            next(run)
        except AssertionError as failed:
            assert str(foreign) in str(failed), "the failure must name the leaked path"
        else:
            raise AssertionError("a leaked folder must fail")
        os.environ.pop("XDG_CONFIG_HOME", None)
        os.environ["HOME"] = str(foreign / "home")
        run = _intact_teardown(tmp_path)
        next(run)
        try:
            next(run)
        except AssertionError as failed:
            assert "foreign" in str(failed), "the failure must name the leaked path"
        else:
            raise AssertionError("a deleted folder with a foreign home must fail")
    finally:
        if saved_xdg is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = saved_xdg
        if saved_home is not None:
            os.environ["HOME"] = saved_home
        shutil.rmtree(foreign, ignore_errors=True)
