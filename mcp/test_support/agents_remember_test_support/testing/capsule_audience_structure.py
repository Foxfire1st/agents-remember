"""Compare current audience and link delivery to its source structure."""

from __future__ import annotations

import re
from pathlib import Path

from agents_remember.models.role_capsules.manifest import parse_composition_manifest

from agents_remember_test_support.testing.manifest_step_derivation import LINK
from agents_remember_test_support.testing.pointer_uniqueness import _resolve


def operation_projection(source: str, audience: str, role: str) -> str:
    """Read marked source sections independently of the compiler's audience selector."""
    pieces = re.split(r"(?m)^<!-- ar:roles ([a-z,\-]+) -->[ \t]*\r?\n", source)
    if audience == "all":
        selected = source
    else:
        assert not pieces[0].strip(), "operation text precedes its audience"
        assert len(pieces) > 1, "operation has no marked sections"
        selected = "".join(
            body
            for recipients, body in zip(pieces[1::2], pieces[2::2], strict=True)
            if "all" in recipients.split(",") or role in recipients.split(",")
        )
    return "".join(
        line
        for line in selected.splitlines(keepends=True)
        if not line.startswith(("<!-- ar:needs ", "<!-- ar:gives "))
    )


def assert_operation_projection(
    root: Path, source: str, audience: str, role: str, actual: str
) -> None:
    """Require all common/addressed source bytes and no foreign-role stage bytes."""
    expected = operation_projection((root / source).read_text(), audience, role)
    assert actual == expected, (source, role, "stage differs from its addressed source sections")


def foreign_passage(source: str, role: str) -> str:
    """Choose an actual nonempty passage addressed exclusively to other roles."""
    pieces = re.split(r"(?m)^<!-- ar:roles ([a-z,\-]+) -->[ \t]*\r?\n", source)
    return next(
        (
            body
            for recipients, body in zip(pieces[1::2], pieces[2::2], strict=True)
            if "all" not in recipients.split(",")
            and role not in recipients.split(",")
            and body.strip()
        ),
        "",
    )


def source_detail_link_count(root: Path, role: str, operation: str, target: str) -> int:
    """Count source link occurrences, including several sections in one action."""
    manifest = parse_composition_manifest((root / "composition-manifest.json").read_bytes())
    entry = manifest.roles[role]
    stage = manifest.operations[operation]
    sources = (
        (entry.file, (root / entry.file).read_text()),
        (
            stage.source,
            operation_projection((root / stage.source).read_text(), stage.audience, role),
        ),
    )
    count = 0
    for source, text in sources:
        fence = False
        for line in text.splitlines():
            if line.lstrip().startswith(("```", "~~~")):
                fence = not fence
            elif not fence:
                count += sum(_resolve(match[1], source) == target for match in LINK.finditer(line))
    return count
