"""R10: every value a producer can emit validates at the wire boundary it crosses.

The failure this exists to make impossible is not a typo. It is a *set difference*: a
producer's vocabulary grows, the response model's hand-written copy does not, and nothing
notices until a real payload carries the new member -- at which point pydantic raises a
`ValidationError` inside an `@server.tool()` handler that has no `except` for one. Measured
before this suite existed: 165 of the 213 `series-contract.md` files on disk (77.5%) made
`context_packet` raise, across seven independent gaps.

WHAT DEFENDS WHAT
-----------------
Read this before trusting a test below, because the mechanisms are not interchangeable and
the AST scan is the weakest of the three. It reads *bare string literals*. It does not
evaluate expressions, so `"a" + "b"`, an f-string, `_MAP["x"]`, a name imported from another
module and a plain local variable all pass through it unseen. Any claim that the scan alone
keeps a vocabulary honest would be false, and was.

    vocabulary                                   defended by
    ------------------------------------------   ----------------------------------------
    the six contract cells                       pyright, at `ContractCells`' typed fields
      workflow_kind, memory_mode,                and `WorktreeContract`'s own. `Produced-
      human_review_status, closeout_status,      LiteralTests` supplies the invariant that
      integration_status, cleanup                makes that total:
                                                 NO `dataclasses.replace` call may carry
                                                 one of these keywords, and every value
                                                 written at a typed writer must be an
                                                 expression this scan can enumerate.
    phase / nextOperation / nextTool             pyright, at `LifecycleGuidance`'s TypedDict
                                                 and `next_guidance`'s typed parameters.
                                                 The scan measures the emitted set and
                                                 asserts it EQUALS the alias.
    the seven listed below                       pyright, at direct typed constructor calls
                                                 (`GitFacts(state=...)` and friends). The
                                                 scan asserts produced == declared, which
                                                 is a real measurement in the *other*
                                                 direction: a member no writer can emit.
    ContractTask.workflow_kind / .memory_mode    runtime, at `_task_vocabulary`. Deliberately
                                                 plain `str` -- it is what a caller asked
                                                 for, arriving from `worktree_start`'s MCP
                                                 signature, and there is no type to check.

`dataclasses.replace` is why the first row needs a second mechanism at all. typeshed types
it `**changes: Any`, so `replace(contract, cleanup="reclaimed-ish")` produced *zero* pyright
diagnostics against a four-member `Literal` -- one `Any` in a third-party stub voiding the
guarantee this whole module is about. `amend_contract(contract, ContractCells(...))` exists to
put those fields back in front of the checker; the no-`replace` rule below is what stops a
future edit from routing around it. `cast` still passes both, as it must: it is a programmer
overriding the checker on purpose, and the scan's readability rule is what refuses it here.

THE THREE RULES
---------------
Deliberately different in kind, so a fix that satisfies one cannot fake the others:

`GuidanceWalkTests`
    Drives every branch of the ``lifecycle_guidance`` state machine and every writable
    ``cleanup`` value, and crosses each result through ``WorktreeSummary``. Behavioural: it
    would catch a phase the machine emits from a branch nobody remembered.
`ProducedLiteralTests`
    Reads the *source* of the package: every literal written onto a contract vocabulary
    field or handed to ``next_guidance`` must validate at its wire field, every such write
    must be statically readable, and none of them may be spelled as a ``replace`` keyword.
`AdvertisedVocabularyTests`
    Holds the published input contract to the published output contract, in BOTH directions:
    the ``workflow_kind`` set ``worktree_start``'s docstring advertises must equal the alias
    (so a member no tool advertises and no producer writes cannot be added silently), a
    ``memory_mode`` the contract parser accepts must validate, and every status the session
    tools' own docstrings roster must validate in the response that reports them.

The same three rules cover seven more wire vocabularies, all of which were measured
*aligned* before they were typed (the produced set equalled the declared set exactly, in
every one). They are here because they had the identical construction -- a hand-written
`Literal` at a boundary fed by an untyped dict, over a vocabulary another module decides --
and so differed from the seven that broke `context_packet` only in not having failed yet:

    RepoSummary.state           <- kernel.git_facts.RepoState
    BranchFreshness.state       <- kernel.git_freshness.FreshnessState
    DriftCheckResponse.status   <- onboarding_drift_check.models.DriftStatus (the last copy)
    FileRead.status             <- controllers.read_files.FileReadStatus
    SpawnAgentSessionResponse   \\
    SessionRetireResponse        > models.terminal, imported BY mcp.tools.terminal
    SessionRenameResponse       /

Each is measured the same way: derive the producible set from the producers' source, assert
every member of it validates at the wire boundary, and assert the scan found the writers so
a scan that silently matched nothing cannot pass. Where the scan is exact the two sets are
asserted EQUAL, which also catches the other direction -- a declared member no producer can
ever emit, i.e. a vocabulary that has outgrown its own writer.

`ContractBoundaryTests` is the other half of the same guarantee, and the one place where
tolerance is the correct answer: what the *reader* does with a contract cell it cannot
classify, and what the *writer* refuses to put on disk.
"""

from __future__ import annotations

import ast
import re
import sys
import tempfile
import unittest
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any, cast, get_args

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

import agents_remember
from agents_remember.controllers.read_files import VALID_FILE_READ_STATUSES
from agents_remember.kernel.git_facts import VALID_REPO_STATES, git_facts_to_packet, read_git_facts
from agents_remember.kernel.git_freshness import (
    VALID_FRESHNESS_STATES,
    freshness_to_packet,
    read_branch_freshness,
)
from agents_remember.models.context_packet import BranchFreshness as WireBranchFreshness
from agents_remember.models.context_packet import MemorySummary, RepoSummary
from agents_remember.models.drift import DriftSummary
from agents_remember.models.memory import DriftCheckResponse
from agents_remember.models.read_files import FileRead
from agents_remember.models.terminal import (
    VALID_SESSION_RENAME_STATUSES,
    VALID_SESSION_RETIRE_STATUSES,
    VALID_SPAWN_AGENT_SESSION_STATUSES,
    SessionRenameResponse,
    SessionRetireResponse,
    SpawnAgentSessionResponse,
)
from agents_remember.models.worktree import WorktreeSummary
from agents_remember.worktrees.modules.guidance import (
    NextOperation,
    NextTool,
    WorktreePhase,
    lifecycle_guidance,
    recovery_guidance,
)
from agents_remember.worktrees.modules.leaf_ref_start import invalid_contract_request_result
from agents_remember.worktrees.status import worktree_status_packet
from agents_remember.worktrees.worktree_contract import (
    VALID_MEMORY_MODES,
    CleanupStatus,
    ContractCells,
    ContractError,
    ContractTask,
    RepoBranchPlan,
    WorkflowKind,
    WorktreeContract,
    amend_contract,
    default_series_contract,
    load_contract,
    write_contract,
)

PACKAGE_ROOT = Path(agents_remember.__file__).resolve().parent

# The guidance keys the packet projection actually copies onto the wire model. `summary` and
# `carryoverDoneAt` are not among them, and `WorktreeSummary` is strict, so filtering here is
# what the production projection does field by field.
GUIDANCE_WIRE_KEYS = ("phase", "nextOperation", "nextTool", "nextArgs", "nextRequiredArgs")

# Contract field -> the `WorktreeSummary` field that reports it.
CONTRACT_FIELD_TO_WIRE_FIELD = {
    "workflow_kind": "workflowKind",
    "memory_mode": "memoryMode",
    "human_review_status": "humanReviewStatus",
    "closeout_status": "closeoutStatus",
    "integration_status": "integrationStatus",
    "cleanup": "cleanup",
}
# The calls that build or amend a worktree contract. `replace(...)` is deliberately NOT here:
# it is the call this file now forbids at these fields (see `TYPED_CONTRACT_WRITERS`).
CONTRACT_CALLS = frozenset({"WorktreeContract", "ContractTask", "ContractCells"})
# ...and the subset whose fields carry the vocabulary as a type, so what reaches them has to be
# an expression this scan can read. `ContractTask` is excluded on purpose: its two vocabulary
# fields are plain `str` because they are a *request*, narrowed at runtime by
# `_task_vocabulary` and pinned by `ContractBoundaryTests`.
TYPED_CONTRACT_WRITERS = frozenset({"WorktreeContract", "ContractCells"})
# The module that DECLARES the vocabularies is the one place a contract cell is legitimately
# written from a runtime-narrowed variable -- `_vocabulary_cell`'s result and
# `_task_vocabulary`'s pair are exactly that narrowing. Everywhere else, a value that is not
# a readable expression is a value nothing checked.
VOCABULARY_DECLARING_MODULE = "worktrees/worktree_contract.py"


def worktree_summary_accepts(field: str, value: object) -> bool:
    return _accepts(WorktreeSummary, {"state": "active"}, field, value)


def _accepts(model: Any, base: dict[str, Any], field: str, value: object) -> bool:
    try:
        model.model_validate({**base, field: value})
    except Exception:
        return False
    return True


def _contract(root: Path, **overrides: Any) -> WorktreeContract:
    """A contract with only the fields the guidance machine reads, plus the overrides."""
    base = WorktreeContract(
        task_id="T",
        task_name="t",
        repo_name="r",
        workflow_kind="light-task",
        memory_mode="internal",
        coordination_root=root,
        task_root=root / "tasks",
        contract_path=root / "tasks" / "series-contract.md",
        task_artifact=root / "tasks" / "task.md",
        worktree_group=root / "wt",
        code_repo_path=root / "repo",
        code_source_branch="main",
        code_work_branch="ar/t",
        code_base_commit="abc1234",
        code_worktree=root / "wt" / "code",
        leaf_id="l",
    )
    return replace(base, **overrides)


def cross_the_wire(contract: WorktreeContract) -> WorktreeSummary:
    """Run the guidance machine and project the result onto the wire model, as production does."""
    guidance = lifecycle_guidance(contract)
    packet: dict[str, Any] = {
        "state": "active",
        "workflowKind": contract.workflow_kind,
        "memoryMode": contract.memory_mode,
        "humanReviewStatus": contract.human_review_status,
        "closeoutStatus": contract.closeout_status,
        "integrationStatus": contract.integration_status,
        "cleanup": contract.cleanup,
    }
    # `.get` rather than indexing: `nextTool`/`nextArgs`/`nextRequiredArgs` are optional on
    # the guidance shape by design, which is the whole point of the omission this file guards.
    packet.update({key: guidance.get(key) for key in GUIDANCE_WIRE_KEYS if key in guidance})
    return WorktreeSummary.model_validate(packet)


class GuidanceWalkTests(unittest.TestCase):
    """Every branch of the state machine, and every writable cleanup value, on the wire."""

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def _phases(self) -> list[tuple[str, WorktreeContract]]:
        """One contract per `lifecycle_guidance` branch, in the order the machine tries them.

        The carryover pair is driven by `carryover_done`'s own inputs rather than a patch:
        external memory whose ledger is not on disk has not carried, internal memory has
        nothing to carry and is reported done.
        """
        return [
            ("cleanup-completed", _contract(self.root, cleanup="completed")),
            ("abandoned", _contract(self.root, cleanup="abandoned")),
            ("integration-blocked", _contract(self.root, integration_status="blocked")),
            (
                "carryover-pending",
                _contract(
                    self.root,
                    integration_status="completed",
                    memory_mode="external",
                    memory_repo_path=self.root / "memory",
                    memory_worktree=self.root / "wt" / "memory",
                    code_commit="def5678",
                ),
            ),
            ("cleanup-pending", _contract(self.root, integration_status="completed")),
            ("integration-pending", _contract(self.root, closeout_status="completed")),
            ("closeout-pending", _contract(self.root, approved_for_commit=True)),
            ("worktree-started", _contract(self.root)),
        ]

    def test_every_lifecycle_phase_validates_at_the_wire_boundary(self) -> None:
        for expected_phase, contract in self._phases():
            with self.subTest(phase=expected_phase):
                summary = cross_the_wire(contract)
                self.assertEqual(summary.phase, expected_phase)

    def test_every_lifecycle_next_move_validates_at_the_wire_boundary(self) -> None:
        seen_operations: set[str | None] = set()
        seen_tools: set[str | None] = set()
        for expected_phase, contract in self._phases():
            with self.subTest(phase=expected_phase):
                summary = cross_the_wire(contract)
                seen_operations.add(summary.nextOperation)
                seen_tools.add(summary.nextTool)
        # The gaps that were measured: the carryover route was writable and unrepresentable.
        self.assertIn("request_carryover_decision", seen_operations)
        self.assertIn("memory_carryover_apply", seen_tools)

    def test_a_done_phase_omits_next_tool_rather_than_inventing_one(self) -> None:
        """`next_guidance` omits `nextTool` when nothing is to be called; so must the wire."""
        summary = cross_the_wire(_contract(self.root, cleanup="completed"))
        self.assertIsNone(summary.nextTool)
        self.assertNotIn("nextTool", summary.model_dump(mode="json", exclude_none=True))

    def test_every_writable_cleanup_value_validates_at_the_wire_boundary(self) -> None:
        for value in produced_literals()["cleanup"] | {"pending"}:
            with self.subTest(cleanup=value):
                summary = cross_the_wire(_contract(self.root, cleanup=value))
                self.assertEqual(summary.cleanup, value)


def _module_trees() -> Iterator[tuple[Path, ast.Module]]:
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        yield path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    return func.attr if isinstance(func, ast.Attribute) else ""


def _string(node: ast.expr | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def produced_literals() -> dict[str, set[str]]:
    """Every vocabulary literal the package writes, keyed by the contract field it is written to.

    Derived from the producers' own source rather than from a Literal alias, so this stays a
    real measurement of what is writable: re-introducing a hand-copied enum somewhere cannot
    make it agree with itself.

    Bare literals only -- that is the whole reach of this function, and the reason two more
    scans stand beside it. A vocabulary defended by this alone would not be defended.
    """
    found: dict[str, set[str]] = {field: set() for field in CONTRACT_FIELD_TO_WIRE_FIELD}
    for _, tree in _module_trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _call_name(node) in CONTRACT_CALLS:
                _collect_contract_keywords(node, found)
    return found


def _collect_contract_keywords(node: ast.Call, found: dict[str, set[str]]) -> None:
    for keyword in node.keywords:
        if keyword.arg in found:
            found[keyword.arg] |= _value_literals(keyword.value)


def contract_cells_written_through_replace() -> list[str]:
    """Every ``replace(...)`` call in the package that carries a contract vocabulary keyword.

    Must be empty, and that is the invariant that makes pyright's coverage of these six fields
    total. ``dataclasses.replace`` is ``**changes: Any`` in typeshed, so a value handed to it
    is checked by nothing at all -- measured, a bare off-vocabulary literal at every one of the
    six produced no diagnostic. Routing them through ``amend_contract``'s typed ``ContractCells``
    record is what restores the check; this refuses the spelling that skips it.

    Matches on the keyword name, so a future ``replace`` on some *other* dataclass that happens
    to own a ``cleanup`` field would be reported here too. That is the intended trade: such a
    call is worth a look, and the alternative -- guessing which dataclass an expression is --
    is the kind of inference that makes a scan lie.

    The declaring module is exempt because ``amend_contract`` IS the one sanctioned ``replace``:
    its own parameters did the checking one line earlier.
    """
    offenders: list[str] = []
    for path, tree in _module_trees():
        if path.as_posix().endswith(VOCABULARY_DECLARING_MODULE):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _call_name(node) != "replace":
                continue
            for keyword in node.keywords:
                if keyword.arg in CONTRACT_FIELD_TO_WIRE_FIELD:
                    offenders.append(f"{path.name}:{node.lineno} replace(..., {keyword.arg}=...)")
    return offenders


def _readable_expression(node: ast.expr) -> bool:
    """True when this scan can enumerate the strings the expression evaluates to.

    A bare literal, a conditional between two readable branches, or an attribute read (which is
    how a lifecycle write says "leave this cell as it was": ``contract.integration_status``).
    Everything else -- a concatenation, an f-string, a dict subscript, a name bound elsewhere,
    a ``cast`` -- is a value whose membership this file cannot show, and the point of saying so
    is that it is also the shape every escape from a literal scan takes.
    """
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str)
    if isinstance(node, ast.IfExp):
        return _readable_expression(node.body) and _readable_expression(node.orelse)
    return isinstance(node, ast.Attribute)


def unreadable_contract_writes() -> list[str]:
    """Contract-vocabulary keywords at a typed writer whose value this scan cannot read."""
    offenders: list[str] = []
    for path, tree in _module_trees():
        if path.as_posix().endswith(VOCABULARY_DECLARING_MODULE):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _call_name(node) not in TYPED_CONTRACT_WRITERS:
                continue
            for keyword in node.keywords:
                if keyword.arg in CONTRACT_FIELD_TO_WIRE_FIELD and not _readable_expression(
                    keyword.value
                ):
                    offenders.append(f"{path.name}:{node.lineno} {keyword.arg}=")
    return offenders


def guidance_next_moves() -> tuple[set[str], set[str]]:
    """Every `(operation, tool)` literal handed to `next_guidance`, anywhere in the package.

    `next_guidance` is the phase machine's builder and its output reaches `WorktreeSummary`
    through `worktrees.status`, so every literal at every call site has to validate there.
    The gate/block payloads use `recovery_guidance` instead -- a separate builder with its own
    vocabulary, because those results are `FlexibleToolResponse`s that never reach this model.
    """
    operations: set[str] = set()
    tools: set[str] = set()
    for _, tree in _module_trees():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _call_name(node) != "next_guidance":
                continue
            operations.update(filter(None, (_string(arg) for arg in node.args)))
            tools.update(
                filter(None, (_string(kw.value) for kw in node.keywords if kw.arg == "tool"))
            )
    return operations, tools


def _dict_literal_values(tree: ast.Module, key: str) -> set[str]:
    """``key``'s literal values in every dict this module builds -- both spellings.

    ``{"phase": "x"}`` is an ``ast.Dict``; ``dict(phase="x")`` is an ``ast.Call`` and is the
    same write. Reading only the first left a producer shape invisible to this scan, which
    matters here even though pyright rejects the second against ``LifecycleGuidance``'s
    TypedDict return -- a scan that reports a set is only useful if the set is the real one.
    """
    values: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _call_name(node) == "dict":
            values |= {
                literal
                for keyword in node.keywords
                if keyword.arg == key and (literal := _string(keyword.value)) is not None
            }
            continue
        if not isinstance(node, ast.Dict):
            continue
        for dict_key, dict_value in zip(node.keys, node.values, strict=True):
            literal = _string(dict_value)
            if _string(dict_key) == key and literal is not None:
                values.add(literal)
    return values


def _module_tree(relative: str) -> ast.Module:
    path = PACKAGE_ROOT / relative
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


# --- the other seven: producers read out of their own source ---------------------------------

TERMINAL_TOOL = "mcp/tools/terminal.py"
# Enough of each strict model to leave only the field under test undecided.
REPO_BASE = {"id": "r", "root": "/r", "branch": "main", "head": "0" * 40, "dirty": False}
SESSION_BASE = {"ok": False, "session": "s"}


def _value_literals(node: ast.expr) -> set[str]:
    """Only the strings an expression can EVALUATE to -- never a comparison operand.

    ``self.status = "leaf-ref-ambiguous" if reason == "ambiguous" else "leaf-ref-not-found"``
    holds three string constants and can produce two of them. A plain ``ast.walk`` reports
    ``"ambiguous"`` as a producible status and then fails on a bug that is not there, which is
    the one way a scan like this lies in the expensive direction.
    """
    literal = _string(node)
    if literal is not None:
        return {literal}
    if isinstance(node, ast.IfExp):
        return _value_literals(node.body) | _value_literals(node.orelse)
    return set()


def _targets_name(target: ast.expr, name: str) -> bool:
    if isinstance(target, ast.Name):
        return target.id == name
    return isinstance(target, ast.Attribute) and target.attr == name


def _assigned_literals(tree: ast.Module, name: str) -> set[str]:
    """Literals assigned to a local or attribute called ``name``, anywhere in the module."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets, value = list(node.targets), node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if any(_targets_name(target, name) for target in targets):
            found |= _value_literals(value)
    return found


def _dataclass_field_index(tree: ast.Module, cls: str, field: str) -> int:
    """``field``'s positional index in ``cls``, read off the class body rather than assumed."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != cls:
            continue
        names = [
            entry.target.id
            for entry in node.body
            if isinstance(entry, ast.AnnAssign) and isinstance(entry.target, ast.Name)
        ]
        return names.index(field)
    raise AssertionError(f"{cls} is not declared in this module")


def _dataclass_field_writes(relative: str, cls: str, field: str) -> set[str]:
    """Every literal this module can put in ``cls(...).field``.

    Both spellings its producers use: the constant handed straight to the constructor (by
    position or by keyword), and the one that arrives through a local of the same name -- which
    is how both git readers pass the state they computed from a branch comparison.
    """
    tree = _module_tree(relative)
    index = _dataclass_field_index(tree, cls, field)
    found = _assigned_literals(tree, field)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _call_name(node) != cls:
            continue
        if len(node.args) > index:
            found |= _value_literals(node.args[index])
        for keyword in node.keywords:
            if keyword.arg == field:
                found |= _value_literals(keyword.value)
    return found


def _returned_tuple_literals(relative: str, function: str, index: int) -> set[str]:
    """Every literal ``function`` returns at position ``index`` of its result tuple."""
    found: set[str] = set()
    for node in ast.walk(_module_tree(relative)):
        if not isinstance(node, ast.FunctionDef) or node.name != function:
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Return) and isinstance(inner.value, ast.Tuple):
                found |= _value_literals(inner.value.elts[index])
    return found


def _payload_statuses(relative: str, operation: str) -> set[str]:
    """``"status": <literal>`` from every payload dict that also declares ``operation``.

    Keying on the operation is what keeps three tools' statuses apart inside the one module
    that writes all three.
    """
    found: set[str] = set()
    for node in ast.walk(_module_tree(relative)):
        if not isinstance(node, ast.Dict):
            continue
        entries = {_string(key): value for key, value in zip(node.keys, node.values, strict=True)}
        if _string(entries.get("operation")) != operation:
            continue
        status = entries.get("status")
        if status is not None:
            found |= _value_literals(status)
    return found


def _table_row_labels(function: ast.FunctionDef) -> set[str]:
    """The first cell of every literal row in a table this function builds."""
    found: set[str] = set()
    for node in ast.walk(function):
        if isinstance(node, ast.Tuple) and node.elts:
            found |= _value_literals(node.elts[0])
    return found


def _builder_statuses(relative: str, builder: str) -> set[str]:
    """Every status that can reach ``builder``'s first parameter.

    Directly at a call site, or -- when a function hands over a variable instead -- through the
    first cell of the table that function walks. ``_knob_refusal`` is the second shape: it
    iterates ``(status, detail)`` rows and refuses on the first detail that is not None, so a
    scan that only read call-site constants would miss two of the thirteen spawn statuses.
    """
    found: set[str] = set()
    for function in ast.walk(_module_tree(relative)):
        if not isinstance(function, ast.FunctionDef):
            continue
        indirect = False
        for node in ast.walk(function):
            if not isinstance(node, ast.Call) or _call_name(node) != builder or not node.args:
                continue
            literals = _value_literals(node.args[0])
            indirect = indirect or not literals
            found |= literals
        if indirect:
            found |= _table_row_labels(function)
    return found


def _model_vocabulary(model: Any) -> set[str]:
    return set(get_args(model.model_fields["status"].annotation))


# A status as a tool docstring writes one: quoted or backticked, lower-case, hyphens allowed.
_ADVERTISED_STATUS = re.compile(r"[`']([a-z][a-z0-9]*(?:-[a-z0-9]+)*)[`']")


def _advertised_statuses(tool: str) -> set[str]:
    """The statuses a session tool's own docstring promises its callers.

    Two places carry them, and both are the published tool description an agent reads before
    it calls: the closing ``Status ...`` roster, and any inline ``status 'x'`` mention in the
    prose above it. Nothing else in the docstring counts -- the backticked ``dispatch-brief``
    in ``spawn_agent_session`` is a message kind, and reading it as a status would make this
    assert the existence of one that was never meant to exist.
    """
    for node in ast.walk(_module_tree("mcp/registration/sessions.py")):
        if not isinstance(node, ast.FunctionDef) or node.name != tool:
            continue
        doc = ast.get_docstring(node) or ""
        # "Status" rather than "Status ": `session_retire` wraps the line straight after it.
        roster = doc[doc.rindex("Status") :] if "Status" in doc else ""
        inline = re.findall(r"status '([a-z][a-z0-9-]*)'", doc)
        return set(_ADVERTISED_STATUS.findall(roster)) | set(inline)
    raise AssertionError(f"{tool} tool declaration not found")


class ProducedLiteralTests(unittest.TestCase):
    """The set difference itself: what the source writes vs. what the wire accepts."""

    def test_every_contract_literal_validates_at_its_wire_field(self) -> None:
        produced = produced_literals()
        for contract_field, values in sorted(produced.items()):
            wire_field = CONTRACT_FIELD_TO_WIRE_FIELD[contract_field]
            for value in sorted(values):
                with self.subTest(field=contract_field, value=value):
                    self.assertTrue(
                        worktree_summary_accepts(wire_field, value),
                        f"{contract_field}={value!r} is written by the package but "
                        f"WorktreeSummary.{wire_field} rejects it",
                    )

    def test_the_scan_actually_found_the_writers(self) -> None:
        """A scan that silently matches nothing would pass every assertion above."""
        produced = produced_literals()
        self.assertIn("reopened", produced["cleanup"])
        self.assertIn("abandoned", produced["cleanup"])
        self.assertIn("completed", produced["closeout_status"])
        self.assertIn("blocked", produced["integration_status"])
        self.assertIn("approved", produced["human_review_status"])

    def test_no_contract_cell_is_written_through_dataclasses_replace(self) -> None:
        """The rule that makes pyright's coverage of these six fields total.

        `replace(contract, cleanup="reclaimed-ish")` is zero pyright errors -- typeshed types
        `replace` as `**changes: Any`, and one `Any` in a stub is enough to void the guarantee
        the aliases exist for. `ContractCells` declares all six as typed fields, so pyright
        checks them again; this refuses the spelling that would go around it.
        """
        self.assertEqual(contract_cells_written_through_replace(), [])

    def test_the_typed_writer_moves_only_the_cells_it_is_handed(self) -> None:
        """`amend_contract` is a `replace` with the vocabularies restored to the signature.

        It must therefore behave like one: an omitted cell keeps its value, a given one moves,
        and nothing else on the contract is touched.
        """
        with tempfile.TemporaryDirectory() as tmp:
            before = _contract(Path(tmp), closeout_status="completed", code_commit="def5678")
        after = amend_contract(before, ContractCells(cleanup="abandoned"))
        self.assertEqual(after.cleanup, "abandoned")
        self.assertEqual(after.closeout_status, "completed")  # untouched
        self.assertEqual(after.code_commit, "def5678")  # and so is everything else
        self.assertEqual(replace(after, cleanup=before.cleanup), before)

    def test_every_contract_cell_is_written_as_something_this_scan_can_read(self) -> None:
        """The rule that closes what pyright cannot: `cast`, and any future untyped writer.

        `cast(CleanupStatus, raw)` satisfies a type checker by construction -- it IS the
        override. At these six fields there is no legitimate use for it outside the module
        that declares the vocabularies, and the same rule refuses every other shape an
        off-vocabulary value arrives in: `"a" + "b"`, an f-string, `_MAP["x"]`, a name
        imported from another module, a local computed elsewhere.
        """
        self.assertEqual(unreadable_contract_writes(), [])

    def test_every_next_guidance_literal_validates_at_its_wire_field(self) -> None:
        """Set EQUALITY, both directions: nothing unreportable is emitted, nothing declared is
        unreachable. `next_guidance`'s parameters are typed, so pyright is what rejects a bad
        literal at a call site; this is the measurement that the alias has not outgrown the
        state machine that fills it."""
        operations, tools = guidance_next_moves()
        self.assertIn("request_carryover_decision", operations)
        self.assertIn("memory_carryover_apply", tools)
        self.assertEqual(operations, set(get_args(NextOperation)))
        self.assertEqual(tools, set(get_args(NextTool)))
        for operation in sorted(operations):
            with self.subTest(nextOperation=operation):
                self.assertTrue(worktree_summary_accepts("nextOperation", operation))
        for tool in sorted(tools):
            with self.subTest(nextTool=tool):
                self.assertTrue(worktree_summary_accepts("nextTool", tool))

    def test_every_phase_the_guidance_module_writes_validates(self) -> None:
        phases = _dict_literal_values(_module_tree("worktrees/modules/guidance.py"), "phase")
        self.assertIn("carryover-pending", phases)
        self.assertIn("abandoned", phases)
        # Equality, so a phase the alias declares but no branch of the machine can reach fails
        # here -- the same direction `AdvertisedVocabularyTests` now holds `WorkflowKind` in.
        self.assertEqual(phases, set(get_args(WorktreePhase)))
        for phase in sorted(phases):
            with self.subTest(phase=phase):
                self.assertTrue(worktree_summary_accepts("phase", phase))

    def test_every_status_the_drift_summary_writes_validates(self) -> None:
        statuses = _dict_literal_values(
            _module_tree("memory_quality/integrity/onboarding_drift_check/summary.py"), "status"
        )
        self.assertIn("error", statuses)
        for status in sorted(statuses):
            with self.subTest(status=status):
                self.assertTrue(_accepts(DriftSummary, {}, "status", status))

    def test_the_drift_error_diagnostic_survives_its_own_boundary(self) -> None:
        """The missing-onboarding-root packet: both the status *and* the key it carries."""
        summary = DriftSummary.model_validate(
            {"status": "error", "error": "onboarding root does not exist: /nope"}
        )
        self.assertEqual(summary.error, "onboarding root does not exist: /nope")

    # --- the seven with the same construction that had not drifted yet -----------------------
    #
    # Each asserts set EQUALITY between what the producers' source writes and what the alias
    # declares, which is stricter than the containment above: it also fails a vocabulary that
    # has outgrown its writer, i.e. a member no code path can ever put on the wire.

    def test_every_repo_state_the_git_facts_reader_writes_validates(self) -> None:
        produced = _dataclass_field_writes("kernel/git_facts.py", "GitFacts", "state")
        # `detached` exists only on the branch-less path and `unavailable` only on the four
        # degrade paths; neither is reachable from a healthy repository, which is exactly why
        # a hand-copied Literal at the packet would not have been measured against them.
        self.assertIn("detached", produced)
        self.assertIn("unavailable", produced)
        self.assertEqual(produced, set(VALID_REPO_STATES))
        for state in sorted(produced):
            with self.subTest(state=state):
                self.assertTrue(_accepts(RepoSummary, REPO_BASE, "state", state))

    def test_every_freshness_state_the_git_reader_writes_validates(self) -> None:
        produced = _dataclass_field_writes("kernel/git_freshness.py", "BranchFreshness", "state")
        # `diverged` is reachable only through the computed local, so a scan that read only
        # constructor arguments would report five of the eight and pass.
        self.assertIn("diverged", produced)
        self.assertIn("no-upstream", produced)
        self.assertEqual(produced, set(VALID_FRESHNESS_STATES))
        for state in sorted(produced):
            with self.subTest(state=state):
                self.assertTrue(_accepts(WireBranchFreshness, {"branch": "main"}, "state", state))

    def test_every_drift_status_validates_at_both_of_its_wire_models(self) -> None:
        """`DriftCheckStatus` is gone: one declaration now serves the packet and the tool."""
        statuses = _dict_literal_values(
            _module_tree("memory_quality/integrity/onboarding_drift_check/summary.py"), "status"
        )
        self.assertIn("error", statuses)
        for status in sorted(statuses):
            with self.subTest(status=status):
                self.assertTrue(_accepts(DriftSummary, {}, "status", status))
                self.assertTrue(_accepts(DriftCheckResponse, {"ok": True}, "status", status))

    def test_every_onboarding_status_the_read_controller_returns_validates(self) -> None:
        produced = _returned_tuple_literals("controllers/read_files.py", "_resolve_onboarding", 0)
        self.assertIn("not_requested", produced)
        self.assertIn("unsupported", produced)
        self.assertEqual(produced, set(VALID_FILE_READ_STATUSES))
        for status in sorted(produced):
            with self.subTest(status=status):
                self.assertTrue(_accepts(FileRead, {"path": "a.py"}, "status", status))

    def test_every_spawn_status_the_tool_can_return_validates(self) -> None:
        produced = (
            _builder_statuses(TERMINAL_TOOL, "_spawn_refusal")
            | _payload_statuses(TERMINAL_TOOL, "spawn_agent_session")
            # The two the tool never writes itself: `LeafRefResolutionError` decides them and
            # `leaf_ref_refusal_payload` copies the value onto the refusal verbatim.
            | _assigned_literals(_module_tree("worktrees/leaf_refs.py"), "status")
        )
        self.assertIn("model-invalid", produced)  # only through `_knob_refusal`'s table
        self.assertIn("spawned-unbriefed", produced)  # the success payload, not a refusal
        self.assertIn("leaf-taken", produced)  # the one refusal with its own payload shape
        self.assertIn("leaf-ref-ambiguous", produced)
        self.assertNotIn("ambiguous", produced)  # the comparison operand beside it
        self.assertEqual(produced, set(VALID_SPAWN_AGENT_SESSION_STATUSES))
        for status in sorted(produced):
            with self.subTest(status=status):
                self.assertTrue(_accepts(SpawnAgentSessionResponse, SESSION_BASE, "status", status))

    def test_every_retire_status_the_tool_writes_validates(self) -> None:
        produced = _builder_statuses(TERMINAL_TOOL, "_retire_payload")
        self.assertIn("unknown-actor", produced)
        self.assertIn("retire-refused", produced)
        self.assertEqual(produced, set(VALID_SESSION_RETIRE_STATUSES))
        for status in sorted(produced):
            with self.subTest(status=status):
                self.assertTrue(_accepts(SessionRetireResponse, SESSION_BASE, "status", status))

    def test_every_rename_status_the_tool_writes_validates(self) -> None:
        produced = _builder_statuses(TERMINAL_TOOL, "_rename_payload")
        self.assertIn("renamed", produced)
        self.assertEqual(produced, set(VALID_SESSION_RENAME_STATUSES))
        for status in sorted(produced):
            with self.subTest(status=status):
                self.assertTrue(_accepts(SessionRenameResponse, SESSION_BASE, "status", status))


class AdvertisedVocabularyTests(unittest.TestCase):
    """The published input contract, held to the published output contract."""

    def test_the_workflow_kinds_advertised_and_declared_are_the_same_set(self) -> None:
        """`worktree_start`'s own docstring names the task formats it accepts -- all of them.

        Asserted EQUAL, in both directions, which is the direction that was missing. Held only
        one way (advertised is a subset of declared), the alias could grow a member no tool
        advertises and no writer emits, and nothing would say so -- which is precisely what had
        happened: `WorkflowKind` carried a bare `chat` and `light` alongside `chat-task` and
        `light-task`, the un-reconciled union of the old hand-written copy and the new one.
        Zero occurrences across the 213 contracts on disk, no production writer, and absent
        from the docstring that is the published input contract. The session-status test below
        pins its own difference explicitly for the same reason; here there is none to pin.
        """
        advertised = _advertised_workflow_kinds()
        self.assertEqual(advertised, set(get_args(WorkflowKind)))
        self.assertEqual(advertised, {"light-task", "chat-task"})
        for kind in sorted(advertised):
            with self.subTest(workflowKind=kind):
                self.assertTrue(worktree_summary_accepts("workflowKind", kind))

    def test_every_status_the_session_tools_roster_validates(self) -> None:
        """A session tool's docstring IS its published contract; it must not over-promise.

        `session_retire` and `session_rename` roster every status they can return, so the
        docstring and the response enum are asserted equal. `spawn_agent_session` rosters ten
        of its thirteen plus one named inline, and the difference is pinned rather than
        tolerated silently: the two leaf-ref refusals are producible and undocumented.
        """
        for tool, model in (
            ("session_retire", SessionRetireResponse),
            ("session_rename", SessionRenameResponse),
        ):
            with self.subTest(tool=tool):
                self.assertEqual(_advertised_statuses(tool), _model_vocabulary(model))

        advertised = _advertised_statuses("spawn_agent_session")
        vocabulary = _model_vocabulary(SpawnAgentSessionResponse)
        self.assertNotIn("dispatch-brief", advertised)  # a message kind, not a status
        self.assertIn("leaf-taken", advertised)  # named inline, not in the closing roster
        self.assertEqual(vocabulary - advertised, {"leaf-ref-not-found", "leaf-ref-ambiguous"})
        for status in sorted(advertised):
            with self.subTest(status=status):
                self.assertTrue(_accepts(SpawnAgentSessionResponse, SESSION_BASE, "status", status))

    def test_every_memory_mode_the_contract_accepts_validates_on_both_fields(self) -> None:
        """One packet reports the mode twice; both copies must accept the same set."""
        for mode in sorted(VALID_MEMORY_MODES):
            with self.subTest(memoryMode=mode):
                self.assertTrue(worktree_summary_accepts("memoryMode", mode))
                self.assertTrue(
                    _accepts(
                        MemorySummary,
                        {"storage": {"mode": "internal", "default": "internal"}},
                        "mode",
                        mode,
                    )
                )


class RecoveryGuidanceTests(unittest.TestCase):
    """The other side of the vocabulary split, so the split cannot be undone by accident.

    The gate and block payloads emit the same four keys as the phase machine and must keep
    doing so -- the split is a type-level one, not a wire-level one. What they must *not* do
    is reach `WorktreeSummary`, whose vocabulary would otherwise have to grow to hold "the
    developer must approve this commit" and "the source branch is stale".
    """

    def test_it_emits_the_same_keys_in_the_same_order(self) -> None:
        payload = recovery_guidance(
            "request_commit_approval",
            tool="worktree_closeout_apply",
            args={"contract_path": "/x"},
            required_args=["intent_note"],
        )
        self.assertEqual(
            list(payload),
            ["nextOperation", "nextTool", "nextArgs", "nextRequiredArgs"],
        )

    def test_it_omits_required_args_when_there_are_none(self) -> None:
        payload = recovery_guidance(
            "choose_provider_setup_recovery",
            tool="worktree_start",
            args={"repo_id": "r"},
        )
        self.assertEqual(list(payload), ["nextOperation", "nextTool", "nextArgs"])

    def test_its_vocabulary_is_not_the_wire_vocabulary(self) -> None:
        for operation in ("request_commit_approval", "choose_stale_base_recovery"):
            with self.subTest(nextOperation=operation):
                self.assertFalse(worktree_summary_accepts("nextOperation", operation))
        for tool in ("worktree_start", "worktree_sync"):
            with self.subTest(nextTool=tool):
                self.assertFalse(worktree_summary_accepts("nextTool", tool))


class ContractBoundaryTests(unittest.TestCase):
    """The other half of the same guarantee: what the parser does with a cell it cannot classify.

    A vocabulary the wire model imports is only closed if nothing can smuggle a foreign value
    past the reader -- but "closed" is not the same as "refused". Refusing was tried and was
    worse than the problem: `load_contract` is the single entry point of `worktree_closeout_apply`,
    `worktree_integrate`, `worktree_cleanup`, `worktree_sync`, `worktree_abandon` and
    `worktree_status`, NONE of which catches `ContractError`, so one unreadable token in one
    cell took all six down at once and left the task unreachable. Measured on contracts that
    are legal on disk: a blanked `workflow_kind`, `cleanup: reclaimed-ish`, `closeout status:
    in-progress`, a `cleanup: Pending` with the wrong case.

    So the split is by direction. The READER substitutes the declared fallback, records the raw
    token, and reports it, because a lifecycle that can still be driven is worth more than a
    parse that is proud. The WRITER refuses, because nothing in this package should ever be the
    thing that put an unknown token on disk. A rewrite by any lifecycle tool then heals the
    file, which is the recovery path a developer actually has.
    """

    # One row per contract cell whose vocabulary the wire model imports: the front-matter line
    # as `write_contract` emits it, the edit that puts an off-vocabulary token there, and the
    # `(wire field, raw token, value it must be read as)` the packet has to report.
    #
    # `closeout` and `integration` both spell their cell `  status:`, so the closeout row edits
    # the FIRST occurrence (`_written_with_cell` replaces once) -- which is the closeout block,
    # because `contract_to_text` writes it first.
    OFF_VOCABULARY_CELLS = (
        (
            "workflow_kind: light-task",
            "workflow_kind: master-task",
            ("workflowKind", "master-task", "light-task"),
        ),
        ("memory_mode: internal", "memory_mode: hybrid", ("memoryMode", "hybrid", "internal")),
        (
            "  status: pending-review",
            "  status: awaiting",
            ("humanReviewStatus", "awaiting", "pending-review"),
        ),
        (
            "  status: not-started",
            "  status: in-progress",
            ("closeoutStatus", "in-progress", "not-started"),
        ),
        (
            "  cleanup: pending",
            "  cleanup: reclaimed-ish",
            ("cleanup", "reclaimed-ish", "pending"),
        ),
    )

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def _written(self, **overrides: Any) -> Path:
        contract = _contract(self.root, **overrides)
        write_contract(contract.contract_path, contract)
        return contract.contract_path

    def _written_with_cell(self, line: str, replacement: str) -> Path:
        path = self._written()
        path.write_text(
            path.read_text(encoding="utf-8").replace(line, replacement, 1), encoding="utf-8"
        )
        return path

    def test_an_unknown_cleanup_cell_degrades_and_names_itself(self) -> None:
        contract = load_contract(
            self._written_with_cell("  cleanup: pending", "  cleanup: reclaimed-ish")
        )
        self.assertEqual(contract.cleanup, "pending")
        self.assertEqual(contract.unknown_cells, ("cleanup='reclaimed-ish' read as 'pending'",))

    def test_a_cell_the_file_leaves_blank_is_the_default_with_nothing_to_report(self) -> None:
        """The six read blank the same way, `workflow_kind` included -- it was the odd one out.

        No `or <default>`, so a cell a developer had emptied was a hard refusal where its five
        siblings normalized. (The limited front-matter parser reads a bare `key:` as the start
        of a section, which is why the value arriving here is `{}` rather than `''`.)
        """
        contract = load_contract(
            self._written_with_cell("workflow_kind: light-task", "workflow_kind:")
        )
        self.assertEqual(contract.workflow_kind, "light-task")
        self.assertEqual(contract.unknown_cells, ())

    def test_every_vocabulary_cell_degrades_rather_than_stranding_the_task(self) -> None:
        for line, edit, (wire_field, raw, expected) in self.OFF_VOCABULARY_CELLS:
            with self.subTest(cell=edit.strip()):
                summary = worktree_status_packet(self._written_with_cell(line, edit))
                # `active`, not `invalidContract`: the lifecycle tools can read this file, so
                # the packet says where the task stands rather than only that it is broken.
                self.assertEqual(summary.state, "active")
                self.assertEqual(getattr(summary, wire_field), expected)
                reported = summary.unknownContractCells or []
                self.assertEqual(len(reported), 1)
                self.assertIn(f"{raw!r} read as {expected!r}", reported[0])

    def test_an_unreadable_memory_mode_degrades_to_the_topology_on_disk(self) -> None:
        """The one cell whose fallback is inferred rather than declared.

        `internal` and `external` decide whether there is a second repository to commit, so
        guessing would make closeout skip work that exists. A recorded memory worktree IS an
        external topology, and that is what the degrade reads.
        """
        external = _contract(
            self.root,
            memory_mode="external",
            memory_repo_path=self.root / "memory",
            memory_worktree=self.root / "wt" / "memory",
            ledger_path=self.root / "wt" / "memory" / "memory.md",
        )
        write_contract(external.contract_path, external)
        external.contract_path.write_text(
            external.contract_path.read_text(encoding="utf-8").replace(
                "memory_mode: external", "memory_mode: hybrid", 1
            ),
            encoding="utf-8",
        )
        contract = load_contract(external.contract_path)
        self.assertEqual(contract.memory_mode, "external")
        self.assertEqual(contract.unknown_cells, ("memory_mode='hybrid' read as 'external'",))

    def test_a_rewrite_heals_the_file_and_that_is_the_recovery_path(self) -> None:
        """What a developer does about it: run the lifecycle. Any tool that writes the contract
        regenerates the document from the model, so the substituted value becomes the file's."""
        path = self._written_with_cell("  cleanup: pending", "  cleanup: reclaimed-ish")
        write_contract(path, load_contract(path))
        healed = load_contract(path)
        self.assertEqual(healed.unknown_cells, ())
        self.assertEqual(healed.cleanup, "pending")
        self.assertNotIn("reclaimed-ish", path.read_text(encoding="utf-8"))

    def test_the_writer_refuses_what_the_reader_tolerated(self) -> None:
        """The other direction of the same boundary: nothing here may PUT one on disk.

        Reached only through a deliberate `cast` -- which is the point: the type says this
        cannot happen, and this is what happens when someone overrides the type anyway.
        """
        smuggled = replace(_contract(self.root), cleanup=cast(CleanupStatus, "reclaimed-ish"))
        with self.assertRaises(ContractError) as raised:
            write_contract(smuggled.contract_path, smuggled)
        # The detail first, the destination after it: the write gate names the file it was
        # about to put this on, the same way the read gate names the one it read.
        self.assertIn("invalid cleanup: reclaimed-ish", str(raised.exception))
        self.assertIn(f"(in {smuggled.contract_path})", str(raised.exception))

    def test_a_document_that_is_not_a_contract_is_still_the_invalid_contract_state(self) -> None:
        """Tolerance is for a cell's VALUE. A file with no schema has nothing to act on."""
        path = self._written_with_cell(
            "schema: ar-series-contract/v1", "schema: ar-series-contract/v99"
        )
        summary = worktree_status_packet(path)
        self.assertEqual(summary.state, "invalidContract")
        self.assertIn("unsupported series contract schema", summary.error or "")
        self.assertIn(f"(in {path.resolve()})", summary.error or "")

    def test_every_refusal_names_the_contract_it_was_reading(self) -> None:
        """The one thing an operator needs from a refusal, on every cell that can provoke one.

        `load_contract` is the single entry point of `worktree_closeout_apply`,
        `worktree_integrate`, `worktree_cleanup`, `worktree_sync`, `worktree_abandon` and
        `worktree_status`, and none of them catches `ContractError`, so whichever tool was run,
        this message is the whole of what comes back. "contract missing required fields:
        task_name" named no file, which on a tasks tree of series and enclosure contracts --
        all of them called `series-contract.md` -- is a search rather than an answer.

        Each row is a legal contract with one front-matter cell emptied or made unreadable, the
        way a hand edit leaves one.
        """
        root = self.root.as_posix()
        empty = "required contract path field is empty"
        for line, edit, refusal in (
            ("task_name: t", "task_name:", "contract missing required fields: task_name"),
            ("kind: leaf", "kind: sapling", "invalid contract kind: sapling"),
            ("  leaf_id: l", "  leaf_id:", "leaf contract missing leaf_id"),
            # `repo_path` and `worktree` exist under both `code:` and `memory:`, so these two
            # name the section as well -- a bare key would not say which line to open.
            (f"  root: {root}", "  root:", f"{empty}: coordination.root"),
            (f"  worktree: {root}/wt/code", "  worktree:", f"{empty}: code.worktree"),
        ):
            with self.subTest(cell=edit.strip()):
                path = self._written_with_cell(line, edit)
                with self.assertRaises(ContractError) as raised:
                    load_contract(path)
                self.assertIn(refusal, str(raised.exception))
                self.assertIn(f"(in {path})", str(raised.exception))

    def test_a_file_that_is_not_a_contract_at_all_is_named_too(self) -> None:
        """The two whole-file refusals. Their subject IS the file, so it ends the sentence.

        They used to print the `series-contract.md` constant -- the name the workflow writes,
        not the path the reader was handed, which told a developer nothing they did not know.
        """
        for text, refusal in (
            ("not a contract\n", "must start with a front matter block"),
            ("---\nschema: ar-series-contract/v1\n", "front matter is not closed"),
        ):
            with self.subTest(refusal=refusal):
                path = self._written()
                path.write_text(text, encoding="utf-8")
                with self.assertRaises(ContractError) as raised:
                    load_contract(path)
                self.assertIn(f"worktree contract {refusal}: {path}", str(raised.exception))

    def test_an_external_memory_contract_with_no_ledger_names_the_file(self) -> None:
        """The last refusal on the read path, and the one furthest from the cell it is about."""
        external = _contract(
            self.root,
            memory_mode="external",
            memory_repo_path=self.root / "memory",
            memory_worktree=self.root / "wt" / "memory",
            ledger_path=self.root / "wt" / "memory" / "memory.md",
        )
        write_contract(external.contract_path, external)
        path = external.contract_path
        ledger = (self.root / "wt" / "memory" / "memory.md").as_posix()
        path.write_text(
            path.read_text(encoding="utf-8").replace(f"  ledger: {ledger}", "  ledger:", 1),
            encoding="utf-8",
        )
        with self.assertRaises(ContractError) as raised:
            load_contract(path)
        self.assertEqual(
            str(raised.exception), f"external-memory contract missing ledger_path (in {path})"
        )

    def test_the_invalid_contract_payload_carries_the_file_to_open(self) -> None:
        """Where an operator actually meets this: `worktree_status`, not a traceback.

        `status.py` puts `str(error)` on the payload verbatim, so the raiser is the only place
        the path had to be added for every tool to gain it -- and the packet already carries
        `contractPath`, but the two are not the same claim: that field is the file the tool was
        pointed at, and the message is the file the refusal is about.
        """
        path = self._written_with_cell("task_name: t", "task_name:")
        summary = worktree_status_packet(path)
        self.assertEqual(summary.state, "invalidContract")
        self.assertIn("contract missing required fields: task_name", summary.error or "")
        self.assertIn(f"(in {path.resolve()})", summary.error or "")

    def test_an_unknown_workflow_kind_request_is_refused(self) -> None:
        """A *request* is not a legacy file: refuse it before it is written down."""
        task = ContractTask(
            name="t",
            repo_name="r",
            coordination_root=self.root,
            workflow_kind="master-task",
            memory_mode="internal",
        )
        with self.assertRaises(ContractError) as raised:
            default_series_contract(task, code=RepoBranchPlan(repo_path=self.root / "repo"))
        self.assertIn("workflow_kind must be one of", str(raised.exception))

    def test_a_refused_start_request_is_a_result_not_an_exception(self) -> None:
        """...and `worktree_start`'s handler has no `except ContractError` either."""
        refused = invalid_contract_request_result(ContractError("workflow_kind must be one of []"))
        self.assertEqual(refused.returncode, 2)
        self.assertEqual(refused.payload["state"], "invalid-request")
        self.assertIn("workflow_kind must be one of", str(refused.payload["summary"]))

    def test_a_contract_path_that_is_not_there_becomes_missing_contract(self) -> None:
        summary = worktree_status_packet(self.root / "nope" / "series-contract.md")
        self.assertEqual(summary.state, "missingContract")

    def test_no_contract_path_is_the_inactive_state(self) -> None:
        self.assertEqual(worktree_status_packet(None).state, "inactive")

    def test_a_live_contract_projects_onto_the_wire_model(self) -> None:
        """The whole seam end to end, offline: no worktrees, no repo, no remote probe.

        A pre-closeout contract keeps `landing_active` false, so this exercises the real
        `status_payload` -> `WorktreeSummary` projection without touching the network.
        """
        summary = worktree_status_packet(self._written())
        self.assertEqual(summary.state, "active")
        self.assertEqual(summary.phase, "worktree-started")
        self.assertEqual(summary.nextTool, "worktree_status")
        self.assertEqual(summary.workflowKind, "light-task")
        self.assertEqual(summary.cleanup, "pending")
        self.assertIsNone(summary.unknownContractCells)

    def test_a_healthy_contract_omits_the_next_required_args_it_has_none_of(self) -> None:
        """The declared wire shape, pinned so it cannot move again unannounced.

        Measured across the 213 contracts on disk: 48 responses that carried
        `"nextRequiredArgs": []` now omit the key. That is the decision, not an accident. The
        projection reports what `next_guidance` said and invents nothing -- the same rule that
        stopped it inventing a `nextTool` of `""` -- and an absent `nextRequiredArgs` means what
        `[]` meant, that the next call needs nothing beyond `nextArgs`. There is no third state
        for it to be confused with.
        """
        body = worktree_status_packet(self._written()).model_dump(mode="json", exclude_none=True)
        self.assertIn("nextTool", body)  # there IS a next move...
        self.assertIn("nextArgs", body)
        self.assertNotIn("nextRequiredArgs", body)  # ...and it asks nothing of the caller
        done = worktree_status_packet(self._written(cleanup="completed")).model_dump(
            mode="json", exclude_none=True
        )
        self.assertEqual({"nextTool", "nextArgs", "nextRequiredArgs"} & set(done), set())


class ProducerWireCrossingTests(unittest.TestCase):
    """The git readers' own degrade paths, projected onto the packet models as production does.

    `GuidanceWalkTests` does this for the phase machine; these are the same idea for the two
    ``kernel`` readers, and they stay offline the same way: a path that does not exist is
    answered before any subprocess runs, and the freshness read is told not to fetch. The
    projection under test is the untyped ``*_to_packet`` dict -- the seam where a state the
    reader invented would have become a ValidationError rather than a type error.
    """

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def test_an_absent_repo_crosses_the_wire_as_unavailable(self) -> None:
        summary = RepoSummary.model_validate(
            git_facts_to_packet(read_git_facts("r", self.root / "nope"))
        )
        self.assertEqual(summary.state, "unavailable")
        self.assertIn("does not exist", summary.error or "")

    def test_a_directory_that_is_not_a_repo_crosses_the_freshness_wire(self) -> None:
        freshness = WireBranchFreshness.model_validate(
            freshness_to_packet(read_branch_freshness(self.root, fetch=False))
        )
        self.assertIn(freshness.state, {"unavailable", "no-branch"})
        self.assertFalse(freshness.fetched)


def _advertised_workflow_kinds() -> set[str]:
    tree = _module_tree("mcp/registration/worktrees.py")
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "worktree_start":
            return set(re.findall(r"'([a-z-]+-task)'", ast.get_docstring(node) or ""))
    raise AssertionError("worktree_start tool declaration not found")


if __name__ == "__main__":
    unittest.main()
