"""F9 follows native received fields rather than outbound agent configuration."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from urllib.parse import unquote, urlparse

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from agent_evals import text as text_capture
from agent_evals.adapters import detail_reads
from agent_evals.adapters.detail_reads import observe_detail_reads
from agent_evals.adapters.execution import CODEX_ACTOR, HERMES_ACTOR, NativeBinding
from agent_evals.adapters.models import NativeCapture, NativeEvent, SourceFile
from agent_evals.adapters.paseo import PaseoConnection, PaseoNativeAdapter
from agent_evals.adapters.text_delivery import (
    DetailFile,
    PointerDelivery,
    bind_text_details,
    observe_text_delivery,
    prepare_text_delivery,
    validate_detail_links,
)
from agent_evals.artifacts import EvidenceFile
from agent_evals.fixtures import CaseBundle
from agent_evals.text import capture_curator_text
from agent_evals.trials import load_trial_text
from agents_remember.application.skill_resources.provider import shipped_composition_corpus
from agents_remember.errors import AuthorityError
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from test_l84_capsule_delivery_proofs import _compiled

FIRST = "Run one leaf's coherence pass and write its verified onboarding."
SECOND = "Keep every valid invariant claim until its cited evidence justifies a change."


def _case(root: Path, content="ordinary case prompt"):
    root.mkdir()
    (root / "prompt.txt").write_text(content)
    manifest = {"actorInputs": [{"source": "prompt.txt", "destination": "inputs/prompt.txt"}]}
    (root / "case.json").write_text(json.dumps(manifest))
    return CaseBundle(root, "CE01", manifest, (EvidenceFile.capture(root, "prompt.txt"),))


def _capture(harness, content, *, role="user"):
    return NativeCapture(
        harness,
        "native-session",
        (
            NativeEvent(
                "message",
                "native-session",
                3,
                {"role": role, "content": content},
                "native-transcript",
                "a" * 64,
            ),
        ),
        (SourceFile("native-transcript", "a" * 64),),
        (),
        (),
    )


@pytest.mark.parametrize("arm", ["old", "new"])
@pytest.mark.parametrize("profile", [CODEX_ACTOR, HERMES_ACTOR])
def test_native_first_message_carries_bound_text_for_both_arms(tmp_path, profile, arm):
    # Arm names never choose text: both are already bound instruction values.
    instructions = FIRST + "\n" + SECOND + ("\nA captured old instruction." if arm == "old" else "")
    details = ()
    shared = None
    if arm == "new":
        code = Path(__file__).resolve().parents[2]
        shared = (code / "skills/l-01-agent-lifecycles/core/every-role.md").read_text()
        captured = capture_curator_text(code)
        assert captured["entries"]["curation"]["instructions"].count(shared) == 1
        bound = bind_text_details(captured, tmp_path / "new-text.md")
        instructions = bound.instructions
        details = tuple(detail.path for detail in bound.details)
    case = _case(tmp_path / "case")
    marker = prepare_text_delivery(instructions, case, (), details=details)
    binding = NativeBinding(tmp_path, tmp_path, tmp_path / "settings", "build", (), profile)
    config = McpRuntimeConfig(binding.settings_path, tmp_path, tmp_path, tmp_path)
    creates = []

    def bridge(command, payload):
        if command == "agents-live":
            return {"complete": True, "agentIds": []}
        if command == "workspace-open":
            return {"workspace": {"id": "workspace"}}
        if command == "agent-create":
            creates.append(payload)
            return {
                "existing": "prompt" in payload,
                "agent": {
                    **payload,
                    "id": payload["agentId"],
                    "cwd": str(tmp_path),
                },
            }
        if command == "agent-wait":
            return {"wait": {"state": "ended", "outcome": "finished"}}
        return {}

    def capture(_):
        # Actual ACP shape: systemPrompt is not part of newSession or received user text.
        # Codex's unchanged route records the applied systemPrompt as developer content.
        received = (
            creates[-1]["prompt"] if profile.harness == "hermes" else creates[0]["systemPrompt"]
        )
        return _capture(
            profile.harness, received, role="user" if profile.harness == "hermes" else "developer"
        )

    adapter = PaseoNativeAdapter(PaseoConnection(config, binding, tmp_path, {}), call=bridge)
    with patch.object(adapter, "probe", return_value={}):
        result = adapter.run(
            instructions=instructions, prompt="ordinary case prompt", capture=capture
        )
    assert result.capture is not None
    observation = observe_text_delivery(marker, result.capture)
    assert observation["received"], observation["unknowns"]
    assert observation["receivedLocations"][0]["source"] == "native-transcript"
    if shared is not None:
        assert instructions.count("## What every role acts on") == 1
        assert instructions.count(shared) == 1
    if profile.harness == "hermes":
        assert "systemPrompt" not in creates[0]
        assert creates[-1]["prompt"].startswith(instructions + "\n\n")
    else:
        assert creates[0]["systemPrompt"] == instructions
        assert creates[-1]["prompt"].startswith("ordinary case prompt\n\n")


def test_current_capture_keeps_every_compiled_origin_after_the_shared_prefix(tmp_path):
    code = Path(__file__).resolve().parents[2]
    captured = capture_curator_text(code)
    shared = (code / "skills/l-01-agent-lifecycles/core/every-role.md").read_text()
    prefix = "## What every role acts on\n\n" + shared + "\n\n"
    for operation, entry in captured["entries"].items():
        compiled = _compiled("curator", operation)
        first, *origins = entry["instructionOrigins"]
        assert first == {"sourcePath": "core/every-role.md", "start": 0, "end": len(prefix)}
        assert entry["instructions"][: first["end"]] == prefix
        cursor = len(prefix)
        for origin, unit in zip(origins, compiled.capsule.instruction_units, strict=True):
            assert origin["sourcePath"] == unit.block.source_path
            assert origin["start"] == cursor
            assert entry["instructions"][origin["start"] : origin["end"]] == unit.block.content
            cursor = origin["end"]
        assert cursor == len(entry["instructions"])
    before = copy.deepcopy(captured)
    delivery = bind_text_details(captured, tmp_path / "delivery/curator.md")
    source_path = "reference/curator/card-formats-and-citations.md"
    with shipped_composition_corpus() as (root, _):
        target = root / source_path
        assert target.is_file()
        frozen = next(
            row for row in captured["entries"]["curation"]["details"] if row["path"] == source_path
        )
        assert frozen["content"] == target.read_text()
    detail = next(item for item in delivery.details if item.source_path == source_path)
    assert detail.path.is_file()
    assert detail.path.resolve().as_posix() in delivery.instructions
    assert captured == before
    assert (
        delivery.composition["baselineDigest"] == captured["entries"]["curation"]["semanticDigest"]
    )


@pytest.mark.parametrize(
    "problem",
    [
        "missing",
        "empty",
        "gap",
        "overlap",
        "tail",
        "duplicate",
        "boolean",
        "absolute",
        "escape",
        "wrong-shape",
    ],
)
def test_current_capture_never_guesses_missing_or_malformed_origins(tmp_path, problem):
    captured = capture_curator_text(Path(__file__).resolve().parents[2])
    entry = captured["entries"]["curation"]
    origins = entry["instructionOrigins"]
    if problem == "missing":
        del entry["instructionOrigins"]
    elif problem == "empty":
        origins.clear()
    elif problem == "gap":
        origins[1]["start"] += 1
    elif problem == "overlap":
        origins[1]["start"] -= 1
    elif problem == "tail":
        origins[-1]["end"] -= 1
    elif problem == "duplicate":
        origins.insert(1, dict(origins[0]))
    elif problem == "boolean":
        origins[0]["start"] = False
    elif problem == "absolute":
        origins[0]["sourcePath"] = "/unrelated/origin.md"
    elif problem == "escape":
        origins[0]["sourcePath"] = "../unrelated/origin.md"
    else:
        entry["instructionOrigins"] = {}
    with pytest.raises(AuthorityError, match="text-delivery-detail-fault:"):
        bind_text_details(captured, tmp_path / "delivery/curator.md")
    assert not (tmp_path / "delivery").exists()


@pytest.mark.parametrize("problem", ["missing", "duplicate", "revision"])
def test_capture_refuses_ambiguous_compiler_shared_provenance(problem):
    original = text_capture.compile_admitted_capsule

    def damaged(*args, **kwargs):
        outcome = original(*args, **kwargs)
        result = outcome.result
        assert result is not None
        sources = list(result.manifest.sources)
        index = next(i for i, source in enumerate(sources) if source.identity == "core:every-role")
        if problem == "missing":
            sources.pop(index)
        elif problem == "duplicate":
            sources.append(sources[index])
        else:
            sources[index] = replace(sources[index], revision="sha256:" + "0" * 64)
        return replace(
            outcome,
            result=replace(result, manifest=replace(result.manifest, sources=tuple(sources))),
        )

    with (
        patch.object(text_capture, "compile_admitted_capsule", side_effect=damaged),
        pytest.raises(AuthorityError, match="ambiguous source provenance"),
    ):
        capture_curator_text(Path(__file__).resolve().parents[2])


def test_marker_is_selected_from_instructions_and_absent_from_entire_case_inventory(tmp_path):
    case = _case(tmp_path / "case", FIRST)
    actor = tmp_path / "before.txt"
    actor.write_text("actor-given input")
    plan = prepare_text_delivery(FIRST + "\n" + SECOND, case, (actor,))
    assert plan.sentence == SECOND
    assert plan.instruction_character_offset == len(FIRST) + 1
    assert len(plan.bundle_files) == 3
    assert all(
        row["sha256"] == hashlib.sha256(Path(row["path"]).read_bytes()).hexdigest()
        for row in plan.bundle_files
    )


def test_markdown_instruction_sentence_remains_a_verbatim_bound_substring(tmp_path):
    instructions = "# Curator\n\n**" + FIRST + "**\n\n" + SECOND
    plan = prepare_text_delivery(instructions, _case(tmp_path / "case"), ())
    assert plan.sentence == FIRST
    assert instructions[plan.instruction_character_offset :].startswith(plan.sentence)


def test_marker_in_case_json_cannot_prove_text_delivery(tmp_path):
    case = _case(tmp_path / "case")
    (case.path / "case.json").write_text(json.dumps({"quoted": FIRST}))
    with pytest.raises(AuthorityError, match="every instruction sentence occurs"):
        prepare_text_delivery(FIRST, case, ())


def test_unlisted_case_file_refuses_absence_proof_without_reading_it(tmp_path):
    case = _case(tmp_path / "case")
    hidden = case.path / "unlisted.txt"
    hidden.write_text(FIRST)
    original = Path.open

    def guarded(path, *args, **kwargs):
        assert path != hidden, "unbound bytes were opened"
        return original(path, *args, **kwargs)

    with (
        patch.object(Path, "open", guarded),
        pytest.raises(AuthorityError, match="unbound case input"),
    ):
        prepare_text_delivery(FIRST, case, ())


def test_marker_crossing_stream_block_boundary_is_not_mistaken_for_absent(tmp_path):
    case = _case(tmp_path / "case")
    actor = tmp_path / "large.txt"
    actor.write_bytes(b"x" * (1024 * 1024 - 8) + FIRST.encode())
    assert prepare_text_delivery(FIRST + "\n" + SECOND, case, (actor,)).sentence == SECOND


@pytest.mark.parametrize("name", [".env", "auth.json", "auth.lock"])
def test_marker_scan_refuses_private_inputs_without_opening_or_hashing(tmp_path, name):
    case = _case(tmp_path / "case")
    private = tmp_path / name
    private.write_text("DO NOT READ")
    original = Path.open

    def guarded(path, *args, **kwargs):
        assert path != private, "credential bytes were opened"
        return original(path, *args, **kwargs)

    with (
        patch.object(Path, "open", guarded),
        pytest.raises(AuthorityError, match="private/symlink"),
    ):
        prepare_text_delivery(FIRST, case, (private,))


def test_marker_scan_refuses_symlink_to_external_input(tmp_path):
    case = _case(tmp_path / "case")
    link = tmp_path / "link"
    link.symlink_to(case.path / "prompt.txt")
    with pytest.raises(AuthorityError, match="private/symlink"):
        prepare_text_delivery(FIRST, case, (link,))


@pytest.mark.parametrize("role", ["assistant", "tool"])
def test_actor_repetition_or_tool_output_is_not_received_instruction_evidence(tmp_path, role):
    plan = prepare_text_delivery(FIRST, _case(tmp_path / "case"), ())
    result = observe_text_delivery(plan, _capture("hermes", FIRST, role=role))
    assert not result["received"]
    assert result["unknowns"][0].startswith("text-delivery-marker-missing:")


def test_codex_native_base_instructions_have_an_explicit_received_location(tmp_path):
    plan = prepare_text_delivery(FIRST, _case(tmp_path / "case"), ())
    capture = NativeCapture(
        "codex",
        "s",
        (
            NativeEvent(
                "session", "s", 0, {"base_instructions": {"text": FIRST}}, "rollout.jsonl", "b" * 64
            ),
        ),
        (),
        (),
        (),
    )
    result = observe_text_delivery(plan, capture)
    assert result["receivedLocations"][0]["path"] == "$.events[0].payload.base_instructions.text"


def test_inactive_original_hermes_user_row_proves_receipt_but_summary_does_not(tmp_path):
    plan = prepare_text_delivery(FIRST, _case(tmp_path / "case"), ())
    summary = _capture("hermes", "[CONTEXT COMPACTION — REFERENCE ONLY] " + FIRST)
    assert not observe_text_delivery(plan, summary)["received"]
    original = {
        "table": "messages",
        "row": {
            "id": 12,
            "session_id": "native-session",
            "role": "user",
            "active": 0,
            "content": FIRST,
        },
    }
    capture = NativeCapture(
        "hermes", "native-session", summary.events, summary.source_files, (original,), ()
    )
    result = observe_text_delivery(plan, capture)
    assert result["received"]
    assert result["receivedLocations"][0]["nativeMessageId"] == 12


def test_outbound_configuration_and_unrelated_session_are_not_arrival_proof(tmp_path):
    plan = prepare_text_delivery(FIRST, _case(tmp_path / "case"), ())
    capture = NativeCapture(
        "hermes",
        "s",
        (),
        (),
        (
            {"systemPrompt": FIRST},
            {"table": "messages", "row": {"session_id": "other", "role": "user", "content": FIRST}},
        ),
        (),
    )
    assert not observe_text_delivery(plan, capture)["received"]


def test_real_c7_native_received_fields_show_codex_text_and_hermes_missing_text(tmp_path):
    path = Path(__file__).parent / "fixtures/text-delivery-c7-native.json"
    assert (
        hashlib.sha256(path.read_bytes()).hexdigest()
        == "28c9ccb3056e1555a222e8d6ca031948eae991a10483b7d25142f0adcb2bfa89"
    )
    fixture = json.loads(path.read_text())
    for row in fixture["rows"]:
        marker = prepare_text_delivery(
            row["instructionMarker"], _case(tmp_path / row["harness"]), ()
        )
        capture = NativeCapture(
            row["harness"],
            row["sessionId"],
            tuple(NativeEvent(**event) for event in row["events"]),
            tuple(SourceFile(**source) for source in row["sourceFiles"]),
            (),
            (),
        )
        result = observe_text_delivery(marker, capture)
        assert result["received"] is (row["harness"] == "codex")
        if row["harness"] == "hermes":
            assert result["unknowns"][0].startswith("text-delivery-marker-missing:")


def _captured_pointer_rows():
    return json.loads(
        (Path(__file__).parent / "fixtures/text-delivery-pointer-artifacts.json").read_text()
    )["rows"]


@pytest.mark.parametrize("row", _captured_pointer_rows(), ids=lambda row: row["arm"])
def test_real_captured_load_text_excludes_inline_detail_bodies(tmp_path, row):

    artifact = {"schema": "ar-curator-effective-text/v1", "entries": {"curation": row["entry"]}}
    source = tmp_path / "captured.json"
    source.write_text(json.dumps(artifact))
    instructions, _, observed = load_trial_text(tmp_path, source)
    assert observed == artifact
    assert instructions == row["entry"]["instructions"]
    assert all(detail["content"] not in instructions for detail in row["entry"]["details"])


@pytest.mark.parametrize("row", _captured_pointer_rows(), ids=lambda row: row["arm"])
@pytest.mark.parametrize("harness", ["codex", "hermes"])
def test_real_captured_artifacts_bind_production_details_without_inline_bodies(
    tmp_path, row, harness
):

    artifact = {"entries": {"curation": row["entry"]}}
    captured = copy.deepcopy(artifact)
    delivery = bind_text_details(artifact, tmp_path / harness / "curator.md")
    assert len(delivery.details) == (8 if row["arm"] == "old" else 9)
    expected = row["entry"]["instructions"]
    for detail, original in zip(delivery.details, row["entry"]["details"], strict=True):
        assert detail.path.is_absolute()
        expected_body = original["content"]
        with shipped_composition_corpus() as (root, _):
            for source in ("templates/curator-handoff-list.md", "criteria/onboarding-memory.md"):
                expected_body = expected_body.replace(
                    "](../" + source, "](" + (root / source).as_posix()
                )
        assert detail.path.read_bytes() == expected_body.encode()
        assert detail.size_bytes == len(expected_body.encode())
        assert delivery.frozen_contents[detail.source_path] == expected_body
        assert detail.sha256 == hashlib.sha256(detail.path.read_bytes()).hexdigest()
        assert detail.path.stat().st_mode & 0o777 == 0o444
        assert original["content"] not in delivery.instructions
        expected = expected.replace("{{detail:" + original["path"] + "}}", detail.path.as_posix())
    assert delivery.instructions == expected
    assert artifact == captured
    assert "frozen_contents" not in delivery.to_dict()
    assert "{{detail:" not in delivery.instructions
    assert validate_detail_links(delivery)["unknowns"] == []
    marker = prepare_text_delivery(
        delivery.instructions,
        _case(tmp_path / "case"),
        (),
        details=tuple(detail.path for detail in delivery.details),
    )
    assert marker.sentence in delivery.instructions
    assert all(marker.sentence not in detail.path.read_text() for detail in delivery.details)
    native = _capture(
        harness, delivery.instructions, role="user" if harness == "hermes" else "developer"
    )
    assert observe_text_delivery(marker, native)["received"]


def _pointer_artifact(content="Detail prose that is not part of the delivered first message."):
    return {
        "entries": {
            "curation": {
                "instructions": FIRST + "\n" + SECOND + "\nRead {{detail:reference/control.md}}.",
                "semanticDigest": "sha256:" + "a" * 64,
                "details": [
                    {
                        "path": "reference/control.md",
                        "step": "at the control step",
                        "revision": "sha256:" + hashlib.sha256(content.encode()).hexdigest(),
                        "content": content,
                    }
                ],
            }
        }
    }


@pytest.mark.parametrize("damage", ["same-size", "size"])
def test_rewritten_detail_uses_emitted_identity_and_keeps_corruption_refusals(tmp_path, damage):
    original = "Read [self](control.md#step) and [source](../SKILL.md).\n"
    artifact = _pointer_artifact(original)
    captured = copy.deepcopy(artifact)
    delivery = bind_text_details(artifact, tmp_path / "delivery/curator.md")
    (detail,) = delivery.details
    with shipped_composition_corpus() as (root, _):
        expected = f"Read [self]({detail.path}#step) and [source]({root / 'SKILL.md'}).\n"
    body = expected.encode()
    assert body != original.encode()
    assert detail.path.read_bytes() == body
    assert detail.size_bytes == len(body)
    assert detail.revision == "sha256:" + hashlib.sha256(body).hexdigest()
    assert delivery.frozen_contents == {detail.source_path: expected}
    assert artifact == captured
    assert validate_detail_links(delivery)["unknowns"] == []
    detail.path.chmod(0o644)
    detail.path.write_bytes(b"X" + body[1:] if damage == "same-size" else body + b"X")
    validation = validate_detail_links(delivery)
    assert validation["unknowns"]
    assert validation["files"][0]["state"] == (
        "digest-mismatch" if damage == "same-size" else "size-mismatch"
    )
    assert delivery.frozen_contents == {detail.source_path: expected}


def test_marker_must_also_be_absent_from_every_delivered_detail(tmp_path):

    delivery = bind_text_details(_pointer_artifact(FIRST), tmp_path / "delivery/curator.md")
    marker = prepare_text_delivery(
        delivery.instructions,
        _case(tmp_path / "case"),
        (),
        details=tuple(detail.path for detail in delivery.details),
    )
    assert marker.sentence == SECOND
    assert str(delivery.details[0].path) in [row["path"] for row in marker.bundle_files]


def test_no_marker_is_claimed_when_case_and_details_cover_all_first_part_sentences(tmp_path):
    delivery = bind_text_details(_pointer_artifact(SECOND), tmp_path / "delivery/curator.md")
    with pytest.raises(AuthorityError, match="every instruction sentence occurs"):
        prepare_text_delivery(
            delivery.instructions,
            _case(tmp_path / "case", FIRST),
            (),
            details=tuple(detail.path for detail in delivery.details),
        )


@pytest.mark.parametrize("mutation", ["missing", "changed", "symlink", "directory", "fifo"])
def test_dead_or_changed_detail_is_its_own_delivery_fault(tmp_path, mutation):

    delivery = bind_text_details(_pointer_artifact(), tmp_path / "delivery/curator.md")
    path = delivery.details[0].path
    path.unlink()
    if mutation == "changed":
        path.write_text("changed")
    elif mutation == "symlink":
        target = tmp_path / "unrelated"
        target.write_text("not detail bytes")
        path.symlink_to(target)
    elif mutation == "directory":
        path.mkdir()
    elif mutation == "fifo":
        os.mkfifo(path)
    checked = validate_detail_links(delivery)
    assert len(checked["unknowns"]) == 1
    assert checked["unknowns"][0].startswith("text-delivery-detail-fault:")
    assert "reference/control.md" in checked["unknowns"][0]


@pytest.mark.parametrize(
    "problem", ["revision", "missing-row", "extra-row", "duplicate", "malformed-token"]
)
def test_captured_pointer_inventory_is_strict_and_never_recompiled(tmp_path, problem):

    artifact = _pointer_artifact()
    entry = artifact["entries"]["curation"]
    if problem == "revision":
        entry["details"][0]["content"] += "changed"
    elif problem == "missing-row":
        entry["details"].clear()
    elif problem == "extra-row":
        entry["instructions"] = FIRST
    elif problem == "duplicate":
        entry["details"].append(dict(entry["details"][0]))
    else:
        entry["instructions"] += " {{detail:unfinished"
    with pytest.raises(AuthorityError, match="text-delivery-detail-fault:"):
        bind_text_details(artifact, tmp_path / "delivery/curator.md")
    assert not (tmp_path / "delivery").exists()


def test_detail_report_path_cannot_write_through_linked_parent(tmp_path):

    actual = tmp_path / "actual"
    actual.mkdir()
    link = tmp_path / "link"
    link.symlink_to(actual, target_is_directory=True)
    with pytest.raises(AuthorityError, match="absolute and nonlinked"):
        bind_text_details(_pointer_artifact(), link / "curator.md")
    assert list(actual.iterdir()) == []


_R2_SAMPLES = Path(__file__).parent / "fixtures/text-delivery-native-reads.json"


def _r2_case(index):
    sample = json.loads(_R2_SAMPLES.read_text())["rows"][index]
    call, result = (NativeEvent(**sample[key]) for key in ("call", "result"))
    args = call.payload["arguments"]
    args = json.loads(args) if isinstance(args, str) else args
    if sample["harness"] == "codex":
        project = Path(unquote(urlparse(args["cwd"]).path))
        target = project / "fixture/authority.json"
    else:
        target = (
            Path(args["path"]) if "path" in args else Path("/home/test/.local/bin/agents-remember")
        )
        project = target.parent
    output = sample["result"]["payload"]["output"]
    output = json.loads(output) if isinstance(output, str) else output
    body = output.get("aggregated_output", output.get("content", output.get("output", "")))
    digest = hashlib.sha256(body.encode()).hexdigest()
    detail = DetailFile(
        source_path="constructed-binding:retained-output",
        path=target,
        revision="sha256:" + digest,
        step="constructed-schema-control",
        sha256=digest,
        size_bytes=len(body.encode()),
    )
    delivery = PointerDelivery("", (detail,), {}, "a" * 64, {detail.source_path: body})
    capture = NativeCapture(
        sample["harness"],
        sample["sourceSessionId"],
        (call, result),
        tuple(SourceFile(**source) for source in sample["sourceFiles"]),
        (),
        (),
    )
    return sample, delivery, capture, project, {detail.source_path: body}


def test_r2_exact_retained_codex_and_hermes_receipts_prove_reads():
    for index in (0, 2):
        sample, delivery, capture, project, frozen = _r2_case(index)
        before = copy.deepcopy(capture.to_dict())
        receipt = observe_detail_reads(delivery, capture, project, frozen_contents=frozen)
        (row,) = receipt["details"]
        assert row["state"] == "opened"
        assert row["call_id"] == sample["call"]["payload"]["call_id"]
        assert row["nativeEvent"]["source"] == sample["call"]["source"]
        assert row["nativeEvent"]["source_sha256"] == sample["call"]["source_sha256"]
        assert row["nativeEvent"]["sequence"] == sample["call"]["sequence"]
        assert row["nativeEvent"]["JSONPath"] == "$.events[0].payload"
        assert receipt["failedAttempts"] == receipt["diagnostics"] == []
        assert receipt["observationOnly"] is True and "rate" not in receipt
        assert capture.to_dict() == before


def test_r2_retained_compounds_require_target_and_returned_body():
    for index, state in ((1, "not seen in kept log"), (3, "opened")):
        _, delivery, capture, project, frozen = _r2_case(index)
        receipt = observe_detail_reads(delivery, capture, project, frozen_contents=frozen)
        assert receipt["details"][0]["state"] == state
        if state == "opened":
            assert receipt["details"][0]["wholeRead"] is False
        else:
            assert (
                receipt["details"][0]["basis"]
                == "the kept log is not known to be complete on this harness"
            )
        assert receipt["failedAttempts"] == []


def test_r2_missing_foreign_and_ambiguous_pairs_never_open_details():
    _, delivery, capture, project, frozen = _r2_case(2)
    call, result = capture.events
    conflicting = copy.deepcopy(call.payload)
    conflicting["arguments"] = json.dumps({"path": "/different-control"})
    controls = [
        (call,),
        (call, replace(result, source_sha256="f" * 64)),
        (call, replace(call, payload=conflicting), result),
    ]
    for events in controls:
        receipt = observe_detail_reads(
            delivery, replace(capture, events=events), project, frozen_contents=frozen
        )
        assert receipt["details"][0]["state"] == "not seen in kept log"
        assert receipt["diagnostics"]
    repeated = observe_detail_reads(
        delivery,
        replace(capture, events=(call, call, result, result)),
        project,
        frozen_contents=frozen,
    )
    assert len(repeated["details"][0]["observations"]) == 1


def test_r2_constructed_failure_and_relative_path_are_not_successes():
    _, delivery, capture, project, frozen = _r2_case(2)
    call, result = capture.events
    payload = copy.deepcopy(result.payload)
    payload["output"] = json.dumps({"not_found": True, "error": "constructed missing-file control"})
    receipt = observe_detail_reads(
        delivery,
        replace(capture, events=(call, replace(result, payload=payload))),
        project,
        frozen_contents=frozen,
    )
    assert receipt["details"][0]["state"] == "not seen in kept log"
    assert receipt["failedAttempts"][0]["state"] == "error"
    ranged = {
        **call.payload,
        "arguments": json.dumps({"path": str(delivery.details[0].path), "offset": 1, "limit": 3}),
    }
    ranged_receipt = observe_detail_reads(
        delivery,
        replace(capture, events=(replace(call, payload=ranged), result)),
        project,
        frozen_contents=frozen,
    )
    assert (
        ranged_receipt["details"][0]["state"] == "opened"
        and ranged_receipt["details"][0]["wholeRead"] is False
    )
    relative = {**call.payload, "arguments": json.dumps({"path": "authority.json"})}
    receipt = observe_detail_reads(
        delivery,
        replace(capture, events=(replace(call, payload=relative), result)),
        project,
        frozen_contents=frozen,
    )
    assert receipt["details"][0]["state"] == "not seen in kept log" and receipt["diagnostics"]


def test_r2_constructed_literal_ranges_searches_and_exec_command():
    _, delivery, capture, project, frozen = _r2_case(2)
    old_call, old_result = capture.events
    target = str(delivery.details[0].path)
    controls = [
        (f"cat -- {target}", "opened", True),
        (f"sed -n '1,5p' {target}", "opened", False),
        (f"head -n 5 {target}", "opened", False),
        (f"tail -5 {target}", "opened", False),
        (f"rg -n control {target}", "searched", False),
    ]
    for command, state, whole in controls:
        call = replace(
            old_call,
            payload={
                "call_id": "constructed-exec",
                "name": "exec_command",
                "server": None,
                "arguments": {"cmd": command, "workdir": str(project)},
            },
        )
        result = replace(
            old_result,
            payload={
                "call_id": "constructed-exec",
                "name": "exec_command",
                "output": {
                    "chunk_id": "constructed",
                    "wall_time_seconds": 0.1,
                    "exit_code": 0,
                    "output": next(iter(frozen.values())),
                },
            },
        )
        receipt = observe_detail_reads(
            delivery,
            replace(capture, harness="codex", events=(call, result)),
            project,
            frozen_contents=frozen,
        )
        assert receipt["details"][0]["state"] == state
        assert receipt["details"][0]["wholeRead"] is whole
        failed_output = {**result.payload, "output": {**result.payload["output"], "exit_code": 1}}
        failed = observe_detail_reads(
            delivery,
            replace(
                capture, harness="codex", events=(call, replace(result, payload=failed_output))
            ),
            project,
            frozen_contents=frozen,
        )
        assert failed["details"][0]["state"] == "not seen in kept log" and failed["failedAttempts"]


def test_r2_constructed_glob_variable_script_and_discovery_are_unknown():
    _, delivery, capture, project, frozen = _r2_case(2)
    call, result = capture.events
    for command in (
        "cat *.md",
        "cat $FILE",
        "cat detail.md && true",
        "python3 -c 'print(1)'",
        "rg --files detail.md",
    ):
        terminal = replace(
            call,
            payload={
                "call_id": "constructed-terminal",
                "name": "terminal",
                "server": None,
                "arguments": json.dumps({"command": command, "workdir": str(project)}),
            },
        )
        completed = replace(
            result,
            payload={
                "call_id": "constructed-terminal",
                "name": "terminal",
                "output": json.dumps({"output": "constructed", "exit_code": 0, "error": None}),
            },
        )
        receipt = observe_detail_reads(
            delivery,
            replace(capture, events=(terminal, completed)),
            project,
            frozen_contents=frozen,
        )
        assert receipt["details"][0]["state"] == "not seen in kept log"
        assert receipt["failedAttempts"] == []


_D646 = Path(__file__).parent / "fixtures/detail-read-observer-d646.json"


def _d646_case(sample):
    delivery = PointerDelivery(
        "",
        tuple(DetailFile(**{**row, "path": Path(row["path"])}) for row in sample["details"]),
        {},
        "",
        sample["frozenContents"],
    )
    capture = NativeCapture(
        sample["harness"],
        sample["sessionId"],
        tuple(NativeEvent(**row) for row in sample["events"]),
        tuple(SourceFile(**row) for row in sample["sourceFiles"]),
        (),
        (),
    )
    return delivery, capture


@pytest.mark.parametrize(
    "sample", json.loads(_D646.read_text())["samples"], ids=lambda row: row["label"]
)
def test_d646_retained_compound_and_execute_code_full_body_reads(sample):
    delivery, capture = _d646_case(sample)
    before = capture.to_dict()
    receipt = observe_detail_reads(
        delivery, capture, Path(sample["projectRoot"]), frozen_contents=sample["frozenContents"]
    )
    whole = {row["path"] for row in receipt["details"] if row["wholeRead"] is True}
    assert whole == set(sample["expectedFullBodyPaths"])
    assert {row["path"] for row in receipt["details"] if row["state"] == "opened"} == set(
        sample["expectedOpenedPaths"]
    )
    assert capture.to_dict() == before
    assert receipt["observationOnly"] and "rate" not in receipt
    for row in receipt["details"]:
        for observation in row["observations"]:
            assert observation["call_id"] == sample["events"][0]["payload"]["call_id"]
            assert observation["call"]["source_sha256"] == sample["events"][0]["source_sha256"]


def test_d646_relative_content_identity_needs_unique_suffix_and_matching_body():
    sample = next(
        row
        for row in json.loads(_D646.read_text())["samples"]
        if row["label"] == "relative-read-file-content-bound"
    )
    delivery, capture = _d646_case(sample)
    frozen = sample["frozenContents"]
    receipt = observe_detail_reads(
        delivery, capture, Path(sample["projectRoot"]), frozen_contents=frozen
    )
    proof = receipt["details"][0]["observations"][0]["bodyCorroboration"]
    assert proof["pathBinding"] == "relative literal suffix and unique frozen body; cwd unavailable"
    assert proof["cwd"] is None and proof["nativeAbsolutePath"] is None
    assert not proof["literalOperand"].startswith("/")
    # Constructed competing delivery: same literal suffix and bytes cannot bind an absolute path.
    first = delivery.details[0]
    competing = replace(first, path=Path("/competing") / first.path.relative_to("/"))
    ambiguous = observe_detail_reads(
        replace(delivery, details=(*delivery.details, competing)),
        capture,
        Path(sample["projectRoot"]),
        frozen_contents=frozen,
    )
    contested = {str(first.path), str(competing.path)}
    assert all(
        row["state"] == "not seen in kept log"
        for row in ambiguous["details"]
        if row["path"] in contested
    )
    assert all(
        row["state"] == "opened" for row in ambiguous["details"] if row["path"] not in contested
    )
    assert any("ambiguous delivered suffix" in row["basis"] for row in ambiguous["diagnostics"])
    # Constructed paired-result mutation: intent plus outer success without the frozen bytes is insufficient.
    call, result = capture.events
    output = json.loads(result.payload["output"])
    output["output"] = "different content"
    changed = replace(result, payload={**result.payload, "output": json.dumps(output)})
    mismatch = observe_detail_reads(
        delivery,
        replace(capture, events=(call, changed)),
        Path(sample["projectRoot"]),
        frozen_contents=frozen,
    )
    assert all(row["state"] == "not seen in kept log" for row in mismatch["details"])


def test_d646_echo_write_and_failed_cat_are_not_read_evidence():
    sample = next(
        row
        for row in json.loads(_D646.read_text())["samples"]
        if row["label"] == "native-terminal-shell-loop-full"
    )
    delivery, capture = _d646_case(sample)
    call, result = capture.events
    target = str(delivery.details[0].path)
    for command in (f"echo {target}", f"printf x > {target}", f"cat {target}; true"):
        changed_call = replace(
            call, payload={**call.payload, "arguments": json.dumps({"command": command})}
        )
        changed_result = replace(
            result,
            payload={**result.payload, "output": json.dumps({"output": target, "exit_code": 0})},
        )
        receipt = observe_detail_reads(
            delivery,
            replace(capture, events=(changed_call, changed_result)),
            Path(sample["projectRoot"]),
            frozen_contents=sample["frozenContents"],
        )
        assert all(row["state"] == "not seen in kept log" for row in receipt["details"])


def test_d646_dedup_and_per_call_work_scale_with_native_identities():
    sample = next(
        row
        for row in json.loads(_D646.read_text())["samples"]
        if row["label"] == "codex-compound-1"
    )
    delivery, capture = _d646_case(sample)
    call, result = capture.events
    for count in (8, 16):
        events = []
        for index in range(count):
            identity = f"constructed-identity-{index}"
            pair = (
                replace(call, sequence=index * 2, payload={**call.payload, "call_id": identity}),
                replace(
                    result, sequence=index * 2 + 1, payload={**result.payload, "call_id": identity}
                ),
            )
            events.extend((*pair, *pair))

        with patch.object(
            detail_reads, "_body_witness", wraps=detail_reads._body_witness
        ) as body_reads:
            receipt = observe_detail_reads(
                delivery,
                replace(capture, events=tuple(events)),
                Path(sample["projectRoot"]),
                frozen_contents=sample["frozenContents"],
            )
        assert body_reads.call_count == count * len(delivery.details)
        assert all(len(row["observations"]) == count for row in receipt["details"])
