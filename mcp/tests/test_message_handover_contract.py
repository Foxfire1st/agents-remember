"""Held delivery and full-store refusal each have one home, even when paraphrased."""

import re
from pathlib import Path

import pytest
from agents_remember.application.role_capsules.compilation import compile_admitted_capsule
from agents_remember.application.skill_resources.capsule import (
    CapsuleSeatAddress,
    routed_admission_for,
)
from agents_remember.cli.role_brief_message import shared_block
from agents_remember.models.role_capsules.types import compute_content_digest
from agents_remember.models.tools.public_roster import PUBLIC_TOOLS
from test_role_capsule_compiler import worker_binding

HOME = (
    Path(__file__).resolve().parents[2]
    / "skills/l-01-agent-lifecycles"
    / "reference/message-handover.md"
)


def _assert_one_receipt_contract(held_text: str, refusal_text: str) -> None:
    # The two receipt branches have separate homes; a combined paraphrase repeats both.
    paragraphs = [
        paragraph
        for text in (held_text, refusal_text)
        for paragraph in re.split(r"\n\s*\n", text)
        if not paragraph.lstrip().startswith("#")
    ]
    held_contracts = [
        paragraph
        for paragraph in paragraphs
        if re.search(r"\bheld\b", paragraph, re.I) and re.search(r"\bqueued\b", paragraph, re.I)
    ]
    refusal_contracts = [
        paragraph
        for paragraph in paragraphs
        if re.search(r"\b(?:recipient-busy|full-store)\b", paragraph, re.I)
    ]
    assert len(held_contracts) == 1, "held/busy contract has multiple semantic homes"
    assert len(refusal_contracts) == 1, "held/busy contract has multiple semantic homes"
    assert held_contracts[0] in held_text
    assert held_contracts[0] not in refusal_text
    assert refusal_contracts[0] in refusal_text
    assert refusal_contracts[0] not in held_text
    held_home = next(
        section
        for section in re.split(r"(?m)^#{1,6} .+$", held_text)
        if held_contracts[0] in section
    )
    refusal_home = next(
        section
        for section in re.split(r"(?m)^#{1,6} .+$", refusal_text)
        if refusal_contracts[0] in section
    )
    assert "never resend it" in held_contracts[0].casefold()
    assert "Withdraw a superseded held message with `withdraw_held_message_id`." in held_home
    for clause in (
        "Record the handover as pending",
        "continue independent work",
        "Resend once before the turn ends",
        "report the second refusal",
        "leave it pending",
    ):
        assert clause in refusal_home


def test_receipt_contract_has_one_home_even_when_repeated_in_other_words() -> None:
    root = HOME.parents[1]
    shared_source = (root / "core/every-role.md").read_text()
    binding = worker_binding(operation="orientation", granted=frozenset(PUBLIC_TOOLS))
    admission = routed_admission_for(
        root,
        "composition-manifest.json",
        (root / "composition-manifest.json").read_bytes(),
        CapsuleSeatAddress("worker", "orientation"),
    )
    outcome = compile_admitted_capsule(binding, admission)
    assert outcome.ok, outcome.render_explanation()
    assert outcome.result is not None
    assert outcome.shared_block is not None
    assert outcome.shared_block == shared_source
    assert shared_block(outcome.shared_block).count(shared_source) == 1
    source = next(
        record for record in outcome.result.manifest.sources if record.path == "core/every-role.md"
    )
    assert source.revision == compute_content_digest(shared_source.encode())
    (reference,) = (
        item
        for item in outcome.result.capsule.detail_references
        if item.source_path == "reference/message-handover.md"
    )
    assert reference.content == HOME.read_bytes()
    assert reference.step.strip()
    rendered = outcome.result.render_instructions()
    marker = "{{detail:reference/message-handover.md}}"
    action = "Before sending a handover, read"
    assert (
        sum(action in paragraph and marker in paragraph for paragraph in rendered.split("\n\n"))
        == 1
    )
    held_text = outcome.shared_block
    text = reference.content.decode()
    _assert_one_receipt_contract(held_text, text)
    for duplicate in (
        "A held message is queued and is not resent. A recipient-busy refusal is "
        "recorded and retried before ending the turn; report a second refusal "
        "under the standing message procedure.",
        "Record a full-store refusal and retry it before ending the turn. "
        "When delivery is held, keep the queued message rather than sending it again.",
    ):
        assert duplicate not in text
        assert duplicate not in held_text
        with pytest.raises(AssertionError, match="multiple semantic homes"):
            _assert_one_receipt_contract(held_text, text + "\n\n" + duplicate)
        with pytest.raises(AssertionError, match="multiple semantic homes"):
            _assert_one_receipt_contract(held_text + "\n\n" + duplicate, text)
    assert "Withdraw a superseded held message with `withdraw_held_message_id`." in held_text
    assert "A receipt or turn-end does not establish semantic acceptance." in text
