"""Canonical total classifier for the content-sealed direct pytest cohort."""

from __future__ import annotations

import ast
import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from agents_remember.models.test_evidence import CandidateBinding
from agents_remember.testing.cohort_manifest import (
    COHORT_MANIFEST_PATH,
    MAX_DIRECT_NODES,
    POLICY_VERSION,
    CohortManifestError,
    DirectCohortManifest,
    load_direct_cohort_manifest,
)
from agents_remember.testing.selection_contract import (
    DependencyObservation,
    DirectRefusalCode,
    DirectSelectionDecision,
    EligibleDirectSelection,
    RefusedDirectSelection,
    ResolvedDependencyClosure,
    UnsafeEffectFamily,
)
from agents_remember.testing.unsafe_effects import unsafe_family_reason


@dataclass(frozen=True)
class _VerifiedCohort:
    closure: ResolvedDependencyClosure
    artifacts: tuple[tuple[str, bytes], ...]


class _CohortRefusal(RuntimeError):
    def __init__(
        self,
        code: DirectRefusalCode,
        message: str,
        observation: DependencyObservation,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.observation = observation


def classify_direct_selection(
    candidate_root: Path,
    explicit_targets: Sequence[str],
) -> DirectSelectionDecision:
    """Classify one explicit request against the sealed repository policy."""

    request = _validated_request(candidate_root, explicit_targets)
    if isinstance(request, RefusedDirectSelection):
        return request
    root, targets = request
    try:
        manifest = load_direct_cohort_manifest(root)
    except CohortManifestError as error:
        return _controlled_refusal(error.code, str(error), error.observation, targets)
    if refusal := _cohort_membership(manifest, targets):
        return refusal
    try:
        verified = _verify_cohort(root, manifest)
    except _CohortRefusal as error:
        return _controlled_refusal(error.code, str(error), error.observation, targets)
    return EligibleDirectSelection(
        candidate_root=root,
        nodes=targets,
        closure=verified.closure,
        binding=_candidate_binding(manifest, targets, verified.artifacts),
    )


def direct_selection_is_current(selection: EligibleDirectSelection) -> bool:
    """Whether the exact request still receives the same audited binding."""

    refreshed = classify_direct_selection(selection.candidate_root, selection.nodes)
    return isinstance(refreshed, EligibleDirectSelection) and refreshed.binding == selection.binding


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
            "direct diagnostics require at least one exact cohort node",
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


def _cohort_membership(
    manifest: DirectCohortManifest,
    targets: tuple[str, ...],
) -> RefusedDirectSelection | None:
    known = set(manifest.node_ids)
    outside = tuple(target for target in targets if target not in known)
    if not outside:
        return None
    code = (
        DirectRefusalCode.MIXED_SELECTION
        if len(outside) != len(targets)
        else DirectRefusalCode.NOT_IN_COHORT
    )
    return _refused(
        code,
        "the request contains a node outside the closed direct cohort; no node was executed",
        target=outside[0],
        refused_nodes=targets,
    )


def _verify_cohort(root: Path, manifest: DirectCohortManifest) -> _VerifiedCohort:
    trees: dict[str, ast.Module] = {}
    artifacts: list[tuple[str, bytes]] = []
    observations: list[DependencyObservation] = []
    for item in manifest.python_files:
        payload = _verified_payload(root, item.path, item.sha256)
        artifacts.append((item.path, payload))
        try:
            tree = ast.parse(payload.decode("utf-8"), filename=item.path)
        except (SyntaxError, UnicodeError) as error:
            raise _refusal(
                DirectRefusalCode.UNRESOLVED_DEPENDENCY,
                item.path,
                "parse",
                f"audited Python file cannot be parsed: {error}",
            ) from error
        _verify_symbols(item.path, tree, item.symbols)
        if not item.effects_known:
            raise _refusal(
                DirectRefusalCode.DYNAMIC_DEPENDENCY,
                item.path,
                "effects",
                "the manifest declares unresolved or dynamic effects",
            )
        if item.effects:
            family = item.effects[0]
            raise _refusal(
                DirectRefusalCode.UNSAFE_EFFECT,
                item.path,
                "effects",
                unsafe_family_reason(family),
                family=family,
            )
        trees[item.path] = tree
        observations.append(
            _observation(item.path, 1, "fingerprint", f"audited safe file: {item.purpose}")
        )
    for item in manifest.configuration:
        payload = _verified_payload(root, item.path, item.sha256)
        artifacts.append((item.path, payload))
        observations.append(
            _observation(item.path, 1, "configuration", f"audited configuration: {item.purpose}")
        )
    observations.extend(_verify_nodes(manifest, trees))
    return _VerifiedCohort(
        ResolvedDependencyClosure(
            paths=tuple(item.path for item in manifest.python_files),
            observations=tuple(observations),
        ),
        tuple(artifacts),
    )


def _verified_payload(root: Path, relative: str, expected: str) -> bytes:
    try:
        payload = (root / relative).read_bytes()
    except OSError as error:
        raise _refusal(
            DirectRefusalCode.INVALID_CANDIDATE,
            relative,
            "fingerprint",
            f"audited cohort path is unavailable: {error}",
        ) from error
    observed = hashlib.sha256(payload).hexdigest()
    if observed != expected:
        raise _refusal(
            DirectRefusalCode.CANDIDATE_CHANGED,
            relative,
            "fingerprint",
            f"expected {expected}, observed {observed}; review closure before updating the manifest",
        )
    return payload


def _verify_symbols(path: str, tree: ast.Module, declared: tuple[str, ...]) -> None:
    counts: dict[str, int] = {}
    for node in tree.body:
        for name in _defined_names(node):
            counts[name] = counts.get(name, 0) + 1
    for name in declared:
        if counts.get(name, 0) == 1:
            continue
        code = (
            DirectRefusalCode.TARGET_AMBIGUOUS
            if counts.get(name, 0) > 1
            else DirectRefusalCode.UNRESOLVED_DEPENDENCY
        )
        raise _refusal(code, path, name, "audited symbol does not resolve exactly once")


def _verify_nodes(
    manifest: DirectCohortManifest,
    trees: dict[str, ast.Module],
) -> list[DependencyObservation]:
    observations: list[DependencyObservation] = []
    files = {item.path: item for item in manifest.python_files}
    for item in manifest.nodes:
        parts = item.node_id.split("::")
        if len(parts) != 2 or not parts[1].startswith("test"):
            raise _refusal(
                DirectRefusalCode.UNSUPPORTED_TARGET,
                parts[0],
                item.node_id,
                "cohort nodes must be exact top-level test functions",
            )
        path, name = parts
        node = _function(trees[path], name)
        if node is None:
            raise _refusal(
                DirectRefusalCode.TARGET_MISSING,
                path,
                name,
                "cohort node does not resolve exactly once",
            )
        if any(_decorator_name(value).endswith("parametrize") for value in node.decorator_list):
            raise _refusal(
                DirectRefusalCode.PARAMETRIZED_TARGET,
                path,
                name,
                "parameterized nodes can expand during collection",
            )
        _verify_fixtures(path, node, item.closure, trees[path], files[path].symbols)
        observations.extend(
            _observation(*_closure_ref(reference), f"audited node closure: {item.rationale}")
            for reference in item.closure
        )
    return observations


def _verify_fixtures(
    path: str,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    closure: tuple[str, ...],
    tree: ast.Module,
    declared_symbols: tuple[str, ...],
) -> None:
    closure_set = set(closure)
    for argument in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
        reference = f"{path}::{argument.arg}"
        fixture = _function(tree, argument.arg)
        if reference not in closure_set or fixture is None or not _is_fixture(fixture):
            raise _refusal(
                DirectRefusalCode.UNSUPPORTED_FIXTURE,
                path,
                argument.arg,
                "fixture parameter is outside the audited node closure",
            )
    for candidate in tree.body:
        if (
            isinstance(candidate, ast.FunctionDef | ast.AsyncFunctionDef)
            and _is_autouse_fixture(candidate)
            and (
                candidate.name not in declared_symbols
                or f"{path}::{candidate.name}" not in closure_set
            )
        ):
            raise _refusal(
                DirectRefusalCode.UNSUPPORTED_FIXTURE,
                path,
                candidate.name,
                "autouse fixture is outside the audited node closure",
            )


def _candidate_binding(
    manifest: DirectCohortManifest,
    targets: tuple[str, ...],
    artifacts: tuple[tuple[str, bytes], ...],
) -> CandidateBinding:
    digest = hashlib.sha256()
    digest.update(POLICY_VERSION.encode("utf-8"))
    digest.update(b"\0manifest\0")
    digest.update(manifest.payload)
    for target in targets:
        digest.update(b"\0node\0")
        digest.update(target.encode("utf-8"))
    for relative, payload in artifacts:
        digest.update(b"\0path\0")
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0bytes\0")
        digest.update(payload)
    return CandidateBinding(
        digest=digest.hexdigest(),
        policy_version=POLICY_VERSION,
        configuration_paths=(
            COHORT_MANIFEST_PATH.as_posix(),
            *(item.path for item in manifest.configuration),
        ),
    )


def _function(
    tree: ast.Module,
    name: str,
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    functions = [
        item
        for item in tree.body
        if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef) and item.name == name
    ]
    return functions[0] if len(functions) == 1 else None


def _defined_names(node: ast.stmt) -> tuple[str, ...]:
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        return (node.name,)
    if isinstance(node, ast.Assign):
        return tuple(target.id for target in node.targets if isinstance(target, ast.Name))
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return (node.target.id,)
    return ()


def _decorator_name(node: ast.expr) -> str:
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _decorator_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _is_fixture(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(_decorator_name(item).endswith("fixture") for item in node.decorator_list)


def _is_autouse_fixture(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        isinstance(decorator, ast.Call)
        and _decorator_name(decorator).endswith("fixture")
        and any(
            keyword.arg == "autouse"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is True
            for keyword in decorator.keywords
        )
        for decorator in node.decorator_list
    )


def _closure_ref(reference: str) -> tuple[str, int, str]:
    path, symbol = reference.rsplit("::", maxsplit=1)
    return path, 1, symbol


def _controlled_refusal(
    code: DirectRefusalCode,
    message: str,
    observation: DependencyObservation | None,
    targets: tuple[str, ...],
) -> RefusedDirectSelection:
    mixed_codes = {
        DirectRefusalCode.DYNAMIC_DEPENDENCY,
        DirectRefusalCode.PARAMETRIZED_TARGET,
        DirectRefusalCode.UNRESOLVED_DEPENDENCY,
        DirectRefusalCode.UNSAFE_EFFECT,
        DirectRefusalCode.UNSUPPORTED_FIXTURE,
        DirectRefusalCode.UNSUPPORTED_TARGET,
    }
    return _refused(
        DirectRefusalCode.MIXED_SELECTION if len(targets) > 1 and code in mixed_codes else code,
        message,
        dependency=observation,
        refused_nodes=targets,
    )


def _refusal(
    code: DirectRefusalCode,
    path: str,
    symbol: str,
    detail: str,
    *,
    family: UnsafeEffectFamily | None = None,
) -> _CohortRefusal:
    return _CohortRefusal(
        code,
        detail,
        DependencyObservation(
            path=path,
            line=1,
            symbol=symbol,
            detail=detail,
            family=family,
        ),
    )


def _observation(
    path: str,
    line: int,
    symbol: str,
    detail: str,
) -> DependencyObservation:
    return DependencyObservation(path=path, line=line, symbol=symbol, detail=detail)


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
