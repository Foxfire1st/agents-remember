"""Unit tests for the layers.toml layering fitness function (260731-EFA-L9 R12)."""

from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

from _quality_admission import QUALITY_TEST_ADMISSION
from agents_remember.code_quality import layering


def _write_tree(root: Path, files: dict[str, str]) -> None:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _contract(root: Path) -> layering.LayersContract:
    return layering.load_contract(root / "layers.toml")


def _make_contract_toml() -> str:
    return """
[contract]
order = ["errors", "kernel", "models", "serving"]

[package.errors]
path = "."
root_modules = ["errors.py", "__init__.py"]
present = true

[package.kernel]
path = "kernel/"
present = true

[package.models]
path = "models/"
present = true

[package.serving]
path = "serving/"
present = true
"""


def test_rank_violation_fails(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write_tree(
        root,
        {
            "layers.toml": _make_contract_toml(),
            "mcp/src/agents_remember/kernel/__init__.py": "",
            "mcp/src/agents_remember/kernel/core.py": (
                "from agents_remember.models.gadget import Gadget\n"
            ),
            "mcp/src/agents_remember/models/__init__.py": "",
            "mcp/src/agents_remember/models/gadget.py": "",
            "mcp/src/agents_remember/serving/__init__.py": "",
        },
    )
    report = layering.check_layering(root)
    assert not report.ok
    assert len(report.violations) == 1
    assert report.violations[0].importer == "kernel"
    assert report.violations[0].imported == "models"


def test_contract_helper_reaches_load(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write_tree(
        root,
        {
            "layers.toml": _make_contract_toml(),
            "mcp/src/agents_remember/__init__.py": "",
            "mcp/src/agents_remember/errors.py": "",
        },
    )
    assert _contract(root).ranks["errors"] == 0


def test_clean_tree_passes(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write_tree(
        root,
        {
            "layers.toml": _make_contract_toml(),
            "mcp/src/agents_remember/__init__.py": "",
            "mcp/src/agents_remember/errors.py": "",
            "mcp/src/agents_remember/kernel/__init__.py": "",
            "mcp/src/agents_remember/kernel/core.py": (
                "from agents_remember.errors import AgentsRememberError\n"
            ),
            "mcp/src/agents_remember/models/__init__.py": "",
            "mcp/src/agents_remember/models/gadget.py": (
                "from agents_remember.kernel.core import thing\n"
            ),
            "mcp/src/agents_remember/serving/__init__.py": "",
            "mcp/src/agents_remember/serving/app.py": (
                "from agents_remember.models.gadget import Gadget\n"
            ),
        },
    )
    report = layering.check_layering(root)
    assert report.ok, layering.render(report)


def test_undeclared_package_directory_fails(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write_tree(
        root,
        {
            "layers.toml": _make_contract_toml(),
            "mcp/src/agents_remember/__init__.py": "",
            "mcp/src/agents_remember/errors.py": "",
            "mcp/src/agents_remember/kernel/__init__.py": "",
            "mcp/src/agents_remember/kernel/core.py": (
                "from agents_remember.errors import AgentsRememberError\n"
            ),
            "mcp/src/agents_remember/models/__init__.py": "",
            "mcp/src/agents_remember/serving/__init__.py": "",
            "mcp/src/agents_remember/rogue/__init__.py": "",
        },
    )
    report = layering.check_layering(root)
    assert not report.ok
    assert report.undeclared_dirs == ["rogue"]
    assert report.undeclared_imports == []
    assert "undeclared package directory" in layering.render(report)


def test_generated_and_data_dirs_are_not_undeclared(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write_tree(
        root,
        {
            "layers.toml": _make_contract_toml(),
            "mcp/src/agents_remember/__init__.py": "",
            "mcp/src/agents_remember/errors.py": "",
            "mcp/src/agents_remember/kernel/__init__.py": "",
            "mcp/src/agents_remember/kernel/core.py": (
                "from agents_remember.errors import AgentsRememberError\n"
            ),
            "mcp/src/agents_remember/models/__init__.py": "",
            "mcp/src/agents_remember/serving/__init__.py": "",
            "mcp/src/agents_remember/package_data/skills/x.txt": "",
            "mcp/src/agents_remember/__pycache__/x.pyc": "",
            "mcp/src/agents_remember/controllers/__pycache__/deleted.cpython-311.pyc": "",
            "mcp/src/agents_remember/.hidden/__init__.py": "",
        },
    )
    report = layering.check_layering(root)
    assert report.ok, layering.render(report)


def test_undeclared_package_import_fails(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write_tree(
        root,
        {
            "layers.toml": _make_contract_toml(),
            "mcp/src/agents_remember/__init__.py": "",
            "mcp/src/agents_remember/errors.py": "",
            "mcp/src/agents_remember/kernel/__init__.py": "",
            "mcp/src/agents_remember/kernel/a.py": ("from agents_remember import rogue\n"),
            "mcp/src/agents_remember/models/__init__.py": "",
            "mcp/src/agents_remember/serving/__init__.py": "",
        },
    )
    report = layering.check_layering(root)
    assert not report.ok
    assert len(report.undeclared_imports) == 1
    statement = report.undeclared_imports[0]
    assert statement.importer == "kernel"
    assert statement.imported == "rogue"
    assert statement.module == "agents_remember.rogue"
    assert "undeclared package import" in layering.render(report)


def test_from_agents_remember_import_star_is_skipped(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write_tree(
        root,
        {
            "layers.toml": _make_contract_toml(),
            "mcp/src/agents_remember/__init__.py": "",
            "mcp/src/agents_remember/errors.py": "",
            "mcp/src/agents_remember/kernel/__init__.py": "",
            "mcp/src/agents_remember/kernel/a.py": ("from agents_remember import *\n"),
            "mcp/src/agents_remember/models/__init__.py": "",
            "mcp/src/agents_remember/serving/__init__.py": "",
        },
    )
    report = layering.check_layering(root)
    assert report.ok, layering.render(report)


def test_from_agents_remember_import_self_is_skipped(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write_tree(
        root,
        {
            "layers.toml": _make_contract_toml(),
            "mcp/src/agents_remember/__init__.py": "",
            "mcp/src/agents_remember/errors.py": "",
            "mcp/src/agents_remember/kernel/__init__.py": "",
            "mcp/src/agents_remember/kernel/a.py": ("from agents_remember import kernel\n"),
            "mcp/src/agents_remember/models/__init__.py": "",
            "mcp/src/agents_remember/serving/__init__.py": "",
        },
    )
    report = layering.check_layering(root)
    assert report.ok, layering.render(report)


def test_from_agents_remember_import_present_false_is_skipped(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    layers_toml = (
        _make_contract_toml()
        + """
[package.future]
path = "future/"
present = false
"""
    )
    layers_toml = layers_toml.replace(
        'order = ["errors", "kernel", "models", "serving"]',
        'order = ["errors", "kernel", "models", "serving", "future"]',
    )
    _write_tree(
        root,
        {
            "layers.toml": layers_toml,
            "mcp/src/agents_remember/__init__.py": "",
            "mcp/src/agents_remember/errors.py": "",
            "mcp/src/agents_remember/kernel/__init__.py": "",
            "mcp/src/agents_remember/kernel/a.py": ("from agents_remember import future\n"),
            "mcp/src/agents_remember/models/__init__.py": "",
            "mcp/src/agents_remember/serving/__init__.py": "",
        },
    )
    report = layering.check_layering(root)
    assert report.ok, layering.render(report)


def test_from_agents_remember_import_declared_package_is_rank_checked(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    _write_tree(
        root,
        {
            "layers.toml": _make_contract_toml(),
            "mcp/src/agents_remember/__init__.py": "",
            "mcp/src/agents_remember/errors.py": "",
            "mcp/src/agents_remember/kernel/__init__.py": "",
            "mcp/src/agents_remember/kernel/core.py": ("from agents_remember import models\n"),
            "mcp/src/agents_remember/models/__init__.py": "",
            "mcp/src/agents_remember/models/gadget.py": "",
            "mcp/src/agents_remember/serving/__init__.py": "",
        },
    )
    report = layering.check_layering(root)
    assert not report.ok
    assert report.undeclared_imports == []
    assert any(
        violation.importer == "kernel" and violation.imported == "models"
        for violation in report.violations
    )


def test_present_false_packages_are_skipped(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write_tree(
        root,
        {
            "layers.toml": """
[contract]
order = ["errors", "future"]

[package.errors]
path = "."
root_modules = ["errors.py", "__init__.py"]
present = true

[package.future]
path = "future/"
present = false
arrives_in = "260731-EFA-L99"
""",
            "mcp/src/agents_remember/__init__.py": "",
            "mcp/src/agents_remember/errors.py": "",
        },
    )
    report = layering.check_layering(root)
    assert report.ok, layering.render(report)


def test_stale_present_flag_fails(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write_tree(
        root,
        {
            "layers.toml": """
[contract]
order = ["errors", "future"]

[package.errors]
path = "."
root_modules = ["errors.py", "__init__.py"]
present = true

[package.future]
path = "future/"
present = false
arrives_in = "260731-EFA-L99"
""",
            "mcp/src/agents_remember/__init__.py": "",
            "mcp/src/agents_remember/errors.py": "",
        },
    )
    monkeypatch.setattr(layering, "_leaf_landed", lambda leaf: leaf == "260731-EFA-L99")
    report = layering.check_layering(root)
    assert not report.ok
    assert report.stale_present_flags == [("future", "260731-EFA-L99")]


def test_wrapper_step_is_registered() -> None:
    check = importlib.import_module("agents_remember.code_quality.check")
    scope = SimpleNamespace(
        coverage_paths=[],
        test_paths=[],
        lint_paths=[],
        type_paths=[],
        size_paths=[],
        coverage_root_modules=[],
    )
    config = check.CheckConfig(
        project_root=Path("."),
        scope=scope,
        admission=QUALITY_TEST_ADMISSION,
        coverage_json=None,
        threshold=20.0,
        top=10,
    )
    steps = check.quality_steps(config, Path("/tmp/coverage.json"))
    assert any(step.name == "layering" for step in steps)
