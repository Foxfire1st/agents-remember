"""MIK-R62 v1-EE1 / v2-EE5: total corpus consistency and mutation counterexamples."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from agents_remember_test_support.testing.instruction_consistency import (
    COMMAND_DOCUMENTATION,
    CONVERSION_MARKER,
    RETIRED_TERMS,
    Registry,
    Surface,
    check_consistency,
    current_record_absent_paths,
    historical_tree,
    load_registry,
    read_corpus,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def registry() -> Registry:
    return load_registry(ROOT)


def _complete_mentions(registry: Registry) -> tuple[Surface, ...]:
    names = "\n".join(
        f"`{name}`" for name in sorted(registry.knowledge_names - COMMAND_DOCUMENTATION.keys())
    )
    return (
        Surface("skills/checker/SKILL.md", names),
        Surface("docs/reference/checker.md", names),
        Surface(
            "docs/guides/first-conversion.md",
            "## Case A — Memory with no attributed baseline\n"
            "agents-remember knowledge-convert MEMORY_ROOT --code CODE_REPOSITORY\n",
        ),
        Surface(
            "docs/tool-details.md",
            "## `knowledge-census` (CLI)\n"
            "agents-remember knowledge-census inventory MEMORY_ROOT\n"
            "agents-remember knowledge-census report MEMORY_ROOT\n"
            "## `knowledge-copies` (CLI)\n"
            "agents-remember knowledge-copies --coordination-root ROOT\n"
            "## `memory-backfill` (CLI)\n"
            "agents-remember memory-backfill --contract CONTRACT\n",
        ),
        Surface("docs/guides/providers.md", "## What remains\nagents-remember providers-remove\n"),
        Surface(
            "docs/reference/settings-json.md",
            "### The user's file is closed to writing\nagents-remember upgrade-user-settings\n"
            "### orchestration.gateDelegation\nagents-remember removed-stores\n",
        ),
    )


def test_complete_instruction_documentation_and_registered_descriptions(registry: Registry) -> None:
    """All scoped bytes, both presence directions, real parser and registered descriptions."""
    surfaces = read_corpus(ROOT) + registry.descriptions
    findings = check_consistency(ROOT, surfaces, registry)
    assert not findings, "\n".join(str(finding) for finding in findings)


def test_each_reference_kind_mutation_fails_on_each_required_surface(registry: Registry) -> None:
    """One bad command/tool/operation/path/retired term cannot hide on another carrier."""
    clean = _complete_mentions(registry)
    assert not check_consistency(ROOT, clean, registry)
    mutations = (
        ("unregistered-command", "agents-remember unregistered-subcommand"),
        ("unregistered-command", "agents-remember knowledge-census unregistered-child"),
        ("unregistered-tool", "Call MCP tool `unregistered_tool`."),
        ("unregistered-tool", "Then call `role_starts` with the request."),
        ("unregistered-tool", "Use `unregistered_tool` for this request."),
        ("unregistered-tool", "`lastUpdated`: use `mcp_time_get_current_time`."),
        ("unregistered-tool", "Invoke `unregistered_tool` for this request."),
        ("unregistered-tool", "Call unregistered_tool(request={})."),
        ("unregistered-tool", "`unregistered_tool(request={})`"),
        ("unregistered-tool", "`unregistered_tool(`"),
        ("unregistered-tool", "unregistered_tool(request={})"),
        ("unregistered-tool", "| Tool | Purpose |\n| --- | --- |\n| `unregistered_tool` | Read |"),
        (
            "unregistered-command",
            "| Command | Purpose |\n| --- | --- |\n| `unregistered-command` | Read |",
        ),
        ("missing-task-doc-operation", 'task_doc(operation="unregistered_operation")'),
        ("missing-task-doc-operation", 'task_doc(\n    operation="unregistered_operation"\n)'),
        ("missing-task-doc-operation", "Call `task_doc.unregistered_operation`."),
        ("missing-path", "Read `mcp/src/agents_remember/unregistered_module.py`."),
        ("missing-path", "Read `skills/unregistered-skill/SKILL.md`."),
        *(("retired-term", term) for term in RETIRED_TERMS),
    )
    for carrier in (
        "skills/checker/detail.md",
        "docs/architecture.md",
        "README.md",
        "tool:knowledge_read",
    ):
        for expected_kind, text in mutations:
            findings = check_consistency(ROOT, (*clean, Surface(carrier, text)), registry)
            assert any(
                finding.path == carrier and finding.kind == expected_kind and finding.line > 0
                for finding in findings
            ), (carrier, text, findings)


def test_bidirectional_knowledge_presence_and_l183_tool_mutations(registry: Registry) -> None:
    """An unmentioned registration or broken L183 tool name is rejected, not sampled away."""
    clean = _complete_mentions(registry)
    # R186/R189's L183 registration, explicitly covered by v2-EE5 and retained in v3.
    assert "explainer_control" in registry.tools
    for prefix in ("skills/", "docs/reference/"):
        for name in registry.knowledge_names - COMMAND_DOCUMENTATION.keys():
            mutated = tuple(
                replace(surface, text=surface.text.replace(name, ""))
                if surface.path.startswith(prefix)
                else surface
                for surface in clean
            )
            findings = check_consistency(ROOT, mutated, registry)
            assert any(
                finding.path == prefix
                and finding.kind == "undocumented-knowledge-name"
                and finding.name == name
                for finding in findings
            ), (prefix, name, findings)
    feature = Surface("docs/reference/explainer.md", "Call MCP tool `explainer_control`.")
    assert not check_consistency(ROOT, (*clean, feature), registry)
    bad = replace(
        feature, text=feature.text.replace("explainer_control", "explainer_control_missing")
    )
    findings = check_consistency(ROOT, (*clean, bad), registry)
    assert any(
        finding.kind == "unregistered-tool" and finding.name == "explainer_control_missing"
        for finding in findings
    ), findings


def test_conversion_exemption_is_explicit_and_confined_to_its_line(registry: Registry) -> None:
    clean = _complete_mentions(registry)
    for term in RETIRED_TERMS:
        conversion = Surface(
            "docs/guides/conversion.md",
            f"Convert old-format cards from {term}. {CONVERSION_MARKER}",
        )
        assert not check_consistency(ROOT, (*clean, conversion), registry)
        no_marker = replace(conversion, text=conversion.text.replace(CONVERSION_MARKER, ""))
        assert any(
            finding.kind == "retired-term"
            for finding in check_consistency(ROOT, (*clean, no_marker), registry)
        )
        next_line = replace(conversion, text=conversion.text + f"\nContinue using {term}.")
        assert any(
            finding.kind == "retired-term" and finding.line == 2
            for finding in check_consistency(ROOT, (*clean, next_line), registry)
        )
    invalid = Surface(
        "docs/guides/conversion.md", f"agents-remember unregistered-subcommand {CONVERSION_MARKER}"
    )
    assert any(
        finding.kind == "unregistered-command"
        for finding in check_consistency(ROOT, (*clean, invalid), registry)
    )


def test_protocol_and_diagnostic_names_are_distinct_from_files_and_commands(
    registry: Registry,
) -> None:
    clean = _complete_mentions(registry)
    prose = Surface(
        "docs/reference/checker-protocol.md",
        "Read the `skills/list` extension.\n"
        "The response status is `knowledge-gate-refused`.\n"
        "Every agents-remember memory line uses text.\n"
        "The schema field is `knowledge_resolution`.\n"
        "Use `task_context` for the request's scope.\n"
        "Use `append_decision` for the task_doc operation.\n"
        "Call the local `build_route_indexes(...)` function.\n"
        "The example uses `secrets.token_urlsafe(32)`.\n"
        "Use exact (case-sensitive) names.\n"
        "Use `draft` as the status and `coveredFiles` as metadata.\n"
        "Call `SessionComposer` for the component and use `lstat` locally.\n"
        "The sidecar is memory/onboarding/mcp/src/agents_remember/mcp/server.py.md.",
    )
    assert not check_consistency(ROOT, (*clean, prose), registry)
    file_reference = replace(prose, text="Read the file `skills/list`.")
    assert any(
        finding.kind == "missing-path"
        for finding in check_consistency(ROOT, (*clean, file_reference), registry)
    )


@pytest.mark.parametrize("command", sorted(COMMAND_DOCUMENTATION))
@pytest.mark.parametrize("relocated", (False, True))
def test_migration_command_requires_its_exact_docs_owner(
    registry: Registry, command: str, relocated: bool
) -> None:
    clean = _complete_mentions(registry)
    assert COMMAND_DOCUMENTATION.keys() <= registry.commands
    assert not check_consistency(ROOT, clean, registry)
    path, _ = COMMAND_DOCUMENTATION[command]
    owner = next(surface for surface in clean if surface.path == path)
    mutated = tuple(surface for surface in clean if surface.path != path)
    if relocated:
        mutated += (replace(owner, path="docs/reference/wrong-owner.md"),)
    findings = check_consistency(ROOT, mutated, registry)
    assert any(
        finding.path == path
        and finding.kind == "undocumented-command-owner"
        and finding.name == command
        for finding in findings
    ), (command, relocated, findings)


@pytest.mark.parametrize(
    "source",
    (
        "capsule-budgets-current.json",
        "capsule-budgets-d694.json",
        "capsule-budgets.json",
        "core/digest-map.json",
    ),
)
def test_authenticated_historical_paths_are_not_current_existence_claims(
    registry: Registry, source: str
) -> None:
    relative = "skills/l-01-agent-lifecycles/" + source
    surface = Surface(relative, (ROOT / relative).read_text())
    assert not check_consistency(ROOT, (*_complete_mentions(registry), surface), registry)
    # A borrowed owner name cannot authenticate altered frozen/current content.
    changed = replace(surface, text=surface.text + "\n ")
    with pytest.raises(AssertionError, match="historical surface differs"):
        check_consistency(ROOT, (*_complete_mentions(registry), changed), registry)
    # Moving the genuine history into another carrier does not carry its exemption.
    misplaced = replace(surface, path="skills/checker/history-like.json")
    assert any(
        finding.kind == "missing-path" and finding.path == misplaced.path
        for finding in check_consistency(ROOT, (*_complete_mentions(registry), misplaced), registry)
    )


def test_history_like_fields_do_not_hide_active_missing_paths(registry: Registry) -> None:
    absent = "skills/l-01-agent-lifecycles/reference/nonexistent-active-source.md"
    for payload in (
        {"schema": "ar-role-capsule-budget-current/v1", "approved_source_sizes": {absent: {}}},
        {"decision": {"removedFromCurrent": [absent]}},
        {
            "decision": {
                "measuredCurrentRenewalV14_557": {
                    "membership": {"sources": {"removedFromCurrent": [absent]}}
                }
            }
        },
        *(
            {
                "decision": {
                    "measuredCurrentRenewalP5_91": {
                        "membership": {"sourceInventory": {field: [absent]}}
                    }
                }
            }
            for field in ("retained", "addedToCurrent")
        ),
        {"units": [{"retiredBinding": {"evidence": [{"path": absent}]}}]},
    ):
        surface = Surface("skills/checker/history-like.json", json.dumps(payload))
        assert any(
            finding.kind == "missing-path" and finding.name == absent
            for finding in check_consistency(
                ROOT, (*_complete_mentions(registry), surface), registry
            )
        )
    relative = "skills/l-01-agent-lifecycles/capsule-budgets-current.json"
    text = (ROOT / relative).read_text()
    document = json.loads(text)
    document["removedFromCurrent"] = [absent]
    with pytest.raises(AssertionError, match="historical surface differs"):
        check_consistency(
            ROOT, (*_complete_mentions(registry), Surface(relative, json.dumps(document))), registry
        )
    document = json.loads(text)
    document["decision"]["measuredCurrentRenewalV14_557"]["membership"]["sources"][
        "removedFromCurrent"
    ].append(absent)
    with pytest.raises(AssertionError, match="historical surface differs"):
        check_consistency(
            ROOT, (*_complete_mentions(registry), Surface(relative, json.dumps(document))), registry
        )

    for field in ("retained", "addedToCurrent"):
        document = json.loads(text)
        document["decision"]["measuredCurrentRenewalP5_91"]["membership"]["sourceInventory"][
            field
        ].append(absent)
        with pytest.raises(AssertionError, match="historical surface differs"):
            check_consistency(
                ROOT,
                (*_complete_mentions(registry), Surface(relative, json.dumps(document))),
                registry,
            )


def _first_list(value: object) -> list | None:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key, item in value.items():
            found = None if key.startswith("removed") else _first_list(item)
            if found is not None:
                return found
    return None


def test_current_budget_record_names_no_absent_path() -> None:
    """P5-25: a record labelled current names no absent path unless marked historical."""
    document = json.loads(
        (ROOT / "skills/l-01-agent-lifecycles/capsule-budgets-current.json").read_text()
    )
    assert current_record_absent_paths(ROOT, document) == set()
    renewals = [
        key
        for key, record in document["decision"].items()
        if key.startswith("measuredCurrentRenewal") and not historical_tree(record)
    ]
    assert renewals, "no current renewal sub-record"
    # The A005 defect: the removed ingestion reference named in current retained sources.
    absent = "reference/curator/handoff-list-ingestion.md"
    assert not (ROOT / "skills/l-01-agent-lifecycles" / absent).exists()
    record = document["decision"][renewals[-1]]
    members = _first_list(record["membership"])
    assert members is not None
    members.append(absent)
    assert current_record_absent_paths(ROOT, document) == {absent}
    record["historicalTree"] = "not-a-tree"
    assert current_record_absent_paths(ROOT, document) == {absent}
    record["historicalTree"] = "9676e772b14971b5184fabac3528a16cb97a9425"
    assert current_record_absent_paths(ROOT, document) == set()
    document["current_detail_population"] = [absent]
    assert current_record_absent_paths(ROOT, document) == {absent}


def _renewal_currentness_problems(document: dict) -> list[str]:
    renewals = {
        key: record
        for key, record in document["decision"].items()
        if key.startswith("measuredCurrentRenewal") and isinstance(record, dict)
    }
    claims = sorted(key for key, record in renewals.items() if record.get("current") is True)
    problems = [] if len(claims) == 1 else [f"current claims {claims}"]
    problems += [
        f"history claims current: {key}"
        for key, record in renewals.items()
        if historical_tree(record) and "current" in record
    ]
    problems += [
        f"not history and not current: {key}"
        for key, record in renewals.items()
        if not historical_tree(record) and key not in claims
    ]
    return problems


def test_exactly_one_renewal_sub_record_claims_current() -> None:
    """P5-25: one current renewal sub-record; a sub-record kept as history claims no currentness."""
    document = json.loads(
        (ROOT / "skills/l-01-agent-lifecycles/capsule-budgets-current.json").read_text()
    )
    assert _renewal_currentness_problems(document) == []
    history = next(
        key
        for key, record in document["decision"].items()
        if key.startswith("measuredCurrentRenewal") and historical_tree(record)
    )
    # The A008 defect: a sub-record marked historical that still says current.
    document["decision"][history]["current"] = True
    assert _renewal_currentness_problems(document)
    document["decision"][history]["current"] = False
    assert _renewal_currentness_problems(document)
    del document["decision"][history]["current"]
    assert _renewal_currentness_problems(document) == []


def test_duplicate_history_key_in_actual_owner_cannot_borrow_effective_fingerprint(
    registry: Registry, tmp_path: Path
) -> None:
    """The real owner bytes can change while ordinary JSON decoding keeps its fingerprint."""
    prefix = "skills/l-01-agent-lifecycles/"
    for source in (
        "capsule-budgets-current.json",
        "capsule-budgets-d694.json",
        "capsule-budgets.json",
        "core/digest-map.json",
    ):
        destination = tmp_path / prefix / source
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((ROOT / prefix / source).read_bytes())
    relative = prefix + "capsule-budgets-current.json"
    owner = tmp_path / relative
    original = owner.read_text()
    forged = {
        "measuredCurrentRenewalP5_91": {
            "membership": {
                "sourceInventory": {
                    "removedFromCurrent": [prefix + "reference/forged-active-missing.md"]
                }
            }
        }
    }
    # The genuine last decision wins normal JSON decoding, preserving the existing fingerprint.
    tampered = '{"decision": ' + json.dumps(forged) + "," + original.lstrip()[1:]
    assert json.loads(tampered) == json.loads(original)
    owner.write_text(tampered)
    with pytest.raises(AssertionError, match="duplicate JSON key in historical owner"):
        check_consistency(
            tmp_path,
            (*_complete_mentions(registry), Surface(relative, owner.read_text())),
            registry,
        )


@pytest.mark.parametrize(
    "source",
    (
        "capsule-budgets-d694.json",
        "capsule-budgets.json",
        "core/digest-map.json",
    ),
)
def test_actual_frozen_owner_change_fails_its_existing_sha_authentication(
    registry: Registry, tmp_path: Path, source: str
) -> None:
    """Matching Surface bytes cannot authenticate a changed historical owner."""
    prefix = "skills/l-01-agent-lifecycles/"
    for original in (
        "capsule-budgets-current.json",
        "capsule-budgets-d694.json",
        "capsule-budgets.json",
        "core/digest-map.json",
    ):
        destination = tmp_path / prefix / original
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((ROOT / prefix / original).read_bytes())
    relative = prefix + source
    owner = tmp_path / relative
    original = owner.read_text()
    changed = original + "\n "
    assert json.loads(changed) == json.loads(original)
    owner.write_text(changed)
    # The genuine current record still authenticates; correspondence and schema pass.
    surface = Surface(relative, owner.read_text())
    assert surface.text.encode("utf-8") == owner.read_bytes()
    with pytest.raises(AssertionError, match="historical owner changed"):
        check_consistency(tmp_path, (*_complete_mentions(registry), surface), registry)
