"""Focused proof for pytest-plugin and support dependency discovery."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from agents_remember.code_quality import dependency_ownership as ownership


def test_file_imports_includes_python_and_declared_pytest_plugins(tmp_path: Path) -> None:
    conftest = tmp_path / "conftest.py"
    conftest.write_text(
        "\n".join(
            (
                "import package.child",
                "from sibling import helper",
                "pytest_plugins: tuple[str, ...] = ('plugins.alpha', 'plugins.beta')",
            )
        ),
        encoding="utf-8",
    )
    imports = ownership.file_imports(conftest, None)
    assert {
        "package",
        "package.child",
        "sibling",
        "sibling.helper",
        "plugins",
        "plugins.alpha",
        "plugins.beta",
    }.issubset(imports)

    ordinary = tmp_path / "module.py"
    ordinary.write_text("pytest_plugins = ('ignored.plugin',)\n", encoding="utf-8")
    assert ownership.file_imports(ordinary, None) == set()
    invalid = tmp_path / "invalid.py"
    invalid.write_text("def broken(:\n", encoding="utf-8")
    with pytest.raises(ownership.ScopeError, match="could not parse"):
        ownership.file_imports(invalid, None)


def test_pytest_plugin_ast_helpers_accept_only_assignment_string_values() -> None:
    tree = ast.parse(
        "pytest_plugins = ['one.plugin', dynamic]\n"
        "pytest_plugins: tuple[str, ...] = ('two.plugin',)\n"
        "other = 'ignored.plugin'\n"
    )
    assignments = [node for node in tree.body if isinstance(node, (ast.Assign, ast.AnnAssign))]
    first, second, other = assignments
    assert isinstance(first, ast.Assign)
    assert isinstance(second, ast.AnnAssign)
    assert isinstance(other, ast.Assign)
    assert ownership._pytest_plugins_value(first) is not None
    assert ownership._pytest_plugins_value(second) is not None
    assert ownership._pytest_plugins_value(other) is None
    assert ownership._pytest_plugins_value(ast.Pass()) is None

    assert len(ownership._assignment_targets(first)) == 1
    assert len(ownership._assignment_targets(second)) == 1
    assert ownership._assignment_targets(ast.Pass()) == ()
    assert ownership._is_pytest_plugins_target(first.targets[0])
    assert not ownership._is_pytest_plugins_target(other.targets[0])

    plugins = ownership._declared_pytest_plugins(first.value)
    assert {"one", "one.plugin"}.issubset(plugins)
    assert ownership._pytest_plugin_name(ast.Constant(value="plugin")) == "plugin"
    assert ownership._pytest_plugin_name(ast.Constant(value=1)) is None
    assert ownership._pytest_plugin_name(ast.Name(id="dynamic")) is None
    assert {"one", "one.plugin", "two", "two.plugin"}.issubset(
        ownership._pytest_plugin_imports(tree)
    )
