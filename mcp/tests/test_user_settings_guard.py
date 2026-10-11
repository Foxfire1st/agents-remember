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
import os
import pwd
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import _user_settings_guard as guard_module
import pytest
from _user_settings_guard import UserSettingsWriteRefused, user_settings_path_for_write

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


def _support_modules() -> list[Path]:
    """Every support module rule 2 binds, without listing any of them by name.

    Recursive over both support roots, so a future nested module is covered
    without being listed; only the guard itself may call the product resolvers.
    """
    modules = [
        path
        for path in sorted(TESTS_ROOT.rglob("*.py"))
        if not path.name.startswith("test_")
        and path.name != GUARD_MODULE
        and "__pycache__" not in path.parts
    ]
    modules.extend(
        path for path in sorted(TEST_SUPPORT_ROOT.rglob("*.py")) if "__pycache__" not in path.parts
    )
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
