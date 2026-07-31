"""Generate a rich sim fixture for stress-testing the cockpit against the full domain.

Produces a mini coordination root the dashboard can replay (build_sim materializes the structural
surfaces + replays the event logs):

* 30+ lifecycles in variation — ~26 *persistent paused* (synthesized from worktree contracts,
  varied closeout/integration/cleanup → hangar variety) + ~8 *event-backed* (running across phases,
  blocked gates, fleeting, completed) across 5 repos;
* task content — single (``light``) docs + a multi-task ``master`` series with ``subTask`` slices,
  keyed by lifecycle;
* provider current-state, per-worktree provider stacks, memory ledgers, drift snapshots, setup
  progress.

Run from the code worktree:
    PYTHONPATH=mcp/src .venv/bin/python mcp/tests/fixtures/build_rich_sim.py [out_dir]
Then:  ... dashboard --sim <out_dir> --sim-speed 10
"""

from __future__ import annotations

import itertools
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from agents_remember.tasks.document import TaskDocument
from agents_remember.worktrees.worktree_contract import load_contract

SHA = "0123456789abcdef0123456789abcdef01234567"
_ts_counter = itertools.count()


def ts() -> str:
    """Increasing timestamps inside a ~15-min window so replay feeds them over a short span."""
    n = next(_ts_counter)
    return f"2026-06-14T09:{n // 60:02d}:{n % 60:02d}+00:00"


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, obj: object) -> None:
    write_text(path, json.dumps(obj, indent=2))


@dataclass(frozen=True)
class ContractSite:
    """Where one contract sits in the simulated coordination root.

    Not five arguments but one address: every path a contract records -- its task root, its
    own file, the worktree group, the code and memory worktrees -- is derived from these
    fields, and a series contract differs from its leaf enclosures only here. The builders
    that write a contract, stat its path and materialize its worktrees all take the same
    site, so the three can never disagree about where a leaf lives.
    """

    root: Path
    repo: str
    task: str
    contract_kind: str = "leaf"
    leaf_id: str | None = None

    @property
    def is_series(self) -> bool:
        return self.contract_kind == "series"

    @property
    def leaf(self) -> str:
        return self.leaf_id or self.task

    @property
    def group(self) -> str:
        return f"{self.leaf}-ar"

    @property
    def task_root(self) -> Path:
        return self.root / "tasks" / self.repo / self.task

    @property
    def group_root(self) -> Path:
        return self.root / "worktrees" / self.repo / self.group

    @property
    def contract_path(self) -> Path:
        if self.is_series:
            return self.task_root / "series-contract.md"
        return self.task_root / "enclosures" / self.leaf / "series-contract.md"

    @property
    def worktree_group(self) -> Path:
        return self.task_root / "enclosures" if self.is_series else self.group_root

    @property
    def code_worktree(self) -> Path:
        if self.is_series:
            return self.root / "repos" / self.repo
        return self.group_root / self.leaf

    @property
    def memory_worktree(self) -> Path:
        return self.group_root / f"memory-{self.leaf}"


@dataclass(frozen=True)
class ContractStatus:
    """How far one contract has travelled through the gates it records.

    The four fields are a single position on the review -> closeout -> integration ->
    cleanup path (a landed leaf is `completed/completed/completed/completed`, an abandoned
    one `pending-review/not-started/not-started/abandoned`), which is why the fixture varies
    them as whole named states rather than four loose strings.
    """

    review: str = "approved"
    closeout: str = "completed"
    integration: str = "not-started"
    cleanup: str = "pending"


AWAITING_INTEGRATION = ContractStatus()
"""The default fixture state: reviewed and closed out, not yet integrated or cleaned up."""


def contract_md(
    site: ContractSite,
    *,
    lifecycle_id: str = "",
    kind: str = "light-task",
    status: ContractStatus = AWAITING_INTEGRATION,
) -> str:
    root, repo, task = site.root, site.repo, site.task
    taskid = task.upper().replace("-", "_")
    contract_path = site.contract_path
    worktree_group = site.worktree_group
    code_worktree = site.code_worktree
    memory_lines = [
        "memory:",
        "  mode: external",
        f"  repo_path: {root.as_posix()}/memory-repos/ar-{repo}",
        "  source_branch: main",
        f"  work_branch: ar/{task}",
        f"  base_commit: {SHA}",
    ]
    if site.is_series:
        memory_lines.append(f"  ledger: {root.as_posix()}/memory-repos/ar-{repo}/memory.md")
    else:
        memory_lines.extend(
            [
                f"  worktree: {site.memory_worktree.as_posix()}",
                f"  ledger: {site.memory_worktree.as_posix()}/memory.md",
            ]
        )
    return f"""---
schema: ar-series-contract/v1
kind: {site.contract_kind}
task_id: {taskid}
task_name: {task}
repo_name: {repo}
workflow_kind: {kind}
memory_mode: external

coordination:
  root: {root.as_posix()}
  task_root: {site.task_root.as_posix()}
  series_contract_path: {contract_path.as_posix()}
  task_artifact: {site.task_root.as_posix()}/task.md
  worktree_group: {worktree_group.as_posix()}
  leaf_id: {site.leaf if site.contract_kind == "leaf" else ""}
  parent_contract_path: {site.task_root.as_posix()}/series-contract.md

code:
  repo_path: {root.as_posix()}/repos/{repo}
  source_branch: main
  work_branch: ar/{task}
  base_commit: {SHA}
  worktree: {code_worktree.as_posix()}

lifecycle:
  id: {lifecycle_id}

{chr(10).join(memory_lines)}

human_review:
  status: {status.review}
  approved_for_commit: no

closeout:
  status: {status.closeout}

integration:
  status: {status.integration}
  cleanup: {status.cleanup}
---

# Series Contract - {taskid}
"""


def materialize_worktrees(site: ContractSite) -> None:
    """Create the worktree dirs a live leaf contract records.

    L11 renders a leaf on the tasks surface ONLY while its worktree physically
    exists (stat'ed at snapshot time), so a fixture leaf that should be visible
    must ship the directories its contract points at — recording the paths
    without creating them replays as an empty Hangar (L11R-1).
    """
    site.code_worktree.mkdir(parents=True, exist_ok=True)
    site.memory_worktree.mkdir(parents=True, exist_ok=True)


def event(
    kind: str, lc: str, *, trust: str = "observed", actor: str = "system", **data: object
) -> dict:
    extra: dict[str, object] = {}
    for key in ("enclosure", "repoId"):
        if key in data:
            extra[key] = data.pop(key)
    return {
        "schema": "ar-observer-event/v1",
        "id": f"{lc}-{next(_ts_counter)}",
        "ts": ts(),
        "kind": kind,
        "trust": trust,
        "actor": actor,
        "data": data,
        "lifecycleId": lc,
        **extra,
    }


_STEP_TITLES = [
    "Reframe + research",
    "Plan + code examples",
    "Implement the core",
    "Wire + unit tests",
    "Onboarding lockstep",
    "Closeout + quality gate",
    "Live verification",
    "Polish",
]


@dataclass(frozen=True)
class Progress:
    """How far a task's step list has run: ``done`` of ``total`` steps.

    One number is meaningless without the other -- ``done == total`` is what makes a slice
    Completed -- so they travel as a pair through every doc builder that renders steps.
    """

    done: int
    total: int

    @property
    def complete(self) -> bool:
        return self.done >= self.total


def steps(progress: Progress, current_in_progress: bool = True) -> list[dict]:
    done, total = progress.done, progress.total
    out: list[dict] = []
    for i in range(total):
        status = (
            "done"
            if i < done
            else ("inProgress" if (i == done and current_in_progress) else "pending")
        )
        step: dict = {
            "id": str(i + 1),
            "title": _STEP_TITLES[i % len(_STEP_TITLES)],
            "status": status,
        }
        if i == 0:  # show substep nesting on the opening step
            step["substeps"] = [
                {"id": "1.1", "title": "Read the notes", "status": "done"},
                {
                    "id": "1.2",
                    "title": "Trust checkpoint",
                    "status": "done" if done > 0 else "inProgress",
                },
            ]
        out.append(step)
    return out


def light_doc(repo: str, task: str, lc: str, *, status: str, progress: Progress) -> dict:
    return {
        "schema": "ar-task-document/v1",
        "id": task.upper().replace("-", "_"),
        "slug": task,
        "title": task.replace("_", " ").replace("-", " ").title(),
        "kind": "light",
        "status": status,
        "repo": repo,
        "type": "Code",
        "createdAt": ts(),
        "lifecycleId": lc,
        "objective": (
            f"Deliver {task.replace('_', ' ')} on {repo}: ship the change end-to-end behind the commit "
            "gate, with onboarding refreshed in lockstep so memory never drifts from the code."
        ),
        "requirements": [
            "Behaviour-preserving where the contract is unchanged.",
            "Quality gate green before each commit (ruff / pyright / complexity / tests).",
            "Every changed source file's onboarding sidecar refreshed in the same pass.",
        ],
        "design": (
            "Reuse the existing reducer seam rather than adding a new call edge; the change rides the "
            "existing analytics delta, so no stream/store wiring changes."
        ),
        "steps": steps(progress),
        "codeExamples": [
            {
                "id": "C1",
                "title": "The pure builder",
                "distinctChange": "new reducer fold",
                "why": "Server-side so every client (dashboard / TUI / agent) shares one honest result.",
                "language": "python",
                "snippet": "def build(items, *, now):\n    return sorted(\n        (project(i, now) for i in items),\n        key=rank,\n    )",
            }
        ],
        "decisions": [
            {
                "at": "2026-06-12T10:00",
                "decision": "Scope to a single slice.",
                "rationale": "Keeps the plan single-page and the commit reviewable.",
            },
            {
                "at": "2026-06-13T14:30",
                "decision": "Defer charts to a later slice.",
                "rationale": "No dense streaming telemetry yet; the SVG primitive suffices.",
            },
        ],
        "openQuestions": ["Does the enclosure span more than one repo pair here?"],
        "references": ["notes/06-attention-queue-ia.md", "docs/design/observable-lifecycle.md"],
    }


@dataclass(frozen=True)
class SeriesSlice:
    """One numbered slice of a master series, with how far it has run.

    The master it belongs to, its number and its name are what make a subTask addressable
    (``id`` is master + number, ``slug`` and ``title`` are number + name), and its progress
    is what decides whether the series shows it as Completed. The fixture defines its slices
    once as these objects and both the master's subTask refs and each subTask doc read them.
    """

    master_slug: str
    number: str
    name: str
    progress: Progress

    @property
    def label(self) -> str:
        return self.name.replace("-", " ")

    @property
    def status(self) -> str:
        return "Completed" if self.progress.complete else "inProgress"

    def master_reference(self) -> dict:
        """The row the master doc carries for this slice."""
        return {"number": self.number, "name": self.name, "status": self.status}


def subtask_doc(repo: str, slice_: SeriesSlice, lc: str) -> dict:
    return {
        "schema": "ar-task-document/v1",
        "id": f"{slice_.master_slug.upper()}_{slice_.number}",
        "slug": f"{slice_.number}_{slice_.name}",
        "title": f"{slice_.number} · {slice_.name.replace('-', ' ').title()}",
        "kind": "subTask",
        "status": slice_.status,
        "repo": repo,
        "createdAt": ts(),
        "master": slice_.master_slug,
        "lifecycleId": lc,
        "objective": (
            f"Series slice {slice_.number}: {slice_.label} — "
            "one reviewable commit behind its own gate."
        ),
        "requirements": [
            "Gate green before the slice commit.",
            "Onboarding lockstep for every changed file.",
        ],
        "design": "Builds on the prior slice's seam; no new call edges.",
        "steps": steps(slice_.progress),
        "decisions": [
            {
                "at": "2026-06-13T09:00",
                "decision": f"Cut {slice_.label} as its own slice.",
                "rationale": "Fails the single-page test if bundled with its siblings.",
            }
        ],
        "references": ["notes/00-index.md"],
    }


def master_doc(repo: str, slug: str, subtasks: list[dict]) -> dict:
    return {
        "schema": "ar-task-document/v1",
        "id": slug.upper().replace("-", "_"),
        "slug": slug,
        "title": slug.replace("_", " ").replace("-", " ").title(),
        "kind": "master",
        "status": "inProgress",
        "repo": repo,
        "createdAt": ts(),
        "subTasks": subtasks,
        "sections": [{"kind": "freeform", "heading": "Objective", "body": f"The {slug} series."}],
    }


def ledger_md(repo: str, rows: int) -> str:
    body = "\n".join(f"| code{i:02d} | mem{i:02d} |" for i in range(rows))
    return (
        "```json ar-memory-ledger\n"
        + json.dumps(
            {
                "schema": "ar-memory-ledger/v1",
                "repoName": repo,
                "baseCodeCommit": SHA,
                "baseMemoryCommit": SHA,
                "lastVerifiedCodeCommit": SHA,
                "lastMemoryContentCommit": SHA,
                "sortOrder": "newest-first",
            },
            indent=2,
        )
        + "\n```\n\n| code commit | memory commit |\n| --- | --- |\n"
        + body
        + "\n"
    )


def drift_snapshot(repo: str, *, current: int, drifted: int, missing: int) -> dict:
    return {
        "schema": "ar-drift-snapshot/v1",
        "repository": repo,
        "branch": "main",
        "counts": {"current": current, "drifted": drifted, "missing": missing},
        "actionableCount": drifted + missing,
        "checkedAt": ts(),
    }


def write_paused_lifecycles(out: Path) -> None:
    """~26 persistent paused lifecycles: worktree contracts and no events.

    Varied closeout/integration/cleanup so the hangar + phase grouping show a full range.
    """
    statuses = [
        ContractStatus(
            closeout="completed", integration="completed", cleanup="completed"
        ),  # landed
        ContractStatus(
            closeout="completed", integration="completed", cleanup="pending"
        ),  # uncleaned (hangar)
        ContractStatus(
            closeout="completed", integration="not-started", cleanup="pending"
        ),  # awaiting integrate
        ContractStatus(
            closeout="not-started",
            integration="not-started",
            cleanup="pending",
            review="pending-review",
        ),
        ContractStatus(
            closeout="not-started",
            integration="not-started",
            cleanup="abandoned",
            review="pending-review",
        ),
    ]
    repos = [
        "agents-remember",
        "agents-remember-md",
        "device-management",
        "ctec-firmware",
        "helpdesk-portal",
    ]
    paused = 0
    for r, repo in enumerate(repos):
        for i in range(5 if repo != "agents-remember" else 6):  # 26 total
            task = f"2606{r:02d}_{repo.split('-')[0]}-task-{i}"
            st = statuses[(r + i) % len(statuses)]
            site = ContractSite(out, repo, task)
            # half the persistent paused get a real lifecycle id + a light task doc (single-task content)
            give_doc = (r + i) % 2 == 0
            lc = f"L-paused-{paused:02d}" if give_doc else ""
            write_text(site.contract_path, contract_md(site, lifecycle_id=lc, status=st))
            if st.cleanup == "pending":
                materialize_worktrees(site)
            if give_doc:
                write_json(
                    out / "tasks" / repo / task / f"{task}.json",
                    light_doc(repo, task, lc, status="inProgress", progress=Progress(i % 4, 4)),
                )
            paused += 1


def write_master_series(out: Path) -> None:
    """A multi-task master series: one lifecycle, a master doc and its subTask slices."""
    series_repo, series_task, series_lc = (
        "agents-remember",
        "260610_browser-dashboard",
        "L-series-dashboard",
    )
    (out / "repos" / series_repo).mkdir(parents=True, exist_ok=True)
    site = ContractSite(out, series_repo, series_task, contract_kind="series")
    write_text(
        site.contract_path,
        contract_md(
            site,
            lifecycle_id=series_lc,
            kind="master",
            status=ContractStatus(closeout="completed", cleanup="pending"),
        ),
    )
    slices = [
        SeriesSlice(series_task, "01", "lifecycle-event-gate-design", Progress(4, 4)),
        SeriesSlice(series_task, "02", "lifecycle-tools-and-events", Progress(6, 6)),
        SeriesSlice(series_task, "03", "observer-projection", Progress(5, 5)),
        SeriesSlice(series_task, "04", "serving-layer", Progress(3, 3)),
        SeriesSlice(series_task, "05", "cockpit-v1", Progress(7, 10)),
    ]
    write_json(
        out / "tasks" / series_repo / series_task / f"{series_task}.json",
        master_doc(series_repo, series_task, [s.master_reference() for s in slices]),
    )
    for slice_ in slices:
        write_json(
            out / "tasks" / series_repo / series_task / f"{slice_.number}_{slice_.name}.json",
            subtask_doc(series_repo, slice_, series_lc),
        )


def lifecycle_logs_root(out: Path) -> Path:
    return out / "logs" / "observer" / "lifecycles"


def write_event_backed_lifecycles(out: Path) -> None:
    """~8 event-backed lifecycles: the active/fleeting variety plus the attention queue."""
    logs = lifecycle_logs_root(out)

    def log(lc: str, events: list[dict]) -> None:
        write_text(logs / lc / "events.jsonl", "\n".join(json.dumps(e) for e in events) + "\n")

    # persistent active (promoted into a worktree); 2 running, 2 blocked gates
    for lc, repo, phase, blocked in [
        ("L-run-build", "agents-remember", "build", False),
        ("L-run-research", "device-management", "reframe-research", False),
        ("L-blocked-plan", "agents-remember-md", "reframe-research", True),
        ("L-blocked-rebase", "device-management", "build", True),
    ]:
        site = ContractSite(out, repo, f"active-{lc}")
        enc = site.contract_path.as_posix()
        write_text(
            site.contract_path,
            contract_md(site, lifecycle_id=lc, status=ContractStatus(cleanup="pending")),
        )
        materialize_worktrees(site)
        write_json(
            out / "tasks" / repo / f"active-{lc}" / f"active-{lc}.json",
            light_doc(repo, f"active-{lc}", lc, status="inProgress", progress=Progress(2, 5)),
        )
        evs = [
            event("lifecycle.started", lc, data_phase="request", fleeting=True, phase="request"),
            event(
                "lifecycle.promoted",
                lc,
                trust="approved",
                actor="developer",
                scope=repo,
                enclosure=enc,
                repoId=repo,
            ),
            event("lifecycle.phase-changed", lc, trust="declared", actor="model", phase=phase),
            event("tool.completed", lc, actor="model", tool="grepai_search", tokens=1400),
        ]
        if blocked:
            q = "Approve the plan?" if "plan" in lc else "Rebase on main — conflict. Resolve?"
            evs.append(
                event("lifecycle.blocked", lc, actor="model", ask={"kind": "gate", "question": q})
            )
        log(lc, evs)

    # fleeting (no worktree) — bare-bones entries
    log(
        "L-fleeting-1",
        [
            event(
                "lifecycle.started",
                "L-fleeting-1",
                data_phase="trust-checkpoint",
                fleeting=True,
                phase="trust-checkpoint",
            )
        ],
    )
    log(
        "L-fleeting-2",
        [
            event("lifecycle.started", "L-fleeting-2", fleeting=True, phase="request"),
            event(
                "lifecycle.phase-changed",
                "L-fleeting-2",
                trust="declared",
                actor="model",
                phase="reframe-research",
            ),
            event(
                "tool.completed",
                "L-fleeting-2",
                actor="model",
                tool="cgc_symbol_search",
                tokens=600,
            ),
        ],
    )
    # completed (terminal)
    for lc, repo in [("L-done-1", "ctec-firmware"), ("L-done-2", "helpdesk-portal")]:
        site = ContractSite(out, repo, f"active-{lc}")
        enc = site.contract_path.as_posix()
        write_text(
            site.contract_path,
            contract_md(
                site,
                lifecycle_id=lc,
                status=ContractStatus(
                    closeout="completed", integration="completed", cleanup="completed"
                ),
            ),
        )
        log(
            lc,
            [
                event("lifecycle.started", lc, fleeting=True, phase="request"),
                event(
                    "lifecycle.promoted",
                    lc,
                    trust="approved",
                    actor="developer",
                    scope=repo,
                    enclosure=enc,
                    repoId=repo,
                ),
                event(
                    "lifecycle.phase-changed", lc, trust="declared", actor="model", phase="close"
                ),
                event(
                    "lifecycle.ended", lc, actor="developer", trust="approved", outcome="completed"
                ),
            ],
        )


def write_provider_state(out: Path) -> None:
    """Provider current-state (workspace), per-worktree stacks, and setup progress."""
    write_json(
        out / "logs" / "providers" / "status" / "workspace" / "projects" / "current.json",
        {
            "checkedAt": ts(),
            "kind": "provider-current-state",
            "ok": True,
            "instance": {"id": "projects", "scope": "workspace"},
            "providers": {
                "codegraphcontext-code": {
                    "id": "codegraphcontext-code",
                    "state": "ready",
                    "ok": True,
                    "watcherUp": True,
                    "indexingState": "indexed",
                },
                "grepai-memory": {
                    "id": "grepai-memory",
                    "state": "ready",
                    "ok": True,
                    "watcherUp": True,
                    "indexingState": "indexing",
                },
            },
        },
    )
    # per-worktree provider stacks (note-03 surface 4) — ready for the per-worktree read fix
    for repo, task in [
        ("agents-remember", "260610_browser-dashboard"),
        ("device-management", "active-L-run-research"),
    ]:
        write_json(
            out / "worktrees" / repo / f"{task}-ar" / "provider-runtime" / "provider-state.json",
            {
                "schema": "ar-worktree-provider-state/v1",
                "repoName": repo,
                "worktreeGroup": (out / "worktrees" / repo / f"{task}-ar").as_posix(),
                "codeWorktree": (out / "worktrees" / repo / f"{task}-ar" / task).as_posix(),
                "memoryWorktree": (
                    out / "worktrees" / repo / f"{task}-ar" / f"memory-{task}"
                ).as_posix(),
                "isolatedProviderSettings": {
                    "providers": ["codegraphcontext-code", "grepai-memory"]
                },
            },
        )

    # setup progress (surface 3): one ok, one failed (failed-setup attention)
    write_json(
        out
        / "worktrees"
        / "agents-remember"
        / "260610_browser-dashboard-ar"
        / "provider-runtime"
        / "setup-progress.json",
        {
            "schema": "ar-worktree-setup-progress/v1",
            "state": "ok",
            "completedPhases": ["seed", "refresh"],
            "currentPhase": None,
            "heartbeatAt": ts(),
        },
    )
    write_json(
        out
        / "worktrees"
        / "device-management"
        / "active-L-run-research-ar"
        / "provider-runtime"
        / "setup-progress.json",
        {
            "schema": "ar-worktree-setup-progress/v1",
            "state": "ready-with-failed-phases",
            "completedPhases": ["seed"],
            "failedPhases": ["cgc-index"],
            "currentPhase": {"provider": "codegraphcontext", "action": "index"},
            "heartbeatAt": ts(),
        },
    )


def write_memory_ledgers_and_drift(out: Path) -> None:
    """Memory ledgers and drift snapshots: the memory mirror + actionable-drift attention."""
    for repo, rows in [
        ("agents-remember", 95),
        ("agents-remember-md", 60),
        ("device-management", 22),
    ]:
        write_text(out / "memory-repos" / f"ar-{repo}" / "memory.md", ledger_md(repo, rows))
    write_json(
        out / "logs" / "observer" / "drift" / "agents-remember.json",
        drift_snapshot("agents-remember", current=372, drifted=4, missing=2),
    )
    write_json(
        out / "logs" / "observer" / "drift" / "device-management.json",
        drift_snapshot("device-management", current=140, drifted=0, missing=0),
    )


def validate_and_report(out: Path) -> None:
    """Self-check: every contract and task doc the fixture wrote must validate."""
    contracts = list(out.glob("tasks/**/*.md"))
    contracts = [path for path in contracts if path.name == "series-contract.md"]
    for c in contracts:
        load_contract(c)
    docs = list(out.glob("tasks/*/*/*.json"))
    for d in docs:
        TaskDocument.model_validate(json.loads(d.read_text()))
    logs = lifecycle_logs_root(out)
    print(f"rich sim fixture written to {out}")
    print(
        f"  contracts: {len(contracts)}  task docs: {len(docs)}  event logs: {len(list(logs.glob('*/events.jsonl')))}"
    )


def main(out: Path) -> None:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    write_paused_lifecycles(out)
    write_master_series(out)
    write_event_backed_lifecycles(out)
    write_provider_state(out)
    write_memory_ledgers_and_drift(out)
    validate_and_report(out)


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "sim-rich"
    main(target.resolve())
