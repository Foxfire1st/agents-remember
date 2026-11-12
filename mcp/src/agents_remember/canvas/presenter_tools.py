"""The explainer's render: its draft loaded in a model-free browser, each run kept on disk.

Every render writes one run folder of its own in the workspace, ``renders/run-NNN/`` (MIK-R211 v5
rules 11 and 12, D605): ``report.json`` holds the whole report, ``page.png`` the draft picture and
``interactions/`` every interaction picture. No picture is returned to the agent inline; the reply
names the folder. Run folders are never published and go with the workspace when it is removed.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any

from agents_remember.canvas.explainer_paths import RENDERS_DIRECTORY
from agents_remember.canvas.presenter_assets import collect_assets
from agents_remember.canvas.presenter_files import ensure_child, open_binary, write_output
from agents_remember.canvas.presenter_refusals import PresenterRefused
from agents_remember.canvas.presenter_store import digest_bytes, read_whole
from agents_remember.canvas.prospective import BrowserProspectiveLoad
from agents_remember.models.knowledge.review_evidence import EvidencePacket
from agents_remember.models.knowledge.review_frame import FrameLimits

#: The whole report of one render, in that render's own run folder.
RUN_REPORT = "report.json"
#: The draft picture of one render.
RUN_PICTURE = "page.png"
#: The folder of one render's interaction pictures: several pictures of one call get their own.
RUN_INTERACTIONS = "interactions"


def _next_run(base: Path) -> str:
    """The next render run's folder name under ``base``: ``run-001``, ``run-002``, ..."""

    return f"run-{max(_run_numbers(base), default=0) + 1:03d}"


def _run_numbers(base: Path) -> list[int]:
    return [
        int(item.name.removeprefix("run-"))
        for item in (base.iterdir() if base.is_dir() else ())
        if item.name.startswith("run-") and item.name.removeprefix("run-").isdecimal()
    ]


class AuthoringTools:
    """Render over one draft folder, reading the packet its grants name.

    The explainer's environment keeps its grants and packet in ``instructions/``, its page draft in
    ``drafts/`` (MIK-R186 rule 2) and its render runs in ``renders/`` beside them. The packet is read
    against the digest its grants recorded, so a render always checks the page against the packet
    the environment was built with.
    """

    def __init__(
        self,
        working: Path,
        *,
        host: Path,
        grants: Mapping[str, Any],
        renders: Path | None = None,
    ):
        self.working = working
        self.host = host
        self.renders = working.parent / RENDERS_DIRECTORY if renders is None else renders
        self.grants: dict[str, Any] = dict(grants)
        if self.grants["working"] != str(working):
            raise ValueError("the grant does not name this prepared folder")
        ensure_child(working, "assets")

    def read(self, name: str) -> str:
        """One granted input, whole, after its digest is checked against the grant."""

        expected = self.grants["inputs"].get(name)
        if expected is None:
            raise ValueError(f"{name} is not an input this environment was built with")
        body = read_whole(self.host / name, reason="input-unreadable", detail=name)
        if digest_bytes(body) != expected:
            raise ValueError("input-changed: the input does not match its admitted digest")
        return body.decode()

    def render(
        self, *, packet: EvidencePacket | None = None, assets: dict[str, bytes] | None = None
    ) -> dict[str, Any]:
        """Load the draft once, keep its picture and interaction pictures, and answer the report.

        A run whose interaction observation ran out of time names what it did not visit; the next
        render of the same draft resumes it (``resume``), visiting only what is left.
        """

        if packet is None:
            packet = EvidencePacket.model_validate_json(self.read("evidence.json"))
        if assets is None:
            assets = collect_assets(self.working, FrameLimits())
        digests = {path: digest_bytes(body) for path, body in assets.items()}
        self.renders.mkdir(parents=True, exist_ok=True)
        resume = self._resumable(digests)
        name = _next_run(self.renders)
        run = f"{RENDERS_DIRECTORY}/{name}"
        with tempfile.TemporaryDirectory(prefix="ar-explainer-render-") as temporary:
            picture = Path(temporary) / RUN_PICTURE
            interactive = Path(temporary) / RUN_INTERACTIONS
            resume_file = None
            if resume is not None:
                resume_file = Path(temporary) / "resume.json"
                resume_file.write_text(json.dumps(resume), encoding="utf-8")
            result = (
                BrowserProspectiveLoad(screenshot=picture, interactive_directory=interactive)
                .resuming(resume_file)
                .load(assets=assets, entrypoint="index.html", packet=packet)
            )
            picture_problem = (
                self._keep(f"{name}/{RUN_PICTURE}", picture)
                if picture.is_file()
                else "the browser made no draft picture"
            )
            if result.interactive is not None:
                screenshots = sorted(interactive.glob("*.png")) if interactive.is_dir() else []
                count, picture_errors = self._keep_pictures(name, screenshots)
                result.interactive["pictureFolder"] = f"{run}/{RUN_INTERACTIONS}"
                result.interactive["pictureCount"] = count
                result.interactive["picturesComplete"] = not picture_errors
                result.interactive["pictureErrors"] = picture_errors
        return {
            **asdict(result),
            "assetDigests": digests,
            "viewport": [1280, 720],
            "run": run,
            "picture": None if picture_problem else f"{run}/{RUN_PICTURE}",
            "pictureError": picture_problem or None,
        }

    def _keep(self, relative: str, source: Path) -> str:
        """Write one picture into the run folder; answer an empty string, or why it was not kept."""

        try:
            write_output(self.renders, relative, source.read_bytes())
        except (OSError, PresenterRefused) as error:
            return f"{source.name}: {error}"
        return ""

    def _keep_pictures(self, name: str, screenshots: list[Path]) -> tuple[int, list[str]]:
        """Write this render's interaction pictures whole into its own folder; count them.

        No picture is dropped or shrunk, and none is carried in the reply: the reply names the folder
        and the count (MIK-R211 v5 rule 11), and the agent opens a picture with its own tools.
        """

        errors = [
            problem
            for screenshot in screenshots
            if (problem := self._keep(f"{name}/{RUN_INTERACTIONS}/{screenshot.name}", screenshot))
        ]
        return len(screenshots) - len(errors), errors

    def _resumable(self, digests: Mapping[str, str]) -> dict[str, Any] | None:
        """The latest run's interactive report, when it rendered these exact assets and left
        passes unvisited; its run folder is named so carried pictures stay where they lie."""

        numbers = sorted(_run_numbers(self.renders), reverse=True)
        for number in numbers:
            folder = self.renders / f"run-{number:03d}"
            try:
                with open_binary(folder / RUN_REPORT) as handle:
                    earlier = json.load(handle)
            except (OSError, ValueError):
                continue
            if earlier.get("assetDigests") != dict(digests):
                return None
            interactive = earlier.get("interactive") or {}
            if not interactive.get("unvisited"):
                return None
            return {
                **interactive,
                "run": f"{RENDERS_DIRECTORY}/{folder.name}/{RUN_INTERACTIONS}",
            }
        return None

    def keep_report(self, report: dict[str, Any]) -> str:
        """Write one render's whole report into its run folder, naming its own path."""

        relative = f"{report['run']}/{RUN_REPORT}"
        report["reportPath"] = relative
        write_output(
            self.renders,
            relative.removeprefix(f"{RENDERS_DIRECTORY}/"),
            json.dumps(report, indent=1, default=str).encode(),
        )
        return relative
