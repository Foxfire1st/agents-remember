"""The reviewer's real-child mutable-input regression, including shared gate currentness."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PosixPath
from typing import Any, cast
from unittest import mock

import pytest
from agents_remember.application import review_leaf_view_memo, review_tree_knowledge
from agents_remember.application.knowledge_gate import evaluate_leaf_gate
from agents_remember.application.knowledge_gate import memo as gate_memo
from agents_remember.application.knowledge_worklist import leaf
from agents_remember.application.knowledge_worklist.leaf import CandidateTrees
from agents_remember.errors import MemoryModeUnsupportedError
from agents_remember.kernel import coordination_context_resolver as resolver
from agents_remember.kernel import reviewer_worklist_process as process_owner
from agents_remember.kernel.coordination_context.paths import settings_path_for_roots
from agents_remember.kernel.coordination_context.resolver import detect_coordination_selection
from agents_remember.kernel.coordination_context.settings import parse_coordination_settings
from agents_remember.kernel.recorded_reads import (
    ABSENT,
    CONFLICTING,
    bytes_identity,
    changed_observations,
    observed_exists,
    recorded_reads,
)
from agents_remember.tasks.leaf_decisions import LeafDocumentUnresolved, strict_leaf_doc
from agents_remember.worktrees.modules import contract_reader
from test_review_git_trees import (
    LEAF,
    MASTER,
    ORIGIN,
    REPO,
    World,
    _repository,
    commit,
    world,
)
from test_review_read_latency import _body, _live, _ports, _query


@pytest.fixture(autouse=True)
def _fresh_memo() -> Iterator[None]:
    review_leaf_view_memo.LEAF_VIEW_MEMO.clear()
    yield
    review_leaf_view_memo.LEAF_VIEW_MEMO.clear()


def test_child_consumed_task_bytes_survive_aba_conflict_absence_and_read_errors(
    world: World,
) -> None:
    world.edit()
    ports = _ports(world)
    path = world.task_root / "leaf.json"
    document = {
        "id": LEAF,
        "slug": "leaf",
        "title": "Leaf",
        "kind": "subTask",
        "repo": REPO,
        "createdAt": "2026-10-03T00:00:00Z",
    }
    original = json.dumps(document)
    altered = json.dumps(
        {
            **document,
            "expectedKnowledgeEffects": [
                {
                    "subject": "invariant:INV-ZZZZZZ",
                    "effect": "clarify",
                    "requirementRef": "MIK-R42@v1",
                }
            ],
        }
    )
    moved = review_tree_knowledge.moved_inputs
    for mode, expected in (
        ("aba", bytes_identity(altered.encode())),
        ("conflict", CONFLICTING),
        ("absent", bytes_identity(altered.encode())),
        ("unreadable", "unreadable (PermissionError)"),
    ):
        if mode == "absent":
            path.unlink(missing_ok=True)
        else:
            path.write_text(original)
        review_leaf_view_memo.LEAF_VIEW_MEMO.clear()
        observed: list[dict[str, str]] = []

        def checked(
            key: Any, reads: dict[str, str], observations: list[dict[str, str]] = observed
        ) -> Any:
            observations.append(dict(reads))
            return moved(key, reads)

        script = f"""
import json, sys
from pathlib import Path
from agents_remember.application import reviewer_worklist_child as child
from agents_remember.application.knowledge_worklist import leaf
path = Path({str(path)!r})
mode = {mode!r}
original = {original!r}
if mode == 'unreadable':
    real = Path.read_bytes
    def denied(self):
        if self == path:
            raise PermissionError('controlled actual child read failure')
        return real(self)
    Path.read_bytes = denied
else:
    path.write_text({altered!r})
if mode == 'conflict':
    effects = leaf.leaf_expected_effects
    def restore(contract):
        result = effects(contract)
        path.write_text(original)
        return result
    leaf.leaf_expected_effects = restore
request = json.load(sys.stdin)
answer = child._run(request)
if mode == 'absent':
    path.unlink()
else:
    path.write_text(original)
json.dump(answer, sys.stdout)
"""
        with (
            _child_script(script),
            mock.patch.object(review_tree_knowledge, "moved_inputs", checked),
        ):
            answer = ports.trees(_query())
        assert answer.state == "refused" and answer.refusal is not None
        assert str(path) in (answer.refusal.offending_input or "")
        # Removing the cross-process read replay fails this assertion, even if missing
        # evidence also refuses: the actual byte identity must reach the parent owner.
        assert observed[-1][str(path)] == expected
        assert len(review_leaf_view_memo.LEAF_VIEW_MEMO) == 0
    path.write_text(original)
    assert ports.trees(_query()).state == "trees"
    assert len(review_leaf_view_memo.LEAF_VIEW_MEMO) == 1
    _settings_reads(world)
    _fallback_reads(world)
    _packet_reads(world)
    selection_reads(world)
    review_leaf_view_memo.LEAF_VIEW_MEMO.clear()
    # A stable canonical task refusal keeps its public incomplete-worklist result, rather
    # than becoming a generic child failure or an empty successful list.
    (world.task_root / "leaf.json").write_text(json.dumps({"id": LEAF}))
    domain = ports.trees(_query())
    assert domain.state == "trees" and domain.worklist is not None
    assert domain.worklist.state == "incomplete" and domain.worklist.incomplete
    assert domain.worklist.incomplete[0]["input"] == "leaf task document"
    assert len(review_leaf_view_memo.LEAF_VIEW_MEMO) == 0


@contextmanager
def _child_script(script: str) -> Iterator[list[subprocess.Popen[Any]]]:
    """Test-only child faults; the product continues to use its fixed package module entry."""

    actual = subprocess.Popen
    children: list[subprocess.Popen[Any]] = []

    def spawn(argv: list[str], *args: Any, **kwargs: Any) -> Any:
        if argv[1:] == ["-P", "-m", process_owner.CHILD_MODULE]:
            child = actual([argv[0], "-P", "-c", script], *args, **kwargs)
            children.append(child)
            return child
        return actual(argv, *args, **kwargs)

    with mock.patch.object(subprocess, "Popen", spawn):
        yield children


def _settings_reads(fixture: World) -> None:
    """A real child parses settings B and restores A before identity validation, for both formats."""

    directory = fixture.memory_worktree / "system"
    directory.mkdir(exist_ok=True)
    markdown = directory / "settings.md"
    machine = markdown.with_suffix(".json")
    md = b"```yaml\r\nonboarding:\r\n  storage:\r\n    mode: memory-repo\r\n    default: memory-repo\r\n```\r\n"
    js = json.dumps(
        {"onboarding": {"storage": {"mode": "memory-repo", "default": "memory-repo"}}}
    ).encode()
    ports = _ports(fixture)
    reply = process_owner._reply
    for form in ("markdown", "json"):
        source, original = (markdown, md) if form == "markdown" else (machine, js)
        altered = (
            original.replace(b"default: memory-repo", b"default: memory-repX")
            if form == "markdown"
            else original.replace(b'"default": "memory-repo"', b'"default": "memory-repX"')
        )
        assert len(original) == len(altered)
        markdown.write_bytes(md)
        machine.unlink(missing_ok=True)
        source.write_bytes(original)
        for failure in (False, True):
            review_leaf_view_memo.LEAF_VIEW_MEMO.clear()
            observations: list[dict[str, Any]] = []

            def observed(
                data: bytes, request: Any, held: list[dict[str, Any]] = observations
            ) -> Any:
                value = reply(data, request)
                held.append(value)
                return value

            script = f"""
import json, sys
from pathlib import Path
from agents_remember.application import reviewer_worklist_child as child
from agents_remember.application.knowledge_worklist import onboarding_trace
path = Path({str(source)!r})
original, altered = {original!r}, {altered!r}
read_bytes, read_text = Path.read_bytes, Path.read_text
def consumed(self):
    if self != path or sys._getframe(1).f_code.co_name == 'file_identity':
        return read_bytes(self)
    if {failure!r}:
        raise PermissionError('controlled actual settings read')
    path.write_bytes(original)
    return altered
def text(self, *args, **kwargs):
    if self != path:
        return read_text(self, *args, **kwargs)
    if {failure!r}:
        raise PermissionError('controlled actual settings read')
    path.write_bytes(original)
    return altered.decode('utf-8').replace('\\r\\n', '\\n').replace('\\r', '\\n')
Path.read_bytes, Path.read_text = consumed, text
parsed = []
parser = onboarding_trace.resolver.parse_coordination_settings
def selected(source):
    result = parser(source)
    parsed.append(result[0].default)
    return result
onboarding_trace.resolver.parse_coordination_settings = selected
try:
    answer = child._run(json.load(sys.stdin))
except Exception as error:
    print(repr(error), file=sys.stderr)
    raise
if answer['document'] is not None:
    answer['document']['_test_settings_defaults'] = parsed
json.dump(answer, sys.stdout)
"""
            with _child_script(script), mock.patch.object(process_owner, "_reply", observed):
                refused = ports.trees(_query())
            assert refused.state == "refused" and refused.refusal is not None
            if not failure:
                assert str(source) in (refused.refusal.offending_input or ""), (
                    refused.refusal.detail
                )
            else:
                assert "PermissionError" in refused.refusal.detail
            expected = "unreadable (PermissionError)" if failure else bytes_identity(altered)
            assert observations[-1]["reads"][str(source)] == expected
            if not failure:
                assert observations[-1]["document"]["_test_settings_defaults"] == ["memory-repX"]
            assert len(review_leaf_view_memo.LEAF_VIEW_MEMO) == 0
        source.write_bytes(original)
        assert ports.trees(_query()).state == "trees"
    machine.unlink()
    markdown.unlink()
    _settings_selection(fixture.root)


def _settings_selection(root: Path) -> None:
    """Skipped higher-priority settings and JSON-only fallback belong to currentness too."""

    memory, coordination = root / "settings-memory", root / "settings-coordination"
    for directory in (memory, coordination):
        (directory / "system").mkdir(parents=True)
    higher = memory / "system/settings.md"
    markdown = coordination / "system/settings.md"
    machine = markdown.with_suffix(".json")
    raw = b"```yaml\ronboarding:\r  storage:\r    mode: memory-repo\r```\r"
    markdown.write_bytes(raw)
    with recorded_reads() as reads:
        selected = settings_path_for_roots(memory, coordination)
        parsed, _ = parse_coordination_settings(selected)
    assert selected == markdown and parsed.mode == "memory-repo"
    assert reads[f"exists:{higher}"] == ABSENT and reads[f"exists:{machine}"] == ABSENT
    assert reads[str(markdown)] == bytes_identity(raw)
    higher.write_bytes(raw)
    assert f"exists:{higher}" in review_leaf_view_memo.moved_inputs(None, reads)
    machine.write_text(json.dumps({"storage": {"default": "JSON takes precedence"}}))
    assert f"exists:{machine}" in review_leaf_view_memo.moved_inputs(None, reads)
    markdown.unlink()
    with recorded_reads() as machine_reads:
        json_only, _ = parse_coordination_settings(markdown)
    assert json_only.default == "JSON takes precedence"
    assert machine_reads == {str(machine): bytes_identity(machine.read_bytes())}
    machine.write_bytes(b"not JSON")
    with recorded_reads() as failed, pytest.raises(ValueError, match="invalid JSON settings"):
        parse_coordination_settings(markdown)
    assert failed[str(machine)] == bytes_identity(b"not JSON")


def _fallback_reads(fixture: World) -> None:
    """Real contract-context fallback and broken-task identity probes retain their consumed bytes."""

    external = fixture.config.coordination_root / "memory-repos" / f"ar-{REPO}"
    external.mkdir(parents=True)
    system = fixture.config.coordination_root / "system"
    system.mkdir()
    (system / "settings.json").write_text(json.dumps({"storage": {"mode": "memory-repo"}}))
    contract, _trees = _live(fixture)
    path = contract.contract_path
    original = path.read_bytes()
    altered = original.replace(b"work_branch: leaf", b"work_branch: leef")
    ports = _ports(fixture)
    reply = process_owner._reply
    observations: list[dict[str, Any]] = []

    def observed(data: bytes, request: Any) -> Any:
        result = reply(data, request)
        observations.append(result)
        return result

    script = f"""
import json, sys
from pathlib import Path
from agents_remember.application import reviewer_worklist_child as child
from agents_remember.tasks.leaf_decisions import LeafDocumentUnresolved, strict_leaf_doc
from agents_remember.worktrees.modules import contract_reader
path = Path({str(path)!r})
original, altered = {original!r}, {altered!r}
read_bytes, read_text = Path.read_bytes, Path.read_text
def consumed(self):
    if self != path or sys._getframe(1).f_code.co_name == 'file_identity':
        return read_bytes(self)
    path.write_bytes(original)
    return altered
def text(self, *args, **kwargs):
    if self != path:
        return read_text(self, *args, **kwargs)
    path.write_bytes(original)
    return altered.decode('utf-8')
Path.read_bytes, Path.read_text = consumed, text
branches = []
loader = contract_reader.load_contract
def loaded(source):
    result = loader(source)
    branches.append(result.code_work_branch)
    return result
contract_reader.load_contract = loaded
answer = child._run(json.load(sys.stdin))
answer['document']['_test_contract_branches'] = branches
json.dump(answer, sys.stdout)
"""
    review_leaf_view_memo.LEAF_VIEW_MEMO.clear()
    with _child_script(script), mock.patch.object(process_owner, "_reply", observed):
        refused = ports.trees(_query())
    assert refused.state == "refused" and refused.refusal is not None
    assert str(path) in (refused.refusal.offending_input or "")
    assert observations[-1]["reads"][str(path)] == bytes_identity(altered)
    assert set(observations[-1]["document"]["_test_contract_branches"]) == {"leef"}
    assert len(review_leaf_view_memo.LEAF_VIEW_MEMO) == 0
    assert ports.trees(_query()).state == "trees"
    claimed = fixture.task_root / "broken-sibling.json"
    claimed.write_text(json.dumps({"id": LEAF}))
    script = f"""
import json, sys
from pathlib import Path
from agents_remember.application import reviewer_worklist_child as child
path = Path({str(claimed)!r})
original, altered = {claimed.read_bytes()!r}, b'{{"id": "another-leaf"}}'
read_bytes, read_text = Path.read_bytes, Path.read_text
def consumed(self):
    if self == path and sys._getframe(1).f_code.co_name == 'observed_text':
        path.write_bytes(original)
        return altered
    return read_bytes(self)
def text(self, *args, **kwargs):
    if self == path:
        path.write_bytes(original)
        return altered.decode('utf-8')
    return read_text(self, *args, **kwargs)
Path.read_bytes, Path.read_text = consumed, text
json.dump(child._run(json.load(sys.stdin)), sys.stdout)
"""
    review_leaf_view_memo.LEAF_VIEW_MEMO.clear()
    with _child_script(script), mock.patch.object(process_owner, "_reply", observed):
        refused = ports.trees(_query())
    assert refused.state == "refused" and observations[-1]["reads"][str(claimed)] == CONFLICTING
    assert len(review_leaf_view_memo.LEAF_VIEW_MEMO) == 0
    claimed.unlink()


def _packet_inputs(fixture: World) -> tuple[Path, Path, Path, Path, bytes, bytes]:
    """One real reconsideration link used by byte and confinement controls."""

    directory = fixture.task_root / "requirements"
    directory.mkdir()
    first, second, alias = (directory / name for name in ("first.md", "second.md", "alias.md"))
    original = b"# Packet\r\n\r\n| Stable ID | MIK-X |\r\n| Version | v1 |\r\n"
    altered = original.replace(b"v1", b"v9")
    first.write_bytes(original)
    second.write_bytes(altered)
    alias.symlink_to(first.name)
    manifest = directory / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "format": "approved-requirement-corpus",
                "packets": [{"id": "MIK-X", "version": "v1", "state": "approved"}],
            }
        )
    )
    reference = {
        "task": {"repository": REPO, "path": MASTER},
        "packet": "requirements/alias.md",
        "id": "MIK-X",
        "version": "v1",
    }
    decision = {
        "schema": "ar-decision/v1",
        "id": "DEC-AAAAAA",
        "revision": 1,
        "status": "active",
        "context": "A real external packet is a computation input.",
        "alternatives": [
            {"option": "Keep", "status": "chosen", "reason": "It holds."},
            {
                "option": "Change",
                "status": "rejected",
                "reason": "Wait.",
                "reconsider_when": "The packet changes.",
            },
        ],
        "consequences": ["Read the owning packet."],
        "decider": "developer",
        "supersedes": [],
        "links": [{"relation": "reconsider_on", "target": reference, "alternative": 1}],
        "admission": "legacy-unassessed",
        "origin": ORIGIN,
    }
    fixture.memory_base = commit(
        fixture.memory,
        {"knowledge/decisions/DEC-AAAAAA-input.json": json.dumps(decision)},
        fixture.code_base,
    )
    fixture.contract()
    return first, second, alias, manifest, original, altered


def _packet_reads(fixture: World) -> None:
    """The real worklist's requirement owner retains logical alias bytes and first IO failures."""

    first, second, alias, manifest, original, altered = _packet_inputs(fixture)
    ports = _ports(fixture)
    reply = process_owner._reply
    for mode in ("packet-aba", "alias-aba", "manifest-failure"):
        review_leaf_view_memo.LEAF_VIEW_MEMO.clear()
        observed: list[dict[str, Any]] = []

        def captured(data: bytes, request: Any, values: list[dict[str, Any]] = observed) -> Any:
            result = reply(data, request)
            values.append(result)
            return result

        script = f"""
import json, sys
from pathlib import Path
from agents_remember.application import reviewer_worklist_child as child
first, second, alias = Path({str(first)!r}), Path({str(second)!r}), Path({str(alias)!r})
manifest = Path({str(manifest)!r})
original, altered, mode = {original!r}, {altered!r}, {mode!r}
read_bytes, read_text = Path.read_bytes, Path.read_text
failed = False
if mode == 'alias-aba':
    alias.unlink(); alias.symlink_to(second.name)
def consumed(self):
    global failed
    if sys._getframe(1).f_code.co_name == 'file_identity':
        return read_bytes(self)
    if mode == 'manifest-failure' and self == manifest and not failed:
        failed = True
        raise PermissionError('controlled first manifest read')
    if mode == 'packet-aba' and self == first:
        first.write_bytes(original)
        return altered
    data = read_bytes(self)
    if mode == 'alias-aba' and self == second:
        alias.unlink(); alias.symlink_to(first.name)
    return data
def text(self, *args, **kwargs):
    if mode == 'packet-aba' and self == first:
        first.write_bytes(original)
        return altered.decode('utf-8')
    return read_text(self, *args, **kwargs)
Path.read_bytes, Path.read_text = consumed, text
json.dump(child._run(json.load(sys.stdin)), sys.stdout)
"""
        with _child_script(script), mock.patch.object(process_owner, "_reply", captured):
            refused = ports.trees(_query())
        assert refused.state == "refused" and refused.refusal is not None, observed
        path, identity = (
            (manifest, "unreadable (PermissionError)")
            if mode == "manifest-failure"
            else (alias, bytes_identity(altered))
        )
        assert observed[-1]["reads"][str(path)] == identity
        assert str(path) in (refused.refusal.offending_input or "")
        assert len(review_leaf_view_memo.LEAF_VIEW_MEMO) == 0
    assert ports.trees(_query()).state == "trees"


@contextmanager
def _observations() -> Iterator[list[dict[str, Any]]]:
    replies: list[dict[str, Any]] = []
    reply = process_owner._reply

    def observed(data: bytes, request: Any) -> Any:
        value = reply(data, request)
        replies.append(value)
        return value

    with mock.patch.object(process_owner, "_reply", observed):
        yield replies


def _subjects(answer: Any, kind: str = "onboarding_trace") -> list[str]:
    assert answer.state == "trees" and answer.worklist is not None
    return [item["subject"] for item in answer.worklist.items if item["kind"] == kind]


def _settings(root: Path, mode: str) -> None:
    (root / "system").mkdir(parents=True)
    (root / "system/settings.md").write_text(
        f"```yaml\nonboarding:\n  storage:\n    mode: {mode}\n```\n"
    )


def _retarget(alias: Path, target: Path) -> None:
    alias.unlink(missing_ok=True)
    alias.symlink_to(target)


def _packet_confinement(fixture: World) -> None:
    first, _second, alias, manifest, original, _altered = _packet_inputs(fixture)
    outside = fixture.root / "outside.md"
    outside.write_bytes(original)
    manifest.write_text(
        json.dumps(
            {
                "format": "approved-requirement-corpus",
                "packets": [{"id": "MIK-X", "version": "v2", "state": "approved"}],
            }
        )
    )
    ports = _ports(fixture)
    contract, trees = _live(fixture)
    candidate = CandidateTrees(trees.record.code_candidate.tree, trees.record.memory_candidate.tree)
    gate_memo.GATE_MEMO.clear()
    review_leaf_view_memo.LEAF_VIEW_MEMO.clear()
    try:
        with _observations() as replies:
            for target, subjects, passing in (
                (outside, [], True),
                (first, ["reconsider:DEC-AAAAAA#1"], False),
                (outside, [], True),
            ):
                _retarget(alias, target)
                count = len(replies)
                read = Path.read_bytes

                def confined(path: Path, actual: Any = read) -> bytes:
                    if path == outside or (path == alias and alias.resolve() == outside):
                        raise AssertionError("read outside packet bytes before confinement")
                    return actual(path)

                with mock.patch.object(Path, "read_bytes", confined):
                    answer = ports.trees(_query())
                    result = evaluate_leaf_gate(
                        contract, candidate, parent_memory_tip=fixture.memory_base
                    )
                assert _subjects(answer, "reconsideration_candidate") == subjects
                assert len(replies) == count + 1
                reads = replies[-1]["reads"]
                assert reads[f"resolve:{alias}"] == f"resolved:{target}"
                if target == outside:
                    assert str(alias) not in reads and str(outside) not in reads
                else:
                    assert reads[str(alias)] == bytes_identity(original)
                assert _body(ports.trees(_query()), by_alias=True) == _body(answer, by_alias=True)
                assert len(replies) == count + 1
                assert result is not None and result.ok == passing
                assert any(one.code == "knowledge-item-open" for one in result.findings) != passing
                assert (
                    evaluate_leaf_gate(contract, candidate, parent_memory_tip=fixture.memory_base)
                    == result
                )
            print(json.dumps({"control": "packet-confinement-and-gate", "replies": replies}))
    finally:
        gate_memo.GATE_MEMO.clear()


def _contract_alias(fixture: World) -> None:
    fixture.edit()
    alias = fixture.contract()
    initial = alias.read_bytes()
    first, second = alias.with_name("physical-a.md"), alias.with_name("physical-b.md")
    alternate = fixture.root / "coordination-B"
    (alternate / "memory-repos" / f"ar-{REPO}").mkdir(parents=True)
    _settings(alternate, "disabled")
    first.write_bytes(initial)
    second.write_bytes(
        initial.replace(str(fixture.config.coordination_root).encode(), str(alternate).encode())
    )
    _retarget(alias, first)
    ports = _ports(fixture)
    script = f"""
import json, sys
from pathlib import Path
from agents_remember.application import reviewer_worklist_child as child
from agents_remember.tasks.leaf_decisions import LeafDocumentUnresolved, strict_leaf_doc
from agents_remember.worktrees.modules import contract_reader
alias, first, second = Path({str(alias)!r}), Path({str(first)!r}), Path({str(second)!r})
alias.unlink(); alias.symlink_to(second)
loader = contract_reader.load_contract
def read(path):
    result = loader(path)
    if path == second:
        alias.unlink(); alias.symlink_to(first)
    return result
contract_reader.load_contract = read
json.dump(child._run(json.load(sys.stdin)), sys.stdout)
"""
    review_leaf_view_memo.LEAF_VIEW_MEMO.clear()
    with _observations() as replies:
        with _child_script(script):
            raced = ports.trees(_query())
        assert raced.state == "refused" and raced.refusal is not None
        assert str(alias) in (raced.refusal.offending_input or "")
        assert replies[-1]["reads"][f"resolve:{alias}"] == CONFLICTING
        assert len(review_leaf_view_memo.LEAF_VIEW_MEMO) == 0
        fresh = ports.trees(_query())
        assert _subjects(fresh) == ["onboarding:overview", "onboarding:pkg/a.py"]
        assert _body(ports.trees(_query()), by_alias=True) == _body(fresh, by_alias=True)
        # the raced read computed twice (its one restart raced again), the fresh read once
        assert len(replies) == 3
        print(json.dumps({"control": "contract-alias-ABA", "replies": replies}))


def _root_selections(fixture: World) -> None:
    fixture.edit()
    ports = _ports(fixture)
    logical = fixture.config.coordination_root / "memory-repos" / f"ar-{REPO}"
    review_leaf_view_memo.LEAF_VIEW_MEMO.clear()
    with _observations() as replies:
        default = ports.trees(_query())
        assert _subjects(default) == ["onboarding:overview", "onboarding:pkg/a.py"]
        assert replies[-1]["reads"][f"exists:{logical}"] == ABSENT
        first, second, equal = (
            fixture.root / name for name in ("settings-A", "settings-B", "settings-equal")
        )
        _settings(first, "memory-repo")
        _settings(second, "disabled")
        _settings(equal, "memory-repo")
        assert (first / "system/settings.md").read_bytes() == (
            equal / "system/settings.md"
        ).read_bytes()
        logical.parent.mkdir()
        for target, subjects in (
            (second, ["onboarding:overview"]),
            (first, ["onboarding:overview", "onboarding:pkg/a.py"]),
            (equal, ["onboarding:overview", "onboarding:pkg/a.py"]),
            (second, ["onboarding:overview"]),
        ):
            _retarget(logical, target)
            count = len(replies)
            answer = ports.trees(_query())
            assert _subjects(answer) == subjects and len(replies) == count + 1
            assert answer.comparison == default.comparison
            assert replies[-1]["reads"][f"resolve:{logical}"] == f"resolved:{target}"
            assert _body(ports.trees(_query()), by_alias=True) == _body(answer, by_alias=True)
            assert len(replies) == count + 1
        internal = fixture.code / "ar-memory"
        for present, subjects in (
            (True, ["onboarding:overview", "onboarding:pkg/a.py"]),
            (False, ["onboarding:overview"]),
        ):
            if present:
                internal.mkdir()
                with (
                    recorded_reads() as refused_reads,
                    pytest.raises(MemoryModeUnsupportedError) as error,
                ):
                    detect_coordination_selection(
                        REPO, fixture.code, coordination_root_hint=fixture.config.coordination_root
                    )
                assert error.value.requested == "internal" and error.value.artifact == str(internal)
                assert refused_reads[f"exists:{internal}"] == "present"
            else:
                internal.rmdir()
            count = len(replies)
            answer = ports.trees(_query())
            assert _subjects(answer) == subjects and len(replies) == count + 1
            assert answer.comparison == default.comparison
            assert replies[-1]["reads"][f"exists:{internal}"] == ("present" if present else ABSENT)
        absent, removed = fixture.root / "missing-internal", fixture.root / "removed-internal"
        removed.mkdir()
        for target, subjects in (
            (absent, ["onboarding:overview"]),
            (removed, ["onboarding:overview", "onboarding:pkg/a.py"]),
        ):
            _retarget(internal, target)
            count = len(replies)
            answer = ports.trees(_query())
            assert _subjects(answer) == subjects and len(replies) == count + 1
            assert replies[-1]["reads"][f"resolve:{internal}"] == f"resolved:{target}"
        internal.unlink()
        _root_errors(ports, (internal, second), replies)
        print(json.dumps({"control": "root-absence-alias-removal-errors", "replies": replies}))


def _root_errors(ports: Any, roots: tuple[Path, ...], replies: list[dict[str, Any]]) -> None:
    for root in roots:
        review_leaf_view_memo.LEAF_VIEW_MEMO.clear()
        script = f"""
import json, sys
from pathlib import Path
from agents_remember.application import reviewer_worklist_child as child
root, exists = Path({str(root)!r}), Path.exists
def denied(path):
    if path == root:
        raise PermissionError('controlled actual root predicate')
    return exists(path)
Path.exists = denied
json.dump(child._run(json.load(sys.stdin)), sys.stdout)
"""
        with _child_script(script):
            answer = ports.trees(_query())
        assert answer.state == "refused" and answer.refusal is not None
        assert "PermissionError" in answer.refusal.detail
        assert replies[-1]["reads"][f"exists:{root}"] == "unreadable (PermissionError)"
        assert len(review_leaf_view_memo.LEAF_VIEW_MEMO) == 0


def _external_ledger(fixture: World) -> None:
    fixture.edit()
    (fixture.config.coordination_root / "memory-repos" / f"ar-{REPO}").mkdir(parents=True)
    other = _repository(fixture.root / "other")
    commit(other, {"a.py": "A = 1\n"})
    memory = _repository(fixture.config.coordination_root / "memory-repos/ar-other")
    raw = b"An intentionally invalid ledger format with actual readable external bytes.\r\n"
    commit(memory, {"memory.md": raw.decode()})
    ledger = memory / "memory.md"
    _settings(fixture.config.coordination_root, "memory-repo")
    (fixture.config.coordination_root / "system/settings.json").write_text(
        json.dumps(
            {
                "storage": {"mode": "memory-repo"},
                "crossRepo": {
                    "allow": [
                        {
                            "repo": "other",
                            "expectedBranch": "main",
                            "includeCode": True,
                            "includeMemory": True,
                        }
                    ]
                },
            }
        )
    )
    ports = _ports(fixture)
    review_leaf_view_memo.LEAF_VIEW_MEMO.clear()
    with _observations() as replies:
        first = ports.trees(_query())
        assert first.state == "trees" and replies[-1]["reads"][str(ledger)] == bytes_identity(raw)
        assert _body(ports.trees(_query()), by_alias=True) == _body(first, by_alias=True)
        assert len(replies) == 1
        ledger.write_bytes(raw + b"More invalid prose.\n")
        again = ports.trees(_query())
        assert _body(again, by_alias=True) == _body(first, by_alias=True)
        assert len(replies) == 2
        assert replies[-1]["reads"][str(ledger)] == bytes_identity(ledger.read_bytes())
        original = ledger.read_bytes()
        altered = original.replace(b"invalid", b"Invalid")
        script = f"""
import json, sys
from pathlib import Path
from agents_remember.application import reviewer_worklist_child as child
ledger, read = Path({str(ledger)!r}), Path.read_bytes
def consumed(path):
    if path == ledger and sys._getframe(1).f_code.co_name == 'observed_text':
        ledger.write_bytes({original!r})
        return {altered!r}
    return read(path)
Path.read_bytes = consumed
json.dump(child._run(json.load(sys.stdin)), sys.stdout)
"""
        review_leaf_view_memo.LEAF_VIEW_MEMO.clear()
        with _child_script(script):
            raced = ports.trees(_query())
        assert raced.state == "refused" and raced.refusal is not None
        assert str(ledger) in (raced.refusal.offending_input or "")
        assert replies[-1]["reads"][str(ledger)] == bytes_identity(altered)
        assert len(review_leaf_view_memo.LEAF_VIEW_MEMO) == 0
        ledger.unlink()
        assert ports.trees(_query()).state == "trees"
        assert replies[-1]["reads"][f"exists:{ledger}"] == ABSENT
        ledger.write_bytes(raw)
        review_leaf_view_memo.LEAF_VIEW_MEMO.clear()
        script = f"""
import json, sys
from pathlib import Path
from agents_remember.application import reviewer_worklist_child as child
ledger, read = Path({str(ledger)!r}), Path.read_bytes
def denied(path):
    if path == ledger:
        raise PermissionError('controlled actual ledger read')
    return read(path)
Path.read_bytes = denied
json.dump(child._run(json.load(sys.stdin)), sys.stdout)
"""
        with _child_script(script):
            failed = ports.trees(_query())
        assert failed.state == "refused" and failed.refusal is not None
        assert "PermissionError" in failed.refusal.detail
        assert replies[-1]["reads"][str(ledger)] == "unreadable (PermissionError)"
        assert len(review_leaf_view_memo.LEAF_VIEW_MEMO) == 0
        print(json.dumps({"control": "actually-consumed-external-ledger", "replies": replies}))


def _gate_failed_selector(fixture: World) -> None:
    contract, trees = _live(fixture)
    candidate = CandidateTrees(trees.record.code_candidate.tree, trees.record.memory_candidate.tree)
    for name in ("skills", "system", "memory-repos"):
        (fixture.config.coordination_root / name).mkdir(exist_ok=True)
    baseline = evaluate_leaf_gate(contract, candidate, parent_memory_tip=fixture.memory_base)
    assert baseline is not None and baseline.ok and baseline.worklist["state"] == "complete"
    gate_memo.GATE_MEMO.clear()
    loader = contract_reader.load_contract

    class DeniedContractPath(PosixPath):
        def read_bytes(self) -> bytes:
            raise PermissionError("controlled actual consumed contract IO")

    def denied(path: Path) -> Any:
        return loader(DeniedContractPath(path))

    admission = mock.Mock(wraps=gate_memo.remember)
    try:
        with (
            mock.patch.object(contract_reader, "load_contract", denied),
            mock.patch.object(
                resolver, "agents_repo_from_script", return_value=fixture.config.coordination_root
            ),
            mock.patch.object(gate_memo, "remember", admission),
        ):
            for _ in range(2):
                result = evaluate_leaf_gate(
                    contract, candidate, parent_memory_tip=fixture.memory_base
                )
                assert result is not None and result.ok == baseline.ok
                assert result.worklist == baseline.worklist and result.memoisable
                assert len(gate_memo.GATE_MEMO) == 0
        assert admission.call_count == 2
        assert all(
            call.args[2][str(contract.contract_path)] == "unreadable (PermissionError)"
            for call in admission.call_args_list
        )
        print(
            json.dumps(
                {
                    "control": "caught-gate-selector-IO",
                    "original_ok": baseline.ok,
                    "failed_input_memo_slots": len(gate_memo.GATE_MEMO),
                }
            )
        )
    finally:
        gate_memo.GATE_MEMO.clear()


def selection_reads(fixture: World) -> None:
    for name, check in (
        ("packet", _packet_confinement),
        ("contract", _contract_alias),
        ("roots", _root_selections),
        ("ledger", _external_ledger),
        ("gate-error", _gate_failed_selector),
    ):
        check(cast(Any, world).__wrapped__(fixture.root / f"selection-{name}"))


# -- MIK-R42 round 3: the view depends only on the task documents it read -------------------------


def _task_document(identity: str, title: str = "Leaf") -> str:
    return json.dumps(
        {
            "id": identity,
            "slug": identity.lower(),
            "title": title,
            "kind": "subTask",
            "repo": REPO,
            "createdAt": "2026-10-03T00:00:00Z",
        }
    )


def test_a_kept_view_ignores_every_task_document_it_did_not_read(world: World) -> None:
    world.edit()
    ports = _ports(world)
    own, other = world.task_root / "leaf.json", world.task_root / "other.json"
    master = world.task_root / "task.json"
    own.write_text(_task_document(LEAF))
    with _observations() as replies:
        first = ports.trees(_query())
        body = _body(first, by_alias=True)
        assert first.state == "trees" and len(replies) == 1
        # The lookup opened the master's own document and ruled it out: not an input.
        assert str(own) in replies[0]["reads"] and str(master) not in replies[0]["reads"]
        key = review_leaf_view_memo.LEAF_VIEW_MEMO
        assert len(key) == 1

        other.write_text(_task_document("260101-TRV-OTHER"))  # another leaf's document appears
        assert _body(ports.trees(_query()), by_alias=True) == body and len(replies) == 1
        other.write_text(_task_document("260101-TRV-OTHER", "Edited"))  # and is edited
        assert _body(ports.trees(_query()), by_alias=True) == body and len(replies) == 1
        master.write_text(master.read_text() + "\n")  # a newline is added to task.json
        assert _body(ports.trees(_query()), by_alias=True) == body and len(replies) == 1
        other.unlink()
        assert _body(ports.trees(_query()), by_alias=True) == body and len(replies) == 1

        own.write_text(_task_document(LEAF, "Retitled"))  # the leaf's own document did change
        assert ports.trees(_query()).state == "trees" and len(replies) == 2
        assert str(own) in replies[1]["reads"]


def test_a_document_that_begins_to_claim_the_leaf_is_not_hidden_by_a_kept_view(
    world: World,
) -> None:
    world.edit()
    ports = _ports(world)
    own, rival = world.task_root / "leaf.json", world.task_root / "rival.json"
    own.write_text(_task_document(LEAF))
    with _observations() as replies:
        assert ports.trees(_query()).state == "trees" and len(replies) == 1
        rival.write_text(_task_document(LEAF, "Second claim"))
        ambiguous = ports.trees(_query())
        # Two documents claim the leaf: the lookup is ambiguous again, as it is on a cold read.
        assert len(replies) == 2
        assert ambiguous.worklist is not None and ambiguous.worklist.state == "incomplete"
        assert len(review_leaf_view_memo.LEAF_VIEW_MEMO) == 1  # only the first, complete view


def test_a_task_document_changing_while_the_view_is_computed_is_computed_again(
    world: World,
) -> None:
    world.edit()
    ports = _ports(world)
    own = world.task_root / "leaf.json"
    own.write_text(_task_document(LEAF))
    script = f"""
import json, sys, time
from pathlib import Path
from agents_remember.application import reviewer_worklist_child as child
Path({str(own)!r}).write_text(json.dumps({{**json.loads({_task_document(LEAF)!r}),
                                         "title": {{"stable": "T", "moving": str(time.time_ns())}}[{{MODE}}]}}))
json.dump(child._run(json.load(sys.stdin)), sys.stdout)
"""
    with _observations() as replies:
        with _child_script(script.replace("{MODE}", "'stable'")):
            settled = ports.trees(_query())
        # The first computation read bytes the request had not seen; the second saw them stable.
        assert settled.state == "trees" and len(replies) == 2
        assert len(review_leaf_view_memo.LEAF_VIEW_MEMO) == 1
        review_leaf_view_memo.LEAF_VIEW_MEMO.clear()
        with _child_script(script.replace("{MODE}", "'moving'")):
            moving = ports.trees(_query())
        assert len(replies) == 4  # one restart, and no more
        assert moving.state == "refused" and moving.refusal is not None
        assert moving.refusal.code == "inputs_changing"
        assert str(own) in (moving.refusal.offending_input or "")
        assert "retry" in moving.refusal.next_action
        assert len(review_leaf_view_memo.LEAF_VIEW_MEMO) == 0
    assert ports.trees(_query()).state == "trees"


def test_a_worklist_that_reads_no_task_document_is_not_refused_as_changed(world: World) -> None:
    world.edit()
    ports = _ports(world)
    (world.task_root / "leaf.json").write_text(_task_document(LEAF))
    script = """
import json, sys
from agents_remember.application import reviewer_worklist_child as child
child.leaf_worklist = lambda *args, **kwargs: None
json.dump(child._run(json.load(sys.stdin)), sys.stdout)
"""
    with _child_script(script):
        answer = ports.trees(_query())
    assert answer.state == "trees" and answer.worklist is not None
    assert answer.worklist.source == "absent"  # nothing applies; nothing changed either
    assert len(review_leaf_view_memo.LEAF_VIEW_MEMO) == 0


def test_moved_inputs_tells_not_read_from_changed_and_unidentified(tmp_path: Path) -> None:
    leaf, new = tmp_path / "leaf.json", tmp_path / "new.json"
    leaf.write_text("{}")
    same = bytes_identity(leaf.read_bytes())
    key = mock.Mock(task_root=tmp_path.as_posix(), task_reads=((leaf.as_posix(), same),))
    moved = review_tree_knowledge.moved_inputs
    assert moved(key, {}) == ()  # the computation never opened the document: not a change
    assert moved(key, {leaf.as_posix(): same}) == ()
    assert moved(key, {leaf.as_posix(): "sha256:" + "b" * 64}) == (leaf.as_posix(),)
    assert moved(key, {leaf.as_posix(): CONFLICTING}) == (leaf.as_posix(),)
    new.write_text("{}")  # a document the key never identified, read by the computation
    assert moved(key, {new.as_posix(): bytes_identity(new.read_bytes())}) == (new.as_posix(),)


def test_the_gates_warm_pass_becomes_the_refusal_when_a_packet_locator_is_retargeted(
    world: World,
) -> None:
    """MIK-R42 ruling 7: the gate's own memo checks locator selections before it serves a pass."""

    first, _second, alias, manifest, original, _altered = _packet_inputs(world)
    outside = world.root / "outside.md"
    outside.write_bytes(original)
    manifest.write_text(
        json.dumps(
            {
                "format": "approved-requirement-corpus",
                "packets": [{"id": "MIK-X", "version": "v2", "state": "approved"}],
            }
        )
    )
    contract, trees = _live(world)
    candidate = CandidateTrees(trees.record.code_candidate.tree, trees.record.memory_candidate.tree)
    gate_memo.GATE_MEMO.clear()
    try:
        _retarget(alias, outside)
        passed = evaluate_leaf_gate(contract, candidate, parent_memory_tip=world.memory_base)
        assert passed is not None and passed.ok and len(gate_memo.GATE_MEMO) == 1
        assert (
            evaluate_leaf_gate(contract, candidate, parent_memory_tip=world.memory_base) == passed
        )  # kept: the second call is the memo's
        _retarget(alias, first)  # the same bytes the alias read before, now at the packet
        refused = evaluate_leaf_gate(contract, candidate, parent_memory_tip=world.memory_base)
        assert refused is not None and not refused.ok
        assert any(finding.code == "knowledge-item-open" for finding in refused.findings)
    finally:
        gate_memo.GATE_MEMO.clear()


def test_a_lookup_without_a_claimant_exports_absence_through_the_child_and_gate(
    world: World,
) -> None:
    world.edit()
    ports = _ports(world)
    own = world.task_root / "leaf.json"
    declared = json.dumps(
        {
            **json.loads(_task_document(LEAF)),
            "expectedKnowledgeEffects": [
                {
                    "subject": "invariant:INV-ZZZZZZ",
                    "effect": "clarify",
                    "requirementRef": "MIK-R42@v2",
                }
            ],
        }
    )
    own.write_text(declared)
    truth = _body(ports.trees(_query()), by_alias=True)
    contract, trees = _live(world)
    candidate = CandidateTrees(trees.record.code_candidate.tree, trees.record.memory_candidate.tree)
    effects, scope = leaf.leaf_expected_effects, leaf.leaf_maintenance_scope
    try:
        for bad in (
            None,
            "",
            "{",
            _task_document("260101-TRV-OTHER"),
            json.dumps({**json.loads(declared), "kind": "master"}),
        ):
            review_leaf_view_memo.LEAF_VIEW_MEMO.clear()
            script = f"""
import json, sys
from pathlib import Path
from agents_remember.application import reviewer_worklist_child as child
own, bad = Path({str(own)!r}), {bad!r}
if bad is None:
    own.unlink()
else:
    own.write_text(bad)
try:
    answer = child._run(json.load(sys.stdin))
finally:
    own.write_text({declared!r})
json.dump(answer, sys.stdout)
"""
            with _observations() as replies, _child_script(script):
                raced = ports.trees(_query())
            assert raced.state == "refused" and raced.refusal is not None
            assert raced.refusal.code == "inputs_changing" and len(replies) == 2
            assert all(f"json-files:{world.task_root}" in reply["reads"] for reply in replies)
            if bad is not None:
                assert all(str(own) in reply["reads"] for reply in replies)
            assert len(review_leaf_view_memo.LEAF_VIEW_MEMO) == 0
            assert _body(ports.trees(_query()), by_alias=True) == truth

            gate_memo.GATE_MEMO.clear()
            expected = evaluate_leaf_gate(contract, candidate, parent_memory_tip=world.memory_base)
            gate_memo.GATE_MEMO.clear()

            def missing(bound: Any, state: str | None = bad) -> Any:
                own.unlink() if state is None else own.write_text(state)
                return effects(bound)

            def restored(bound: Any) -> Any:
                result = scope(bound)
                own.write_text(declared)
                return result

            with (
                mock.patch.object(leaf, "leaf_expected_effects", missing),
                mock.patch.object(leaf, "leaf_maintenance_scope", restored),
            ):
                evaluate_leaf_gate(contract, candidate, parent_memory_tip=world.memory_base)
            assert (
                evaluate_leaf_gate(contract, candidate, parent_memory_tip=world.memory_base)
                == expected
            )
    finally:
        own.write_text(declared)
        gate_memo.GATE_MEMO.clear()


def test_a_refused_lookup_replays_every_opened_document(tmp_path: Path) -> None:
    first, second = tmp_path / "first.json", tmp_path / "second.json"
    first.write_text(_task_document(LEAF))
    second.write_text(_task_document(LEAF, "Second"))
    with recorded_reads() as reads, pytest.raises(LeafDocumentUnresolved):
        strict_leaf_doc(tmp_path, LEAF)
    assert reads[str(first)] == bytes_identity(first.read_bytes())
    assert reads[str(second)] == bytes_identity(second.read_bytes())
    first.unlink()
    second.unlink()
    named = tmp_path / f"{LEAF}.json"
    named.write_text(_task_document(LEAF))
    with (
        mock.patch.object(Path, "read_bytes", side_effect=PermissionError("denied")),
        recorded_reads() as reads,
        pytest.raises(LeafDocumentUnresolved),
    ):
        strict_leaf_doc(tmp_path, LEAF)
    assert reads[str(named)] == "unreadable (PermissionError)"


def test_a_false_existence_probe_rechecks_its_predicate_in_a_stable_unreadable_layout(
    tmp_path: Path,
    world: World,
) -> None:
    parent, loop = tmp_path / "file", tmp_path / "loop"
    parent.write_text("not a directory")
    loop.symlink_to(loop)
    for path in (parent / "settings.md", loop, tmp_path / "missing"):
        with recorded_reads() as reads:
            assert not observed_exists(path)
        assert reads == {f"exists:{path}": ABSENT}
        assert changed_observations(reads) == ()
    world.edit()
    (world.memory_worktree / "system").write_text("not a directory")
    with _observations() as replies:
        answer = _ports(world).trees(_query())
    assert answer.state == "trees" and len(replies) == 1
