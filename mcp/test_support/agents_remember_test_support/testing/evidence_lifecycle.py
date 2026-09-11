"""Typed lifecycle authority for durable test evidence and shared support."""

from __future__ import annotations

import argparse
import ast
import json
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path, PurePosixPath

from agents_remember_test_support.testing.dependency_facts import RepositoryDependencyFacts
from agents_remember_test_support.testing.evidence_governance import (
    LIFECYCLE_CATALOG_PATH,
    governed_artifact_paths,
)

CATALOG_PATH = Path(LIFECYCLE_CATALOG_PATH)
CATALOG_SCHEMA = "ar-test-evidence-lifecycle/v3"
CONTRACT_REFERENCE_PREFIX = "contract:"
NODE_REFERENCE_PREFIX = "node:"


class EvidenceLifecycleError(RuntimeError):
    """The durable evidence catalog is incomplete or contradictory."""


class EvidenceKind(StrEnum):
    FIXTURE = "fixture"
    RECORDING = "recording"
    RECORDING_GENERATOR = "recording-generator"
    SHARED_SUPPORT = "shared-support"
    MIGRATION_PROOF = "migration-proof"


class EvidenceAuthority(StrEnum):
    INTERNAL_CANONICAL = "internal-canonical"
    INTERNAL_HAND_AUTHORED = "internal-hand-authored"
    EXTERNAL_RECORDED = "external-recorded"
    EXTERNAL_SPEC_DERIVED = "external-spec-derived"
    EXTERNAL_MALFORMED = "external-malformed"


class EvidenceCategory(StrEnum):
    UNIT_REGRESSION = "unit-regression"
    PUBLIC_CONTRACT = "public-contract"
    INTEGRATION = "integration"
    PROVIDER_CONFORMANCE = "provider-conformance"
    STRESS_DURABILITY = "stress-durability"
    ARCHITECTURE_FITNESS = "architecture-fitness"
    MIGRATION = "migration"
    DIAGNOSTIC = "diagnostic"


class EvidenceFidelity(StrEnum):
    """Lowest execution fidelity that can truthfully prove an evidence fact."""

    IN_PROCESS = "in-process"
    PUBLIC_BOUNDARY = "public-boundary"
    LOCAL_COMPOSITION = "local-composition"
    INDEPENDENT_BOUNDARY = "independent-boundary"
    PROCESS_RACE = "process-race"
    REPOSITORY_STRUCTURE = "repository-structure"
    TRANSITION_COMPARISON = "transition-comparison"
    CONTROLLED_INVESTIGATION = "controlled-investigation"


class EvidenceCadence(StrEnum):
    AFFECTED = "affected"
    PROVIDER_BUMP = "provider-bump"
    SCHEDULED_RELEASE = "scheduled-release"
    RELEASE = "release"
    DEMO_SMOKE = "demo-smoke"
    MIGRATION_WINDOW = "migration-window"


class EvidenceLifetime(StrEnum):
    PERMANENT = "permanent"
    VERSIONED = "versioned"
    TEMPORARY = "temporary"
    DEMO_ONLY = "demo-only"


class ConsumerScope(StrEnum):
    EXACT = "exact"
    EXACT_SOURCE = "exact-source"
    ALL_TESTS = "all-tests"


@dataclass(frozen=True)
class EvidenceContract:
    """One real owner and executable node behind a stable contract identity."""

    identity: str
    owner: str
    evidence_node: str


@dataclass(frozen=True)
class EvidenceMetadata:
    """One durable artifact and the contract that governs its continued presence."""

    path: str
    kind: EvidenceKind
    authority: EvidenceAuthority
    owner: str
    category: EvidenceCategory
    fidelity: EvidenceFidelity
    cadence: EvidenceCadence
    source_version_or_generator: str
    introduced_by: str
    lifetime: EvidenceLifetime
    replacement_contract: str
    consumer_scope: ConsumerScope
    consumers: tuple[str, ...]
    expires_after: date | None = None
    permanence_rationale: str | None = None


@dataclass(frozen=True)
class EvidenceInventory:
    """Validated catalog used by quality, selection, retry, and reporting."""

    artifacts: tuple[EvidenceMetadata, ...]
    contracts: tuple[EvidenceContract, ...]
    large_fixture_bytes: int

    def by_path(self) -> dict[Path, EvidenceMetadata]:
        return {Path(item.path): item for item in self.artifacts}

    def consumers_for(self, path: Path) -> tuple[Path, ...] | None:
        item = self.by_path().get(path)
        return None if item is None else tuple(Path(value) for value in item.consumers)

    def payload(self) -> dict[str, object]:
        return {
            "schemaVersion": CATALOG_SCHEMA,
            "largeFixtureBytes": self.large_fixture_bytes,
            "contracts": [asdict(item) for item in self.contracts],
            "artifacts": [
                {
                    **asdict(item),
                    "kind": item.kind.value,
                    "authority": item.authority.value,
                    "category": item.category.value,
                    "fidelity": item.fidelity.value,
                    "cadence": item.cadence.value,
                    "lifetime": item.lifetime.value,
                    "consumer_scope": item.consumer_scope.value,
                    "expires_after": (
                        item.expires_after.isoformat() if item.expires_after is not None else None
                    ),
                }
                for item in self.artifacts
            ],
        }


def load_evidence_inventory(
    project_root: Path,
    *,
    today: date | None = None,
) -> EvidenceInventory:
    """Load and completely validate the repository-owned evidence catalog."""

    root = project_root.resolve()
    catalog = root / CATALOG_PATH
    try:
        with catalog.open("rb") as handle:
            raw = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise EvidenceLifecycleError(f"cannot read evidence catalog {catalog}: {error}") from error
    findings: list[str] = []
    if raw.get("schema_version") != CATALOG_SCHEMA:
        findings.append(f"schema_version must be {CATALOG_SCHEMA!r}")
    large_fixture_bytes = raw.get("large_fixture_bytes")
    if isinstance(large_fixture_bytes, bool) or not isinstance(large_fixture_bytes, int):
        findings.append("large_fixture_bytes must be a positive integer")
        large_fixture_bytes = 25_000
    elif large_fixture_bytes <= 0:
        findings.append("large_fixture_bytes must be a positive integer")
    contracts = _load_contracts(raw.get("contract"), findings)
    facts = RepositoryDependencyFacts.build(root)
    if facts.parse_error is not None:
        findings.append(f"source-derived consumer graph is incomplete: {facts.parse_error}")
    if facts.ambiguous_modules:
        findings.append(
            "source-derived consumer graph has ambiguous modules: "
            f"{sorted(facts.ambiguous_modules)}"
        )
    artifacts = _load_artifacts(raw.get("artifact"), facts, findings)
    _validate_contracts(root, contracts, artifacts, findings)
    findings.extend(_artifact_findings(root, artifacts, contracts, facts, today or date.today()))
    _validate_catalog_coverage(
        root,
        artifacts,
        findings,
        large_fixture_bytes=large_fixture_bytes,
    )
    if findings:
        rendered = "\n".join(f"  - {finding}" for finding in findings)
        raise EvidenceLifecycleError(
            f"test evidence lifecycle has {len(findings)} finding(s):\n{rendered}"
        )
    return EvidenceInventory(tuple(artifacts), tuple(contracts), large_fixture_bytes)


def _load_contracts(raw: object, findings: list[str]) -> list[EvidenceContract]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        findings.append("[[contract]] must be a list when stable contract identities are used")
        return []
    contracts: list[EvidenceContract] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            findings.append(f"contract[{index}] must be a table")
            continue
        unknown = set(item) - {"id", "owner", "evidence_node"}
        if unknown:
            findings.append(f"contract[{index}] has unknown fields: {sorted(unknown)}")
            continue
        try:
            contracts.append(
                EvidenceContract(
                    identity=_required_text(item, "id"),
                    owner=_required_text(item, "owner"),
                    evidence_node=_required_text(item, "evidence_node"),
                )
            )
        except (KeyError, TypeError) as error:
            findings.append(f"contract[{index}] is invalid: {error}")
    return contracts


def _load_artifacts(
    raw: object,
    facts: RepositoryDependencyFacts,
    findings: list[str],
) -> list[EvidenceMetadata]:
    if not isinstance(raw, list) or not raw:
        findings.append("catalog must contain at least one [[artifact]]")
        return []
    artifacts: list[EvidenceMetadata] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            findings.append(f"artifact[{index}] must be a table")
            continue
        try:
            artifacts.append(_metadata_from(item, facts))
        except (KeyError, TypeError, ValueError) as error:
            findings.append(f"artifact[{index}] is invalid: {error}")
    return artifacts


def _metadata_from(
    raw: Mapping[str, object],
    facts: RepositoryDependencyFacts,
) -> EvidenceMetadata:
    allowed = {
        "path",
        "kind",
        "authority",
        "owner",
        "category",
        "fidelity",
        "cadence",
        "source_version_or_generator",
        "introduced_by",
        "lifetime",
        "replacement_contract",
        "consumer_scope",
        "consumers",
        "expires_after",
        "permanence_rationale",
    }
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"unknown fields: {sorted(unknown)}")
    consumer_scope = ConsumerScope(_required_text(raw, "consumer_scope"))
    raw_consumers = raw.get("consumers")
    if consumer_scope in {ConsumerScope.EXACT, ConsumerScope.EXACT_SOURCE}:
        if (
            not isinstance(raw_consumers, list)
            or not raw_consumers
            or not all(isinstance(value, str) and value.strip() for value in raw_consumers)
        ):
            raise TypeError(
                "exact and exact-source consumer_scope require a non-empty consumers string list"
            )
        consumers = tuple(raw_consumers)
    else:
        if raw_consumers not in (None, []):
            raise TypeError("all-tests consumer_scope must not duplicate the derived population")
        consumers = tuple(path.as_posix() for path in facts.tests)
    expires = raw.get("expires_after")
    if expires is not None and not isinstance(expires, date):
        raise TypeError("expires_after must be a TOML date")
    return EvidenceMetadata(
        path=_required_text(raw, "path"),
        kind=EvidenceKind(_required_text(raw, "kind")),
        authority=EvidenceAuthority(_required_text(raw, "authority")),
        owner=_required_text(raw, "owner"),
        category=EvidenceCategory(_required_text(raw, "category")),
        fidelity=EvidenceFidelity(_required_text(raw, "fidelity")),
        cadence=EvidenceCadence(_required_text(raw, "cadence")),
        source_version_or_generator=_required_text(raw, "source_version_or_generator"),
        introduced_by=_required_text(raw, "introduced_by"),
        lifetime=EvidenceLifetime(_required_text(raw, "lifetime")),
        replacement_contract=_required_text(raw, "replacement_contract"),
        consumer_scope=consumer_scope,
        consumers=consumers,
        expires_after=expires,
        permanence_rationale=_optional_text(raw, "permanence_rationale"),
    )


def _required_text(raw: Mapping[str, object], key: str) -> str:
    value = raw[key]
    if not isinstance(value, str) or not value.strip():
        raise TypeError(f"{key} must be a non-empty string")
    return value.strip()


def _optional_text(raw: Mapping[str, object], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise TypeError(f"{key} must be a non-empty string when present")
    return value.strip()


def _artifact_findings(
    root: Path,
    artifacts: Sequence[EvidenceMetadata],
    contracts: Sequence[EvidenceContract],
    facts: RepositoryDependencyFacts,
    today: date,
) -> list[str]:
    findings: list[str] = []
    seen: set[str] = set()
    contract_ids = {item.identity for item in contracts}
    for item in artifacts:
        _validate_path_and_consumers(root, item, facts, seen, findings)
        _validate_lifetime(item, today, findings)
        _validate_authority(item, findings)
        _validate_replacement(root, item, contract_ids, findings)
    return findings


def _validate_path_and_consumers(
    root: Path,
    item: EvidenceMetadata,
    facts: RepositoryDependencyFacts,
    seen: set[str],
    findings: list[str],
) -> None:
    path = PurePosixPath(item.path)
    if path.is_absolute() or ".." in path.parts:
        findings.append(f"{item.path}: path must be repository-relative and confined")
    if item.path in seen:
        findings.append(f"{item.path}: duplicate catalog entry")
    seen.add(item.path)
    if not (root / item.path).is_file():
        findings.append(f"{item.path}: cataloged artifact does not exist")
    missing_consumers = [value for value in item.consumers if not (root / value).is_file()]
    if missing_consumers:
        findings.append(f"{item.path}: missing consumers {missing_consumers}")
    observed_paths = (
        facts.observed_source_consumers(Path(item.path))
        if item.consumer_scope is ConsumerScope.EXACT_SOURCE
        else facts.observed_test_consumers(Path(item.path))
    )
    observed = {path.as_posix() for path in observed_paths}
    declared = set(item.consumers)
    if observed != declared:
        missing = sorted(observed - declared)
        unsupported = sorted(declared - observed)
        findings.append(
            f"{item.path}: consumer proof differs from source-derived ownership; "
            f"missing={missing}, unsupported={unsupported}"
        )


def _validate_lifetime(
    item: EvidenceMetadata,
    today: date,
    findings: list[str],
) -> None:
    if item.lifetime is EvidenceLifetime.TEMPORARY:
        if item.expires_after is None:
            findings.append(f"{item.path}: temporary evidence requires expires_after")
        elif item.expires_after < today:
            findings.append(
                f"{item.path}: evidence expired on {item.expires_after.isoformat()}; "
                f"graduate to or verify {item.replacement_contract!r}, then remove it"
            )
        if item.permanence_rationale is not None:
            findings.append(f"{item.path}: temporary evidence cannot claim permanence")
        return
    if item.expires_after is not None:
        findings.append(f"{item.path}: non-temporary evidence cannot declare expires_after")
    if item.permanence_rationale is None:
        findings.append(f"{item.path}: retained evidence requires permanence_rationale")


def _validate_authority(item: EvidenceMetadata, findings: list[str]) -> None:
    if (
        item.category is EvidenceCategory.MIGRATION
        and item.lifetime is not EvidenceLifetime.TEMPORARY
    ):
        findings.append(f"{item.path}: migration evidence must be temporary")
    external = {
        EvidenceAuthority.EXTERNAL_RECORDED,
        EvidenceAuthority.EXTERNAL_SPEC_DERIVED,
        EvidenceAuthority.EXTERNAL_MALFORMED,
    }
    if item.lifetime is EvidenceLifetime.VERSIONED and item.authority not in external:
        findings.append(f"{item.path}: versioned evidence must have external authority")


def _validate_replacement(
    root: Path,
    item: EvidenceMetadata,
    contract_ids: set[str],
    findings: list[str],
) -> None:
    replacement = item.replacement_contract
    if replacement.startswith(CONTRACT_REFERENCE_PREFIX):
        identity = replacement.removeprefix(CONTRACT_REFERENCE_PREFIX).strip()
        if not identity:
            findings.append(f"{item.path}: replacement contract identity is empty")
        elif identity not in contract_ids:
            findings.append(
                f"{item.path}: replacement contract has no registered owner/node: {identity!r}"
            )
        if item.lifetime is EvidenceLifetime.TEMPORARY:
            findings.append(
                f"{item.path}: temporary evidence requires an executable node: replacement"
            )
        return
    if replacement.startswith(NODE_REFERENCE_PREFIX):
        node_id = replacement.removeprefix(NODE_REFERENCE_PREFIX).strip()
        path_text, separator, selector = node_id.partition("::")
        path = PurePosixPath(path_text)
        source = root / path
        if path.is_absolute() or ".." in path.parts or not source.is_file():
            findings.append(f"{item.path}: replacement node path does not exist: {path_text!r}")
        elif not separator or not selector.strip():
            findings.append(f"{item.path}: replacement node must include an exact :: selector")
        elif not _replacement_selector_exists(source, selector):
            findings.append(f"{item.path}: replacement node selector does not exist: {node_id!r}")
        return
    findings.append(
        f"{item.path}: replacement_contract must start with "
        f"{CONTRACT_REFERENCE_PREFIX!r} or {NODE_REFERENCE_PREFIX!r}"
    )


def _validate_contracts(
    root: Path,
    contracts: Sequence[EvidenceContract],
    artifacts: Sequence[EvidenceMetadata],
    findings: list[str],
) -> None:
    seen: set[str] = set()
    referenced = {
        item.replacement_contract.removeprefix(CONTRACT_REFERENCE_PREFIX).strip()
        for item in artifacts
        if item.replacement_contract.startswith(CONTRACT_REFERENCE_PREFIX)
    }
    for contract in contracts:
        if contract.identity in seen:
            findings.append(f"duplicate contract identity: {contract.identity!r}")
        seen.add(contract.identity)
        owner = PurePosixPath(contract.owner)
        if owner.is_absolute() or ".." in owner.parts or not (root / owner).is_file():
            findings.append(
                f"{contract.identity}: contract owner path does not exist: {contract.owner!r}"
            )
        path_text, separator, selector = contract.evidence_node.partition("::")
        node_path = PurePosixPath(path_text)
        node_source = root / node_path
        if node_path.is_absolute() or ".." in node_path.parts or not node_source.is_file():
            findings.append(
                f"{contract.identity}: evidence node path does not exist: {path_text!r}"
            )
        elif not separator or not selector.strip():
            findings.append(f"{contract.identity}: evidence_node requires an exact :: selector")
        elif not _replacement_selector_exists(node_source, selector):
            findings.append(
                f"{contract.identity}: evidence node selector does not exist: "
                f"{contract.evidence_node!r}"
            )
    stale = sorted(seen - referenced)
    if stale:
        findings.append(f"contract registry contains unreferenced identities: {stale}")


def _replacement_selector_exists(path: Path, selector: str) -> bool:
    """Whether an exact top-level function or class-method selector exists once."""

    parts = _selector_parts(selector)
    if parts is None:
        return False
    body = _selector_body(path)
    if body is None:
        return False
    return _selector_exists(body, parts)


def _selector_parts(selector: str) -> tuple[str, ...] | None:
    parts = tuple(selector.split("::"))
    invalid = any(_selector_part_invalid(part) for part in parts)
    return None if invalid else parts


def _selector_part_invalid(part: str) -> bool:
    return (bool(part), "[" in part, "]" in part) != (True, False, False)


def _selector_body(path: Path) -> Sequence[ast.stmt] | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path)).body
    except (OSError, SyntaxError, UnicodeError):
        return None


def _selector_exists(current: Sequence[ast.stmt], parts: tuple[str, ...]) -> bool:
    match = _single_selector_declaration(current, parts[0])
    if match is None:
        return False
    return _selector_tail_exists(match, parts)


def _single_selector_declaration(
    current: Sequence[ast.stmt],
    name: str,
) -> ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef | None:
    matches: list[ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef] = []
    for node in current:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and (
            node.name == name
        ):
            matches.append(node)
    return matches[0] if len(matches) == 1 else None


def _selector_tail_exists(
    match: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
    parts: tuple[str, ...],
) -> bool:
    if len(parts) == 1:
        return isinstance(match, (ast.FunctionDef, ast.AsyncFunctionDef))
    if not isinstance(match, ast.ClassDef):
        return False
    return _selector_exists(match.body, parts[1:])


def _validate_catalog_coverage(
    root: Path,
    artifacts: Sequence[EvidenceMetadata],
    findings: list[str],
    *,
    large_fixture_bytes: int,
) -> None:
    discovered = governed_artifact_paths(
        root,
        large_fixture_bytes=large_fixture_bytes,
    )
    cataloged = {item.path for item in artifacts}
    missing = sorted(discovered - cataloged)
    stale = sorted(cataloged - discovered)
    if missing:
        findings.append(f"governed evidence has no lifecycle metadata: {missing}")
    if stale:
        findings.append(f"lifecycle metadata points outside governed evidence: {stale}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args(argv)
    try:
        inventory = load_evidence_inventory(args.project_root)
    except EvidenceLifecycleError as error:
        print(error)
        return 1
    payload = inventory.payload()
    if args.json_output is not None:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(f"evidence-lifecycle: PASS ({len(inventory.artifacts)} governed artifacts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
