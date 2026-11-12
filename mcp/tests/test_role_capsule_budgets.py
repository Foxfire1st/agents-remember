"""Role-capsule and separately delivered step-file budgets (MIK-R101/R125).

The matrix includes every role/operation and launcher capsule. Capsule size is the
whitespace-split word count of render_instructions(), excluding task data and seals.
Each declared step file is measured from its complete source text. The registry is
skills/l-01-agent-lifecycles/capsule-budgets.json. Historical size_words and provisional
records stay unchanged. The historical approval names 24 limits; one exact detail approval supersedes its
previous limit. Other budgets
retain their historical capped eleven-tenths formula. Current measurements are pinned
separately from historical records; overages remain failures. Ceilings are 4631 Architect,
4513 Manager, 4100 Curator, 4000 Reviewer/Bootstrap, 3700 others, 2700 details and 1474
digest. Missing and orphan entries, changed measurements, unauthorized limits and ceiling
violations fail. Historical provisional fixtures still exercise their original source and
authority checks.
"""

from __future__ import annotations

import json
import math
import shutil
import sys
from hashlib import sha256
from pathlib import Path

import pytest
from agents_remember.application.role_capsules.compilation import compile_admitted_capsule
from agents_remember.application.role_capsules.sources import CapsuleAdmissionRequest
from agents_remember.application.skill_resources.capsule import (
    CapsuleSeatAddress,
    routed_admission_for,
)
from agents_remember.models.role_capsules.manifest import parse_composition_manifest
from agents_remember.models.role_capsules.types import (
    CapsuleAdmittedFacts,
    CapsuleBinding,
    CapsuleCompilationResult,
    CapsuleLauncherSeat,
    CapsuleRoleSeat,
    CapsuleToolPolicy,
    compute_content_digest,
)
from agents_remember.models.tools.public_roster import PUBLIC_TOOLS
from agents_remember_test_support.testing.capsule_budget_d694 import (
    current_budget_findings,
    current_budget_view,
    current_capsule_ceiling,
    load_current_budget_view,
)
from agents_remember_test_support.testing.capsule_budget_d695 import (
    current_budget_findings as d695_current_budget_findings,
)
from agents_remember_test_support.testing.capsule_budget_d695 import (
    current_budget_view as d695_current_budget_view,
)
from agents_remember_test_support.testing.capsule_budget_d695 import (
    current_capsule_ceiling as d695_current_capsule_ceiling,
)
from agents_remember_test_support.testing.capsule_budget_d695 import (
    load_current_budget_view as d695_load_current_budget_view,
)
from agents_remember_test_support.testing.capsule_budget_snapshot import (
    _D681_DECISION_SHA256,
    _D681_HISTORY_SHA256,
    _D681_LIMITS,
    _D681_MEASUREMENTS,
    _D682_DECISION,
)

__all__ = [
    "current_budget_findings",
    "current_budget_view",
    "current_capsule_ceiling",
    "load_current_budget_view",
]

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
LIFECYCLE_ROOT = REPOSITORY_ROOT / "skills" / "l-01-agent-lifecycles"
BUDGETS_PATH = LIFECYCLE_ROOT / "capsule-budgets.json"
CURRENT_BUDGETS_PATH = LIFECYCLE_ROOT / "capsule-budgets-d694.json"
D695_CURRENT_BUDGETS_PATH = LIFECYCLE_ROOT / "capsule-budgets-current.json"
MANIFEST_RELATIVE = "composition-manifest.json"


def matrix_keys() -> list[str]:
    """Every capsule key the landed source yields, each under role and operation."""
    parsed = parse_composition_manifest((LIFECYCLE_ROOT / MANIFEST_RELATIVE).read_bytes())
    keys = [
        f"{role}/{operation}"
        for role in parsed.role_order
        for operation in parsed.operations
        if role in parsed.operations[operation].applies_to_roles
    ]
    keys.extend(f"launcher/{operation}" for operation in parsed.launcher.operations)
    return keys


def compiled_capsule(
    role: str, operation: str
) -> tuple[CapsuleCompilationResult, CapsuleAdmissionRequest]:
    """Compile the same binding and source admission used by the budget guard."""
    manifest_bytes = (LIFECYCLE_ROOT / MANIFEST_RELATIVE).read_bytes()
    parsed = parse_composition_manifest(manifest_bytes)
    if role == "launcher":
        seat: CapsuleRoleSeat | CapsuleLauncherSeat = CapsuleLauncherSeat(
            routing_condition=parsed.launcher.routing_condition
        )
        request = CapsuleAdmissionRequest(
            root=LIFECYCLE_ROOT,
            manifest=MANIFEST_RELATIVE,
            core=tuple(parsed.core[name].source for name in parsed.launcher.core),
            operation=(parsed.operations[operation].source,),
            skill=tuple(entry.source for entry in parsed.skills.values()),
        )
    else:
        seat = CapsuleRoleSeat(role=role, altitude=parsed.roles[role].altitude)  # type: ignore[arg-type]
        request = routed_admission_for(
            LIFECYCLE_ROOT,
            MANIFEST_RELATIVE,
            manifest_bytes,
            CapsuleSeatAddress(role=role, operation=operation),  # type: ignore[arg-type]
        )
    binding = CapsuleBinding(
        operation=operation,  # type: ignore[arg-type]
        admitted=CapsuleAdmittedFacts(
            task_reference="agents-remember/tasks/demo/task.json",
            task_document_digest=compute_content_digest(b'{"id": "demo"}'),
            seat=seat,
            repository_id="agents-remember",
            work_branch="fixture",
            tool_policy=CapsuleToolPolicy(granted=frozenset(PUBLIC_TOOLS)),
        ),
    )
    outcome = compile_admitted_capsule(binding, request)
    assert outcome.ok, outcome.render_explanation()
    assert outcome.result is not None
    return outcome.result, request


def compiled_size(role: str, operation: str) -> int:
    """Whitespace-split words of the compiled instruction text, seals excluded."""
    result, _request = compiled_capsule(role, operation)
    return len(result.render_instructions().split())


def matrix_measurements() -> dict[str, dict[str, object]]:
    """Keep each complete compiler input set beside its actual output identity."""
    rows = {}
    for key in matrix_keys():
        result, request = compiled_capsule(*key.split("/", 1))
        rendered = result.render_instructions()
        row = result.manifest.summary()
        row.update(
            sizeWords=len(rendered.split()),
            renderedInstructions=rendered,
            renderedDigest=compute_content_digest(rendered.encode()),
            taskDocumentDigest=result.manifest.task_document_digest,
            grantedTools=list(result.manifest.granted_tools),
            compositionOrder=list(result.manifest.composition_order),
            inputSources=[
                {
                    "path": path,
                    "revision": compute_content_digest((request.root / path).read_bytes()),
                }
                for path in request.paths()
            ],
            admission={
                "root": str(request.root),
                "manifest": request.manifest,
                "core": request.core,
                "role": request.role,
                "operation": request.operation,
                "specialization": request.specialization,
                "skill": request.skill,
            },
        )
        for source in result.manifest.sources:
            assert source.revision == compute_content_digest(
                (request.root / source.path).read_bytes()
            ), source.path
        assert {source.path for source in result.manifest.sources} <= set(request.paths())
        assert result.semantic_digest == result.manifest.semantic_digest
        rows[key] = row
    return rows


def detail_sizes() -> dict[str, int]:
    """Every manifest-declared step file gets a budget, even before its first launch."""
    parsed = parse_composition_manifest((LIFECYCLE_ROOT / MANIFEST_RELATIVE).read_bytes())
    return {
        entry.source: len((LIFECYCLE_ROOT / entry.source).read_text(encoding="utf-8").split())
        for entry in parsed.detail_references
    }


def digest_size() -> int:
    """The shared first-message block has its own literal word budget."""
    return len((LIFECYCLE_ROOT / "core/every-role.md").read_text(encoding="utf-8").split())


def _budget_findings(
    sizes: dict[str, int],
    entries: dict,
    ceiling: int,
    *,
    measurements: dict[str, int] | None = None,
    limits: dict[str, int] | None = None,
) -> list[str]:
    findings = [f"{key}: missing budget" for key in sizes.keys() - entries.keys()]
    findings.extend(
        f"{key}: budget names no current file or capsule" for key in entries.keys() - sizes.keys()
    )
    for key in sizes.keys() & entries.keys():
        size, entry = sizes[key], entries[key]
        budget = entry["budget_words"]
        measured = entry["size_words"] if measurements is None else measurements.get(key)
        if size != measured:
            findings.append(
                f"{key}: stale recorded size {measured}; current source has {size} words"
            )
        if size > ceiling or entry["size_words"] > ceiling or budget > ceiling:
            findings.append(
                f"{key}: size {size}, recorded size {entry['size_words']}, "
                f"budget {budget}; ceiling {ceiling}"
            )
        expected_budget = min(math.ceil(entry["size_words"] * 11 / 10), ceiling)
        if limits is not None and key in limits:
            expected_budget = limits[key]
        if budget != expected_budget:
            findings.append(f"{key}: budget does not follow the capped eleven-tenths rule")
        if size > budget:
            findings.append(f"{key}: {size} words exceed recorded budget {budget}")
    return findings


def _projection_findings(entries: dict) -> list[str]:
    findings = []
    for key, entry in entries.items():
        if key == "how":
            continue
        size, budget = entry["size_tokens"], entry["budget_tokens"]
        if size > 3300 or budget > 3300:
            findings.append(f"{key}: projection size {size}, budget {budget}; ceiling 3300 tokens")
        if budget != min(math.ceil(size * 11 / 10), 3300):
            findings.append(
                f"{key}: projection budget does not follow the capped eleven-tenths rule"
            )
    return findings


def _provisional_findings(budgets: dict, sizes: dict[str, int]) -> tuple[list[str], list[str]]:
    provisional = budgets.get("provisional_measurements", {})
    assert set(provisional) <= {"digest", "orchestrator"}, (
        "unrecognized provisional measurement key"
    )
    findings, overages = [], []
    for subject, record in provisional.items():
        is_digest = subject == "digest"
        source = "core/every-role.md" if is_digest else "roles/orchestrator.md"
        ceiling = 1100 if is_digest else 3700
        expected = (
            {source: digest_size()}
            if is_digest
            else {key: size for key, size in sizes.items() if key.startswith("orchestrator/")}
        )
        previous = (
            {source: budgets["digest"]}
            if is_digest
            else {
                key: entry
                for key, entry in budgets["capsules"].items()
                if key.startswith("orchestrator/")
            }
        )
        assert set(previous) == set(expected), "provisional old budget keyset changed"
        assert set(record) == {
            "source",
            "sha256",
            "size_words" if is_digest else "current_sizes",
            "previous",
            "previous_ceiling",
            "state",
            "authority",
        }, "invalid provisional fields"
        assert record["source"] == source, "wrong provisional source"
        assert record["state"] == "awaiting-developer-size-decision", "invalid provisional state"
        assert record["authority"] == "MIK-R84@v6 part 3 requirement 7", (
            "invalid provisional authority"
        )
        assert record["previous"] == (budgets["digest"] if is_digest else previous), (
            "provisional old budget changed"
        )
        assert record["previous_ceiling"] == ceiling, "provisional old ceiling changed"
        assert record["sha256"] == sha256((LIFECYCLE_ROOT / source).read_bytes()).hexdigest(), (
            "stale provisional source hash"
        )
        measured = {source: record["size_words"]} if is_digest else record["current_sizes"]
        assert set(measured) == set(expected), "provisional size keyset changed"
        assert all(type(size) is int for size in measured.values()) and measured == expected, (
            "stale provisional size"
        )
        findings += _budget_findings(
            {key: entry["size_words"] for key, entry in previous.items()}, previous, ceiling
        )
        for key, size in expected.items():
            if size > previous[key]["budget_words"]:
                overages.append(
                    f"{key}: {size} words exceed old budget {previous[key]['budget_words']}"
                )
            if size > ceiling:
                overages.append(f"{key}: {size} words exceed old ceiling {ceiling}")
    return findings, overages


def capsule_ceiling(key: str) -> int:
    """D633/D656 literals apply to the role at every operation, not one stage."""
    role = key.split("/", 1)[0]
    # D656/v11 amendment2: ceil(4210*1.1)=4631 Architect (formerly4200);
    # ceil(4102*1.1)=4513 Manager (formerly4000); no duty cut.
    if role == "architect":
        return 4631
    if role == "manager":
        return 4513
    if role == "curator":
        return 4100
    if role in {"reviewer", "bootstrap"}:
        return 4000
    return 3700


def _capsule_findings(
    sizes: dict[str, int],
    entries: dict,
    *,
    measurements: dict[str, int] | None = None,
    limits: dict[str, int] | None = None,
) -> list[str]:
    return [
        finding
        for ceiling in (4631, 4513, 4100, 4000, 3700)
        for finding in _budget_findings(
            {key: size for key, size in sizes.items() if capsule_ceiling(key) == ceiling},
            {key: entry for key, entry in entries.items() if capsule_ceiling(key) == ceiling},
            ceiling,
            measurements=measurements,
            limits=limits,
        )
    ]


def test_every_yielded_capsule_has_a_budget_and_no_other_key_does() -> None:
    budgets = d695_current_budget_view(d695_load_current_budget_view(D695_CURRENT_BUDGETS_PATH))
    assert budgets["schema"] == "ar-role-capsule-budgets/v1"
    assert sorted(budgets["capsules"]) == sorted(matrix_keys())
    assert sorted(budgets.get("step_details", {})) == sorted(detail_sizes())
    assert set(budgets["digest"]) == {"size_words", "budget_words"}


def _assert_raise_duty_words(raise_record: dict) -> None:
    evidence = raise_record["dutyWordEvidence"]
    assert evidence["afterWords"] == raise_record["measuredWords"]
    assert evidence["afterWords"] - evidence["beforeWords"] == evidence["netWords"]
    assert "D656" in raise_record["authority"]
    if evidence["kind"] == "unchanged-digest":
        assert len(evidence["text"].split()) == evidence["beforeWords"]
        assert evidence["netWords"] == 0
        assert evidence["beforeSha256"] == evidence["afterSha256"]
    else:
        assert evidence["kind"] == "rendered-passage-changes"
        assert evidence["passages"]
        for passage in evidence["passages"]:
            assert len(passage["beforeText"].split()) == passage["beforeWords"]
            assert len(passage["afterText"].split()) == passage["afterWords"]
            assert passage["afterWords"] - passage["beforeWords"] == passage["netWords"]
            assert passage["afterSource"]["path"]
            assert passage["afterSource"]["sha256"]
        assert sum(p["netWords"] for p in evidence["passages"]) == evidence["netWords"]


def test_ceiling_raises_keep_the_measured_duty_words() -> None:
    budgets = json.loads(BUDGETS_PATH.read_text(encoding="utf-8"))
    raises = budgets["decisions_D656_R8"]["raises"]
    expected = {
        "architect": (4166, 4210, 44),
        "manager": (3910, 4102, 192),
        "shared digest": (1000, 1000, 0),
    }
    assert {row["subject"] for row in raises} == set(expected)
    for row in raises:
        _assert_raise_duty_words(row)
        evidence = row["dutyWordEvidence"]
        assert (evidence["beforeWords"], evidence["afterWords"], evidence["netWords"]) == (
            expected[row["subject"]]
        )
        missing = dict(row)
        del missing["dutyWordEvidence"]
        with pytest.raises(KeyError):
            _assert_raise_duty_words(missing)
        wrong = json.loads(json.dumps(row))
        wrong["dutyWordEvidence"]["netWords"] += 1
        with pytest.raises(AssertionError):
            _assert_raise_duty_words(wrong)


def _approved_budget_findings(
    budgets: dict, sizes: dict[str, int], details: dict[str, int], digest: int
) -> list[str]:
    """Check exact current approvals and frozen historical budgets."""

    def fingerprint(value: object) -> str:
        return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    assert fingerprint(budgets["decisions_D681"]) == _D681_DECISION_SHA256, (
        "D681 authority or exact decision record changed"
    )
    assert budgets.get("decisions_D682") == _D682_DECISION, (
        "D682 authority or exact decision record missing or changed"
    )
    history = {
        key: value
        for key, value in budgets.items()
        if key
        not in {
            "authority",
            "how",
            "budget_authority",
            "capsule_ceilings_words",
            "capsules",
            "step_details",
            "digest",
            "decisions_D681",
            "decisions_D682",
        }
    }
    history["size_words"] = {
        area: {key: entry["size_words"] for key, entry in budgets[area].items()}
        for area in ("capsules", "step_details")
    }
    history["size_words"]["digest"] = budgets["digest"]["size_words"]
    assert fingerprint(history) == _D681_HISTORY_SHA256, "historical record or size_words changed"
    assert budgets["capsule_ceilings_words"] == {
        "architect": 4631,
        "manager": 4513,
        "curator": 4100,
        "reviewer": 4000,
        "bootstrap": 4000,
        "every_other_role": 3700,
    }, "unauthorized capsule ceiling"
    findings = _capsule_findings(
        sizes,
        budgets["capsules"],
        measurements=_D681_MEASUREMENTS["capsules"],
        limits=_D681_LIMITS["capsules"],
    )
    findings += _budget_findings(
        details,
        budgets["step_details"],
        2700,
        measurements=_D681_MEASUREMENTS["step_details"],
        limits={
            **_D681_LIMITS["step_details"],
            _D682_DECISION["key"]: _D682_DECISION["newBudgetWords"],
        },
    )
    findings += _budget_findings(
        {"core/every-role.md": digest},
        {"core/every-role.md": budgets["digest"]},
        1474,
        measurements=_D681_MEASUREMENTS["digest"],
        limits=_D681_LIMITS["digest"],
    )
    findings += _projection_findings(budgets["handover"])
    return findings


def test_no_capsule_exceeds_its_recorded_budget() -> None:
    budgets = d695_load_current_budget_view(D695_CURRENT_BUDGETS_PATH)
    sizes = {key: compiled_size(*key.split("/", 1)) for key in matrix_keys()}
    details = detail_sizes()
    findings = d695_current_budget_findings(budgets, sizes, details, digest_size(), REPOSITORY_ROOT)
    findings += _projection_findings(json.loads(BUDGETS_PATH.read_text())["handover"])
    assert not findings, "\n".join(findings)
    print(
        json.dumps(
            {
                "capsules": sizes,
                "step_details": details,
                "capsule_ceilings": {
                    key: d695_current_capsule_ceiling(key, budgets) for key in sizes
                },
                "step_detail_ceiling": budgets["current_step_ceiling_words"],
                "digest_words": digest_size(),
                "digest_ceiling": budgets["current_digest_ceiling_words"],
                "historical_provisional_measurements": json.loads(BUDGETS_PATH.read_text())[
                    "provisional_measurements"
                ],
            },
            sort_keys=True,
        )
    )


def _assert_current_fixture_budgets() -> None:
    document = json.loads(BUDGETS_PATH.read_text())
    findings = _capsule_findings(
        {key: compiled_size(*key.split("/", 1)) for key in matrix_keys()}, document["capsules"]
    )
    findings += _budget_findings(detail_sizes(), document["step_details"], 2700)
    findings += _budget_findings(
        {"core/every-role.md": digest_size()}, {"core/every-role.md": document["digest"]}, 1474
    )
    findings += _projection_findings(document["handover"])
    assert not findings, "\n".join(findings)


def test_budget_guard_rejects_growth_ceiling_and_unregistered_details(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutate the real guard's inputs, not a second implementation of its rules."""
    module = sys.modules[__name__]
    path = tmp_path / "budgets.json"
    monkeypatch.setattr(module, "BUDGETS_PATH", path)
    monkeypatch.setattr(module, "matrix_keys", lambda: ["worker/implementation"])
    observed = {"capsule": 100, "detail": 10}
    monkeypatch.setattr(module, "compiled_size", lambda *_: observed["capsule"])
    monkeypatch.setattr(module, "digest_size", lambda: observed.get("digest", 10))
    monkeypatch.setattr(
        module, "detail_sizes", lambda: {"reference/example.md": observed["detail"]}, raising=False
    )
    capsule = {"size_words": 100, "budget_words": 110}
    detail = {"size_words": 10, "budget_words": 11}

    def write(capsule_entry: dict, detail_entries: dict) -> None:
        path.write_text(
            json.dumps(
                {
                    "capsules": {"worker/implementation": capsule_entry},
                    "step_details": detail_entries,
                    "digest": {"size_words": 10, "budget_words": 11},
                    "handover": {"fixture": {"size_tokens": 100, "budget_tokens": 110}},
                }
            )
        )

    write(capsule, {"reference/example.md": detail})
    _assert_current_fixture_budgets()
    for count, entry, details, expected in (
        (111, capsule, {"reference/example.md": detail}, "exceed"),
        (
            3710,
            {"size_words": 3710, "budget_words": 4081},
            {"reference/example.md": detail},
            "ceiling",
        ),
        (
            100,
            {"size_words": 3701, "budget_words": 3700},
            {"reference/example.md": detail},
            "ceiling",
        ),
        (100, capsule, {}, "missing"),
        (
            100,
            capsule,
            {"reference/example.md": {"size_words": 2701, "budget_words": 2972}},
            "ceiling",
        ),
    ):
        observed["capsule"] = count
        write(entry, details)
        with pytest.raises(AssertionError, match=expected):
            _assert_current_fixture_budgets()

    # Exercise the real guard at both literal ceilings, including recorded-budget
    # attempts to raise one; no new test population or parameter budget is added.
    for role, ceiling in (
        ("architect", 4631),
        ("curator", 4100),
        ("reviewer", 4000),
        ("bootstrap", 4000),
        ("worker", 3700),
        ("manager", 4513),
        ("orchestrator", 3700),
        ("designer", 3700),
        ("investigator", 3700),
        ("strategist", 3700),
        ("launcher", 3700),
    ):
        for operation in ("orientation", "recovery"):
            key = f"{role}/{operation}"
            assert capsule_ceiling(key) == ceiling
            monkeypatch.setattr(module, "matrix_keys", lambda key=key: [key])

            def write_at_limit(size: int, budget: int, *, key: str = key) -> None:
                path.write_text(
                    json.dumps(
                        {
                            "capsules": {key: {"size_words": size, "budget_words": budget}},
                            "step_details": {"reference/example.md": detail},
                            "digest": {"size_words": 10, "budget_words": 11},
                            "handover": {"fixture": {"size_tokens": 100, "budget_tokens": 110}},
                        }
                    )
                )

            observed["capsule"] = ceiling
            write_at_limit(ceiling, ceiling)
            _assert_current_fixture_budgets()
            observed["capsule"] = ceiling + 1
            write_at_limit(ceiling, ceiling)
            with pytest.raises(AssertionError, match="ceiling"):
                _assert_current_fixture_budgets()
            observed["capsule"] = ceiling
            write_at_limit(ceiling + 1, ceiling + 1)
            with pytest.raises(AssertionError, match="ceiling"):
                _assert_current_fixture_budgets()

    # A generous capsule budget cannot waive the separately delivered digest's
    # literal 1474-word ceiling or its own capped eleven-tenths budget.
    observed["capsule"] = 100
    monkeypatch.setattr(module, "matrix_keys", lambda: ["worker/implementation"])
    for size, entry, expected in (
        (1474, {"size_words": 1474, "budget_words": 1474}, None),
        (1475, {"size_words": 1474, "budget_words": 1474}, "ceiling"),
        (1474, {"size_words": 1475, "budget_words": 1475}, "ceiling"),
    ):
        observed["digest"] = size
        write(capsule, {"reference/example.md": detail})
        document = json.loads(path.read_text())
        document["digest"] = entry
        path.write_text(json.dumps(document))
        if expected is None:
            _assert_current_fixture_budgets()
        else:
            with pytest.raises(AssertionError, match=expected):
                _assert_current_fixture_budgets()


def _assert_provisional_fixture_budgets(value: dict) -> None:
    BUDGETS_PATH.write_text(json.dumps(value))
    sizes = {key: compiled_size(*key.split("/", 1)) for key in matrix_keys()}
    findings, overages = _provisional_findings(value, sizes)
    provisional = value.get("provisional_measurements", {})
    ordinary = {
        key: size
        for key, size in sizes.items()
        if "orchestrator" not in provisional or not key.startswith("orchestrator/")
    }
    entries = {
        key: entry
        for key, entry in value["capsules"].items()
        if "orchestrator" not in provisional or not key.startswith("orchestrator/")
    }
    findings += _capsule_findings(ordinary, entries)
    if "digest" not in provisional:
        findings += _budget_findings(
            {"core/every-role.md": digest_size()}, {"core/every-role.md": value["digest"]}, 1100
        )
    assert not findings, "\n".join(findings)
    print(json.dumps({"digest_ceiling": 1100, "provisional_budget_overages": overages}))


@pytest.mark.parametrize("subject", ["digest", "orchestrator"])
def test_provisional_measurements_preserve_old_limits_and_reject_unbound_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    subject: str,
) -> None:
    module = sys.modules[__name__]
    keys = [key for key in matrix_keys() if key.startswith("orchestrator/")]
    is_digest = subject == "digest"
    source_name = "core/every-role.md" if is_digest else "roles/orchestrator.md"
    size = 1201 if is_digest else 3701
    monkeypatch.setattr(module, "LIFECYCLE_ROOT", tmp_path)
    monkeypatch.setattr(module, "BUDGETS_PATH", tmp_path / "budgets.json")
    monkeypatch.setattr(module, "matrix_keys", lambda: [*keys, "worker/implementation"])
    monkeypatch.setattr(
        module,
        "compiled_size",
        lambda role, _: size if role == "orchestrator" and not is_digest else 100,
    )
    monkeypatch.setattr(module, "digest_size", lambda: size if is_digest else 1000)
    monkeypatch.setattr(module, "detail_sizes", lambda: {})
    source = tmp_path / source_name
    source.parent.mkdir()
    source.write_text("word " * size)
    old = {"size_words": 1000, "budget_words": 1100}
    capsules = {
        key: {"size_words": 100, "budget_words": 110} for key in [*keys, "worker/implementation"]
    }
    previous = old.copy() if is_digest else {key: capsules[key].copy() for key in keys}
    record = {
        "source": source_name,
        "sha256": sha256(source.read_bytes()).hexdigest(),
        "previous": previous,
        "previous_ceiling": 1100 if is_digest else 3700,
        "state": "awaiting-developer-size-decision",
        "authority": "MIK-R84@v6 part 3 requirement 7",
    }
    size_key = "size_words" if is_digest else "current_sizes"
    record[size_key] = size if is_digest else dict.fromkeys(keys, size)
    document = {
        "capsules": capsules,
        "step_details": {},
        "digest": old.copy(),
        "handover": {},
        "provisional_measurements": {subject: record},
    }

    _assert_provisional_fixture_budgets(document)
    output = json.loads(capsys.readouterr().out)
    assert output["digest_ceiling"] == 1100
    assert len(output["provisional_budget_overages"]) == (2 if is_digest else 2 * len(keys))
    assert document["digest"] == old
    for field, value, expected in (
        ("sha256", "0" * 64, "hash"),
        (size_key, size - 1 if is_digest else dict.fromkeys(keys, size - 1), "size"),
        ("source", "core/other.md", "source"),
        ("state", "approved", "state"),
        ("authority", "unapproved", "authority"),
        ("previous_ceiling", 1201, "ceiling"),
        ("previous", {}, "old budget"),
    ):
        changed = json.loads(json.dumps(document))
        changed["provisional_measurements"][subject][field] = value
        with pytest.raises(AssertionError, match=expected):
            _assert_provisional_fixture_budgets(changed)
    changed = json.loads(json.dumps(document))
    changed["provisional_measurements"]["unknown"] = record
    with pytest.raises(AssertionError, match="key"):
        _assert_provisional_fixture_budgets(changed)
    if not is_digest:
        changed = json.loads(json.dumps(document))
        del changed["provisional_measurements"][subject]["current_sizes"][keys[0]]
        with pytest.raises(AssertionError, match="keyset"):
            _assert_provisional_fixture_budgets(changed)
    del changed["provisional_measurements"]
    with pytest.raises(AssertionError, match="ceiling"):
        _assert_provisional_fixture_budgets(changed)
    changed = json.loads(json.dumps(document))
    changed["capsules"]["worker/implementation"]["budget_words"] = 99
    with pytest.raises(AssertionError, match="eleven-tenths"):
        _assert_provisional_fixture_budgets(changed)


def test_changed_source_inside_budget_requires_a_fresh_measurement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A changed source is stale even when its growth still fits the old slack."""
    module = sys.modules[__name__]
    shutil.copy2(LIFECYCLE_ROOT / MANIFEST_RELATIVE, tmp_path / MANIFEST_RELATIVE)
    for relative in detail_sizes():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(LIFECYCLE_ROOT / relative, target)
    monkeypatch.setattr(module, "LIFECYCLE_ROOT", tmp_path)
    sizes = detail_sizes()
    entries = {
        key: {"size_words": size, "budget_words": min(math.ceil(size * 11 / 10), 2700)}
        for key, size in sizes.items()
    }
    assert not _budget_findings(sizes, entries, 2700)
    key = "reference/refused-write.md"
    target = tmp_path / key
    target.write_text(target.read_text() + "\nmeasured\n")
    assert detail_sizes()[key] < entries[key]["budget_words"]
    assert any(
        key in finding and "stale" in finding
        for finding in _budget_findings(detail_sizes(), entries, 2700)
    )


def test_projection_registry_cannot_change_its_literal_or_formula() -> None:
    entries = json.loads(BUDGETS_PATH.read_text())["handover"]
    assert not _projection_findings(entries)
    for size, budget, expected in (
        (3300, 3300, None),
        (3301, 3300, "ceiling 3300 tokens"),
        (3300, 3301, "ceiling 3300 tokens"),
        (100, 109, "eleven-tenths"),
    ):
        findings = _projection_findings(
            {"projection": {"size_tokens": size, "budget_tokens": budget}}
        )
        if expected is None:
            assert not findings
        else:
            assert any(expected in finding for finding in findings)


def test_full_matrix_binds_admitted_sources_and_rendered_identities() -> None:
    rows = matrix_measurements()
    assert set(rows) == set(matrix_keys())
    for key, row in rows.items():
        assert row["operation"] == key.split("/", 1)[1]
        rendered = row["renderedInstructions"]
        assert isinstance(rendered, str)
        assert row["sizeWords"] == len(rendered.split())
        assert row["renderedDigest"] == compute_content_digest(rendered.encode())
        assert row["semanticDigest"]
    json.dumps(rows)


def test_d681_preserves_history_and_only_the_24_named_limits() -> None:
    budgets = json.loads(BUDGETS_PATH.read_text())
    sizes = dict(_D681_MEASUREMENTS["capsules"])
    details = dict(_D681_MEASUREMENTS["step_details"])
    architect = "reference/architect-working-rules.md"
    assert not _approved_budget_findings(budgets, sizes, details, 1474)
    # An approved increase does not authorize later growth past the new limit.
    details[architect] = 2201
    assert any(
        architect in finding and "2201 words exceed recorded budget 2200" in finding
        for finding in _approved_budget_findings(budgets, sizes, details, 1474)
    )
    details[architect] = 2166
    measurements = _D681_MEASUREMENTS["step_details"]
    # Do not change the pinned candidate to manufacture a passing real-source test.
    assert any(
        architect in finding and "stale" in finding
        for finding in _approved_budget_findings(budgets, sizes, details, 1474)
    )
    details[architect] = measurements[architect]
    for area, limits in _D681_LIMITS.items():
        for key in limits:
            changed = json.loads(json.dumps(budgets))
            entry = changed["digest"] if area == "digest" else changed[area][key]
            entry["budget_words"] += 1
            assert any(
                "eleven-tenths" in finding
                for finding in _approved_budget_findings(changed, sizes, details, 1474)
            )
    for mutate, expected in (
        (lambda b: b.pop("decisions_D682"), "missing"),
        (lambda b: b["decisions_D682"].update(authority="unapproved"), "authority"),
        (lambda b: b["decisions_D682"].update(newBudgetWords=2201), "decision"),
        (lambda b: b["decisions_D682"].update(key="reference/child-questions.md"), "decision"),
        (lambda b: b["decisions_D681"].update(authority="unapproved"), "authority"),
        (lambda b: b["decisions_D681"]["budgets"].pop(), "decision"),
        (
            lambda b: b["decisions_D681"]["budgets"].append(
                dict(b["decisions_D681"]["budgets"][0])
            ),
            "decision",
        ),
        (lambda b: b["provisional_measurements"]["digest"].update(sha256="0" * 64), "historical"),
        (lambda b: b["capsules"]["worker/implementation"].update(size_words=0), "historical"),
        (lambda b: b["capsule_ceilings_words"].update(curator=4101), "ceiling"),
    ):
        changed = json.loads(json.dumps(budgets))
        mutate(changed)
        with pytest.raises(AssertionError, match=expected):
            _approved_budget_findings(changed, sizes, details, 1474)
    for key in ("orchestrator/orientation", "worker/implementation"):
        changed_sizes = dict(sizes)
        changed_sizes[key] += 1
        assert any(
            key in finding and "stale" in finding
            for finding in _approved_budget_findings(budgets, changed_sizes, details, 1474)
        )
    for changed_sizes, expected in (
        (
            {key: size for key, size in sizes.items() if key != "worker/implementation"},
            "no current",
        ),
        ({**sizes, "worker/unregistered": 1}, "missing budget"),
    ):
        assert any(
            expected in finding
            for finding in _approved_budget_findings(budgets, changed_sizes, details, 1474)
        )

    changed = json.loads(json.dumps(budgets))
    changed["capsules"]["worker/implementation"]["budget_words"] += 1
    assert any(
        "worker/implementation" in finding and "eleven-tenths" in finding
        for finding in _approved_budget_findings(changed, sizes, details, 1474)
    )
    for changed_details, expected in (
        (
            {key: size for key, size in details.items() if key != "reference/child-questions.md"},
            "no current",
        ),
        ({**details, "reference/unregistered.md": 1}, "missing budget"),
    ):
        assert any(
            expected in finding
            for finding in _approved_budget_findings(budgets, sizes, changed_details, 1474)
        )
