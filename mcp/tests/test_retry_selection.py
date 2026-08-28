from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest import mock

import pytest
from agents_remember_test_support.testing import retry_selection


def test_retry_selection_keeps_only_explicit_affected_modules(tmp_path: Path) -> None:
    selected_path = tmp_path / "mcp/tests/test_selected.py"
    other_path = tmp_path / "mcp/tests/test_other.py"
    selected_path.parent.mkdir(parents=True)
    selected_path.touch()
    other_path.touch()
    selected = SimpleNamespace(path=selected_path, nodeid="selected")
    other = SimpleNamespace(path=other_path, nodeid="other")
    items = cast(list[pytest.Item], [selected, other])
    hook = mock.Mock()
    config = mock.Mock(rootpath=tmp_path, hook=hook)
    config.getoption.return_value = ["mcp/tests/test_selected.py"]

    retry_selection.pytest_collection_modifyitems(config, items)

    assert items == [selected]
    hook.pytest_deselected.assert_called_once_with(items=[other])


def test_retry_selection_accepts_successfully_collected_zero_body_modules(
    tmp_path: Path,
) -> None:
    zero_body_path = tmp_path / "mcp/tests/test_shared_definitions.py"
    other_path = tmp_path / "mcp/tests/test_other.py"
    zero_body_path.parent.mkdir(parents=True)
    zero_body_path.touch()
    other_path.touch()
    other = SimpleNamespace(path=other_path, nodeid="other")
    items = cast(list[pytest.Item], [other])
    hook = mock.Mock()
    config = mock.Mock(rootpath=tmp_path, hook=hook)
    config.getoption.return_value = ["mcp/tests/test_shared_definitions.py"]
    retry_selection.pytest_configure(config)
    retry_selection.pytest_collectreport(
        cast(
            retry_selection._CollectReport,
            SimpleNamespace(passed=True, fspath=str(zero_body_path)),
        )
    )

    retry_selection.pytest_collection_modifyitems(config, items)

    assert items == []
    hook.pytest_deselected.assert_called_once_with(items=[other])


def test_retry_selection_rejects_missing_or_escaping_population(tmp_path: Path) -> None:
    config = mock.Mock(rootpath=tmp_path, hook=mock.Mock())
    config.getoption.return_value = []
    with pytest.raises(pytest.UsageError, match="one or more explicit test paths"):
        retry_selection.pytest_collection_modifyitems(config, [])

    config.getoption.return_value = ["../outside.py"]
    with pytest.raises(pytest.UsageError, match="candidate-relative"):
        retry_selection.pytest_collection_modifyitems(config, [])

    config.getoption.return_value = ["mcp/tests/test_missing.py"]
    retry_selection.pytest_configure(config)
    with pytest.raises(pytest.UsageError, match="were not collected as test modules"):
        retry_selection.pytest_collection_modifyitems(config, [])
