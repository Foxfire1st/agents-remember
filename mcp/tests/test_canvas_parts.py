"""The page's part map and the two operations that read it (MIK-R104 rules 1, 2, 4).

Each case protects one way a page's parts can be read wrongly, or one way the two new operations can
carry something the protocol does not admit: a claim that lands in the wrong part, a part id no
reader may be told to mark, an offer that exceeds its bounds, and a ``stale-parts`` message that
names a part the revision does not hold or is bound to another session.
"""

from __future__ import annotations

import json

import pytest
from agents_remember.canvas.parts import derive_part_map, part_id_problem
from agents_remember.canvas.protocol import (
    HOST_INITIATED_OPS,
    MAX_STALE_PARTS,
    Binding,
    ProtocolRefusal,
    encode_host_message,
    parse_frame_message,
    parse_host_message,
    stale_parts_payload,
)
from agents_remember.canvas.readiness import check_page
from canvas_host_test_support import packet, record

BINDING = Binding(session="session-0123456789", revision=3, comparison="review-comparison:t/l/7")


def _page(body: str) -> dict[str, bytes]:
    return {"index.html": body.encode()}


def test_a_claim_belongs_to_the_innermost_part() -> None:
    # A title is content: one past the old 120-character cut is kept whole (the Manager's 14:29Z
    # ruling); a view may wrap it, the map does not shorten it.
    title = "The change " + "and why it matters " * 10
    part_map = derive_part_map(
        f"<div data-part='outer' data-part-title='{title}'>"
        "<span>intro</span><div><p>nested</p></div>"
        "<span data-evidence-claim='c-present'>x</span>"
        "<div data-part='inner'><span data-evidence-claim='c-gone'>y</span></div>"
        "<img src='a.svg'><br>"
        "<b data-evidence-claim='c-after'>z</b>"
        "</div>"
        "<section data-part='same' data-evidence-claim='c-same'>same element</section>"
    )
    assert not part_map.problems
    assert [entry.part_id for entry in part_map.parts] == ["outer", "inner", "same"]
    assert part_map.parts[0].title == title.strip()
    assert part_map.parts[0].claims == ("c-present", "c-after")
    assert part_map.parts[1].claims == ("c-gone",)
    assert part_map.parts[2].claims == ("c-same",)
    assert part_map.part_of("c-gone") == "inner"
    assert part_map.part_of("c-after") == "outer"
    assert part_map.part_of("c-same") == "same"


def test_static_dom_containment_decides_every_part_boundary() -> None:
    """E09: real element nesting, repeated tags, voids, same-element marks, matched closes only."""
    # A claim after a nested NON-part element closes stays in the outer part: the part's scope is
    # the element nesting, not the most recent close tag.
    after_inner_close = derive_part_map(
        "<div data-part='a'><span>x</span><u data-evidence-claim='c1'>y</u></div>"
    )
    assert after_inner_close.parts[0].claims == ("c1",)
    assert not after_inner_close.problems

    # A closing tag that matches nothing ends no scope, and a repeated tag pair closes innermost.
    unmatched = derive_part_map(
        "<div data-part='a'><div data-part='b'>x</div></i>"
        "<span data-evidence-claim='c1'>y</span></div>"
    )
    assert [entry.part_id for entry in unmatched.parts] == ["a", "b"]
    assert unmatched.parts[0].claims == ("c1",)
    assert not unmatched.problems

    # Voids never open a scope even with the part attribute; their mark lands on the enclosing part,
    # and a void with its own part takes the same-element mark with it.
    voids = derive_part_map(
        "<div data-part='a'><br data-part='brp' data-evidence-claim='c-own'>"
        "<img data-evidence-claim='c-outer'><hr></div>"
    )
    assert {entry.part_id: list(entry.claims) for entry in voids.parts} == {
        "brp": ["c-own"],
        "a": ["c-outer"],
    }
    assert not voids.problems

    # A self-closing non-void element is a part and a claim on one element.
    self_closed = derive_part_map("<div data-part='s' data-evidence-claim='c-self'/>")
    assert self_closed.parts[0].claims == ("c-self",)
    assert not self_closed.problems

    # Sibling parts after one closes: the later claim belongs to the later part only.
    siblings = derive_part_map(
        "<div data-part='a'><span data-evidence-claim='c1'>x</span></div>"
        "<div data-part='b'><span data-evidence-claim='c2'>y</span></div>"
    )
    assert {entry.part_id: list(entry.claims) for entry in siblings.parts} == {
        "a": ["c1"],
        "b": ["c2"],
    }
    assert not siblings.problems


def test_a_part_id_readiness_cannot_name_is_refused() -> None:
    assert part_id_problem("the-change") is None
    assert part_id_problem("") is not None
    assert part_id_problem("Two") is not None
    # An id is the page's address: past 64 characters it is refused, naming the id and how to
    # shorten it (the Manager's 14:29Z ruling, K on its three conditions).
    too_long = part_id_problem("a" * 65)
    assert too_long is not None and repr("a" * 65) in too_long
    assert "shorten it to 64 characters or fewer" in too_long
    assert part_id_problem("page") is not None
    twice = derive_part_map("<div data-part='a'></div><div data-part='a'></div>")
    assert any("declared twice" in problem for problem in twice.problems)


def test_a_claim_outside_every_part_is_a_problem_and_a_page_with_no_parts_is_one_part() -> None:
    outside = derive_part_map(
        "<span data-evidence-claim='c-present'>x</span><div data-part='a'></div>"
    )
    assert outside.problems
    assert outside.claims_outside_parts == ("c-present",)
    # The problem names every claim outside the parts, not the first five of them.
    many = derive_part_map(
        "".join(f"<span data-evidence-claim='c-{n}'>x</span>" for n in range(7))
        + "<div data-part='a'></div>"
    )
    named = next(problem for problem in many.problems if "outside every part" in problem)
    assert all(f"c-{n}" in named for n in range(7)), named
    whole = derive_part_map("<span data-evidence-claim='c-present'>x</span>")
    assert whole.implicit
    assert whole.parts[0].part_id == "page"
    assert whole.parts[0].claims == ("c-present",)


def test_readiness_reads_a_claim_id_and_refuses_a_basis_reference() -> None:
    good = _page(
        "<div data-part='a'>"
        "<span data-evidence-claim='c-present'>x</span>"
        "<span data-evidence-claim='c-gone'>y</span>"
        "</div>"
    )
    admitted = check_page(good, entrypoint="index.html", packet=packet(record()))
    assert admitted.ready, admitted.diagnostics()
    assert admitted.part_map is not None and not admitted.part_map.implicit

    incomplete = _page("<div data-part='a'><span data-evidence-claim='c-present'>x</span></div>")
    missing_claim = check_page(incomplete, entrypoint="index.html", packet=packet(record()))
    assert not missing_claim.ready
    assert "c-gone" in {check.name: check.detail for check in missing_claim.failures}["part-map"]

    cross_part = _page(
        "<div data-part='a'><span data-evidence-claim='c-present'>x</span></div>"
        "<div data-part='b'>"
        "<span data-evidence-claim='c-present'>x2</span>"
        "<span data-evidence-claim='c-gone'>y</span>"
        "</div>"
    )
    duplicated = check_page(cross_part, entrypoint="index.html", packet=packet(record()))
    assert not duplicated.ready
    assert "both part" in {check.name: check.detail for check in duplicated.failures}["part-map"]

    stale = _page(
        "<div data-part='a'><span data-evidence-claim='code-candidate:src/app.py'>x</span></div>"
    )
    refused = check_page(stale, entrypoint="index.html", packet=packet(record()))
    assert not refused.ready
    detail = {check.name: check.detail for check in refused.failures}["part-map"]
    assert "claim id" in detail


def test_an_offer_is_bounded_and_carries_plain_text_verbatim() -> None:
    raw = json.dumps(
        {
            "schema": "ar-canvas-message/v1",
            "protocolVersion": 1,
            "type": "offer-selections",
            "requestId": "request-0001",
            "session": BINDING.session,
            "revision": BINDING.revision,
            "comparison": BINDING.comparison,
            "payload": {"selections": [{"question": "Which variant?", "choice": "the dense one"}]},
        }
    ).encode()
    message = parse_frame_message(raw, binding=BINDING)
    assert message.operation == "offer-selections"
    assert message.payload["selections"] == [
        {"question": "Which variant?", "choice": "the dense one"}
    ]


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ({"selections": []}, "malformed"),
        ({"selections": [{"question": "q", "choice": "c"}] * 13}, "oversized"),
        ({"selections": [{"question": "q", "choice": "c" * 81}]}, "oversized"),
        ({"selections": [{"question": "q", "choice": "c", "extra": "x"}]}, "malformed"),
    ],
)
def test_an_offer_outside_its_bounds_is_refused_by_name(payload, reason) -> None:
    raw = json.dumps(
        {
            "schema": "ar-canvas-message/v1",
            "protocolVersion": 1,
            "type": "offer-selections",
            "requestId": "request-0002",
            "session": BINDING.session,
            "revision": BINDING.revision,
            "comparison": BINDING.comparison,
            "payload": payload,
        }
    ).encode()
    with pytest.raises(ProtocolRefusal) as refused:
        parse_frame_message(raw, binding=BINDING)
    assert refused.value.reason == reason


def test_stale_parts_drops_unknown_ids_and_keeps_the_true_count() -> None:
    payload, dropped = stale_parts_payload(
        known_part_ids={"intro", "proof"},
        stale_part_ids=["intro", "invented", "page"],
        total=4,
    )
    assert payload == {"parts": ["intro"], "total": 4}
    assert dropped == ("invented", "page")

    many = stale_parts_payload(
        known_part_ids={"intro", "proof"},
        stale_part_ids=[f"part-{index}" for index in range(MAX_STALE_PARTS + 4)],
        total=MAX_STALE_PARTS + 4,
    )
    assert many[0]["parts"] == []
    assert many[0]["total"] == MAX_STALE_PARTS + 4


def test_a_host_message_round_trips_and_is_bound_to_its_own_frame() -> None:
    payload, _dropped = stale_parts_payload(known_part_ids={"intro"}, stale_part_ids=["intro"])
    raw = encode_host_message(BINDING, "stale-parts", payload)
    operation, parsed = parse_host_message(raw, binding=BINDING)
    assert operation == "stale-parts"
    assert parsed == {"parts": ["intro"], "total": 1}

    other = Binding(session="session-9999999999", revision=3, comparison=BINDING.comparison)
    with pytest.raises(ProtocolRefusal) as refused:
        parse_host_message(raw, binding=other)
    assert refused.value.reason == "foreign-session"


def test_the_host_may_only_open_the_operations_it_owns() -> None:
    assert HOST_INITIATED_OPS == ("stale-parts",)
    with pytest.raises(ProtocolRefusal) as refused:
        encode_host_message(BINDING, "ready", {})
    assert refused.value.reason == "unknown-op"
