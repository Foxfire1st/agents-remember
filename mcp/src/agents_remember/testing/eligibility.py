"""Canonical total classifier for an exact bounded direct pytest selection."""

from __future__ import annotations

import ast
import hashlib
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

from agents_remember.models.test_evidence import CandidateBinding
from agents_remember.testing.dependency_closure import DependencyClosureAnalyzer
from agents_remember.testing.selection_contract import (
    ClosureRefusal,
    DependencyObservation,
    DirectRefusalCode,
    DirectSelectionDecision,
    EligibleDirectSelection,
    RefusedDirectSelection,
    ResolvedDependencyClosure,
    ResolvedTestTarget,
)

POLICY_VERSION = "python-direct-eligibility/v1"
MAX_DIRECT_NODES = 8
TEST_ROOT = Path("mcp/tests")
CONFIGURATION_PATHS = (Path("pyproject.toml"),)


def classify_direct_selection(
    candidate_root: Path,
    explicit_targets: Sequence[str],
) -> DirectSelectionDecision:
    """Classify the complete explicit request without importing or executing its tests."""

    request = _validated_request(candidate_root, explicit_targets)
    if isinstance(request, RefusedDirectSelection):
        return request
    root, targets = request

    resolved: list[ResolvedTestTarget] = []
    for target in targets:
        decision = _resolve_exact_target(root, target)
        if isinstance(decision, RefusedDirectSelection):
            return decision
        resolved.append(decision)

    closure = DependencyClosureAnalyzer(root).analyze(tuple(resolved))
    if isinstance(closure, ClosureRefusal):
        return _closure_refusal(closure, tuple(resolved), targets)

    binding = _candidate_binding(root, targets, closure)
    if isinstance(binding, RefusedDirectSelection):
        return binding
    return EligibleDirectSelection(
        candidate_root=root,
        nodes=targets,
        closure=closure,
        binding=binding,
    )


def _validated_request(
    candidate_root: Path,
    explicit_targets: Sequence[str],
) -> tuple[Path, tuple[str, ...]] | RefusedDirectSelection:
    root = candidate_root.resolve()
    if not root.is_dir():
        return _refused(
            DirectRefusalCode.INVALID_CANDIDATE,
            f"candidate root is not a directory: {root}",
        )
    targets = tuple(explicit_targets)
    if not targets:
        return _refused(
            DirectRefusalCode.EMPTY_SELECTION,
            "direct diagnostics require at least one exact pytest node",
        )
    if len(targets) > MAX_DIRECT_NODES:
        return _refused(
            DirectRefusalCode.OVERSIZED_SELECTION,
            f"direct diagnostics accept at most {MAX_DIRECT_NODES} exact nodes",
            refused_nodes=targets,
        )
    if len(set(targets)) != len(targets):
        return _refused(
            DirectRefusalCode.DUPLICATE_TARGET,
            "direct diagnostics refuse duplicate node IDs",
            refused_nodes=targets,
        )

    return root, targets


def _closure_refusal(
    closure: ClosureRefusal,
    resolved: tuple[ResolvedTestTarget, ...],
    targets: tuple[str, ...],
) -> RefusedDirectSelection:
    code = DirectRefusalCode.MIXED_SELECTION if len(resolved) > 1 else closure.code
    message = closure.message
    if code is DirectRefusalCode.MIXED_SELECTION:
        message = (
            "the exact selection mixes eligible and refused dependency/effect closure; "
            "no node was executed: " + message
        )
    return _refused(
        code,
        message,
        target=next(
            (item.node_id for item in resolved if item.path.as_posix() == closure.observation.path),
            None,
        ),
        dependency=closure.observation,
        refused_nodes=targets,
    )


def direct_selection_is_current(selection: EligibleDirectSelection) -> bool:
    """Whether the exact files and configuration still match the classified decision."""

    refreshed = _candidate_binding(
        selection.candidate_root,
        selection.nodes,
        selection.closure,
    )
    return isinstance(refreshed, CandidateBinding) and refreshed == selection.binding


def _resolve_exact_target(
    root: Path,
    target: str,
) -> ResolvedTestTarget | RefusedDirectSelection:
    normalized = _normalized_target(target)
    if isinstance(normalized, RefusedDirectSelection):
        return normalized
    relative_path, class_name, function_name = normalized
    parsed = _parsed_target_module(root, target, relative_path)
    if isinstance(parsed, RefusedDirectSelection):
        return parsed
    tree = parsed
    if not function_name.startswith("test"):
        return _target_refusal(
            DirectRefusalCode.UNSUPPORTED_TARGET,
            target,
            "the final node must be an explicit pytest test function or method",
        )
    functions = _target_functions(tree, class_name, function_name)
    if len(functions) != 1:
        code = (
            DirectRefusalCode.TARGET_MISSING
            if not functions
            else DirectRefusalCode.TARGET_AMBIGUOUS
        )
        return _target_refusal(
            code,
            target,
            "the exact test node did not resolve uniquely",
        )
    function = functions[0]
    if any(_decorator_name(item).endswith("parametrize") for item in function.decorator_list):
        return _target_refusal(
            DirectRefusalCode.PARAMETRIZED_TARGET,
            target,
            "parameterized nodes are not supported because one selector can expand at collection",
        )
    return ResolvedTestTarget(
        node_id=target,
        path=relative_path,
        class_name=class_name,
        function_name=function_name,
        line=function.lineno,
    )


def _normalized_target(
    target: str,
) -> tuple[Path, str | None, str] | RefusedDirectSelection:
    if not target or "\\" in target:
        return _target_refusal(
            DirectRefusalCode.UNSUPPORTED_TARGET,
            target,
            "node IDs must use repository-relative POSIX paths",
        )
    parts = target.split("::")
    if len(parts) not in {2, 3} or any(not part for part in parts):
        return _target_refusal(
            DirectRefusalCode.UNSUPPORTED_TARGET,
            target,
            "select exactly one function or one class method with path.py::node syntax",
        )
    relative = PurePosixPath(parts[0])
    if relative.is_absolute() or ".." in relative.parts or relative.suffix != ".py":
        return _target_refusal(
            DirectRefusalCode.UNSUPPORTED_TARGET,
            target,
            "test paths must be normalized repository-relative Python files",
        )
    relative_path = Path(*relative.parts)
    if relative_path == TEST_ROOT or not relative_path.is_relative_to(TEST_ROOT):
        return _target_refusal(
            DirectRefusalCode.TARGET_OUTSIDE_TEST_ROOT,
            target,
            f"direct diagnostic nodes must live below {TEST_ROOT.as_posix()}",
        )
    return relative_path, parts[1] if len(parts) == 3 else None, parts[-1]


def _parsed_target_module(
    root: Path,
    target: str,
    relative_path: Path,
) -> ast.Module | RefusedDirectSelection:
    path = root / relative_path
    if not path.is_file():
        return _target_refusal(
            DirectRefusalCode.TARGET_MISSING,
            target,
            f"test module does not exist: {relative_path.as_posix()}",
        )
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeError) as error:
        return _target_refusal(
            DirectRefusalCode.UNRESOLVED_DEPENDENCY,
            target,
            f"test module cannot be parsed: {error}",
        )


def _target_functions(
    tree: ast.Module,
    class_name: str | None,
    function_name: str,
) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    if class_name is None:
        return [
            item
            for item in tree.body
            if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
            and item.name == function_name
        ]
    classes = [
        item for item in tree.body if isinstance(item, ast.ClassDef) and item.name == class_name
    ]
    if len(classes) != 1:
        return []
    return [
        item
        for item in classes[0].body
        if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef) and item.name == function_name
    ]


def _decorator_name(node: ast.expr) -> str:
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _decorator_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _candidate_binding(
    root: Path,
    targets: tuple[str, ...],
    closure: ResolvedDependencyClosure,
) -> CandidateBinding | RefusedDirectSelection:
    paths = tuple(
        dict.fromkeys((*closure.paths, *(item.as_posix() for item in CONFIGURATION_PATHS)))
    )
    digest = hashlib.sha256()
    digest.update(POLICY_VERSION.encode("utf-8"))
    for target in targets:
        digest.update(b"\0node\0")
        digest.update(target.encode("utf-8"))
    for relative in paths:
        path = root / relative
        try:
            payload = path.read_bytes()
        except OSError as error:
            return _refused(
                DirectRefusalCode.INVALID_CANDIDATE,
                f"candidate configuration/dependency is unavailable: {relative}: {error}",
                refused_nodes=targets,
            )
        digest.update(b"\0path\0")
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0bytes\0")
        digest.update(payload)
    return CandidateBinding(
        digest=digest.hexdigest(),
        policy_version=POLICY_VERSION,
        configuration_paths=tuple(item.as_posix() for item in CONFIGURATION_PATHS),
    )


def _target_refusal(
    code: DirectRefusalCode,
    target: str,
    message: str,
) -> RefusedDirectSelection:
    return _refused(code, message, target=target, refused_nodes=(target,))


def _refused(
    code: DirectRefusalCode,
    message: str,
    *,
    target: str | None = None,
    dependency: DependencyObservation | None = None,
    refused_nodes: tuple[str, ...] = (),
) -> RefusedDirectSelection:
    return RefusedDirectSelection(
        code=code,
        message=message,
        target=target,
        dependency=dependency,
        refused_nodes=refused_nodes,
    )
