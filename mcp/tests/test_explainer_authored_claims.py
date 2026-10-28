"""F12: authored claims are admitted from confined assets and reach the revision."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agents_remember.application.review_frame_store import frame_scope, reopen_frame
from agents_remember.canvas.explainer_errors import ExplainerRefused
from agents_remember.canvas.explainer_paths import EXPLAINER_FRAME
from agents_remember.canvas.explainer_server import render_and_publish
from agents_remember.canvas.explainer_state import ExplainerBinding, write_binding
from agents_remember.canvas.presenter_store import PresenterRefused
from agents_remember.canvas.prospective import BrowserProspectiveLoad, ProspectiveResult
from canvas_host_test_support import LEAF
from explainer_server_test_support import _world


def _setup(tmp_path, monkeypatch):
    world = _world(tmp_path)
    source = world.workspace / "instructions/evidence.json"
    packet = json.loads(source.read_bytes())
    packet["claims"] = {"claims": []}
    source.write_text(json.dumps(packet), encoding="utf-8")
    before = source.read_bytes()
    assets = world.workspace / "drafts/assets"
    assets.mkdir(exist_ok=True)
    observed = []

    def load(_self, **kwargs):
        observed.append(kwargs)
        return ProspectiveResult(state="loaded", kind="browser")

    monkeypatch.setattr(BrowserProspectiveLoad, "load", load)
    claim = {
        "claim_id": "binding-claim",
        "authoring_revision": "authored-draft-1",
        "classification": "mechanical-signal",
        "subject": "Exact comparison",
        "applicability": "This draft only",
        "statement": "This page names its admitted comparison.",
        "bases": [
            {
                "kind": "comparison-field",
                "reference": packet["comparison"]["comparison_reference"],
                "state": "present",
                "comparison_number": packet["comparison"]["comparison_number"],
            }
        ],
    }
    return world, source, before, assets, observed, claim


def _page(assets: Path, *, claims=True, evidence=None):
    mark = ' data-evidence-claim="binding-claim"' if claims else ""
    reference = f' data-evidence="{evidence}"' if evidence else ""
    (assets / "index.html").write_text(
        '<!doctype html><html><body><section data-part="summary">'
        f"<p{mark}{reference}>This is the comparison.</p>"
        "</section></body></html>",
        encoding="utf-8",
    )


def _claims(assets: Path, claim):
    (assets / "claim-map.json").write_text(json.dumps({"claims": [claim]}), encoding="utf-8")


def test_authored_claims_reach_browser_readiness_and_published_manifest(tmp_path, monkeypatch):
    world, source, before, assets, observed, claim = _setup(tmp_path, monkeypatch)
    # Past the old 1 MiB claim-map and manifest bounds: the map is admitted, the manifest that
    # carries it is written, and the revision reopens from it (MIK-R211 v4 rule 3).
    claim["statement"] += " " + "x" * (1024 * 1024)
    _claims(assets, claim)
    _page(assets, evidence=claim["bases"][0]["reference"])
    # And past the old draft depth (8) and entry (1,024) refusals: a draft is read whole however it
    # is laid out (the Manager's 14:29Z ruling on presenter_assets).
    deep = assets.joinpath(*(f"d{level}" for level in range(10)))
    deep.mkdir(parents=True)
    (deep / "figure.svg").write_text("<svg/>", encoding="utf-8")
    for number in range(1030):  # entries the old walk counted; empty folders publish no file
        (assets / "many" / f"{number:04d}").mkdir(parents=True)
    captured = {
        p.relative_to(assets).as_posix(): p.read_bytes() for p in assets.rglob("*") if p.is_file()
    }

    report = render_and_publish(world.workspace, task_root=world.task_root, seat=world.seat)

    assert report["readiness"]["ready"] is True
    assert report["publication"]["state"] == "published"
    assert len(observed) == 1
    assert observed[0]["packet"].claims.claims[0].claim_id == "binding-claim"
    assert observed[0]["assets"] == captured
    opened = reopen_frame(frame_scope(world.task_root, LEAF, EXPLAINER_FRAME))
    assert opened.manifest is not None
    retained = opened.manifest.evidence.claims.claims
    assert len(retained) == 1
    assert retained[0].model_dump(mode="json", exclude_none=True) == claim
    assert opened.manifest.part_map[0].claims == ("binding-claim",)
    assert (assets / "claim-map.json").stat().st_size > 1024 * 1024
    assert source.read_bytes() == before


def test_no_authored_map_preserves_an_honestly_empty_claim_map(tmp_path, monkeypatch):
    world, source, before, assets, observed, _claim = _setup(tmp_path, monkeypatch)
    _page(assets, claims=False)

    report = render_and_publish(world.workspace, task_root=world.task_root, seat=world.seat)

    assert report["publication"]["state"] == "published"
    assert observed[0]["packet"].claims.claims == ()
    opened = reopen_frame(frame_scope(world.task_root, LEAF, EXPLAINER_FRAME))
    assert opened.manifest is not None and opened.manifest.evidence.claims.claims == ()
    assert source.read_bytes() == before


@pytest.mark.parametrize("failure", ["schema", "symlink"])
def test_invalid_claim_asset_is_refused_before_browser_load(tmp_path, monkeypatch, failure):
    world, source, before, assets, observed, _claim = _setup(tmp_path, monkeypatch)
    _page(assets)
    target = assets / "claim-map.json"
    if failure == "schema":
        target.write_text('{"claims":[{"claim_id":"incomplete"}]}')
        expected = ExplainerRefused
    else:
        outside = tmp_path / "outside-claims.json"
        outside.write_text('{"claims":[]}')
        target.symlink_to(outside)
        expected = PresenterRefused

    with pytest.raises(expected):
        render_and_publish(world.workspace, task_root=world.task_root, seat=world.seat)

    assert observed == []
    assert source.read_bytes() == before
    opened = reopen_frame(frame_scope(world.task_root, LEAF, EXPLAINER_FRAME))
    assert opened.manifest is None


@pytest.mark.parametrize("failure", ["unknown-reference", "wrong-tree"])
def test_authored_map_does_not_admit_unresolved_evidence(tmp_path, monkeypatch, failure):
    world, source, before, assets, _observed, claim = _setup(tmp_path, monkeypatch)
    if failure == "wrong-tree":
        claim["bases"] = [
            {
                "kind": "tree-blob",
                "reference": "wrong-tree",
                "state": "present",
                "side": "code-candidate",
                "tree": "f" * 40,
                "path": "src/app.py",
            }
        ]
    _claims(assets, claim)
    _page(assets, evidence="unknown-reference" if failure == "unknown-reference" else None)

    report = render_and_publish(world.workspace, task_root=world.task_root, seat=world.seat)

    assert report["readiness"]["ready"] is False
    assert "publication" not in report
    assert source.read_bytes() == before
    opened = reopen_frame(frame_scope(world.task_root, LEAF, EXPLAINER_FRAME))
    assert opened.manifest is None


def test_authored_claims_cannot_publish_after_binding_moves(tmp_path, monkeypatch):
    world, source, before, assets, _observed, claim = _setup(tmp_path, monkeypatch)
    _claims(assets, claim)
    _page(assets)
    current = json.loads((world.workspace / "binding.json").read_bytes())
    moved = ExplainerBinding.model_validate(
        {
            **current,
            "binding": {**current["binding"], "comparison_number": 9},
        }
    )
    write_binding(world.workspace, moved)

    report = render_and_publish(world.workspace, task_root=world.task_root, seat=world.seat)

    assert report["publication"]["refusal"] == "binding-moved"
    assert source.read_bytes() == before
    opened = reopen_frame(frame_scope(world.task_root, LEAF, EXPLAINER_FRAME))
    assert opened.manifest is None
