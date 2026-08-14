"""L6 closeout coverage tests for projection-types generator edge branches."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.code_quality.projection_types import (
    ProjectionTypeGenerationError,
    _array_type,
    _enum_values,
    _json_literal,
    _nullable_variants,
    _object,
    _objects,
    _ref_name,
    _schema_allowed_keywords,
    _schema_type,
    _state_partition,
    _strings,
    _validate_schema,
    _vocabulary,
    _vocabulary_block,
    _without_null,
    render_typescript,
    served_projection_schema,
    stale_generated_files,
    workspace_projection_schema,
)


class TestPrimitiveValidation:
    def test_object_objects_strings(self) -> None:
        assert _object({}, "x") == {}
        with pytest.raises(ProjectionTypeGenerationError, match="must be an object"):
            _object([], "x")
        assert _objects([{}], "x") == [{}]
        with pytest.raises(ProjectionTypeGenerationError, match="must be an array"):
            _objects({}, "x")
        with pytest.raises(ProjectionTypeGenerationError, match="must be an object"):
            _objects([1], "x")
        assert _strings(["a"], "x") == ("a",)
        with pytest.raises(ProjectionTypeGenerationError, match="array of strings"):
            _strings([1], "x")
        with pytest.raises(ProjectionTypeGenerationError, match="array of strings"):
            _strings("a", "x")

    def test_ref_name_and_nullable(self) -> None:
        assert _ref_name("#/$defs/Foo") == "Foo"
        assert _ref_name("#/$defs/ServingBuildPayload") == "ServingBuild"
        with pytest.raises(ProjectionTypeGenerationError, match="unsupported schema reference"):
            _ref_name("Foo")
        variants = _nullable_variants({"anyOf": [{"type": "null"}, {"type": "string"}]})
        assert variants is not None and len(variants) == 2
        with pytest.raises(ProjectionTypeGenerationError, match="must be an array"):
            _nullable_variants({"anyOf": "x"})
        assert _without_null({"anyOf": [{"type": "null"}, {"type": "string"}]}) == {
            "type": "string"
        }
        with pytest.raises(ProjectionTypeGenerationError, match="exactly one non-null"):
            _without_null({"anyOf": [{"type": "null"}, {"type": "string"}, {"type": "number"}]})

    def test_json_literal_and_enum(self) -> None:
        assert _json_literal(1) == "1"
        with pytest.raises(ProjectionTypeGenerationError, match="JSON scalars"):
            _json_literal({"a": 1})
        with pytest.raises(ProjectionTypeGenerationError, match="not finite JSON"):
            _json_literal(float("nan"))
        assert _enum_values({"enum": ["a"]}) == ("a",)
        with pytest.raises(ProjectionTypeGenerationError, match="array of strings"):
            _enum_values({"enum": "a"})


class TestSchemaAllowedAndType:
    def test_allowed_keywords_branches(self) -> None:
        findings: list[str] = []
        assert "const" in _schema_allowed_keywords({"const": 1}, "x", findings)
        assert "enum" in _schema_allowed_keywords({"enum": ["a"]}, "x", findings)
        assert "items" in _schema_allowed_keywords({"type": "array"}, "x", findings)
        allowed = _schema_allowed_keywords({"type": "object"}, "x", findings)
        assert "properties" in allowed and "required" in allowed
        assert "type" in _schema_allowed_keywords({"type": "string"}, "x", findings)

    def test_validate_schema_flags_unexpected_keywords(self) -> None:
        with pytest.raises(ProjectionTypeGenerationError, match="unsupported JSON Schema"):
            _validate_schema({"type": "object", "items": {}}, "Root")
        with pytest.raises(ProjectionTypeGenerationError, match="unsupported JSON Schema"):
            _validate_schema({"type": "string", "properties": {}}, "Root")
        with pytest.raises(ProjectionTypeGenerationError, match="incompatible schema selectors"):
            _validate_schema({"const": 1, "enum": ["a"]}, "Root")
        with pytest.raises(ProjectionTypeGenerationError, match="JSON scalars"):
            _validate_schema({"const": {"a": 1}}, "Root")

    def test_schema_type_branches(self) -> None:
        vocab = {("a", "b"): "AB"}
        assert _schema_type({"enum": ["a", "b"]}, vocab) == "AB"
        assert (
            _schema_type({"anyOf": [{"type": "null"}, {"type": "string"}]}, {}) == "null | string"
        )
        assert _schema_type({"type": "object", "additionalProperties": True}, {}) == (
            "Record<string, unknown>"
        )
        assert _schema_type({"type": "object", "additionalProperties": {"type": "number"}}, {}) == (
            "Record<string, number>"
        )
        with pytest.raises(ProjectionTypeGenerationError, match="unsupported schema node"):
            _schema_type({"type": "date"}, {})
        with pytest.raises(ProjectionTypeGenerationError, match="unsupported schema node"):
            _schema_type({"foo": 1}, {})
        with pytest.raises(ProjectionTypeGenerationError, match="additionalProperties"):
            _schema_type({"type": "object"}, {})
        assert (
            _array_type({"items": {"anyOf": [{"type": "string"}, {"type": "number"}]}}, {})
            == "(string | number)[]"
        )

    def test_vocabulary_and_block_raises(self) -> None:
        schema = {
            "$defs": {
                "LifecycleProjection": {
                    "properties": {
                        "state": {"enum": ["x"]},
                        "phase": {"type": "string"},
                    }
                },
                "Metrics": {"properties": {"xCount": {"type": "number"}}},
            }
        }
        with pytest.raises(ProjectionTypeGenerationError, match="must be an enum"):
            _vocabulary(schema, "LifecycleProjection", "phase", "Phase")
        with pytest.raises(ProjectionTypeGenerationError, match="state and phase must be enums"):
            _vocabulary_block(schema)

    def test_render_supplements_mismatch(self) -> None:
        core = workspace_projection_schema()
        served = served_projection_schema()
        defs = served.setdefault("$defs", {})
        assert isinstance(defs, dict)
        defs["Extra"] = {"type": "string"}
        with pytest.raises(ProjectionTypeGenerationError, match="served projection schema tail"):
            render_typescript(core, served)

    def test_stale_generated_files(self, tmp_path: Path) -> None:
        stale = stale_generated_files(tmp_path)
        assert Path("dashboard/src/types/projection.schema.json") in stale


class TestStatePartition:
    def test_state_not_enum(self) -> None:
        schema = {
            "$defs": {
                "LifecycleProjection": {
                    "properties": {"state": {"type": "string"}},
                },
                "Metrics": {"properties": {}},
            }
        }
        with pytest.raises(ProjectionTypeGenerationError, match="must be an enum"):
            _state_partition(schema)

    def test_bucket_mapping_mismatch(self) -> None:
        schema = {
            "$defs": {
                "LifecycleProjection": {
                    "properties": {"state": {"enum": ["x"]}},
                },
                "Metrics": {"properties": {"yCount": {"type": "number"}}},
            }
        }
        with pytest.raises(ProjectionTypeGenerationError, match="one-to-one"):
            _state_partition(schema)
