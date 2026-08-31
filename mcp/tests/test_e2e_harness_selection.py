from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from agents_remember_test_support.testing.dependency_facts import RepositoryDependencyFacts

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
HARNESS_ROOT = Path("scripts/e2e_harness")


def _selection_module() -> ModuleType:
    path = REPOSITORY_ROOT / HARNESS_ROOT / "selection.py"
    spec = importlib.util.spec_from_file_location("ar_e2e_selection", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _direct_repository_dependencies(facts: RepositoryDependencyFacts) -> set[Path]:
    by_module = {module: path for path, module in facts.modules.items()}
    dependencies: set[Path] = set()
    for harness in facts.python_paths:
        if harness.parent != HARNESS_ROOT:
            continue
        imported = facts.imports[harness]
        resolved = {module: by_module[module] for module in imported if module in by_module}
        dependencies.update(
            path
            for module, path in resolved.items()
            if not any(other != module and other.startswith(f"{module}.") for other in resolved)
        )
    return dependencies


def test_targeted_selection_covers_every_direct_repository_dependency() -> None:
    selection = _selection_module()
    facts = RepositoryDependencyFacts.build(REPOSITORY_ROOT)
    dependencies = _direct_repository_dependencies(facts)
    unselected = sorted(
        path.as_posix() for path in dependencies if not selection.selected_paths((path.as_posix(),))
    )

    assert unselected == [], (
        "ambient-role E2E directly imports repository inputs outside its targeted selector: "
        f"{unselected}"
    )
