"""Pure contract tests for direct-test evidence and structural eligibility."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agents_remember.models.test_evidence import (
    CandidateBinding,
    CertifyingTestEvidence,
    DiagnosticTestEvidence,
    EvidenceConsumer,
    EvidenceConsumerRefusal,
    EvidencePayloadError,
    _certifying_evidence_from_verified_dagger,
    load_diagnostic_test_evidence,
    require_certifying_evidence,
    test_evidence_payload,
)
from agents_remember.testing.consumer_inventory import ACCEPTING_CONSUMER_INVENTORY
from agents_remember.testing.eligibility import (
    MAX_DIRECT_NODES,
    classify_direct_selection,
    direct_selection_is_current,
)
from agents_remember.testing.selection_contract import (
    DirectRefusalCode,
    EligibleDirectSelection,
    RefusedDirectSelection,
    UnsafeEffectFamily,
)
from agents_remember.testing.unsafe_effects import UNSAFE_EFFECT_RULES


class EvidenceAltitudeTests(unittest.TestCase):
    def test_diagnostic_altitude_survives_a_strict_file_boundary(self) -> None:
        evidence = DiagnosticTestEvidence(
            binding=CandidateBinding("0" * 64, "test-policy", ("pyproject.toml",)),
            nodes=("mcp/tests/test_value.py::test_value",),
            exit_code=0,
        )

        payload = test_evidence_payload(evidence)

        self.assertEqual(load_diagnostic_test_evidence(payload), evidence)
        payload["altitude"] = "certifying"
        with self.assertRaises(EvidencePayloadError):
            load_diagnostic_test_evidence(payload)

    def test_accepting_consumer_inventory_is_closed_and_diagnostic_unreachable(self) -> None:
        expected = set(EvidenceConsumer) - {EvidenceConsumer.LOCAL_FEEDBACK}
        self.assertEqual(
            {contract.consumer for contract in ACCEPTING_CONSUMER_INVENTORY},
            expected,
        )
        self.assertTrue(
            all(not contract.direct_route_reachable for contract in ACCEPTING_CONSUMER_INVENTORY)
        )

    def test_diagnostic_evidence_cannot_enter_accepting_consumers(self) -> None:
        evidence = DiagnosticTestEvidence(
            binding=CandidateBinding("0" * 64, "test-policy", ("pyproject.toml",)),
            nodes=("mcp/tests/test_value.py::test_value",),
            exit_code=0,
        )

        for consumer in EvidenceConsumer:
            if consumer is EvidenceConsumer.LOCAL_FEEDBACK:
                continue
            with self.subTest(consumer=consumer), self.assertRaises(EvidenceConsumerRefusal):
                require_certifying_evidence(evidence, consumer=consumer)

    def test_certifying_evidence_requires_the_verified_dagger_factory(self) -> None:
        with self.assertRaises(TypeError):
            CertifyingTestEvidence(
                candidate_tree="a" * 40,
                result_sha256="b" * 64,
                _authority=object(),
            )

        evidence = _certifying_evidence_from_verified_dagger(
            candidate_tree="a" * 40,
            result_sha256="b" * 64,
        )
        self.assertIs(
            require_certifying_evidence(evidence, consumer=EvidenceConsumer.CLOSEOUT),
            evidence,
        )


class DirectEligibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self._write("pyproject.toml", "[tool.pytest.ini_options]\ntestpaths = ['mcp/tests']\n")

    def test_plain_computation_and_unittest_method_are_eligible(self) -> None:
        self._write(
            "mcp/tests/test_values.py",
            """\
import unittest

def plus_one(value):
    return value + 1

def test_function():
    assert plus_one(1) == 2

class ValueTests(unittest.TestCase):
    def test_method(self):
        self.assertEqual(plus_one(2), 3)
""",
        )

        decision = self._classify(
            "mcp/tests/test_values.py::test_function",
            "mcp/tests/test_values.py::ValueTests::test_method",
        )

        self.assertIsInstance(decision, EligibleDirectSelection)
        assert isinstance(decision, EligibleDirectSelection)
        self.assertEqual(
            decision.nodes,
            (
                "mcp/tests/test_values.py::test_function",
                "mcp/tests/test_values.py::ValueTests::test_method",
            ),
        )
        self.assertTrue(direct_selection_is_current(decision))

    def test_qualified_safe_call_is_not_confused_with_dynamic_builtin(self) -> None:
        self._write(
            "mcp/tests/test_regex.py",
            """\
import re

PATTERN = re.compile(r"^[a-z]+$")

def test_pattern():
    assert PATTERN.pattern == r"^[a-z]+$"
""",
        )

        decision = self._classify("mcp/tests/test_regex.py::test_pattern")

        self.assertIsInstance(decision, EligibleDirectSelection)

    def test_allowed_fixture_and_transitive_helper_chain_are_resolved(self) -> None:
        self._write(
            "mcp/tests/test_fixture.py",
            """\
import pytest

def helper():
    return 2

@pytest.fixture
def value():
    return helper()

def test_value(value):
    assert value == 2
""",
        )

        decision = self._classify("mcp/tests/test_fixture.py::test_value")

        self.assertIsInstance(decision, EligibleDirectSelection)
        assert isinstance(decision, EligibleDirectSelection)
        symbols = {observation.symbol for observation in decision.closure.observations}
        self.assertGreaterEqual(symbols, {"helper", "value", "test_value"})

    def test_every_closed_unsafe_family_has_a_stable_refusal(self) -> None:
        imports = {
            UnsafeEffectFamily.GIT_WORKTREE: "git",
            UnsafeEffectFamily.PROCESS_CONTROL: "subprocess",
            UnsafeEffectFamily.SOCKET_SERVICE: "socket",
            UnsafeEffectFamily.PROVIDER_CONTAINER: "dagger",
            UnsafeEffectFamily.BROWSER_EXTERNAL: "playwright",
            UnsafeEffectFamily.MACHINE_STATE: "keyring",
            UnsafeEffectFamily.MUTABLE_GLOBAL_STATE: "atexit",
            UnsafeEffectFamily.DURABILITY_INTEGRATION: "sqlite3",
        }
        self.assertEqual(set(imports), {rule.family for rule in UNSAFE_EFFECT_RULES})

        for index, (family, imported) in enumerate(imports.items()):
            relative = f"mcp/tests/test_unsafe_{index}.py"
            self._write(relative, f"import {imported}\n\ndef test_value():\n    assert True\n")
            decision = self._classify(f"{relative}::test_value")
            with self.subTest(family=family):
                self.assertRefused(decision, DirectRefusalCode.UNSAFE_EFFECT, family)

    def test_transitive_unsafe_helper_refuses_before_execution(self) -> None:
        self._write(
            "mcp/tests/unsafe_helper.py",
            """\
import subprocess

def compute():
    return 2
""",
        )
        self._write(
            "mcp/tests/test_transitive.py",
            """\
from unsafe_helper import compute

RAISE_IF_IMPORTED = int("classification must not execute candidate code")

def test_value():
    assert compute() == 2
""",
        )

        decision = self._classify("mcp/tests/test_transitive.py::test_value")

        self.assertRefused(
            decision,
            DirectRefusalCode.UNSAFE_EFFECT,
            UnsafeEffectFamily.PROCESS_CONTROL,
        )

    def test_same_named_methods_do_not_share_dependency_cache(self) -> None:
        self._write(
            "mcp/tests/test_duplicate_method_names.py",
            """\
class SafeValues:
    def test_value(self):
        assert 1 + 1 == 2

class UnsafeValues:
    def test_value(self):
        import subprocess
        assert subprocess is not None
""",
        )

        decision = self._classify(
            "mcp/tests/test_duplicate_method_names.py::SafeValues::test_value",
            "mcp/tests/test_duplicate_method_names.py::UnsafeValues::test_value",
        )

        self.assertRefused(
            decision,
            DirectRefusalCode.MIXED_SELECTION,
            UnsafeEffectFamily.PROCESS_CONTROL,
        )

    def test_from_package_import_submodule_resolves_the_submodule_closure(self) -> None:
        self._write("mcp/src/agents_remember/helpers/__init__.py", "")
        self._write(
            "mcp/src/agents_remember/helpers/unsafe_child.py",
            "import subprocess\n",
        )
        self._write(
            "mcp/tests/test_imported_submodule.py",
            """\
from agents_remember.helpers import unsafe_child

def test_value():
    assert unsafe_child is not None
""",
        )

        decision = self._classify("mcp/tests/test_imported_submodule.py::test_value")

        self.assertRefused(
            decision,
            DirectRefusalCode.UNSAFE_EFFECT,
            UnsafeEffectFamily.PROCESS_CONTROL,
        )

    def test_unsafe_autouse_fixture_refuses_even_when_the_body_is_pure(self) -> None:
        self._write(
            "mcp/tests/test_autouse.py",
            """\
import pytest

@pytest.fixture(autouse=True)
def unsafe_guard():
    import subprocess
    yield

def test_value():
    assert 1 + 1 == 2
""",
        )

        decision = self._classify("mcp/tests/test_autouse.py::test_value")

        self.assertRefused(
            decision,
            DirectRefusalCode.UNSAFE_EFFECT,
            UnsafeEffectFamily.PROCESS_CONTROL,
        )

    def test_marker_and_safe_name_cannot_override_transitive_unsafety(self) -> None:
        self._write(
            "mcp/tests/test_unit_pure.py",
            """\
import pytest
import subprocess

@pytest.mark.pure
def test_pure():
    assert True
""",
        )

        decision = self._classify("mcp/tests/test_unit_pure.py::test_pure")

        self.assertRefused(
            decision,
            DirectRefusalCode.UNSAFE_EFFECT,
            UnsafeEffectFamily.PROCESS_CONTROL,
        )

    def test_unknown_dynamic_dependency_refuses(self) -> None:
        self._write(
            "mcp/tests/test_dynamic.py",
            """\
def test_value():
    assert getattr('value', 'upper')() == 'VALUE'
""",
        )

        decision = self._classify("mcp/tests/test_dynamic.py::test_value")

        self.assertRefused(decision, DirectRefusalCode.DYNAMIC_DEPENDENCY)

    def test_request_shape_refusals_are_total(self) -> None:
        self._write("mcp/tests/test_value.py", "def test_value():\n    assert True\n")
        exact = "mcp/tests/test_value.py::test_value"

        cases = (
            ((), DirectRefusalCode.EMPTY_SELECTION),
            ((exact, exact), DirectRefusalCode.DUPLICATE_TARGET),
            (("mcp/tests/test_value.py",), DirectRefusalCode.UNSUPPORTED_TARGET),
            (("mcp/tests/missing.py::test_value",), DirectRefusalCode.TARGET_MISSING),
            (
                tuple(
                    f"mcp/tests/test_value.py::test_{index}"
                    for index in range(MAX_DIRECT_NODES + 1)
                ),
                DirectRefusalCode.OVERSIZED_SELECTION,
            ),
        )
        for targets, code in cases:
            with self.subTest(code=code):
                self.assertRefused(self._classify(*targets), code)

    def test_parameterized_and_ambiguous_nodes_refuse(self) -> None:
        self._write(
            "mcp/tests/test_param.py",
            """\
import pytest

@pytest.mark.parametrize('value', [1, 2])
def test_value(value):
    assert value
""",
        )
        self._write(
            "mcp/tests/test_ambiguous.py",
            """\
def test_value():
    assert True

def test_value():
    assert False
""",
        )

        self.assertRefused(
            self._classify("mcp/tests/test_param.py::test_value"),
            DirectRefusalCode.PARAMETRIZED_TARGET,
        )
        self.assertRefused(
            self._classify("mcp/tests/test_ambiguous.py::test_value"),
            DirectRefusalCode.TARGET_AMBIGUOUS,
        )

    def test_mixed_selection_refuses_as_one_unit(self) -> None:
        self._write("mcp/tests/test_safe.py", "def test_safe():\n    assert True\n")
        self._write(
            "mcp/tests/test_unsafe.py",
            "import socket\n\ndef test_unsafe():\n    assert True\n",
        )

        decision = self._classify(
            "mcp/tests/test_safe.py::test_safe",
            "mcp/tests/test_unsafe.py::test_unsafe",
        )

        self.assertRefused(
            decision,
            DirectRefusalCode.MIXED_SELECTION,
            UnsafeEffectFamily.SOCKET_SERVICE,
        )
        assert isinstance(decision, RefusedDirectSelection)
        self.assertEqual(len(decision.refused_nodes), 2)

    def test_candidate_or_configuration_drift_invalidates_decision(self) -> None:
        self._write(
            "mcp/tests/test_value.py",
            """\
def helper():
    return 2

def test_value():
    assert helper() == 2
""",
        )
        decision = self._classify("mcp/tests/test_value.py::test_value")
        self.assertIsInstance(decision, EligibleDirectSelection)
        assert isinstance(decision, EligibleDirectSelection)

        self._write("pyproject.toml", "[tool.pytest.ini_options]\nstrict = true\n")

        self.assertFalse(direct_selection_is_current(decision))

    def _classify(self, *targets: str):
        return classify_direct_selection(self.root, targets)

    def _write(self, relative: str, content: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def assertRefused(
        self,
        decision,
        code: DirectRefusalCode,
        family: UnsafeEffectFamily | None = None,
    ) -> None:
        self.assertIsInstance(decision, RefusedDirectSelection)
        assert isinstance(decision, RefusedDirectSelection)
        self.assertEqual(decision.code, code)
        if family is not None:
            self.assertIsNotNone(decision.dependency)
            assert decision.dependency is not None
            self.assertEqual(decision.dependency.family, family)


if __name__ == "__main__":
    unittest.main()
