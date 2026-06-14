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


def contract_md(
    root: Path,
    repo: str,
    task: str,
    *,
    lifecycle_id: str = "",
    kind: str = "light-task",
    review: str = "approved",
    closeout: str = "completed",
    integration: str = "not-started",
    cleanup: str = "pending",
) -> str:
    taskid = task.upper().replace("-", "_")
    group = f"{task}-ar"
    return f"""---
schema: ar-worktree-contract/v1
task_id: {taskid}
task_name: {task}
repo_name: {repo}
workflow_kind: {kind}
memory_mode: external

coordination:
  root: {root.as_posix()}
  task_root: {root.as_posix()}/tasks/{repo}/{task}
  contract_path: {root.as_posix()}/tasks/{repo}/{task}/contract.md
  task_artifact: {root.as_posix()}/tasks/{repo}/{task}/task.md
  worktree_group: {root.as_posix()}/worktrees/{repo}/{group}

code:
  repo_path: {root.as_posix()}/repos/{repo}
  source_branch: main
  work_branch: ar/{task}
  base_commit: {SHA}
  worktree: {root.as_posix()}/worktrees/{repo}/{group}/{task}

lifecycle:
  id: {lifecycle_id}

memory:
  mode: external
  repo_path: {root.as_posix()}/memory-repos/ar-{repo}
  source_branch: main
  work_branch: ar/{task}
  base_commit: {SHA}
  worktree: {root.as_posix()}/worktrees/{repo}/{group}/memory-{task}
  ledger: {root.as_posix()}/worktrees/{repo}/{group}/memory-{task}/memory.md

human_review:
  status: {review}

closeout:
  status: {closeout}

integration:
  status: {integration}
  cleanup: {cleanup}
---

# Worktree Contract - {taskid}
"""


def event(kind: str, lc: str, *, trust: str = "observed", actor: str = "system", **data: object) -> dict:
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


def steps(done: int, total: int, current_in_progress: bool = True) -> list[dict]:
    out: list[dict] = []
    for i in range(total):
        status = "done" if i < done else ("inProgress" if (i == done and current_in_progress) else "pending")
        step: dict = {"id": str(i + 1), "title": _STEP_TITLES[i % len(_STEP_TITLES)], "status": status}
        if i == 0:  # show substep nesting on the opening step
            step["substeps"] = [
                {"id": "1.1", "title": "Read the notes", "status": "done"},
                {"id": "1.2", "title": "Trust checkpoint", "status": "done" if done > 0 else "inProgress"},
            ]
        out.append(step)
    return out


def light_doc(repo: str, task: str, lc: str, *, status: str, done: int, total: int) -> dict:
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
        "steps": steps(done, total),
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


def subtask_doc(repo: str, master_slug: str, num: str, name: str, lc: str, *, done: int, total: int) -> dict:
    return {
        "schema": "ar-task-document/v1",
        "id": f"{master_slug.upper()}_{num}",
        "slug": f"{num}_{name}",
        "title": f"{num} · {name.replace('-', ' ').title()}",
        "kind": "subTask",
        "status": "inProgress" if done < total else "Completed",
        "repo": repo,
        "createdAt": ts(),
        "master": master_slug,
        "lifecycleId": lc,
        "objective": f"Series slice {num}: {name.replace('-', ' ')} — one reviewable commit behind its own gate.",
        "requirements": [
            "Gate green before the slice commit.",
            "Onboarding lockstep for every changed file.",
        ],
        "design": "Builds on the prior slice's seam; no new call edges.",
        "steps": steps(done, total),
        "decisions": [
            {
                "at": "2026-06-13T09:00",
                "decision": f"Cut {name.replace('-', ' ')} as its own slice.",
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


def main(out: Path) -> None:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    # --- ~26 persistent paused lifecycles (worktree contracts, no events) --------------------
    # Varied closeout/integration/cleanup so the hangar + phase grouping show a full range.
    statuses = [
        dict(closeout="completed", integration="completed", cleanup="completed"),  # fully landed
        dict(closeout="completed", integration="completed", cleanup="pending"),  # uncleaned (hangar)
        dict(closeout="completed", integration="not-started", cleanup="pending"),  # awaiting integrate
        dict(closeout="not-started", integration="not-started", cleanup="pending", review="pending-review"),
        dict(closeout="not-started", integration="not-started", cleanup="abandoned", review="pending-review"),
    ]
    repos = ["agents-remember", "agents-remember-md", "device-management", "ctec-firmware", "helpdesk-portal"]
    paused = 0
    for r, repo in enumerate(repos):
        for i in range(5 if repo != "agents-remember" else 6):  # 26 total
            task = f"2606{r:02d}_{repo.split('-')[0]}-task-{i}"
            st = statuses[(r + i) % len(statuses)]
            # half the persistent paused get a real lifecycle id + a light task doc (single-task content)
            give_doc = (r + i) % 2 == 0
            lc = f"L-paused-{paused:02d}" if give_doc else ""
            write_text(out / "tasks" / repo / task / "contract.md", contract_md(out, repo, task, lifecycle_id=lc, **st))
            if give_doc:
                write_json(
                    out / "tasks" / repo / task / f"{task}.json",
                    light_doc(repo, task, lc, status="inProgress", done=(i % 4), total=4),
                )
            paused += 1

    # --- a multi-task master series (one lifecycle, master doc + subTask slices) --------------
    series_repo, series_task, series_lc = "agents-remember", "260610_browser-dashboard", "L-series-dashboard"
    write_text(
        out / "tasks" / series_repo / series_task / "contract.md",
        contract_md(out, series_repo, series_task, lifecycle_id=series_lc, kind="master", closeout="completed", cleanup="pending"),
    )
    subs = [
        ("01", "lifecycle-event-gate-design", 4, 4),
        ("02", "lifecycle-tools-and-events", 6, 6),
        ("03", "observer-projection", 5, 5),
        ("04", "serving-layer", 3, 3),
        ("05", "cockpit-v1", 7, 10),
    ]
    sub_refs = [{"number": n, "name": nm, "status": "Completed" if d >= t else "inProgress"} for n, nm, d, t in subs]
    write_json(out / "tasks" / series_repo / series_task / f"{series_task}.json", master_doc(series_repo, series_task, sub_refs))
    for n, nm, d, t in subs:
        write_json(out / "tasks" / series_repo / series_task / f"{n}_{nm}.json", subtask_doc(series_repo, series_task, n, nm, series_lc, done=d, total=t))

    # --- ~8 event-backed lifecycles (the active/fleeting variety + the attention queue) -------
    logs = out / "logs" / "observer" / "lifecycles"

    def log(lc: str, events: list[dict]) -> None:
        write_text(logs / lc / "events.jsonl", "\n".join(json.dumps(e) for e in events) + "\n")

    # persistent active (promoted into a worktree); 2 running, 2 blocked gates
    for lc, repo, phase, blocked in [
        ("L-run-build", "agents-remember", "build", False),
        ("L-run-research", "device-management", "reframe-research", False),
        ("L-blocked-plan", "agents-remember-md", "reframe-research", True),
        ("L-blocked-rebase", "device-management", "build", True),
    ]:
        enc = (out / "tasks" / repo / f"active-{lc}" / "contract.md").as_posix()
        write_text(out / "tasks" / repo / f"active-{lc}" / "contract.md", contract_md(out, repo, f"active-{lc}", lifecycle_id=lc, cleanup="pending"))
        write_json(out / "tasks" / repo / f"active-{lc}" / f"active-{lc}.json", light_doc(repo, f"active-{lc}", lc, status="inProgress", done=2, total=5))
        evs = [
            event("lifecycle.started", lc, data_phase="request", fleeting=True, phase="request"),
            event("lifecycle.promoted", lc, trust="approved", actor="developer", scope=repo, enclosure=enc, repoId=repo),
            event("lifecycle.phase-changed", lc, trust="declared", actor="model", phase=phase),
            event("tool.completed", lc, actor="model", tool="grepai_search", tokens=1400),
        ]
        if blocked:
            q = "Approve the plan?" if "plan" in lc else "Rebase on main — conflict. Resolve?"
            evs.append(event("lifecycle.blocked", lc, actor="model", ask={"kind": "gate", "question": q}))
        log(lc, evs)

    # fleeting (no worktree) — bare-bones entries
    log("L-fleeting-1", [event("lifecycle.started", "L-fleeting-1", data_phase="trust-checkpoint", fleeting=True, phase="trust-checkpoint")])
    log("L-fleeting-2", [
        event("lifecycle.started", "L-fleeting-2", fleeting=True, phase="request"),
        event("lifecycle.phase-changed", "L-fleeting-2", trust="declared", actor="model", phase="reframe-research"),
        event("tool.completed", "L-fleeting-2", actor="model", tool="cgc_symbol_search", tokens=600),
    ])
    # completed (terminal)
    for lc, repo in [("L-done-1", "ctec-firmware"), ("L-done-2", "helpdesk-portal")]:
        enc = (out / "tasks" / repo / f"active-{lc}" / "contract.md").as_posix()
        write_text(out / "tasks" / repo / f"active-{lc}" / "contract.md", contract_md(out, repo, f"active-{lc}", lifecycle_id=lc, closeout="completed", integration="completed", cleanup="completed"))
        log(lc, [
            event("lifecycle.started", lc, fleeting=True, phase="request"),
            event("lifecycle.promoted", lc, trust="approved", actor="developer", scope=repo, enclosure=enc, repoId=repo),
            event("lifecycle.phase-changed", lc, trust="declared", actor="model", phase="close"),
            event("lifecycle.ended", lc, actor="developer", trust="approved", outcome="completed"),
        ])

    # --- provider current-state (workspace) + per-worktree stacks ----------------------------
    write_json(
        out / "logs" / "providers" / "status" / "workspace" / "projects" / "current.json",
        {
            "checkedAt": ts(),
            "kind": "provider-current-state",
            "ok": True,
            "instance": {"id": "projects", "scope": "workspace"},
            "providers": {
                "codegraphcontext-code": {"id": "codegraphcontext-code", "state": "ready", "ok": True, "watcherUp": True, "indexingState": "indexed"},
                "grepai-memory": {"id": "grepai-memory", "state": "ready", "ok": True, "watcherUp": True, "indexingState": "indexing"},
            },
        },
    )
    # per-worktree provider stacks (note-03 surface 4) — ready for the per-worktree read fix
    for repo, task in [("agents-remember", "260610_browser-dashboard"), ("device-management", "active-L-run-research")]:
        write_json(
            out / "worktrees" / repo / f"{task}-ar" / "provider-runtime" / "provider-state.json",
            {
                "schema": "ar-worktree-provider-state/v1",
                "repoName": repo,
                "worktreeGroup": (out / "worktrees" / repo / f"{task}-ar").as_posix(),
                "codeWorktree": (out / "worktrees" / repo / f"{task}-ar" / task).as_posix(),
                "memoryWorktree": (out / "worktrees" / repo / f"{task}-ar" / f"memory-{task}").as_posix(),
                "isolatedProviderSettings": {"providers": ["codegraphcontext-code", "grepai-memory"]},
            },
        )

    # setup progress (surface 3): one ok, one failed (failed-setup attention)
    write_json(
        out / "worktrees" / "agents-remember" / "260610_browser-dashboard-ar" / "provider-runtime" / "setup-progress.json",
        {"schema": "ar-worktree-setup-progress/v1", "state": "ok", "completedPhases": ["seed", "refresh"], "currentPhase": None, "heartbeatAt": ts()},
    )
    write_json(
        out / "worktrees" / "device-management" / "active-L-run-research-ar" / "provider-runtime" / "setup-progress.json",
        {"schema": "ar-worktree-setup-progress/v1", "state": "ready-with-failed-phases", "completedPhases": ["seed"], "failedPhases": ["cgc-index"], "currentPhase": {"provider": "codegraphcontext", "action": "index"}, "heartbeatAt": ts()},
    )

    # --- memory ledgers + drift snapshots (memory mirror + actionable-drift attention) -------
    for repo, rows in [("agents-remember", 95), ("agents-remember-md", 60), ("device-management", 22)]:
        write_text(out / "memory-repos" / f"ar-{repo}" / "memory.md", ledger_md(repo, rows))
    write_json(out / "logs" / "observer" / "drift" / "agents-remember.json", drift_snapshot("agents-remember", current=372, drifted=4, missing=2))
    write_json(out / "logs" / "observer" / "drift" / "device-management.json", drift_snapshot("device-management", current=140, drifted=0, missing=0))

    # --- self-check: every contract + task doc validates -------------------------------------
    contracts = list(out.glob("tasks/*/*/contract.md"))
    for c in contracts:
        load_contract(c)
    docs = list(out.glob("tasks/*/*/*.json"))
    for d in docs:
        TaskDocument.model_validate(json.loads(d.read_text()))
    print(f"rich sim fixture written to {out}")
    print(f"  contracts: {len(contracts)}  task docs: {len(docs)}  event logs: {len(list(logs.glob('*/events.jsonl')))}")


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "sim-rich"
    main(target.resolve())
