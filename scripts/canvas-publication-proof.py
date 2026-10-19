#!/usr/bin/env python3
"""Real Chromium publication refusals; the authoring runner is explicitly a model-free fixture.

Unlike unit tests, this opt-in witness needs the qualified local Chromium dependency. It creates
only a disposable task and never contacts Paseo. Every publication uses the product Presenter,
static readiness, prospective browser load and immutable frame writer.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from agents_remember.application.review_frame_store import reopen_frame
from agents_remember.canvas.presenter import Presenter, PresenterRefused, RunnerReceipt
from agents_remember.canvas.prospective import BrowserProspectiveLoad
from agents_remember.controlplane.durable_store import declare_process_role
from canvas_proof_app import FRAME, LEAF, REPO, TASK, _evidence, _record, build

GOOD = '<!doctype html><p data-evidence="code-candidate:src/probe.py">Actual fixture basis</p>'
CASES = {
    "quoted-unknown": '<p data-evidence="unknown:object">unknown</p>',
    "unquoted-unknown": "<p data-evidence=unknown:object>unknown</p>",
    "dynamic-unknown": '<p id="claim">unknown</p><script>document.querySelector("#claim").dataset.evidence="unknown:object"</script>',
    "reduced-motion-invisible": "<style>@media(prefers-reduced-motion:reduce){body{opacity:0}}</style><p>Invisible under reduce</p>",
}


class FixtureRunner:
    """No model, no host and no confinement claim: merely lets the proof prepare exact assets."""

    def start(self, attempt, instructions, working):
        del (
            instructions,
            working,
        )  # Explicitly no authoring or assignment interpretation in this fixture.
        return RunnerReceipt(
            execution_id=f"fixture-{attempt.attempt_id}",
            session_id=f"fixture-{attempt.attempt_id}",
            model="explicit-model-free-fixture",
        )


def run(root: Path) -> list[dict]:
    os.environ["XDG_CONFIG_HOME"] = str(root / "config")
    _app, hosts, _manifest = build(root, "http://127.0.0.1:1")
    presenter = Presenter(
        hosts.config,
        repository_id=REPO,
        task=TASK,
        leaf_id=LEAF,
        frame_id=FRAME,
        runner=FixtureRunner(),
        prospective=BrowserProspectiveLoad(),
    )
    packet = _evidence(_record())
    records = []
    seed = presenter.start(
        caller="opt-in browser proof",
        request_id="seed",
        activation="explicit",
        evidence=packet,
        focus="model-free publication fixture",
    )
    (Path(seed.working_directory) / "assets").mkdir(exist_ok=True)
    (Path(seed.working_directory) / "assets/index.html").write_text(GOOD)
    publication = presenter.publish(seed.attempt_id, entrypoint="index.html")
    assert publication.published and publication.manifest
    ready_revision = publication.manifest.revision
    records.append(
        {"case": "admitted-reference-control", "published": True, "revision": ready_revision}
    )
    for name, markup in CASES.items():
        attempt = presenter.start(
            caller="opt-in browser proof",
            request_id=name,
            activation="explicit",
            evidence=packet,
            focus="model-free publication refusal fixture",
        )
        assets = Path(attempt.working_directory) / "assets"
        assets.mkdir(exist_ok=True)
        (assets / "index.html").write_text("<!doctype html>" + markup)
        try:
            presenter.publish(attempt.attempt_id, entrypoint="index.html")
        except PresenterRefused as refusal:
            opening = reopen_frame(presenter.scope)
            assert opening.revision == ready_revision
            records.append(
                {
                    "case": name,
                    "refused": refusal.reason,
                    "detail": str(refusal),
                    "readyBefore": ready_revision,
                    "readyAfter": opening.revision,
                }
            )
        else:
            raise AssertionError(f"{name} was published")
        presenter.cancel(attempt.attempt_id, reason="the proof has observed this refusal")
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    declare_process_role("mcp")
    with tempfile.TemporaryDirectory(prefix="ar-publication-proof-") as temporary:
        records = run(Path(temporary))
    result = {
        "state": "passed",
        "browser": "real Chromium",
        "modelStarts": 0,
        "authoring": "explicit model-free fixture; no presenter qualification claimed",
        "cases": records,
    }
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
