"""The model-free readiness check a revision passes before it becomes ready (MIK-R104 rule 14).

A revision is published as ready only after the product has *loaded* it once without a model. What
"loaded" means here is the part this host can establish itself, from the exact bytes it is about to
publish:

* the entrypoint exists, is text, and parses as one HTML document;
* every subresource the page names -- ``href``, ``src``, ``srcset``, ``poster`` and CSS ``url()`` /
  ``@import`` -- is an admitted asset of this same revision, and every asset the manifest carries is
  reachable by a path that cannot leave the revision's own prefix. A page that requests the network,
  another revision, a task report, a source checkout or a ``..`` path is not ready;
* every evidence reference the page cites is one the admitted packet carries. A reference the packet
  does not carry would open nothing, so it is a failed check rather than a limitation;
* the page is usable with reduced motion: no autoplaying media, and declared animation or transition
  styles are accompanied by a ``prefers-reduced-motion: reduce`` rule.

The check says nothing about how good the explanation is, and it does not replace the browser
isolation proof: rule 10 proves the boundary a page executes inside, rule 14 proves the page itself
is complete and self-contained. It is also not a rendering check -- it never starts a browser and
never starts a model.

Generated scripts run in the browser's sandboxed frame (D331), so a page that carries a ``<script>``
is *not* failed here. This check is static: it resolves references, refuses network requests, requires
a reduced-motion rule and checks the evidence binding, and it says so in its notes rather than
pretending to have executed the page.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from typing import Any, Literal

from agents_remember.application.review_evidence_claims import resolve_claim
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

MAX_REFERENCES = 400
MAX_PAGE_BYTES = 4 * 1024 * 1024

#: One expression for the three ways an asset reference appears in HTML or CSS.
_REFERENCE = re.compile(
    r"""(?:href|src|poster)\s*=\s*["']([^"']*)["']|url\(\s*["']?([^"')]*)["']?\s*\)""",
    re.IGNORECASE,
)
_SRCSET = re.compile(r"""srcset\s*=\s*["']([^"']*)["']""", re.IGNORECASE)
#: The HTML attribute grammar allows a double-quoted, single-quoted or unquoted value; the gate reads
#: all three, because a page that names an unadmitted reference must not become invisible by leaving
#: the quotes out (L104-A5). ``data-evidence-claim`` is not this attribute: a claim mark carries a
#: claim id and belongs to the part map (:mod:`agents_remember.canvas.parts`).
_EVIDENCE = re.compile(
    r"""data-evidence\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>"']+))""", re.IGNORECASE
)
_SCRIPT = re.compile(r"<script\b", re.IGNORECASE)
_AUTOPLAY = re.compile(r"<(?:audio|video)\b[^>]*\bautoplay\b", re.IGNORECASE)
_MOTION = re.compile(r"@keyframes|animation\s*:|transition\s*:", re.IGNORECASE)
_REDUCED_MOTION = re.compile(r"prefers-reduced-motion", re.IGNORECASE)
_ABSOLUTE = re.compile(r"^(?:[a-z][a-z0-9+.\-]*:|//)", re.IGNORECASE)
_IGNORED = ("#", "?", "data:")


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


def _references(text: str) -> list[str]:
    found: list[str] = []
    for match in _REFERENCE.finditer(text):
        value = match.group(1) if match.group(1) is not None else match.group(2)
        if value:
            found.append(value.strip())
    for match in _SRCSET.finditer(text):
        for entry in match.group(1).split(","):
            candidate = entry.strip().split(maxsplit=1)[0]
            if candidate:
                found.append(candidate)
    return found


def _evidence_references(text: str) -> list[str]:
    """Every evidence reference in one document, quoted or not."""

    found: list[str] = []
    for double, single, bare in _EVIDENCE.findall(text):
        value = (double or single or bare).strip()
        if value:
            found.append(value)
    return found


def _resolve(referrer: str, reference: str) -> str | None:
    """The asset path a reference names inside the revision, or ``None`` when it leaves it."""

    if reference.startswith(_IGNORED) or not reference:
        return None
    base = posixpath.dirname(referrer)
    bare = reference.split("#", maxsplit=1)[0].split("?", maxsplit=1)[0]
    joined = posixpath.normpath(posixpath.join(base, bare))
    if joined.startswith("../") or joined == ".." or joined.startswith("/"):
        return None
    return joined


def _check_entry(assets: dict[str, bytes], entrypoint: str) -> ReadinessCheck:
    payload = assets.get(entrypoint)
    if payload is None:
        return ReadinessCheck("entrypoint", "failed", f"the revision holds no {entrypoint!r}")
    if not payload.strip():
        return ReadinessCheck("entrypoint", "failed", f"{entrypoint!r} is empty")
    if len(payload) > MAX_PAGE_BYTES:
        return ReadinessCheck(
            "entrypoint", "failed", f"{entrypoint!r} is {len(payload)} bytes, beyond the page bound"
        )
    try:
        payload.decode("utf-8")
    except UnicodeDecodeError:
        return ReadinessCheck("entrypoint", "failed", f"{entrypoint!r} is not UTF-8 text")
    return ReadinessCheck("entrypoint", "passed", f"{entrypoint!r} is one admitted UTF-8 document")


#: Which documents name subresources at all. A JavaScript bundle is not one: it may hold
#: ``src="..."`` inside a selector or a template string, and reading that as a request would refuse a
#: legitimate library bundle the kit ships (L104-B13). HTML and SVG name references in attributes; CSS
#: names them in ``url()`` only.
_REFERENCE_MEDIA = (".html", ".htm", ".svg", ".css")


def _names_references(path: str) -> bool:
    return path.lower().endswith(_REFERENCE_MEDIA)


def _offending(assets: dict[str, bytes]) -> tuple[list[str], list[str]]:
    """Every reference that leaves the revision, and every one that names no admitted asset."""

    outside: list[str] = []
    missing: list[str] = []
    scanned = 0
    for path, payload in sorted(assets.items()):
        if not _names_references(path):
            continue
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            continue
        for reference in _references(text):
            scanned += 1
            if scanned > MAX_REFERENCES:
                missing.append(f"more than {MAX_REFERENCES} references in one revision")
                return outside, missing
            if _ABSOLUTE.match(reference):
                outside.append(f"{path} -> {reference}")
                continue
            target = _resolve(path, reference)
            if target is None:
                if not reference.startswith(_IGNORED):
                    outside.append(f"{path} -> {reference}")
            elif target not in assets:
                missing.append(f"{path} -> {reference}")
    return outside, missing


def _check_assets(assets: dict[str, bytes]) -> ReadinessCheck:
    outside, missing = _offending(assets)
    if outside:
        return ReadinessCheck(
            "admitted-assets",
            "failed",
            "the page requests something outside its own admitted assets: "
            + "; ".join(outside[:5]),
        )
    if missing:
        return ReadinessCheck(
            "admitted-assets",
            "failed",
            "the page names assets the revision does not carry: " + "; ".join(missing[:5]),
        )
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
    for payload in assets.values():
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
            + "; ".join(sorted(set(unknown))[:5]),
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
                "; ".join(part_map.problems[:5]),
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
                + ", ".join(sorted(set(unknown))[:5])
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
                + ", ".join(unowned[:5]),
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
            ReadinessCheck("part-map", "failed", "; ".join(basis_problems[:5])),
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
            return "; ".join(resolution.unresolved[:2])
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


def _check_motion(assets: dict[str, bytes]) -> ReadinessCheck:
    text = "\n".join(payload.decode("utf-8", errors="ignore") for payload in assets.values())
    if _AUTOPLAY.search(text):
        return ReadinessCheck(
            "reduced-motion", "failed", "the page autoplays media a reader cannot stop"
        )
    if _MOTION.search(text) and not _REDUCED_MOTION.search(text):
        return ReadinessCheck(
            "reduced-motion",
            "failed",
            "the page animates without a prefers-reduced-motion: reduce rule",
        )
    return ReadinessCheck(
        "reduced-motion", "passed", "declared motion is paired with a reduced-motion rule"
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
        _check_motion(assets),
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
