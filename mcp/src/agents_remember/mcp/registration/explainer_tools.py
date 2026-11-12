"""The explainer's four tools, declared in a light owner (MIK-R211 v5 rule 3, MIK-R204 @ v1 rule 1).

The explainer works in a normal workspace with its harness's own tools and AR's general tools; its own
server keeps exactly what no harness tool can do (D603, D607): ``render`` (the browser check and the
publication into the task) and the three grade tools ``define_category``, ``remove_category`` and
``grade`` (validated grades). It reports to the architect with ``role_message``, as every agent does.

The module imports the answer boundary and the tool framework and resolves the implementations from
``canvas.explainer_server`` **on the call**, so declaring the tools loads no renderer or grade store.
Every answer passes its own bound (``canvas.explainer_answer_bounds``, rule 11): content beyond it is a
file in the workspace that the answer names.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from agents_remember.canvas.explainer_answer_bounds import (
    EXPLAINER_ANSWER_BOUNDS,
    answer_tokens,
    bounded,
)
from agents_remember.canvas.explainer_answers import answered
from agents_remember.canvas.explainer_inputs import ExplainerInputs, now_iso


def _render(
    workspace: Path,
    *,
    task_root: Path | None = None,
    seat: str | None = None,
    now: Callable[[], str] | None = None,
) -> dict[str, Any]:
    return import_module("agents_remember.canvas.explainer_server").render_and_publish(
        workspace, task_root=task_root, seat=seat, now=now
    )


def register_render_tool(
    server: FastMCP,
    workspace: Path,
    *,
    task_root: Path | None = None,
    seat: str | None = None,
    now: Callable[[], str] | None = None,
) -> None:
    """``render``: the one publish command, over this environment's own folders."""

    @server.tool()
    def render() -> dict:
        """Check the draft in drafts/assets/ in Chromium and publish it as the task's next revision.

        Write authored claims as {"claims": [...]} to drafts/assets/claim-map.json first; the map uses
        EvidenceClaim fields and must cite admitted evidence. Fix readiness failures and call render
        again; publication.state published (or unchanged, for the same page) confirms publication,
        and a new revision is copied into revisions/<n>/ at once (publication.workspace says so).
        Each call writes its own run folder renders/run-NNN/: report.json (the whole report),
        page.png (the draft picture) and interactions/ (every interaction picture). The answer names
        them, the publication state and revision, and a summary and the errors that fit 1,000 tokens;
        `moved` names what is only in the report. No picture is returned: open them from the folder.
        An interaction run that ran out of time names its unvisited passes; render the same draft
        again to visit only those.
        """
        report = answered(lambda: _render(workspace, task_root=task_root, seat=seat, now=now))
        return bounded("render", render_answer(report), workspace)


def render_answer(report: dict[str, Any]) -> dict[str, Any]:
    """The answer to one render: where its run is, its publication, its counts and first errors.

    The identity items come first and are never trimmed: the run folder, its report and picture, the
    interaction folder and count, and the publication. The summary (the report's own counts) and then
    whole errors fill what is left of render's 1,000 tokens. A part that does not fit is not cut: it is
    named in ``moved`` with the report file that holds all of it (D597).
    """

    if report.get("state") == "refused" and "run" not in report:
        return report  # refused before a run began: the refusal is the whole answer
    run = report.get("run")
    path = report.get("reportPath")
    interactive = report.get("interactive") or {}
    publication = report.get("publication") or {"state": "not attempted"}
    answer: dict[str, Any] = {
        "state": report.get("state"),
        "run": None if run is None else f"{run}/",
        "report": path,
        "picture": report.get("picture"),
        "interactions": {
            "folder": interactive.get("pictureFolder"),
            "count": interactive.get("pictureCount", 0),
        },
        "publication": {
            key: publication[key]
            for key in ("state", "revision", "coverage", "workspace", "refusal")
            if key in publication
        },
        **({"resumedFrom": interactive["resumedFrom"]} if interactive.get("resumedFrom") else {}),
    }
    return _packed(answer, report, path)


def _packed(answer: dict[str, Any], report: dict[str, Any], path: Any) -> dict[str, Any]:
    """Add the summary, then whole errors, while the answer stays within render's bound."""

    bound = EXPLAINER_ANSWER_BOUNDS["render"]
    errors = list(report.get("allErrors") or [])
    summary = report.get("summary")
    # What a `moved` entry costs at most, kept free so naming the moved part never passes the bound.
    most = len(errors)
    reserve = answer_tokens(
        {
            "moved": [
                {"part": "summary", "to": path},
                {"part": "errors", "shown": most, "total": most, "to": path},
            ]
        }
    )
    answer = {**answer, "errorsTotal": len(errors)}
    moved: list[dict[str, Any]] = []
    if summary is not None:
        if answer_tokens({**answer, "summary": summary}) + reserve <= bound:
            answer["summary"] = summary
        else:
            moved.append({"part": "summary", "to": path})
    shown: list[str] = []
    for error in errors:  # whole errors only: one never arrives cut
        if answer_tokens({**answer, "errors": [*shown, error]}) + reserve > bound:
            break
        shown.append(error)
    while True:  # tokens do not add exactly across joined JSON: the whole answer is what is held
        packed = {**answer, "errors": shown, "moved": _moved(moved, shown, errors, path)}
        if answer_tokens(packed) <= bound or not shown:
            return packed
        shown = shown[:-1]


def _moved(
    moved: list[dict[str, Any]], shown: list[str], errors: list[str], path: Any
) -> list[dict[str, Any]]:
    if len(shown) == len(errors):
        return moved
    return [*moved, {"part": "errors", "shown": len(shown), "total": len(errors), "to": path}]


def register_judgment_tools(
    server: FastMCP,
    workspace: Path,
    *,
    task_root: Path | None,
    seat: str | None,
    fingerprints: Any,
    now: Any,
) -> None:
    """MIK-R187 rule 2's three grade tools."""

    # One instance per registration, built on the first call and kept: the judgment carries the seat's
    # identity and fingerprints, and a per-call reconstruction is a different object (the A14 failure).
    kept: list[Any] = []

    def judgment() -> Any:
        if not kept:
            kept.append(
                import_module("agents_remember.canvas.explainer_server").ExplainerJudgment(
                    workspace, task_root, seat, fingerprints, now
                )
            )
        return kept[0]

    @server.tool()
    def define_category(
        name: str, scale: list[dict[str, str]], binding: str, meaning: str = ""
    ) -> dict:
        """Define a category the review page can sort blocks by: 2 to 5 grades, highest first.

        Each scale point names its grade and its meaning. `binding` is the label (or digest) in your
        workspace's binding.json, e.g. '#7'. The answer is the category and its grade names in order,
        the names your grade calls use. A name with another scale or meaning is refused
        `category-exists`.
        """
        answer = answered(lambda: judgment().define(name, scale, binding, meaning=meaning))
        return bounded("define_category", answer, workspace)

    @server.tool()
    def remove_category(name: str, binding: str) -> dict:
        """Remove one category and, with it, its grades; the answer says how many grades went."""
        answer = answered(lambda: judgment().remove(name, binding))
        return bounded("remove_category", answer, workspace)

    @server.tool()
    def grade(references: list[str], category: str, grade: str, reason: str, binding: str) -> dict:
        """Grade review blocks in a category; answer how many landed and which were refused, and why.

        `references` name blocks by L89's identity; `reason` is one line. The grade carries the
        binding (the label in binding.json) and the block's fingerprint, and is refused
        `binding-moved` when the environment has left that binding. A long refused list is written
        to scratch/grades/ and the answer names the file.
        """
        answer = answered(lambda: judgment().grade(references, category, grade, reason, binding))
        return bounded("grade", answer, workspace)


def register_bound_explainer_tools(
    server: FastMCP,
    *,
    workspace: Path,
    seat: str | None = None,
    inputs: ExplainerInputs | None = None,
) -> None:
    """Register the explainer's four tools on the server the normal seat path already built.

    MIK-R186 @ v2 rule 3: there is no separate explainer server. The normal `agents-remember-task`
    server serves the explainer with AR's general tools, and this adds the four on the instance the
    launch already created -- the same implementations, one copy, no parallel entry point.

    ``seat`` is the caller identity the launch binding names; the grade tools refuse every other
    identity, because a grade is the seat's own act.
    """

    given = inputs or ExplainerInputs()
    workspace = Path(workspace)
    now = given.now or now_iso
    seat = seat or os.environ.get(
        import_module("agents_remember.canvas.explainer_state").AGENT_MARKER
    )
    register_render_tool(server, workspace, task_root=given.task_root, seat=seat, now=now)
    register_judgment_tools(
        server,
        workspace,
        task_root=given.task_root,
        seat=seat,
        fingerprints=given.fingerprints,
        now=now,
    )
