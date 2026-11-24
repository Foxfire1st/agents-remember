"""The model-free readiness check a revision passes before it becomes ready.

A revision is published as ready only after the product has *loaded* it once without a model. What
"loaded" means here is the part this host can establish itself, from the exact bytes it is about to
publish:

* the entrypoint exists, is text, and parses as one HTML document;
* every subresource the page names -- HTML/SVG ``href``, ``src``, ``srcset``, ``poster``, inline
  styles/scripts, CSS ``url()`` and ``@import``, and non-kit JavaScript imports -- is an admitted
  asset of this same revision, and every ``kit/...`` asset matches the shipped kit byte-for-byte.
  A page that requests the network, another revision, a task report, a source checkout or a ``..``
  path is not ready;
* every evidence reference the page cites in HTML/SVG markup is one the admitted packet carries. A
  reference the packet does not carry would open nothing, so it is a failed check rather than a
  limitation;
* HTML audio and video elements do not autoplay or loop and carry controls a reader can use.

The check says nothing about how good the explanation is, and it does not replace the browser
isolation proof or prospective browser load. It is static: it resolves references, verifies kit
bytes, refuses network requests, checks HTML media controls, and checks static evidence bindings,
noting when scripts are present rather than pretending to execute them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Literal

from agents_remember.application.review_evidence_claims import resolve_claim
from agents_remember.canvas.kit import KIT_CONTRACT_PREFIX
from agents_remember.canvas.kit_references import (
    extract_references,
    is_external_reference,
    is_ignored_reference,
    resolve_reference,
    verify_kit_assets,
)
from agents_remember.canvas.parts import WHOLE_PAGE_PART, PartEntry, PartMap, derive_part_map
from agents_remember.models.knowledge.review_evidence import (
    ClaimBasis,
    EvidenceClaim,
    EvidencePacket,
)

__all__ = [
    "ReadinessCheck",
    "ReadinessReport",
    "check_page",
    "claim_basis_problem",
    "evidence_reference_state",
]

#: The HTML attribute grammar allows a double-quoted, single-quoted or unquoted value; the gate reads
#: all three, because a page that names an unadmitted reference must not become invisible by leaving
#: the quotes out. ``data-evidence-claim`` is not this attribute: a claim mark carries a claim id and
#: belongs to the part map (:mod:`agents_remember.canvas.parts`).
_EVIDENCE = re.compile(
    r"""data-evidence\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>"']+))""", re.IGNORECASE
)
_SCRIPT = re.compile(r"<script\b", re.IGNORECASE)
_HTML_MEDIA = (".html", ".htm")
_EVIDENCE_MEDIA = (".html", ".htm", ".svg")


class _MediaTags(HTMLParser):
    """Collect audio and video start tags with their real attribute names and values."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, dict[str, str]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"audio", "video"}:
            self.tags.append(
                (
                    tag.lower(),
                    {name.lower(): "" if value is None else value for name, value in attrs},
                )
            )

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)


def _media_tags(text: str) -> tuple[tuple[str, dict[str, str]], ...]:
    """The page's audio and video tags, read as attributes rather than as words in the tag."""

    parser = _MediaTags()
    parser.feed(text)
    parser.close()
    return tuple(parser.tags)


def _media_label(tag: str, attrs: dict[str, str]) -> str:
    """A readable form of one media tag for a failure message."""

    rendered = " ".join(f'{name}="{value}"' for name, value in attrs.items())
    return f"<{tag}{(' ' + rendered) if rendered else ''}>"


@dataclass(frozen=True)
class ReadinessCheck:
    """One named check of the prospective revision, and what it observed."""

    name: str
    state: Literal["passed", "failed"]
    detail: str


@dataclass(frozen=True)
class ReadinessReport:
    """Every check of one prospective revision. ``ready`` is true only when none failed."""

    checks: tuple[ReadinessCheck, ...]
    notes: tuple[str, ...] = ()
    #: The part map the product writes into the manifest when this revision publishes (MIK-R104
    #: rule 4). It is present even for a report that fails, so a refusal can name the parts it read.
    part_map: PartMap | None = None

    @property
    def ready(self) -> bool:
        return all(check.state == "passed" for check in self.checks)

    @property
    def failures(self) -> tuple[ReadinessCheck, ...]:
        return tuple(check for check in self.checks if check.state == "failed")

    def diagnostics(self) -> dict[str, Any]:
        """What a presenter may read: the failed checks and what each observed, never prose only."""

        return {
            "ready": self.ready,
            "failed": [{"check": check.name, "detail": check.detail} for check in self.failures],
            "notes": list(self.notes),
        }


def _evidence_references(text: str) -> list[str]:
    """Every evidence reference in one document, quoted or not."""

    found: list[str] = []
    for double, single, bare in _EVIDENCE.findall(text):
        value = (double or single or bare).strip()
        if value:
            found.append(value)
    return found


def _check_entry(assets: dict[str, bytes], entrypoint: str) -> ReadinessCheck:
    payload = assets.get(entrypoint)
    if payload is None:
        return ReadinessCheck("entrypoint", "failed", f"the revision holds no {entrypoint!r}")
    if not payload.strip():
        return ReadinessCheck("entrypoint", "failed", f"{entrypoint!r} is empty")
    try:
        payload.decode("utf-8")
    except UnicodeDecodeError:
        return ReadinessCheck("entrypoint", "failed", f"{entrypoint!r} is not UTF-8 text")
    return ReadinessCheck("entrypoint", "passed", f"{entrypoint!r} is one admitted UTF-8 document")


def _offending(assets: dict[str, bytes]) -> tuple[list[str], list[str]]:
    """Every reference that leaves the revision, and every one that names no admitted asset."""

    outside: list[str] = []
    missing: list[str] = []
    for path, payload in sorted(assets.items()):
        for reference in extract_references(path, payload):
            if is_ignored_reference(reference):
                continue
            if is_external_reference(reference):
                outside.append(f"{path} -> {reference}")
                continue
            target = resolve_reference(path, reference)
            if target is None:
                outside.append(f"{path} -> {reference}")
            elif target not in assets:
                missing.append(f"{path} -> {reference}")
    return outside, missing


def _check_assets(assets: dict[str, bytes]) -> ReadinessCheck:
    kit_problems = verify_kit_assets(assets)
    outside, missing = _offending(assets)
    problems: list[str] = []
    if kit_problems:
        problems.append("; ".join(kit_problems))
    if outside:
        problems.append(
            "the page requests something outside its own admitted assets: " + "; ".join(outside)
        )
    if missing:
        problems.append("the page names assets the revision does not carry: " + "; ".join(missing))
    if problems:
        return ReadinessCheck("admitted-assets", "failed", "; ".join(problems))
    return ReadinessCheck(
        "admitted-assets",
        "passed",
        "every reference stays inside this revision and names an admitted asset",
    )


def evidence_reference_state(packet: EvidencePacket, reference: str) -> str | None:
    """The state a packet gives one reference: ``present``/``changed``/``unavailable`` or None."""

    for claim in packet.claims.claims:
        for basis in claim.bases:
            if basis.reference == reference:
                return basis.state
    for receipt in packet.supplemental:
        if receipt.receipt_id == reference:
            return receipt.read_state
    return None


def _check_evidence(assets: dict[str, bytes], packet: EvidencePacket) -> ReadinessCheck:
    seen: list[str] = []
    for path, payload in sorted(assets.items()):
        if path.startswith(KIT_CONTRACT_PREFIX) or not path.lower().endswith(_EVIDENCE_MEDIA):
            continue
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            continue
        seen.extend(_evidence_references(text))
    if not seen:
        return ReadinessCheck(
            "evidence-references", "passed", "the page cites no evidence reference"
        )
    unknown = [
        reference for reference in seen if evidence_reference_state(packet, reference) is None
    ]
    if unknown:
        return ReadinessCheck(
            "evidence-references",
            "failed",
            "the page cites references the admitted packet does not carry: "
            + "; ".join(sorted(set(unknown))),
        )
    states = sorted({evidence_reference_state(packet, reference) or "" for reference in seen})
    return ReadinessCheck(
        "evidence-references",
        "passed",
        f"{len(set(seen))} cited reference(s) resolve in the admitted packet ({', '.join(states)})",
    )


def _check_parts(document: str, packet: EvidencePacket) -> tuple[ReadinessCheck, PartMap]:
    """The page's part map, and the three checks readiness owes it (MIK-R104 rule 4).

    The product writes the map, never the explainer: every part carries an id readiness admits, a
    claim belongs to exactly one part (the innermost), and every ``data-evidence-claim`` value is a
    claim id of the revision's claim map whose bases resolve in the packet. A page without parts is
    one part holding every claim, and its staleness is whole-page.
    """

    part_map = derive_part_map(document)
    if part_map.problems:
        return (
            ReadinessCheck(
                "part-map",
                "failed",
                "; ".join(part_map.problems),
            ),
            part_map,
        )
    known_order = tuple(claim.claim_id for claim in packet.claims.claims)
    known = set(known_order)
    unknown = [claim_id for claim_id in part_map.claims() if claim_id not in known]
    if unknown:
        return (
            ReadinessCheck(
                "part-map",
                "failed",
                "the page marks values that are not claim ids of this revision's claim map: "
                + ", ".join(sorted(set(unknown)))
                + "; a claim mark carries a claim id, never a basis reference or a receipt id",
            ),
            part_map,
        )
    if part_map.implicit and not part_map.claims() and known_order:
        part_map = PartMap(
            parts=(PartEntry(WHOLE_PAGE_PART, "the page", known_order),),
            implicit=True,
        )
    mapped = set(part_map.claims())
    unowned = [claim_id for claim_id in known_order if claim_id not in mapped]
    if unowned:
        return (
            ReadinessCheck(
                "part-map",
                "failed",
                "every claim of the revision must belong to one part; unmapped claim(s): "
                + ", ".join(unowned),
            ),
            part_map,
        )
    basis_problems = [
        problem
        for claim in packet.claims.claims
        if (problem := claim_basis_problem(packet, claim)) is not None
    ]
    if basis_problems:
        return (
            ReadinessCheck("part-map", "failed", "; ".join(basis_problems)),
            part_map,
        )
    titles = ", ".join(
        f"{entry.part_id} ({len(entry.claims)} claim(s))" for entry in part_map.parts
    )
    detail = (
        "one implicit part holds every claim; staleness is whole-page"
        if part_map.implicit
        else f"{len(part_map.parts)} part(s): {titles}"
    )
    return ReadinessCheck("part-map", "passed", detail), part_map


_BASIS_COLLECTIONS: dict[str, tuple[str, ...]] = {
    "tree-blob": ("source-inventory-and-linked-locations", "recorded-effects"),
    "knowledge-record": ("judgments", "recorded-effects", "family-guarantees-and-membership"),
    "judgment-row": ("judgments",),
    "proof-entry": ("proofs",),
    "history-event": ("judgments",),
    "worklist-row": ("worklist",),
}


def claim_basis_problem(packet: EvidencePacket, claim: EvidenceClaim) -> str | None:
    """Why a claim's bases fail to resolve in ``packet``, or ``None`` when they resolve."""

    if not claim.bases:
        return f"claim {claim.claim_id!r} resolves to no basis in the evidence packet"
    for basis in claim.bases:
        problem = _single_basis_problem(packet, claim.claim_id, basis)
        if problem is not None:
            return problem
    return None


def _single_basis_problem(packet: EvidencePacket, claim_id: str, basis: ClaimBasis) -> str | None:
    if (
        basis.comparison_number is not None
        and basis.comparison_number != packet.comparison.comparison_number
    ):
        return (
            f"claim {claim_id!r} basis {basis.reference!r} names comparison "
            f"{basis.comparison_number}, not {packet.comparison.comparison_number}"
        )
    side_problem = _side_tree_problem(packet, claim_id, basis)
    if side_problem is not None:
        return side_problem
    if basis.kind == "supplemental-receipt":
        return _receipt_basis_problem(packet, claim_id, basis)
    if basis.state == "present" and _collections_built(packet, basis.kind):
        resolution = resolve_claim(packet, claim_id)
        if not resolution.resolvable:
            return "; ".join(resolution.unresolved)
    return None


def _side_tree_problem(packet: EvidencePacket, claim_id: str, basis: ClaimBasis) -> str | None:
    if basis.side is None:
        return None
    comparison = packet.comparison
    sides: dict[str, tuple[str, set[str]]] = {
        "code-base": (comparison.code_base.state, {comparison.code_base.tree}),
        "code-candidate": (comparison.code_candidate.state, {comparison.code_candidate.tree}),
        "memory-base": (
            comparison.memory_base.state,
            {comparison.stored_memory_base_tree, comparison.compared_memory_base_tree},
        ),
        "memory-candidate": (
            comparison.memory_candidate.state,
            {comparison.memory_candidate.tree},
        ),
    }
    entry = sides.get(basis.side)
    if entry is None:
        return None
    state, valid_trees = entry
    if basis.state == "present" and state != "available":
        return (
            f"claim {claim_id!r} basis {basis.reference!r} is present on {basis.side}, which is "
            f"{state}"
        )
    if basis.tree is not None and basis.tree not in valid_trees:
        return (
            f"claim {claim_id!r} basis {basis.reference!r} names tree {basis.tree!r}, which does "
            f"not resolve in {basis.side}"
        )
    return None


def _receipt_basis_problem(packet: EvidencePacket, claim_id: str, basis: ClaimBasis) -> str | None:
    receipts = {receipt.receipt_id: receipt for receipt in packet.supplemental}
    held = receipts.get(basis.receipt_id or basis.reference)
    if held is None:
        return f"claim {claim_id!r} basis {basis.reference!r} names no receipt in the packet"
    if basis.state == "present" and (held.read_state != "present" or held.digest is None):
        return (
            f"claim {claim_id!r} basis {basis.reference!r} is present, but receipt "
            f"{held.receipt_id!r} is {held.read_state}"
        )
    return None


def _collections_built(packet: EvidencePacket, kind: str) -> bool:
    names = _BASIS_COLLECTIONS.get(kind, ())
    return any(
        (col := packet.collection(name)) is not None
        and col.state in {"present", "measured-empty", "truncated"}
        for name in names
    )


def _check_media_controls(assets: dict[str, bytes]) -> ReadinessCheck:
    tags: list[tuple[str, dict[str, str]]] = []
    for path, payload in sorted(assets.items()):
        if path.startswith(KIT_CONTRACT_PREFIX) or not path.lower().endswith(_HTML_MEDIA):
            continue
        tags.extend(_media_tags(payload.decode("utf-8", errors="ignore")))
    if any("autoplay" in attrs for _tag, attrs in tags):
        return ReadinessCheck(
            "media-controls", "failed", "the page autoplays media a reader cannot stop"
        )
    if any("loop" in attrs for _tag, attrs in tags):
        return ReadinessCheck(
            "media-controls", "failed", "the page loops media a reader cannot stop"
        )
    without_controls = [_media_label(tag, attrs) for tag, attrs in tags if "controls" not in attrs]
    if without_controls:
        return ReadinessCheck(
            "media-controls",
            "failed",
            "the page plays audio or video without controls a reader can use: "
            + without_controls[0],
        )
    return ReadinessCheck(
        "media-controls",
        "passed",
        "HTML media elements carry reader controls without autoplay or looping",
    )


def check_page(
    assets: dict[str, bytes], *, entrypoint: str, packet: EvidencePacket
) -> ReadinessReport:
    """Check one prospective revision without a model and without a browser."""

    if entrypoint not in assets:
        return ReadinessReport(
            checks=(
                ReadinessCheck("entrypoint", "failed", f"the revision holds no {entrypoint!r}"),
            )
        )
    document = assets[entrypoint].decode("utf-8", errors="ignore")
    parts_check, part_map = _check_parts(document, packet)
    checks = (
        _check_entry(assets, entrypoint),
        _check_assets(assets),
        _check_evidence(assets, packet),
        parts_check,
        _check_media_controls(assets),
    )
    notes: list[str] = []
    entry_text = assets[entrypoint].decode("utf-8", errors="ignore")
    if _SCRIPT.search(entry_text) or any(
        _SCRIPT.search(payload.decode("utf-8", errors="ignore")) for payload in assets.values()
    ):
        notes.append(
            "the page carries a script; this check is static, so the script is not executed here "
            "(generated scripts run in the sandboxed frame, MIK-R104 rule 8 under D331); the "
            "prospective load that executes it is not this gate"
        )
    return ReadinessReport(checks=checks, notes=tuple(notes), part_map=part_map)
