"""The canonical curator and planner surfaces describe the writer's supported corrections."""

from pathlib import Path

from agents_remember.application.knowledge_gate.gate import __doc__ as GATE_GUIDE
from agents_remember.application.knowledge_writer import __doc__ as WRITER_GUIDE

ROOT = Path(__file__).resolve().parents[2] / "skills/l-01-agent-lifecycles"


def test_curator_guidance_names_one_run_rows_proofs_and_link_recovery() -> None:
    markers = (
        "handoff:<entry id>",
        "replace_evidence",
        "recompute the worklist",
        "line_range",
        "link_target",
        "refresh_not_current",
        "identical changes",
        "current design",
    )
    for relative in ("roles/curator.md", "templates/curator-handoff-list.md"):
        text = " ".join((ROOT / relative).read_text(encoding="utf-8").split())
        for marker in markers:
            assert marker in text, (relative, marker)


def test_planning_declares_only_packet_named_recorded_effects_before_implementation() -> None:
    for relative in ("roles/architect.md", "operations/planning.md"):
        text = " ".join((ROOT / relative).read_text(encoding="utf-8").split())
        for marker in ("expectedKnowledgeEffects", "before implementation", "declares nothing"):
            assert marker in text, (relative, marker)


def test_writer_and_gate_do_not_teach_the_retired_installed_cutover() -> None:
    assert WRITER_GUIDE is not None and GATE_GUIDE is not None
    writer = " ".join(WRITER_GUIDE.split())
    gate = " ".join(GATE_GUIDE.split())
    assert "installed database ingest until the cutover" not in writer
    assert "Unconverted memory converts before this writer is used" in writer
    assert "not wired here" not in gate
    assert "cutover's installed build" not in gate
    assert "An unreadable layout probe is never treated as an exemption" in gate
