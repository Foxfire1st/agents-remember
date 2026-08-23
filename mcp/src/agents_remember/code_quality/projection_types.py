"""Generate the dashboard projection contract from the Python wire schemas.

``WorkspaceProjection.model_json_schema()`` owns the persisted projection. The HTTP/SSE
snapshot adds two serve-time fields declared by ``ServedWorkspaceProjection``; their
Pydantic definitions are folded into the TypeScript output so the existing public
dashboard contract remains complete without a second field list.

The schema describes accepted model input, while the dashboard reads serialized output.
Core projection and serving-build models use ``exclude_none=True``, so nullable fields are
omitted and become optional TypeScript properties. AgentNotifier heartbeat deliberately dumps
nulls, so its nullable properties remain required ``T | null`` values. Non-null defaults
are always serialized and therefore remain required on the output contract.

The renderer rejects schema forms it does not understand. Widening an unfamiliar form to
``unknown`` would let a Python contract change pass the sync gate while weakening the
generated TypeScript, which is the drift this generator exists to prevent.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

from agents_remember.observer.projection import WorkspaceProjection
from agents_remember.serving.served_state import ServedWorkspaceProjection

SCHEMA_OUTPUT = Path("dashboard/src/types/projection.schema.json")
TYPESCRIPT_OUTPUT = Path("dashboard/src/types/projection.ts")

DEFINITION_RENAMES = {
    "ServingBuildPayload": "ServingBuild",
    "AgentNotifierHeartbeatPayload": "AgentNotifierHeartbeat",
}
NULL_PRESERVING_MODELS = frozenset({"AgentNotifierHeartbeatPayload"})
SCHEMA_ANNOTATION_KEYWORDS = frozenset({"default", "description", "title"})
SCHEMA_KEYWORDS = SCHEMA_ANNOTATION_KEYWORDS | {
    "$defs",
    "$ref",
    "additionalProperties",
    "anyOf",
    "const",
    "enum",
    "items",
    "properties",
    "required",
    "type",
}
PROJECT_PYTHON = "PYTHONPATH=mcp/src python"
REGENERATE_COMMAND = f"{PROJECT_PYTHON} scripts/sync-projection-types.py"
CHECK_COMMAND = f"{REGENERATE_COMMAND} --check"


class ProjectionTypeGenerationError(ValueError):
    """A Python schema shape cannot be represented by this generator."""


def workspace_projection_schema() -> dict[str, object]:
    """The canonical projection schema, copied so callers may safely mutate fixtures."""
    return cast(dict[str, object], WorkspaceProjection.model_json_schema())


def served_projection_schema() -> dict[str, object]:
    """The declared HTTP/SSE snapshot schema, including its serve-time tail."""
    return cast(dict[str, object], ServedWorkspaceProjection.model_json_schema())


def schema_json(schema: Mapping[str, object] | None = None) -> str:
    """Stable bytes for the committed JSON Schema artifact."""
    source = workspace_projection_schema() if schema is None else schema
    return json.dumps(source, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _object(value: object, context: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ProjectionTypeGenerationError(f"{context} must be an object")
    return cast(Mapping[str, object], value)


def _objects(value: object, context: str) -> list[Mapping[str, object]]:
    if not isinstance(value, list):
        raise ProjectionTypeGenerationError(f"{context} must be an array")
    return [_object(item, f"{context}[{index}]") for index, item in enumerate(value)]


def _strings(value: object, context: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ProjectionTypeGenerationError(f"{context} must be an array of strings")
    return tuple(cast(list[str], value))


def _definitions(schema: Mapping[str, object]) -> Mapping[str, object]:
    return _object(schema.get("$defs"), "$defs")


def _properties(model: Mapping[str, object], context: str) -> Mapping[str, object]:
    return _object(model.get("properties"), f"{context}.properties")


def _ref_name(reference: object) -> str:
    prefix = "#/$defs/"
    if not isinstance(reference, str) or not reference.startswith(prefix):
        raise ProjectionTypeGenerationError(f"unsupported schema reference: {reference!r}")
    return DEFINITION_RENAMES.get(reference.removeprefix(prefix), reference.removeprefix(prefix))


def _nullable_variants(node: Mapping[str, object]) -> list[Mapping[str, object]] | None:
    raw = node.get("anyOf")
    return None if raw is None else _objects(raw, "anyOf")


def _is_null(node: Mapping[str, object]) -> bool:
    return node.get("type") == "null"


def _is_nullable(node: Mapping[str, object]) -> bool:
    variants = _nullable_variants(node)
    return variants is not None and any(_is_null(variant) for variant in variants)


def _json_literal(value: object) -> str:
    if value is not None and not isinstance(value, (str, int, float, bool)):
        raise ProjectionTypeGenerationError(
            f"const values must be JSON scalars, got {type(value).__name__}"
        )
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    except ValueError as exc:
        raise ProjectionTypeGenerationError(f"const value is not finite JSON: {value!r}") from exc


def _enum_values(node: Mapping[str, object]) -> tuple[str, ...] | None:
    raw = node.get("enum")
    return None if raw is None else _strings(raw, "enum")


def _schema_allowed_keywords(
    node: Mapping[str, object], path: str, findings: list[str]
) -> frozenset[str] | set[str]:
    selectors = [key for key in ("$ref", "const", "enum", "anyOf") if key in node]
    if len(selectors) > 1:
        findings.append(f"{path}: incompatible schema selectors {', '.join(selectors)}")

    allowed: frozenset[str] | set[str] = SCHEMA_ANNOTATION_KEYWORDS
    if "$ref" in node:
        allowed = allowed | {"$ref"}
    elif "const" in node:
        allowed = allowed | {"const", "type"}
    elif "enum" in node:
        allowed = allowed | {"enum", "type"}
    elif "anyOf" in node:
        allowed = allowed | {"anyOf"}
    elif node.get("type") == "array":
        allowed = allowed | {"items", "type"}
    elif node.get("type") == "object":
        allowed = allowed | {
            "$defs",
            "additionalProperties",
            "properties",
            "required",
            "type",
        }
    else:
        allowed = allowed | {"type"}
    return allowed


def _schema_children(
    node: Mapping[str, object], path: str
) -> list[tuple[str, Mapping[str, object]]]:
    children: list[tuple[str, Mapping[str, object]]] = []
    definitions = node.get("$defs")
    if isinstance(definitions, Mapping):
        children.extend(
            (str(name), cast(Mapping[str, object], child))
            for name, child in definitions.items()
            if isinstance(child, Mapping)
        )
    properties = node.get("properties")
    if isinstance(properties, Mapping):
        children.extend(
            (f"{path}.{name}", cast(Mapping[str, object], child))
            for name, child in properties.items()
            if isinstance(child, Mapping)
        )
    items = node.get("items")
    if isinstance(items, Mapping):
        children.append((f"{path}[]", cast(Mapping[str, object], items)))
    additional = node.get("additionalProperties")
    if isinstance(additional, Mapping):
        children.append((f"{path}{{value}}", cast(Mapping[str, object], additional)))
    variants = node.get("anyOf")
    if isinstance(variants, list):
        children.extend(
            (f"{path}.anyOf[{index}]", cast(Mapping[str, object], child))
            for index, child in enumerate(variants)
            if isinstance(child, Mapping)
        )
    return children


def _validate_schema_node(node: Mapping[str, object], path: str, findings: list[str]) -> None:
    unknown = set(node) - SCHEMA_KEYWORDS
    findings.extend(f"{path}: unsupported keyword {keyword!r}" for keyword in sorted(unknown))

    allowed = _schema_allowed_keywords(node, path, findings)
    unexpected = (set(node) & SCHEMA_KEYWORDS) - set(allowed)
    findings.extend(
        f"{path}: keyword {keyword!r} is unsupported for this schema shape"
        for keyword in sorted(unexpected)
    )

    if "const" in node:
        try:
            _json_literal(node["const"])
        except ProjectionTypeGenerationError as exc:
            findings.append(f"{path}: {exc}")

    for child_path, child in _schema_children(node, path):
        _validate_schema_node(child, child_path, findings)


def _validate_schema(schema: Mapping[str, object], root_name: str) -> None:
    findings: list[str] = []
    _validate_schema_node(schema, root_name, findings)
    if not findings:
        return
    details = "\n".join(f"- {finding}" for finding in sorted(set(findings)))
    raise ProjectionTypeGenerationError(
        "unsupported JSON Schema constraints:\n"
        f"{details}\n"
        "Remediation: extend projection_types.py to render every listed constraint exactly, "
        "or change its owning Pydantic model; never widen the TypeScript type."
    )


def _array_type(node: Mapping[str, object], vocabularies: Mapping[tuple[str, ...], str]) -> str:
    item = _object(node.get("items"), "array.items")
    rendered = _schema_type(item, vocabularies)
    if " | " in rendered:
        rendered = f"({rendered})"
    return f"{rendered}[]"


def _object_type(node: Mapping[str, object], vocabularies: Mapping[tuple[str, ...], str]) -> str:
    additional = node.get("additionalProperties")
    if additional is True:
        return "Record<string, unknown>"
    if isinstance(additional, Mapping):
        value_type = _schema_type(cast(Mapping[str, object], additional), vocabularies)
        return f"Record<string, {value_type}>"
    raise ProjectionTypeGenerationError(
        "inline object schemas must declare additionalProperties as true or a schema"
    )


def _schema_type(node: Mapping[str, object], vocabularies: Mapping[tuple[str, ...], str]) -> str:
    reference = node.get("$ref")
    if reference is not None:
        return _ref_name(reference)
    if "const" in node:
        return _json_literal(node["const"])
    enum = _enum_values(node)
    if enum is not None:
        return vocabularies.get(enum, " | ".join(_json_literal(value) for value in enum))
    variants = _nullable_variants(node)
    if variants is not None:
        rendered = [_schema_type(variant, vocabularies) for variant in variants]
        return " | ".join(dict.fromkeys(rendered))
    schema_type = node.get("type")
    if not isinstance(schema_type, str):
        raise ProjectionTypeGenerationError(f"unsupported schema node: {dict(node)!r}")
    primitive = {
        "string": "string",
        "integer": "number",
        "number": "number",
        "boolean": "boolean",
        "null": "null",
    }.get(schema_type)
    if primitive is not None:
        return primitive
    if schema_type == "array":
        rendered = _array_type(node, vocabularies)
    elif schema_type == "object":
        rendered = _object_type(node, vocabularies)
    else:
        raise ProjectionTypeGenerationError(f"unsupported schema node: {dict(node)!r}")
    return rendered


def _without_null(node: Mapping[str, object]) -> Mapping[str, object]:
    variants = _nullable_variants(node)
    if variants is None:
        return node
    non_null = [variant for variant in variants if not _is_null(variant)]
    if len(non_null) == len(variants):
        return node
    if not non_null:
        raise ProjectionTypeGenerationError("nullable fields must contain a non-null type")
    annotations = {key: value for key, value in node.items() if key != "anyOf"}
    if len(non_null) == 1:
        return {**non_null[0], **annotations}
    return {**annotations, "anyOf": non_null}


def _property_line(
    owner: str,
    name: str,
    node: Mapping[str, object],
    vocabularies: Mapping[tuple[str, ...], str],
) -> str:
    preserve_null = owner in NULL_PRESERVING_MODELS
    optional = _is_nullable(node) and not preserve_null
    rendered = _schema_type(node if preserve_null else _without_null(node), vocabularies)
    return f"  {name}{'?' if optional else ''}: {rendered};"


def _model_interface(
    schema_name: str,
    model: Mapping[str, object],
    vocabularies: Mapping[tuple[str, ...], str],
) -> str:
    public_name = DEFINITION_RENAMES.get(schema_name, schema_name)
    properties = _properties(model, schema_name)
    omitted = set(_metric_bucket_fields(model)) if schema_name == "Metrics" else set()
    extension = " extends LifecycleStateCounts" if schema_name == "Metrics" else ""
    lines = [f"export interface {public_name}{extension} {{"]
    lines.extend(
        _property_line(schema_name, name, _object(node, f"{schema_name}.{name}"), vocabularies)
        for name, node in properties.items()
        if name not in omitted
    )
    lines.append("}")
    return "\n".join(lines)


def _state_count_field(state: str) -> str:
    head, *tail = state.split("-")
    return head + "".join(word[:1].upper() + word[1:] for word in tail) + "Count"


def _state_partition(schema: Mapping[str, object]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    definitions = _definitions(schema)
    lifecycle = _object(definitions.get("LifecycleProjection"), "LifecycleProjection")
    state = _object(_properties(lifecycle, "LifecycleProjection").get("state"), "state")
    states = _enum_values(state)
    if states is None:
        raise ProjectionTypeGenerationError("LifecycleProjection.state must be an enum")
    metrics = _object(definitions.get("Metrics"), "Metrics")
    buckets = set(_metric_bucket_fields(metrics))
    live = tuple(state for state in states if _state_count_field(state) in buckets)
    terminal = tuple(state for state in states if state not in live)
    mapped = {_state_count_field(state) for state in live}
    if mapped != buckets:
        raise ProjectionTypeGenerationError(
            f"Metrics lifecycle buckets do not map one-to-one onto State: {sorted(buckets ^ mapped)}"
        )
    return live, terminal


def _metric_bucket_fields(metrics: Mapping[str, object]) -> tuple[str, ...]:
    properties = _properties(metrics, "Metrics")
    return tuple(name for name in properties if name.endswith("Count") and name != "lifecycleCount")


def _vocabulary(
    schema: Mapping[str, object], model_name: str, property_name: str, label: str
) -> tuple[tuple[str, ...], str]:
    model = _object(_definitions(schema).get(model_name), model_name)
    prop = _object(
        _properties(model, model_name).get(property_name), f"{model_name}.{property_name}"
    )
    values = _enum_values(prop)
    if values is None:
        raise ProjectionTypeGenerationError(f"{model_name}.{property_name} must be an enum")
    return values, label


def _tuple_constant(name: str, values: Sequence[str]) -> str:
    rendered = ", ".join(_json_literal(value) for value in values)
    return f"export const {name} = [{rendered}] as const;"


def _vocabulary_block(schema: Mapping[str, object]) -> tuple[str, dict[tuple[str, ...], str]]:
    live, terminal = _state_partition(schema)
    definitions = _definitions(schema)
    lifecycle = _object(definitions.get("LifecycleProjection"), "LifecycleProjection")
    props = _properties(lifecycle, "LifecycleProjection")
    state = _enum_values(_object(props.get("state"), "LifecycleProjection.state"))
    phase = _enum_values(_object(props.get("phase"), "LifecycleProjection.phase"))
    if state is None or phase is None:
        raise ProjectionTypeGenerationError("LifecycleProjection state and phase must be enums")
    named = [
        (state, "State"),
        (phase, "Phase"),
        _vocabulary(schema, "AttentionItem", "severity", "AttentionSeverity"),
        _vocabulary(schema, "AttentionItem", "lane", "AttentionLane"),
        _vocabulary(schema, "CommitRefNode", "factState", "ProcessFactState"),
        _vocabulary(schema, "EngineProcessNode", "health", "ProcessHealth"),
    ]
    vocabulary_types = dict(named)
    sections = [
        _tuple_constant("LIVE_STATES", live),
        _tuple_constant("TERMINAL_STATES", terminal),
        "export const LIFECYCLE_STATES = [...LIVE_STATES, ...TERMINAL_STATES] as const;",
        "export type State = (typeof LIFECYCLE_STATES)[number];",
        "export type TerminalState = (typeof TERMINAL_STATES)[number];",
        "export type ActiveState = (typeof LIVE_STATES)[number];",
        "export const ACTIVE_STATES: readonly ActiveState[] = LIVE_STATES;",
        "type FiledOnce<S extends never> = S;",
        "export type StatesAreFiledOnce = FiledOnce<ActiveState & TerminalState>;",
        _tuple_constant("PHASES", phase),
        "export type Phase = (typeof PHASES)[number];",
        _tuple_constant("ATTENTION_SEVERITIES", named[2][0]),
        "export type AttentionSeverity = (typeof ATTENTION_SEVERITIES)[number];",
        _tuple_constant("ATTENTION_LANES", named[3][0]),
        "export type AttentionLane = (typeof ATTENTION_LANES)[number];",
        _tuple_constant("PROCESS_FACT_STATES", named[4][0]),
        "export type ProcessFactState = (typeof PROCESS_FACT_STATES)[number];",
        _tuple_constant("PROCESS_HEALTHS", named[5][0]),
        "export type ProcessHealth = (typeof PROCESS_HEALTHS)[number];",
    ]
    return "\n\n".join(sections), vocabulary_types


METRICS_HELPERS = """type Camel<S extends string> = S extends `${infer Head}-${infer Tail}`
  ? `${Head}${Capitalize<Camel<Tail>>}`
  : S;

export type StateCountField<S extends ActiveState> = `${Camel<S>}Count`;

export type LifecycleStateCounts = { [S in ActiveState as StateCountField<S>]: number };

export function stateCountField<S extends ActiveState>(state: S): StateCountField<S> {
  const [head, ...rest] = state.split("-");
  const camel = head + rest.map((word) => word.charAt(0).toUpperCase() + word.slice(1)).join("");
  return `${camel}Count` as StateCountField<S>;
}

function lifecycleStateCounts(
  lifecycles: readonly Pick<LifecycleProjection, "state">[],
): LifecycleStateCounts {
  return Object.fromEntries(
    ACTIVE_STATES.map((state) => [
      stateCountField(state),
      lifecycles.filter((entry) => entry.state === state).length,
    ]),
  ) as LifecycleStateCounts;
}"""

METRICS_FACTORY = """export function metricsFor(lifecycles: readonly LifecycleProjection[]): Metrics {
  return {
    lifecycleCount: lifecycles.length,
    totalTokens: lifecycles.reduce((sum, entry) => sum + entry.tokens, 0),
    stalenessHistogram: {},
    ...lifecycleStateCounts(lifecycles),
  };
}"""

HEADER = f"""// TypeScript mirror of WorkspaceProjection — GENERATED FILE; DO NOT EDIT.
// Canonical core model: WorkspaceProjection.model_json_schema().
// Schema artifact: dashboard/src/types/projection.schema.json.
// Served-only tail: ServedWorkspaceProjection.model_json_schema().
// Generator: scripts/sync-projection-types.py.
// Regenerate: {REGENERATE_COMMAND}
// Drift check: {CHECK_COMMAND}"""


def render_typescript(schema: Mapping[str, object], served_schema: Mapping[str, object]) -> str:
    """Render the public projection module from emitted Pydantic schemas."""
    _validate_schema(schema, "WorkspaceProjection")
    _validate_schema(served_schema, "ServedWorkspaceProjection")
    vocabulary_block, vocabularies = _vocabulary_block(schema)
    definitions = _definitions(schema)
    served_definitions = _definitions(served_schema)
    supplements = {
        name: model for name, model in served_definitions.items() if name not in definitions
    }
    expected_supplements = set(DEFINITION_RENAMES)
    if set(supplements) != expected_supplements:
        raise ProjectionTypeGenerationError(
            "served projection schema tail changed: "
            f"expected {sorted(expected_supplements)}, got {sorted(supplements)}"
        )
    blocks = [HEADER, vocabulary_block]
    for name, raw_model in sorted({**definitions, **supplements}.items()):
        if name == "Metrics":
            blocks.append(METRICS_HELPERS)
        blocks.append(_model_interface(name, _object(raw_model, name), vocabularies))
        if name == "Metrics":
            blocks.append(METRICS_FACTORY)
    blocks.append("export type SubTaskRow = TaskSubTaskRefNode | SeriesSubTaskNode;")
    root = dict(_properties(schema, "WorkspaceProjection"))
    served_root = _properties(served_schema, "ServedWorkspaceProjection")
    root.update({name: node for name, node in served_root.items() if name not in root})
    blocks.append(_model_interface("WorkspaceProjection", {"properties": root}, vocabularies))
    return "\n\n".join(blocks) + "\n"


def typescript_text() -> str:
    """Generate TypeScript from parsed deterministic schema bytes."""
    core = _object(json.loads(schema_json()), "WorkspaceProjection schema")
    served = _object(json.loads(schema_json(served_projection_schema())), "served schema")
    return render_typescript(core, served)


def generated_files() -> Mapping[Path, str]:
    return {SCHEMA_OUTPUT: schema_json(), TYPESCRIPT_OUTPUT: typescript_text()}


def stale_generated_files(repo_root: Path) -> tuple[Path, ...]:
    stale: list[Path] = []
    for relative_path, expected in generated_files().items():
        path = repo_root / relative_path
        if not path.is_file() or path.read_text(encoding="utf-8") != expected:
            stale.append(relative_path)
    return tuple(stale)


def sync_generated_files(repo_root: Path) -> None:
    for relative_path, content in generated_files().items():
        path = repo_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
