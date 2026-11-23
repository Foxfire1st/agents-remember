"""MIK-R182 and MIK-R211 v4 rule 3: a read that runs past the object's end is complete.

The evidence reader counts only lines that exist, and it answers in full: a requested range that
runs past the end ends at the object's last line in the same read, no window or page cuts a line or
a range, and a start line past the end is a measured absence with the object's true line count.

These cases are MIK-R182@v1 rule 5's proofs, restated for reads with no product page size. They
have their own under-limit owner because the packet module they were first written in is close to
the repository's 1,200-line hard limit (`[tool.agents_remember] file_size_armed`), and a repair
must not grow such a module. The comparison world and its helpers
stay where they are; this module imports them.
"""

from __future__ import annotations

import tracemalloc
from tempfile import TemporaryFile

from agents_remember.application.review_evidence_read import (
    _fetch_blob,
    _read_spooled_blob,
    read_bound_source,
    read_whole_source,
)
from agents_remember.application.review_tree_comparison import write_files_tree
from agents_remember.models.knowledge.review_trees import ReviewTreeSide
from test_review_evidence_packet import world

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


def test_f03_r182_a_range_past_the_object_end_answers_every_line_in_one_read(world) -> None:
    lines = [f"line {number}" for number in range(450)]
    body = ("\n".join(lines) + "\n").encode()
    tree = write_files_tree(world["code"], {"paged.txt": body})
    record = world["record"].model_copy(
        update={"code_candidate": ReviewTreeSide(repository=str(world["code"]), tree=tree)}
    )
    receipt = read_bound_source(record, "code-candidate", "paged.txt", start_line=1, end_line=1000)
    assert receipt.state == "present", receipt.detail
    assert receipt.returned_range == (1, 450)
    assert receipt.continuation is None and receipt.limit is None
    assert receipt.remaining_state == "known" and receipt.remaining_lines == 0
    assert receipt.object_lines == 450
    assert "line 450" in receipt.detail and "runs past it" in receipt.detail
    assert (receipt.text or "").encode() + b"\n" == body


def test_f03_r182_a_line_longer_than_any_old_window_comes_back_whole(world) -> None:
    line = "x" * (256 * 1024 + 4096)
    body = f"{line}\nlast\n".encode()
    tree = write_files_tree(world["code"], {"wide.txt": body})
    record = world["record"].model_copy(
        update={"code_candidate": ReviewTreeSide(repository=str(world["code"]), tree=tree)}
    )
    receipt = read_bound_source(record, "code-candidate", "wide.txt", start_line=1, end_line=1)
    assert receipt.state == "present", receipt.detail
    assert receipt.text == line
    assert receipt.returned_range == (1, 1)
    assert receipt.examined_bytes == len(body)
    assert receipt.next_byte == len(line) + 1


def test_f03_r182_a_start_line_one_past_the_object_end_is_a_measured_absence(world) -> None:
    body = b"one\ntwo\n"
    tree = write_files_tree(world["code"], {"two.txt": body})
    record = world["record"].model_copy(
        update={"code_candidate": ReviewTreeSide(repository=str(world["code"]), tree=tree)}
    )
    receipt = read_bound_source(record, "code-candidate", "two.txt", start_line=3, end_line=1000)
    assert receipt.state == "absent"
    assert receipt.text is None and receipt.returned_range is None
    assert receipt.continuation is None
    assert "2 line(s)" in receipt.detail and "measured range absence" in receipt.detail


def test_whole_blob_transport_allocates_one_payload_copy(world) -> None:
    for size in (3 * 1024 * 1024, 12 * 1024 * 1024):
        text = f"é\r\n{'x' * size}\r\n"
        body = text.encode("utf-8")
        tree = write_files_tree(world["code"], {"wide.txt": body})
        record = world["record"].model_copy(
            update={"code_candidate": ReviewTreeSide(repository=str(world["code"]), tree=tree)}
        )
        receipt = read_whole_source(record, "code-candidate", "wide.txt")
        assert receipt.state == "present", receipt.detail
        assert receipt.text == text
        assert receipt.object_lines == 2
        assert receipt.examined_bytes == len(body)
        assert receipt.object_id is not None

        tracemalloc.start()
        try:
            answer = _fetch_blob(world["code"], receipt.object_id)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        assert answer == body
        assert peak < len(body) * 3 // 2, (len(body), peak)


def test_spooled_evidence_keeps_the_blob_reader_failure_details() -> None:
    object_id = "1" * 40
    unreadable = f"the object {object_id} is not a blob this repository can read"
    frames = (
        ("", f"{unreadable} (no header)"),
        (f"{object_id} missing\n", f"{unreadable} ({object_id} missing)"),
        (f"{object_id} tree 0\n\n", f"{unreadable} ({object_id} tree 0)"),
        (f"{object_id} blob size\n", f"the object {object_id} reported no readable size"),
        (
            f"{object_id} blob 1073741824\npartial",
            f"the blob stream for {object_id} ended after 7 of its 1073741824 byte(s)",
        ),
    )
    for frame, detail in frames:
        with TemporaryFile() as output:
            output.write(frame.encode("utf-8"))
            output.flush()
            output.seek(0)
            assert _read_spooled_blob(output, object_id) == detail
