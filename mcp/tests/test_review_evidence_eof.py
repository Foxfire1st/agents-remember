"""MIK-R182: a bounded read that runs past the object's end is complete.

The evidence reader counts only lines that exist. A requested range that runs past the end ends at
the object's last line; a remaining count is stated as known only where the read measured it; and no
continuation leads at or past the end.

These five cases are MIK-R182@v1 rule 5's four proofs plus the one-past-the-end start line they
imply. They have their own under-limit owner because the packet module they were first written in
is already past the repository's 1,200-line hard limit (`[tool.agents_remember]
file_size_armed`), and a repair must not grow such a module. The comparison world and its helpers
stay where they are; this module imports them.
"""

from __future__ import annotations

import pytest
from agents_remember.application.review_evidence_read import (
    DEFAULT_READ_LIMITS,
    MAX_PAGE_LINES,
    read_bound_source,
)
from agents_remember.application.review_tree_comparison import write_files_tree
from agents_remember.models.knowledge.review_trees import ReviewTreeSide
from test_review_evidence_packet import _continuation, world

__all__ = ["world"]


def test_f03_r182_a_read_past_the_object_end_is_present_at_its_last_line(world) -> None:
    body = "".join(f"line {number}\n" for number in range(1, 7)).encode()
    tree = write_files_tree(world["code"], {"six.txt": body})
    record = world["record"].model_copy(
        update={"code_candidate": ReviewTreeSide(repository=str(world["code"]), tree=tree)}
    )
    receipt = read_bound_source(record, "code-candidate", "six.txt", start_line=1, end_line=100)
    assert receipt.state == "present"
    assert receipt.requested_range == (1, 100)
    assert receipt.returned_range == (1, 6)
    assert receipt.remaining_state == "known" and receipt.remaining_lines == 0
    assert receipt.continuation is None
    assert receipt.limit is None
    assert "line 6" in receipt.detail and "runs past it" in receipt.detail


def test_f03_r182_a_page_past_the_object_end_stops_at_its_last_line(world) -> None:
    lines = [f"line {number}" for number in range(450)]
    body = ("\n".join(lines) + "\n").encode()
    tree = write_files_tree(world["code"], {"paged.txt": body})
    record = world["record"].model_copy(
        update={"code_candidate": ReviewTreeSide(repository=str(world["code"]), tree=tree)}
    )
    first = read_bound_source(record, "code-candidate", "paged.txt", start_line=1, end_line=1000)
    assert first.state == "truncated"
    assert first.limit == "page_lines"
    assert first.returned_range == (1, MAX_PAGE_LINES)
    assert first.remaining_state == "known" and first.remaining_lines == 50
    pieces = []
    receipt = first
    for _page in range(DEFAULT_READ_LIMITS.pages_per_read):
        pieces.append(receipt.text or "")
        if receipt.state == "present":
            break
        assert receipt.state == "truncated" and receipt.continuation is not None
        offset, resume_line = _continuation(receipt.continuation)
        assert offset < len(body), "no continuation leads at or past the object's end"
        receipt = read_bound_source(
            record,
            "code-candidate",
            "paged.txt",
            start_line=resume_line,
            end_line=1000,
            start_byte=offset,
            resume_line=resume_line,
        )
    else:
        pytest.fail("the continuation never reached the object's end")
    assert receipt.state == "present"
    assert receipt.returned_range == (MAX_PAGE_LINES + 1, 450)
    assert receipt.continuation is None and receipt.limit is None
    assert receipt.remaining_lines == 0
    assert "line 450" in receipt.detail and "runs past it" in receipt.detail
    assert "\n".join(pieces).encode() + b"\n" == body


def test_f03_r182_an_unmeasured_remainder_is_unknown_not_the_requested_difference(world) -> None:
    body = "".join(f"line {number}\n" for number in range(1, 60)).encode()
    tree = write_files_tree(world["code"], {"capped.txt": body})
    record = world["record"].model_copy(
        update={"code_candidate": ReviewTreeSide(repository=str(world["code"]), tree=tree)}
    )
    tight = DEFAULT_READ_LIMITS.model_copy(update={"bytes_per_read": 64})
    receipt = read_bound_source(
        record, "code-candidate", "capped.txt", start_line=1, end_line=40, limits=tight
    )
    assert receipt.state == "truncated"
    assert receipt.limit == "bytes_per_read"
    assert receipt.remaining_state == "unknown"
    assert receipt.remaining_lines is None
    assert receipt.returned_range is not None and receipt.returned_range[1] < 40


def test_f03_r182_a_read_started_at_the_object_end_is_a_measured_absence(world) -> None:
    body = b"one\ntwo\nthree\n"
    tree = write_files_tree(world["code"], {"short.txt": body})
    record = world["record"].model_copy(
        update={"code_candidate": ReviewTreeSide(repository=str(world["code"]), tree=tree)}
    )
    receipt = read_bound_source(
        record,
        "code-candidate",
        "short.txt",
        start_line=4,
        end_line=1000,
        start_byte=len(body),
        resume_line=4,
    )
    assert receipt.state == "absent"
    assert receipt.text is None
    assert receipt.returned_range is None
    assert receipt.continuation is None
    assert "measured range absence" in receipt.detail


def test_f03_r182_a_start_line_one_past_the_object_end_is_a_measured_absence(world) -> None:
    body = b"one\ntwo\n"
    tree = write_files_tree(world["code"], {"two.txt": body})
    record = world["record"].model_copy(
        update={"code_candidate": ReviewTreeSide(repository=str(world["code"]), tree=tree)}
    )
    receipt = read_bound_source(record, "code-candidate", "two.txt", start_line=3, end_line=3)
    assert receipt.state == "absent"
    assert receipt.text is None and receipt.returned_range is None
    assert "2 line(s)" in receipt.detail
