"""The reviewer has one source shape, including its browser consumers."""

from pathlib import Path


def test_reviewer_sources_do_not_name_the_removed_comparison_shape() -> None:
    root = Path(__file__).resolve().parents[2]
    forbidden = "dataset" + " review"
    offenders = []
    for directory, suffixes in (
        (root / "dashboard/src", {".ts", ".tsx"}),
        (root / "mcp/src/agents_remember", {".py"}),
    ):
        for path in sorted(directory.rglob("*")):
            if path.suffix in suffixes and forbidden in path.read_text(encoding="utf-8").lower():
                offenders.append(str(path.relative_to(root)))
    assert not offenders, "Removed comparison shape remains in: " + ", ".join(offenders)
