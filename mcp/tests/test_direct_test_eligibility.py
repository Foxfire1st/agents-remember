"""Pure contract tests for evidence altitude and the sealed direct cohort."""

from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path

from _direct_cohort_candidate import SyntheticCohortOptions, write_synthetic_direct_cohort
from _evidence_catalog_fixture import write_synthetic_evidence_catalog
from agents_remember.models.test_evidence import (
    CandidateBinding,
    CertifyingTestEvidence,
    DiagnosticTestEvidence,
    EvidenceConsumer,
    EvidenceConsumerRefusal,
    EvidencePayloadError,
    _certifying_evidence_from_verified_dagger,
    evidence_payload,
    load_diagnostic_test_evidence,
    require_certifying_evidence,
)
from agents_remember.testing.cohort_manifest import MAX_DIRECT_NODES
from agents_remember.testing.consumer_inventory import ACCEPTING_CONSUMER_INVENTORY
from agents_remember.testing.eligibility import (
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

        payload = evidence_payload(evidence)

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
        self._write("mcp/tests/_catalog_anchor.py", "VALUE = 1\n")
        self._write("mcp/tests/test_catalog_consumer.py", "def test_anchor():\n    pass\n")
        write_synthetic_evidence_catalog(
            self.root,
            {"mcp/tests/_catalog_anchor.py": ("mcp/tests/test_catalog_consumer.py",)},
        )

    def test_fixture_and_helper_closure_is_content_sealed(self) -> None:
        path = "mcp/tests/test_values.py"
        self._write(
            path,
            """\
import pytest

def plus_one(value):
    return value + 1

@pytest.fixture
def value():
    return plus_one(1)

def test_value(value):
    assert value == 2
""",
        )
        node = f"{path}::test_value"
        self._write_manifest((node,), (path,))

        decision = self._classify(node)

        self.assertIsInstance(decision, EligibleDirectSelection)
        assert isinstance(decision, EligibleDirectSelection)
        self.assertEqual(decision.nodes, (node,))
        self.assertTrue(direct_selection_is_current(decision))
        self.assertEqual(decision.closure.paths, (path,))

    def test_only_explicit_manifest_nodes_can_enter(self) -> None:
        path = "mcp/tests/test_values.py"
        self._write(
            path,
            "def test_admitted():\n    assert True\n\ndef test_outside():\n    assert True\n",
        )
        admitted = f"{path}::test_admitted"
        outside = f"{path}::test_outside"
        self._write_manifest((admitted,), (path,))

        self.assertRefused(self._classify(outside), DirectRefusalCode.NOT_IN_COHORT)
        self.assertRefused(
            self._classify(admitted, outside),
            DirectRefusalCode.MIXED_SELECTION,
        )

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
        path = "mcp/tests/test_unsafe.py"
        node = f"{path}::test_value"

        for family, imported in imports.items():
            self._write(path, f"import {imported}\n\ndef test_value():\n    assert True\n")
            self._write_manifest(
                (node,),
                (path,),
                options=SyntheticCohortOptions(effects={path: (family.value,)}),
            )
            with self.subTest(family=family):
                self.assertRefused(
                    self._classify(node),
                    DirectRefusalCode.UNSAFE_EFFECT,
                    family,
                )

    def test_transitive_unsafe_helper_refuses(self) -> None:
        helper = "mcp/tests/unsafe_helper.py"
        test = "mcp/tests/test_transitive.py"
        self._write(helper, "import subprocess\n\ndef compute():\n    return 2\n")
        self._write(
            test,
            "from unsafe_helper import compute\n\ndef test_value():\n    assert compute() == 2\n",
        )
        node = f"{test}::test_value"
        self._write_manifest(
            (node,),
            (test, helper),
            options=SyntheticCohortOptions(
                local_imports={test: (helper,)},
                effects={helper: (UnsafeEffectFamily.PROCESS_CONTROL.value,)},
            ),
        )

        self.assertRefused(
            self._classify(node),
            DirectRefusalCode.UNSAFE_EFFECT,
            UnsafeEffectFamily.PROCESS_CONTROL,
        )

    def test_omitted_candidate_import_is_unresolved(self) -> None:
        helper = "mcp/tests/value_helper.py"
        test = "mcp/tests/test_transitive.py"
        self._write(helper, "def compute():\n    return 2\n")
        self._write(
            test,
            "from value_helper import compute\n\ndef test_value():\n    assert compute() == 2\n",
        )
        node = f"{test}::test_value"
        self._write_manifest(
            (node,),
            (test,),
            options=SyntheticCohortOptions(local_imports={test: (helper,)}),
        )

        self.assertRefused(self._classify(node), DirectRefusalCode.UNRESOLVED_DEPENDENCY)

    def test_audited_file_unreachable_from_every_node_is_unresolved(self) -> None:
        test = "mcp/tests/test_value.py"
        unused = "mcp/tests/unused_safe_helper.py"
        node = f"{test}::test_value"
        self._write(test, "def test_value():\n    assert True\n")
        self._write(unused, "def harmless():\n    return 1\n")
        self._write_manifest(
            (node,),
            (test, unused),
            options=SyntheticCohortOptions(closures={node: (node,)}),
        )

        self.assertRefused(self._classify(node), DirectRefusalCode.UNRESOLVED_DEPENDENCY)

    def test_dynamic_and_unsafe_calls_refuse(self) -> None:
        path = "mcp/tests/test_calls.py"
        node = f"{path}::test_value"
        cases = (
            (
                "def test_value():\n    assert getattr('value', 'upper')() == 'VALUE'\n",
                DirectRefusalCode.DYNAMIC_DEPENDENCY,
                None,
                False,
            ),
            (
                "import os\n\ndef test_value():\n    os.system('true')\n",
                DirectRefusalCode.UNSAFE_EFFECT,
                UnsafeEffectFamily.PROCESS_CONTROL,
                True,
            ),
        )
        for source, code, family, known in cases:
            self._write(path, source)
            self._write_manifest(
                (node,),
                (path,),
                options=SyntheticCohortOptions(
                    effects={} if family is None else {path: (family.value,)},
                    effects_known={path: known},
                ),
            )
            with self.subTest(code=code):
                self.assertRefused(self._classify(node), code, family)

    def test_unknown_external_and_unaudited_autouse_dependencies_refuse(self) -> None:
        path = "mcp/tests/test_dependencies.py"
        node = f"{path}::test_value"
        self._write(path, "import mystery_runtime\n\ndef test_value():\n    assert True\n")
        self._write_manifest(
            (node,),
            (path,),
            options=SyntheticCohortOptions(effects_known={path: False}),
        )
        self.assertRefused(self._classify(node), DirectRefusalCode.DYNAMIC_DEPENDENCY)

        self._write(
            path,
            """\
import pytest

@pytest.fixture(autouse=True)
def hidden_fixture():
    yield

def test_value():
    assert True
""",
        )
        self._write_manifest((node,), (path,), symbols={path: ("test_value",)})
        self.assertRefused(self._classify(node), DirectRefusalCode.UNSUPPORTED_FIXTURE)

    def test_request_and_node_shape_refusals_are_total(self) -> None:
        path = "mcp/tests/test_value.py"
        node = f"{path}::test_value"
        self._write(path, "def test_value():\n    assert True\n")
        self._write_manifest((node,), (path,))

        self.assertRefused(self._classify(), DirectRefusalCode.EMPTY_SELECTION)
        self.assertRefused(self._classify(node, node), DirectRefusalCode.DUPLICATE_TARGET)
        self.assertRefused(
            self._classify(*(node for _ in range(MAX_DIRECT_NODES + 1))),
            DirectRefusalCode.OVERSIZED_SELECTION,
        )

        self._write(
            path,
            "import pytest\n\n@pytest.mark.parametrize('value', [1, 2])\ndef test_value(value):\n    assert value\n",
        )
        self._write_manifest((node,), (path,))
        self.assertRefused(self._classify(node), DirectRefusalCode.PARAMETRIZED_TARGET)

        self._write(path, "def test_value():\n    pass\n\ndef test_value():\n    pass\n")
        self._write_manifest((node,), (path,), symbols={path: ("test_value",)})
        self.assertRefused(self._classify(node), DirectRefusalCode.TARGET_AMBIGUOUS)

    def test_source_or_configuration_drift_never_silently_rebaselines(self) -> None:
        path = "mcp/tests/test_value.py"
        node = f"{path}::test_value"
        self._write(path, "def test_value():\n    assert True\n")
        self._write_manifest((node,), (path,))
        decision = self._classify(node)
        self.assertIsInstance(decision, EligibleDirectSelection)
        assert isinstance(decision, EligibleDirectSelection)

        self._write("pyproject.toml", "[tool.pytest.ini_options]\nstrict = true\n")

        self.assertFalse(direct_selection_is_current(decision))
        self.assertRefused(self._classify(node), DirectRefusalCode.CANDIDATE_CHANGED)

    def _classify(self, *targets: str):
        return classify_direct_selection(self.root, targets)

    def _write_manifest(
        self,
        nodes: tuple[str, ...],
        python_paths: tuple[str, ...],
        *,
        symbols: dict[str, tuple[str, ...]] | None = None,
        options: SyntheticCohortOptions | None = None,
    ) -> None:
        python_symbols: dict[str, tuple[str, ...]] = {}
        for relative in python_paths:
            source = (self.root / relative).read_text(encoding="utf-8")
            python_symbols[relative] = (
                self._symbol_names(source)
                if symbols is None or relative not in symbols
                else symbols[relative]
            )
        write_synthetic_direct_cohort(
            self.root,
            nodes,
            python_symbols,
            options,
        )

    @staticmethod
    def _symbol_names(source: str) -> tuple[str, ...]:
        tree = ast.parse(source)
        names: list[str] = []
        for node in tree.body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                names.append(node.name)
            elif isinstance(node, ast.Assign):
                names.extend(target.id for target in node.targets if isinstance(target, ast.Name))
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names.append(node.target.id)
        return tuple(dict.fromkeys(names))

    def _write(self, relative: str, content: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def assertRefused(
        self,
        decision: object,
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
