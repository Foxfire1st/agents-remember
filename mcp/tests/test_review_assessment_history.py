"""Public comparison recording retains owner-authored assessments through cleanup and restart."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from agents_remember.application import review_curator_records as curator
from agents_remember.models.knowledge.base import PROSE_MAX_LENGTH

pytestmark = pytest.mark.evidence_unit


def _historical(endpoint, selector=None):
    request = endpoint.request().model_copy(update={"history": "recorded"})
    return request if selector is None else request.model_copy(update={"selector": selector})


def _curator_channel(monkeypatch, paths: tuple[str, ...]):
    owner = next(iter(curator.RESERVED_CURATOR_OWNERS))
    evidence = [SimpleNamespace(owner=owner, state="missing", relative_path=path) for path in paths]
    resolved = SimpleNamespace(
        contract=object(),
        closed_leaf=SimpleNamespace(reopened=SimpleNamespace(evidence=evidence)),
    )

    def missing(_resolved: object) -> None:
        raise ValueError(f"Bound curator artifacts are unavailable: {', '.join(paths)}")

    monkeypatch.setattr(curator, "_historical_records", missing)
    return curator.review_curator_records(resolved).channel  # type: ignore[arg-type]


def test_a_leaf_binding_many_missing_curator_artifacts_reads_as_unavailable(monkeypatch) -> None:
    """MIK-L25 Q8 (fixed in MIK-L31): ICR-L47 binds 94 curator artifacts; with them missing, the
    owner's error names every path and the review failed on the detail's length (a ValidationError)
    instead of reporting the channel ``unavailable``. A detail over the prose bound now names the
    count and the first few; one that fits keeps its landed wording exactly (review F6).
    ``unreadable`` lists every artifact either way."""

    paths = tuple(
        f"notes/reports/curator-coherence/L47/judgment-evidence/{n:064x}" for n in range(100)
    )
    channel = _curator_channel(monkeypatch, paths)
    assert channel.state == "unavailable" and channel.unreadable == paths
    assert channel.detail.startswith("The curator owner could not be read (100 bound artifacts")
    assert paths[0] in channel.detail and paths[-1] not in channel.detail.split("):")[0]

    # Ten artifacts: over the three the summary names, so the two branches write different text,
    # yet the landed detail still fits the prose bound and is kept exactly (review R2-2).
    few = paths[:10]
    fits = _curator_channel(monkeypatch, few)
    error = f"Bound curator artifacts are unavailable: {', '.join(few)}"
    landed = f"The curator owner could not be read ({', '.join(few)}): {error}"
    assert len(landed) <= PROSE_MAX_LENGTH
    assert fits.detail == landed and "bound artifacts, listed as unreadable" not in fits.detail
    assert fits.unreadable == few
