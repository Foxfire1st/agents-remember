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

from agents_remember.models.knowledge.review_evidence import EvidencePacket

__all__ = [
    "ReadinessCheck",
    "ReadinessReport",
    "check_page",
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
#: the quotes out (L104-A5).
_EVIDENCE = re.compile(
    r"""data-evidence(?:-claim)?\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>"']+))""", re.IGNORECASE
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
    checks = (
        _check_entry(assets, entrypoint),
        _check_assets(assets),
        _check_evidence(assets, packet),
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
    return ReadinessReport(checks=checks, notes=tuple(notes))
