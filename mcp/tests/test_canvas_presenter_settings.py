"""A source grant reads the exact packet tree, never the current working file."""

from __future__ import annotations

import subprocess
from pathlib import Path

from agents_remember.canvas.presenter_evidence import source as read_source
from agents_remember.models.knowledge.review_trees import ReviewTreeSide
from canvas_host_test_support import (
    packet,
    record,
)


def test_source_grant_reads_the_packet_tree_and_never_the_working_file(tmp_path: Path) -> None:
    repository = tmp_path / "exact-source"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    path = repository / "source.txt"
    path.write_text("the admitted historical bytes\nand their second line\n")
    subprocess.run(["git", "-C", str(repository), "add", "source.txt"], check=True)
    tree = subprocess.check_output(["git", "-C", str(repository), "write-tree"], text=True).strip()
    comparison = record().model_copy(
        update={"code_candidate": ReviewTreeSide(repository=str(repository), tree=tree)}
    )
    admitted = packet(comparison, claims=())
    path.write_text("CURRENT WORKTREE MUST NOT BE READ\n")
    receipt = read_source(admitted, "code-candidate", "source.txt", start=1, end=1)
    assert receipt["state"] == "present", receipt
    assert receipt["text"] == "the admitted historical bytes"
    assert receipt["tree"] == tree
    whole = read_source(admitted, "code-candidate", "source.txt", start=1)
    assert whole["state"] == "present", whole
    assert whole["text"] == "the admitted historical bytes\nand their second line"
    for side, target in (
        ("HEAD", "source.txt"),
        ("code-candidate", "../source.txt"),
        ("code-candidate", "/etc/hosts"),
        ("code-candidate", ":(glob)*"),
    ):
        refused = read_source(admitted, side, target, start=1, end=10)
        assert refused["state"] == "refused"
        assert not refused["text"]
