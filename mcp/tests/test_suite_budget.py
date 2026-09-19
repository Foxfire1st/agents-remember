"""Collection budgets count parametrized items without spawning another collection."""

from __future__ import annotations

import ast
import tomllib
from types import SimpleNamespace
from typing import cast

import conftest
import pytest


class _Item:
    def __init__(self, integration: bool) -> None:
        self.integration = integration

    def get_closest_marker(self, name: str) -> bool | None:
        assert name == "integration"
        return True if self.integration else None


@pytest.mark.parametrize(
    ("units", "integrations", "exceeded"),
    [(1000, 250, None), (1001, 0, "unit"), (0, 251, "integration")],
)
def test_selected_case_budgets(units: int, integrations: int, exceeded: str | None) -> None:
    config = SimpleNamespace(
        getini={"unit_case_budget": 1000, "integration_case_budget": 250}.__getitem__
    )
    session = cast(
        pytest.Session,
        SimpleNamespace(items=[_Item(False)] * units + [_Item(True)] * integrations, config=config),
    )
    if exceeded:
        with pytest.raises(pytest.UsageError, match=rf"{exceeded} suite.*explicit"):
            conftest.pytest_collection_finish(session)
    else:
        conftest.pytest_collection_finish(session)


def test_the_effective_budgets_are_the_repository_rails(pytestconfig: pytest.Config) -> None:
    """The enforced inifile, the live configuration and the root declaration are one rail.

    ``mcp/tests/conftest.py`` only REGISTERS these two names. The numbers live once, in the
    repository-root ``pyproject.toml``, because ``mcp/pyproject.toml`` declares no
    ``[tool.pytest.ini_options]`` and pytest therefore walks up to the root file. This case reads
    the value the RUNNING configuration enforces rather than the text of either file, so it is red
    when the root file stops being the inifile, when a declaration is renamed out from under the
    enforcement, or when the two files state different rails (D-20; the code-side twin of the
    memory layer's stale 1000/150 line).
    """

    declared = tomllib.loads((conftest.REPOSITORY_ROOT / "pyproject.toml").read_text("utf-8"))[
        "tool"
    ]["pytest"]["ini_options"]
    assert pytestconfig.inipath == conftest.REPOSITORY_ROOT / "pyproject.toml"
    assert pytestconfig.getini("unit_case_budget") == declared["unit_case_budget"]
    assert pytestconfig.getini("integration_case_budget") == declared["integration_case_budget"]


def test_the_option_declarations_state_no_budget_of_their_own() -> None:
    """Neither declaration may carry a ``default=`` number of its own.

    A ``default=`` here is never in effect -- the root ini value always wins -- so it can only
    mislead: a terminal reader who finds ``default=1100`` concludes the unit rail is 1100 while the
    tree is judged against 2300. Red the moment a dead number returns to either declaration.
    """

    tree = ast.parse((conftest.REPOSITORY_ROOT / "mcp/tests/conftest.py").read_text("utf-8"))
    declarations = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "addini"
    ]
    assert {
        node.args[0].value
        for node in declarations
        if isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)
    } == {
        "unit_case_budget",
        "integration_case_budget",
    }
    assert [
        keyword.arg
        for node in declarations
        for keyword in node.keywords
        if keyword.arg == "default"
    ] == []


def test_an_absent_rail_refuses_by_name_instead_of_running_unbounded() -> None:
    """A declaration with no number in either place fails CLOSED with its own message.

    With the dead defaults removed, an absent root declaration resolves to the int type's own
    default of ``0``, and the existing ``budget < 1`` guard refuses the collection. This case states
    that consequence, so removing the defaults can never silently mean "no ceiling": red if the
    guard is dropped, if the refusal stops naming the budget, or if a future ``default=`` makes an
    absent rail read as a permissive one.
    """

    config = SimpleNamespace(
        getini={"unit_case_budget": 0, "integration_case_budget": 400}.__getitem__
    )
    session = cast(pytest.Session, SimpleNamespace(items=[_Item(False)], config=config))
    with pytest.raises(pytest.UsageError, match=r"unit suite has 1 cases; budget is 0"):
        conftest.pytest_collection_finish(session)
