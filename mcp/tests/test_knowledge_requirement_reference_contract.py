"""The requirement reference and its owner: reference, never replacement.

A requirement reference names the task plane's packet by the same three components the task plane
uses, and the owner's own resolver answers whether it resolves. These cases measure which three
components a reference carries, and whose refusal is carried when the owner cannot resolve one.

The owner cases are measured against the owner's **real** resolver over a real task root, so the
codes asserted are the codes the task plane returns rather than string literals chosen here:
``task-intent-requirement-packet-missing``, ``task-intent-requirement-packet-outside-task`` (both
the non-Markdown target and the path outside the task root) and
``task-intent-requirement-packet-version-mismatch``.

(The database record group that stored such references, and its derived views, were retired with
the canonical database, MIK-R26. The reference model and the owner resolution serve the text
format's requirement endpoints, MIK-R13.)
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from agents_remember.memory.knowledge.requirement_owner import consume_owner_resolution
from agents_remember.models.knowledge.requirement import (
    REQUIREMENT_PACKET_VERSION_PATTERN,
    RequirementOwnerRef,
)
from agents_remember.models.task_intent import ApprovedRequirementPacketRef

OWNER_PATH = "requirements/KS-R19-v1-requirement-revision-as-substrate-record.md"


def _owner(
    path: str = OWNER_PATH, stable_id: str = "KS-R19", version: str = "v1"
) -> RequirementOwnerRef:
    return RequirementOwnerRef(path=path, stableId=stable_id, version=version)


def _write_packet(
    task_root: Path,
    relative: str,
    *,
    stable_id: str = "KS-R19",
    version: str = "v1",
    title: str = "a packet",
) -> Path:
    """Author one requirement packet under a task root, in the shipped Markdown form."""

    target = task_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "\n".join(
            (
                f"# {title}",
                "",
                "| Field | Value |",
                "| --- | --- |",
                f"| Stable ID | `{stable_id}` |",
                f"| Version | `{version}` |",
                "",
                "## Problem",
                "",
                "Packet body.",
                "",
            )
        ),
        encoding="utf-8",
    )
    return target


def test_the_owner_reference_carries_exactly_the_task_planes_three_components() -> None:
    """The reference names the same three components, spelled the same way.

    Catches a fourth addressing scheme, or ``stableId``/``version`` spelled differently from the
    task plane, which is how one version comes to be written two ways.
    """

    plane_fields = set(ApprovedRequirementPacketRef.model_fields)
    substrate_fields = set(RequirementOwnerRef.model_fields)
    # The task plane's own reference carries a ``kind`` discriminator beside the three identity
    # components; that is its own typing, not a fourth addressing component. What matters is that
    # the three components are spelled identically and that this side declares exactly those three.
    assert {"path", "stableId", "version"} <= plane_fields
    assert substrate_fields == {"path", "stableId", "version"}

    plane_pattern = ApprovedRequirementPacketRef.model_fields["version"].metadata
    assert REQUIREMENT_PACKET_VERSION_PATTERN in {
        str(getattr(item, "pattern", "")) for item in plane_pattern
    }

    with pytest.raises(ValueError):
        RequirementOwnerRef.model_validate(
            {"path": OWNER_PATH, "stableId": "KS-R19", "version": "v1", "packet_uuid": str(uuid4())}
        )


@pytest.mark.parametrize("version", ["1", "V1", "v0", "v01", "v1.0", "", "v-1"])
def test_a_version_outside_the_admitted_spelling_is_refused(version: str) -> None:
    """Only ``^v[1-9][0-9]*$`` is admitted, because that is the one spelling the task plane admits.

    Catches two spellings of one version comparing unequal on the two sides.
    """

    with pytest.raises(ValueError):
        _owner(version=version)


def test_the_reference_does_not_police_the_path_the_owner_owns() -> None:
    """A path the owner would refuse is still *representable*, so the owner's answer is the one.

    Catches a second resolver: a reference that confined the path to a task root, or required a
    ``.md`` suffix, would refuse before the owner was asked and would then have no owner refusal to
    carry -- substituting its own answer for the owner's.
    """

    for path in (
        "../outside.md",
        "/etc/passwd.md",
        "notes/not-markdown.txt",
        "deep/../../escape.md",
    ):
        assert _owner(path=path).path == path


def test_each_owner_refusal_is_carried_verbatim(tmp_path: Path) -> None:
    """The owner's own refusal is the resolution's state, code and detail.

    Catches a resolution that dropped an unresolvable reference, or substituted its own refusal for
    the owner's. Each shape is measured against the owner's real resolver, so the code asserted is
    the code the owner returns.
    """

    task_root = tmp_path / "task"
    _write_packet(task_root, OWNER_PATH, stable_id="KS-R19", version="v1")
    _write_packet(task_root, "requirements/KS-R19-v1-other.md", stable_id="KS-R99", version="v1")

    cases: tuple[tuple[str, str, str], ...] = (
        (
            "an absent packet",
            "requirements/absent.md",
            "task-intent-requirement-packet-missing",
        ),
        (
            "a non-Markdown target",
            "requirements/not-markdown.txt",
            "task-intent-requirement-packet-outside-task",
        ),
        (
            "a path outside the task root",
            "../outside.md",
            "task-intent-requirement-packet-outside-task",
        ),
        (
            "a metadata mismatch",
            "requirements/KS-R19-v1-other.md",
            "task-intent-requirement-packet-version-mismatch",
        ),
    )

    for label, path, expected_code in cases:
        reference = _owner(path=path)
        resolution = consume_owner_resolution(task_root, reference)
        assert resolution.state == "unresolved", label
        assert resolution.refusal_code == expected_code, label
        assert resolution.refusal_detail, label
        assert reference.path == path, label


def test_a_resolved_owner_is_reported_as_resolved(tmp_path: Path) -> None:
    """The owner's acceptance of the reference is the resolution, verbatim.

    Catches a resolution invented on this side rather than consumed from the owner.
    """

    task_root = tmp_path / "task"
    _write_packet(task_root, OWNER_PATH)
    resolution = consume_owner_resolution(task_root, _owner())
    assert resolution.state == "resolved"
    assert resolution.refusal_code is None and resolution.refusal_detail is None
