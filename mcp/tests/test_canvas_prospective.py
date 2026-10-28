"""L104-B14: ready publication is gated by a model-free load of the prospective revision.

The static readiness check reads the bytes; it does not load them. These cases hold the gate that
sits after it: the presenter runs the exact prospective assets through :class:`ProspectiveLoad`,
publishes only on ``loaded``, and refuses with the load's own diagnostics otherwise, while the last
ready revision stays current.

One case per behaviour:

1. a ``failed`` load refuses publication, names the loader's diagnostics, and leaves the previous
   ready revision in place;
2. a ``loaded`` load publishes the exact revision the loader was shown;
3. ``unavailable`` refuses publication and says nothing was observed -- the honest default when no
   browser is present;
4. :class:`StaticProspectiveLoad` answers ``unavailable`` for a page the static check accepts and
   ``failed`` for one it refuses, so a byte scan can never stand in for a load;
5. :class:`BrowserProspectiveLoad` answers ``unavailable`` when its runner is missing, and reads a
   child's JSON without trusting a ``loaded`` answer that names its own faults.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from agents_remember.application.review_frame_store import frame_scope, reopen_frame
from agents_remember.canvas import presenter_tools, prospective
from agents_remember.canvas.explainer_answer_bounds import answer_tokens
from agents_remember.canvas.presenter import Presenter, PresenterRefused
from agents_remember.canvas.prospective import (
    BrowserProspectiveLoad,
    ProspectiveResult,
    StaticProspectiveLoad,
    default_load_script,
)
from agents_remember.mcp.registration.explainer_tools import render_answer
from canvas_host_test_support import (
    AT,
    FRAME,
    LEAF,
    REPO,
    TASK,
    FakeRunner,
    config,
    packet,
    record,
    stored_attempt,
    task,
    write_settings,
)

LAUNCH = {"harness": "hermes", "model": "the-presenter-model", "effort": "high", "mode": "auto"}
PAGE = b"<!doctype html><p data-evidence='code-candidate:src/app.py'>the explained change</p>"
CLEAN = {
    "state": "loaded",
    "errors": [],
    "foreignRequests": [],
    "reducedMotionOk": True,
    "unresolvedReferences": [],
    "evidenceReferences": [],
    "detail": "",
}


@dataclass
class StubLoad:
    """A prospective load whose answer the test decides, and which records what it was given."""

    result: ProspectiveResult
    calls: list[dict] = field(default_factory=list)

    def load(self, *, assets, entrypoint, packet):
        self.calls.append({"assets": dict(assets), "entrypoint": entrypoint, "packet": packet})
        return self.result


def _presenter(settings, *, prospective) -> Presenter:
    return Presenter(
        settings,
        repository_id=REPO,
        task=TASK,
        leaf_id=LEAF,
        frame_id=FRAME,
        runner=FakeRunner(),
        prospective=prospective,
        clock=lambda: AT,
    )


def _start(presenter: Presenter, *, request: str):
    attempt = stored_attempt(presenter, evidence=packet(record()), request=request)
    assets = Path(attempt.working_directory) / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "index.html").write_bytes(PAGE)
    return attempt


def _arising(tmp_path: Path):
    """A page with one live attempt holding a static-check-clean revision, and its frame scope."""

    settings = config(tmp_path)
    root = task(settings)
    write_settings(settings, LAUNCH)
    return settings, frame_scope(root, LEAF, FRAME)


def test_a_failed_load_refuses_publication_and_keeps_the_last_ready_revision(
    tmp_path: Path,
) -> None:
    """Rule 14: the refusal carries what the load observed, and revision 1 stays ready."""

    settings, scope = _arising(tmp_path)
    loader = StubLoad(ProspectiveResult(state="loaded", kind="browser", detail="no fault observed"))
    presenter = _presenter(settings, prospective=loader)
    first = _start(presenter, request="req-loads")
    assert presenter.publish(first.attempt_id, entrypoint="index.html").published is True
    assert reopen_frame(scope).revision == 1

    loader.result = ProspectiveResult(
        state="failed",
        kind="browser",
        errors=("console: the chart script threw", "pageerror: explain is not defined"),
        foreign_requests=("https://cdn.example.com/chart.js",),
        reduced_motion_ok=True,
        unresolved_references=("code-candidate:src/gone.py",),
        detail="the page requested a foreign script and cited an unknown reference",
    )
    second = _start(presenter, request="req-broken")
    with pytest.raises(PresenterRefused) as refused:
        presenter.publish(second.attempt_id, entrypoint="index.html")
    assert refused.value.reason == "prospective-load-failed"
    detail = refused.value.detail
    assert "explain is not defined" in detail
    assert "https://cdn.example.com/chart.js" in detail
    assert "code-candidate:src/gone.py" in detail
    assert "the page requested a foreign script" in detail
    # The ready pointer never moved, and the refused attempt is still the presenter's live one.
    assert reopen_frame(scope).revision == 1
    kept = presenter.read(second.attempt_id)
    assert kept is not None and kept.published_revision is None


def test_a_loaded_revision_publishes_the_exact_assets_the_loader_saw(tmp_path: Path) -> None:
    """Rule 14: publication follows a load of the same bytes, not of some earlier revision."""

    settings, scope = _arising(tmp_path)
    loader = StubLoad(ProspectiveResult(state="loaded", kind="browser", detail="no fault observed"))
    presenter = _presenter(settings, prospective=loader)
    attempt = _start(presenter, request="req-loaded")

    publication = presenter.publish(attempt.attempt_id, entrypoint="index.html")

    assert publication.published is True
    assert publication.manifest is not None and publication.manifest.revision == 1
    assert len(loader.calls) == 1
    seen = loader.calls[0]
    assert seen["entrypoint"] == "index.html"
    assert seen["assets"] == {"index.html": PAGE}
    assert seen["packet"] is not None
    assert reopen_frame(scope).revision == 1


def test_an_unavailable_load_refuses_publication_and_names_why(tmp_path: Path) -> None:
    """Rule 14: no browser ran, so nothing is claimed -- and the refusal says exactly that."""

    settings, scope = _arising(tmp_path)
    loader = StubLoad(
        ProspectiveResult(
            state="unavailable",
            kind="browser",
            errors=("no node executable at 'node', so the browser load could not be started",),
            detail="the load did not run, so nothing about the page was observed",
        )
    )
    presenter = _presenter(settings, prospective=loader)
    attempt = _start(presenter, request="req-unavailable")

    with pytest.raises(PresenterRefused) as refused:
        presenter.publish(attempt.attempt_id, entrypoint="index.html")

    assert refused.value.reason == "prospective-load-unavailable"
    assert "no node executable" in refused.value.detail
    assert "nothing about the page was observed" in refused.value.detail
    assert reopen_frame(scope).revision is None


def test_the_static_check_never_stands_in_for_a_load(tmp_path: Path) -> None:
    """The static port answers ``unavailable`` on a good page and ``failed`` on a bad one."""

    evidence = packet(record())
    loader = StaticProspectiveLoad()
    accepted = loader.load(assets={"index.html": PAGE}, entrypoint="index.html", packet=evidence)
    assert (accepted.state, accepted.kind) == ("unavailable", "static")
    assert "has not loaded the page" in accepted.detail
    refused = loader.load(
        assets={"index.html": b"<!doctype html><img src='https://example.com/x.png'>"},
        entrypoint="index.html",
        packet=evidence,
    )
    assert (refused.state, refused.kind) == ("failed", "static")
    assert any("admitted-assets" in error for error in refused.errors)
    assert any("example.com/x.png" in error for error in refused.errors)


def test_a_missing_browser_runner_is_unavailable_not_a_pass(tmp_path: Path) -> None:
    """Rule 14: no script, no load, no pass -- and the answer names the path it looked for."""

    missing = tmp_path / "no-such-runner.mjs"
    loader = BrowserProspectiveLoad(script=missing)
    result = loader.load(
        assets={"index.html": PAGE}, entrypoint="index.html", packet=packet(record())
    )
    assert result.state == "unavailable"
    assert result.kind == "browser"
    assert any(str(missing) in error for error in result.errors)
    assert "no browser ran" in result.detail


def _fake_node(tmp_path: Path, body: str, *, exit_code: int = 0) -> str:
    """A stand-in for ``node`` that runs ``body``, so the subprocess plumbing is exercised."""

    runner = tmp_path / "fake-node"
    runner.write_text(
        f"#!{sys.executable}\nimport sys\n{body}\nsys.exit({exit_code})\n", encoding="utf-8"
    )
    runner.chmod(0o755)
    return str(runner)


def _child(tmp_path: Path, body: str) -> BrowserProspectiveLoad:
    """A loader whose child is the stand-in node, over a stand-in runner script."""

    script = tmp_path / "runner.mjs"
    script.write_text("// stand-in: the fake node answers instead of this file\n", encoding="utf-8")
    return BrowserProspectiveLoad(script=script, node=_fake_node(tmp_path, body))


def _load_with(tmp_path: Path, body: str) -> ProspectiveResult:
    return _child(tmp_path, body).load(
        assets={"index.html": PAGE}, entrypoint="index.html", packet=packet(record())
    )


def test_a_child_answer_is_read_as_reported(tmp_path: Path) -> None:
    """A clean ``loaded`` answer is loaded, and its observations survive the round trip."""

    answer = {**CLEAN, "detail": 'no fault observed; page title "comparison"'}
    result = _load_with(tmp_path, f"import json\nprint(json.dumps({answer!r}))\n")
    assert (result.state, result.kind) == ("loaded", "browser")
    assert result.detail.startswith("no fault observed")


def test_a_self_contradicting_load_is_downgraded_to_failed(tmp_path: Path) -> None:
    """A child that calls itself loaded while naming an error is not a pass."""

    answer = {
        "state": "loaded",
        "errors": ["console: the chart threw"],
        "foreignRequests": ["https://cdn.example.com/chart.js"],
        "reducedMotionOk": False,
        "unresolvedReferences": ["code-candidate:src/gone.py"],
        "evidenceReferences": [],
        "detail": "ignored",
    }
    result = _load_with(tmp_path, f"import json\nprint(json.dumps({answer!r}))\n")
    assert result.state == "failed"
    assert result.detail.startswith("the load called itself loaded while naming")
    assert "console: the chart threw" in result.errors
    assert result.foreign_requests == ("https://cdn.example.com/chart.js",)
    assert result.reduced_motion_ok is False


@pytest.mark.skipif(os.name == "nt", reason="the fake node relies on a POSIX shebang")
def test_an_unreadable_child_answer_is_unavailable_and_carries_stderr(tmp_path: Path) -> None:
    """No JSON means no evidence: the result keeps the child's own stderr and stdout, whole.

    MIK-R211 v5 rule 11: the render report holds them whole; the reply's budget, not a cut of the
    record, decides what the agent sees inline.
    """

    result = _load_with(
        tmp_path,
        "sys.stderr.write('Error: browser closed unexpectedly\\n' + 'x' * 1000 + '\\nlast line\\n')\n"
        "print('not json ' + 'y' * 500 + ' end-of-stdout')\n",
    )
    assert result.state == "unavailable"
    assert "no readable result" in result.errors[0]
    assert "browser closed unexpectedly" in result.errors[0]
    assert "last line" in result.errors[0] and "end-of-stdout" in result.errors[0]


@pytest.mark.skipif(os.name == "nt", reason="the fake node relies on a POSIX shebang")
def test_a_missing_node_executable_is_unavailable(tmp_path: Path) -> None:
    """An absent node is a load that never ran, named rather than assumed."""

    script = tmp_path / "runner.mjs"
    script.write_text("// never executed\n", encoding="utf-8")
    loader = BrowserProspectiveLoad(script=script, node=str(tmp_path / "no-node-here"))
    result = loader.load(
        assets={"index.html": PAGE}, entrypoint="index.html", packet=packet(record())
    )
    assert result.state == "unavailable"
    assert "no node executable" in result.errors[0]


def test_the_shipped_runner_is_a_package_resource(monkeypatch) -> None:
    """Source and installed interpreters resolve the same package-owned runner."""

    monkeypatch.delenv("AR_CANVAS_PROSPECTIVE_LOAD", raising=False)

    script = default_load_script()
    assert script.name == "canvas-prospective-load.mjs"
    assert script.parent == Path(prospective.__file__).parent
    assert script.is_file(), f"the shipped runner is missing at {script}"


@pytest.mark.skipif(os.name == "nt", reason="the fake node relies on a POSIX shebang")
def test_the_child_receives_the_exact_staged_assets_and_cleans_up(tmp_path: Path) -> None:
    """The child sees the exact prospective bytes at the path it was given, and that tree is gone."""

    capture = tmp_path / "seen.json"
    body = (
        "import json\n"
        "args = sys.argv[1:]\n"
        "root = args[args.index('--dir') + 1]\n"
        "staged = {}\n"
        "for path in sorted(__import__('pathlib').Path(root).rglob('*')):\n"
        "    if path.is_file():\n"
        "        staged[path.relative_to(root).as_posix()] = path.read_bytes().decode()\n"
        f"json.dump({{'argv': args, 'staged': staged}}, open({str(capture)!r}, 'w'))\n"
        f"print(json.dumps({CLEAN!r}))\n"
    )
    assets = {"index.html": PAGE.decode(), "img/mark.svg": "<svg/>"}
    result = _child(tmp_path, body).load(
        assets={key: value.encode() for key, value in assets.items()},
        entrypoint="index.html",
        packet=packet(record()),
    )

    assert result.state == "loaded"
    seen = json.loads(capture.read_text(encoding="utf-8"))
    assert seen["staged"] == assets
    assert seen["argv"][seen["argv"].index("--entry") + 1] == "index.html"
    staged = Path(seen["argv"][seen["argv"].index("--dir") + 1])
    assert not staged.exists()


def test_a_page_invisible_under_reduce_is_not_a_pass(tmp_path: Path) -> None:
    """L104-A5: reduced motion asks for a usable page, not for the absence of media playback.

    The loader reports `reducedMotionOk: false` when nothing of the document is visible under the
    preference; a child that still calls itself loaded is downgraded, so the ready pointer cannot move
    over a page a person cannot see.
    """

    answer = {
        "state": "loaded",
        "errors": [],
        "foreignRequests": [],
        "reducedMotionOk": False,
        "unresolvedReferences": [],
        "evidenceReferences": [],
        "detail": "0 running animation(s) under reduce, INVISIBLE under reduce",
    }
    result = _load_with(tmp_path, f"import json\nprint(json.dumps({answer!r}))\n")
    assert result.state == "failed"
    assert "reduced motion" in result.detail or "usable" in result.detail


def test_rendered_reference_inventory_is_checked_against_the_actual_packet(tmp_path):
    answer = {**CLEAN, "evidenceReferences": ["not-in-packet"]}
    result = _load_with(tmp_path, f"import json\nprint(json.dumps({answer!r}))\n")
    assert result.state == "failed"
    assert result.unresolved_references == ("not-in-packet",)

    # Real claim IDs in claimIds pass when they belong to the packet and their bases resolve,
    # while keeping data-evidence reference validation separate.
    good_claims = {
        **CLEAN,
        "evidenceReferences": ["code-candidate:src/app.py"],
        "claimIds": ["c-present", "c-gone"],
    }
    loaded = _load_with(tmp_path, f"import json\nprint(json.dumps({good_claims!r}))\n")
    assert loaded.state == "loaded"
    assert loaded.claim_ids == ("c-present", "c-gone")

    # A claim ID placed in evidenceReferences (data-evidence) fails reference resolution.
    claim_as_ref = {**CLEAN, "evidenceReferences": ["c-present"]}
    failed_ref = _load_with(tmp_path, f"import json\nprint(json.dumps({claim_as_ref!r}))\n")
    assert failed_ref.state == "failed"
    assert failed_ref.unresolved_references == ("c-present",)

    # A basis reference placed in claimIds (data-evidence-claim) fails claim resolution.
    ref_as_claim = {**CLEAN, "claimIds": ["code-candidate:src/app.py"]}
    failed_claim = _load_with(tmp_path, f"import json\nprint(json.dumps({ref_as_claim!r}))\n")
    assert failed_claim.state == "failed"
    assert failed_claim.unresolved_references == ("code-candidate:src/app.py",)

    # MIK-R211 v5 rule 2: every rendered reference is read and checked, however many and however
    # long; the inventory is never cut to a count (the browser side once kept 401, this side 400).
    many = {
        **CLEAN,
        "evidenceReferences": ["code-candidate:src/app.py"] * 450,
        "claimIds": ["c-present"] * 450,
    }
    counted = _load_with(tmp_path, f"import json\nprint(json.dumps({many!r}))\n")
    assert counted.state == "loaded"
    assert (len(counted.evidence_references), len(counted.claim_ids)) == (450, 450)
    long_reference = "code-candidate:" + "a" * 600
    long_answer = {**CLEAN, "evidenceReferences": [long_reference]}
    long_result = _load_with(tmp_path, f"import json\nprint(json.dumps({long_answer!r}))\n")
    assert long_result.state == "failed"  # read and checked against the packet, not unreadable
    assert long_result.unresolved_references == (long_reference,)

    script_text = default_load_script().read_text(encoding="utf-8")
    assert "claimIds" in script_text
    assert 'querySelectorAll("[data-evidence]")' in script_text
    assert 'querySelectorAll("[data-evidence-claim]")' in script_text
    assert "401" not in script_text  # the browser keeps every reference it sees


def test_interactive_observations_never_change_the_reduced_motion_gate(tmp_path: Path) -> None:
    """D565/D518: full-motion errors remain report-only, including runner failures."""
    script = tmp_path / "runner.mjs"
    script.write_text("// the fake node chooses the observation by invocation mode\n")
    interactive = {
        "reportOnly": True,
        "state": "observed",
        "passes": [{"width": 390, "errors": ["pageerror: full-motion script failed"]}],
    }
    seen = tmp_path / "interactive-argv.json"
    node = _fake_node(
        tmp_path,
        "import json\n"
        f"if '--interactive-dir' in sys.argv: json.dump(sys.argv, open({str(seen)!r}, 'w'))\n"
        f"print(json.dumps({interactive!r} if '--interactive-dir' in sys.argv else {CLEAN!r}))",
    )
    loader = BrowserProspectiveLoad(
        script=script, node=node, interactive_directory=tmp_path / "pictures"
    )
    result = loader.load(
        assets={"index.html": PAGE}, entrypoint="index.html", packet=packet(record())
    )
    assert result.loaded
    assert result.errors == ()
    assert result.interactive == interactive
    # The observation has no clock of its own: it is told the load's own limit and stops only
    # before it (the Manager's 15:43Z ruling; five passes measured at 74-120 s on L183 and L191).
    argv = json.loads(seen.read_text())
    assert argv[argv.index("--time-limit") + 1] == "180", argv
    # Publication consumes the same result; diagnostic failures are not refusal inputs.
    settings, scope = _arising(tmp_path / "publication")
    presenter = _presenter(settings, prospective=StubLoad(result))
    attempt = _start(presenter, request="interactive-is-not-a-gate")
    assert presenter.publish(attempt.attempt_id, entrypoint="index.html").published
    assert reopen_frame(scope).revision == 1

    node = _fake_node(
        tmp_path,
        f"import json\nprint('bad report' if '--interactive-dir' in sys.argv else json.dumps({CLEAN!r}))",
    )
    result = BrowserProspectiveLoad(
        script=script, node=node, interactive_directory=tmp_path / "pictures"
    ).load(assets={"index.html": PAGE}, entrypoint="index.html", packet=packet(record()))
    assert result.loaded
    assert result.interactive is not None
    assert result.interactive["state"] == "unavailable"
    assert "unvisited" in str(result.interactive["coverage"])


def test_interactive_runner_failure_does_not_erase_successful_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even a diagnostic timeout is kept outside the publication result."""
    real_run = subprocess.run

    def run(command, **kwargs):
        if "--interactive-dir" in command:
            raise subprocess.TimeoutExpired(command, 1)
        return real_run(command, **kwargs)

    monkeypatch.setattr(prospective.subprocess, "run", run)
    loader = _child(tmp_path, f"import json\nprint(json.dumps({CLEAN!r}))")
    loader._interactive_directory = tmp_path / "pictures"
    result = loader.load(
        assets={"index.html": PAGE}, entrypoint="index.html", packet=packet(record())
    )
    assert result.loaded
    assert result.interactive is not None
    assert "did not finish" in str(result.interactive["errors"])


@pytest.mark.parametrize("picture_size", [16, 2 * 1024 * 1024 + 1])
def test_each_render_keeps_its_pictures_whole_in_its_own_run_folder_and_resumes_what_it_left(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, picture_size: int
) -> None:
    """MIK-R211 v5 rules 11-12: each render writes renders/run-NNN/ -- page.png and interactions/,
    every picture whole -- and returns no picture; the report names the files. A run whose
    interaction observation ran out of time names what it left unvisited, and the next render of
    the same draft is handed that report to resume; a changed draft is observed afresh."""

    seen: list[object] = []

    class Browser:
        def __init__(self, *, screenshot, interactive_directory):
            self.picture = screenshot
            self.interactive = interactive_directory

        def resuming(self, report):
            seen.append(None if report is None else json.loads(report.read_text(encoding="utf-8")))
            return self

        def load(self, **_kwargs):
            self.picture.write_bytes(b"d" * picture_size)
            self.interactive.mkdir()
            (self.interactive / "1280-click-initial.png").write_bytes(b"p" * picture_size)
            (self.interactive / "390-initial.png").write_bytes(b"q" * 7)
            left = [{"pass": "390-keyboard", "controls": 4, "started": True}]
            return ProspectiveResult(
                state="loaded",
                kind="browser",
                interactive={"reportOnly": True, "passes": [], "unvisited": left},
            )

    monkeypatch.setattr(presenter_tools, "BrowserProspectiveLoad", Browser)
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    authoring = presenter_tools.AuthoringTools(
        drafts, host=tmp_path / "instructions", grants={"working": str(drafts), "inputs": {}}
    )
    runs = []
    for assets in ({"index.html": PAGE}, {"index.html": PAGE}, {"index.html": PAGE + b" "}):
        report = authoring.render(packet=packet(record()), assets=assets)
        authoring.keep_report(report)
        assert report["state"] == "loaded" and report["pictureError"] is None
        interactive = report["interactive"]
        assert "pictures" not in interactive  # one folder named, not each picture
        assert interactive["pictureCount"] == 2 and interactive["picturesComplete"] is True
        runs.append(report["run"])
        run = tmp_path / report["run"]
        assert report["picture"] == f"{report['run']}/page.png"
        assert (run / "page.png").read_bytes() == b"d" * picture_size
        assert interactive["pictureFolder"] == f"{report['run']}/interactions"
        assert (run / "interactions" / "1280-click-initial.png").read_bytes() == (
            b"p" * picture_size
        )
        assert report["reportPath"] == f"{report['run']}/report.json"
    assert runs == ["renders/run-001", "renders/run-002", "renders/run-003"]
    assert not list((drafts / "assets").iterdir())  # nothing enters the published assets
    assert not (drafts / "host").exists()
    # The first run is fresh; the second, on the same draft, resumes the first; a changed draft is not.
    assert seen[0] is None and seen[2] is None
    assert isinstance(seen[1], dict) and seen[1]["run"] == "renders/run-001/interactions"
    assert seen[1]["unvisited"] == [{"pass": "390-keyboard", "controls": 4, "started": True}]


def test_the_render_reply_fits_1000_tokens_in_total_and_names_what_moved() -> None:
    """D597 and MIK-R211 v5 rule 11: the whole answer is at most 1,000 tokens, identity included.
    The summary and whole errors fill what the identity items leave; what does not fit is named
    with the report file holding it; the totals stay the run's; no picture is in the answer."""

    identity = {
        "state": "loaded",
        "run": "renders/run-003",
        "reportPath": "renders/run-003/report.json",
        "picture": "renders/run-003/page.png",
        "publication": {
            "state": "published",
            "revision": 4,
            "coverage": "2 of 5 passes",
            "partMap": [{"part": "p"}],
        },
        "interactive": {"pictureFolder": "renders/run-003/interactions", "pictureCount": 335},
        "readiness": {"ready": True, "failed": [], "notes": []},
    }
    errors = [f"load: failure {index:02d} " + "y" * 300 for index in range(30)]
    summary = {
        "passes": 5,
        "passesIncomplete": 0,
        "controlsVisited": 12,
        "controlsFailed": 2,
        "checksPassed": 4,
        "checksFailed": 1,
        "errors": 30,
    }
    # Errors overflow: the summary fits, then whole errors until the bound, and the rest is named.
    reply = render_answer({**identity, "allErrors": errors, "summary": summary})
    # The publication says the revision's coverage, never the part map (MIK-R211 v5, 15:43Z).
    assert reply["publication"] == {
        "state": "published",
        "revision": 4,
        "coverage": "2 of 5 passes",
    }
    assert reply["summary"] == summary  # the report's own counts, copied
    assert reply["errorsTotal"] == 30 and reply["errors"] == errors[: len(reply["errors"])]
    assert reply["moved"] == [
        {
            "part": "errors",
            "shown": len(reply["errors"]),
            "total": 30,
            "to": "renders/run-003/report.json",
        }
    ]
    assert (
        answer_tokens(reply)
        <= 1000
        < answer_tokens({**reply, "errors": errors[: len(reply["errors"]) + 1]})
    )
    # The summary alone past the bound: it moves, named, and the totals and identity stay.
    wide = {f"count-{index:04d}": index for index in range(400)}
    reply = render_answer({**identity, "allErrors": errors[:2], "summary": wide})
    assert answer_tokens({"summary": wide}) > 1000
    assert "summary" not in reply and {"part": "summary", "to": reply["report"]} in reply["moved"]
    assert reply["errorsTotal"] == 2 and reply["errors"] == errors[:2]
    for kept in (reply, render_answer({**identity, "allErrors": errors, "summary": wide})):
        assert answer_tokens(kept) <= 1000
        assert kept["run"] == "renders/run-003/" and kept["report"] == "renders/run-003/report.json"
        assert kept["picture"] == "renders/run-003/page.png"
        assert kept["interactions"] == {"folder": "renders/run-003/interactions", "count": 335}
        assert kept["publication"] == {
            "state": "published",
            "revision": 4,
            "coverage": "2 of 5 passes",
        }
    assert "readiness" not in reply and "allErrors" not in reply  # the rest stays in the file
