"""Fresh durable topology and Codex configuration for one clean-room run."""

from __future__ import annotations

import json
import re
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path

from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks import TaskDocument, write_task_doc
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    default_contract,
    write_contract,
)

# Sol's Responses Lite path intentionally omits the top-level tool collection for custom
# Responses providers.  The clean-room provider scripts function selection, so it uses the
# current non-Lite model profile while the installed Codex version remains the subject under test.
CODEX_MODEL = "gpt-5.5"


@dataclass(frozen=True)
class E2EFixture:
    root: Path
    repository_root: Path
    coordination_root: Path
    workspace_repo: Path
    leaf_worktree: Path
    authority_path: Path
    codex_home: Path
    sprint: TaskDocumentRef
    master: TaskDocumentRef
    leaf: TaskDocumentRef
    architect_brief: str


def create_fixture(root: Path, *, repository_root: Path, responses_base_url: str) -> E2EFixture:
    coordination = root / "coordination"
    workspace_repo = root / "workspace" / "repo"
    codex_home = root / "codex-home"
    for path in (coordination, workspace_repo, codex_home):
        path.mkdir(parents=True, exist_ok=True)

    source_commit = _initialize_repository(workspace_repo)
    sprint, master, leaf = _write_topology(coordination)
    contract = default_contract(
        ContractTask(
            name="master",
            repo_name="repo",
            coordination_root=coordination,
            workflow_kind="light-task",
            memory_mode="disabled",
        ),
        leaf=LeafIdentity(worktree_name="leaf-1", leaf_id="leaf-1"),
        code=RepoBranchPlan(
            repo_path=workspace_repo,
            source_branch="ar/super",
            work_branch="ar/leaf-1",
            base_commit=source_commit,
        ),
    )
    contract.code_worktree.parent.mkdir(parents=True, exist_ok=True)
    _git(workspace_repo, "worktree", "add", contract.code_worktree.as_posix(), "ar/leaf-1")
    write_contract(contract.contract_path, contract)

    dashboard_port = _unused_port()
    authority = root / "mcp-authority.json"
    authority.write_text(
        json.dumps(
            {
                "version": 1,
                "coordinationRoot": coordination.as_posix(),
                "workspaceRoot": (root / "workspace").as_posix(),
                "transcriptRoot": (coordination / "logs" / "mcp").as_posix(),
                "repositories": {"repo": {}},
                "providers": {},
                "dashboard": {"autoStart": True, "port": dashboard_port},
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    _write_agentic_settings(coordination)

    architect_brief = compile_architect_brief(repository_root, coordination)
    _write_codex_config(
        codex_home,
        authority_path=authority,
        responses_base_url=responses_base_url,
    )
    return E2EFixture(
        root=root,
        repository_root=repository_root,
        coordination_root=coordination,
        workspace_repo=workspace_repo,
        leaf_worktree=contract.code_worktree,
        authority_path=authority,
        codex_home=codex_home,
        sprint=sprint,
        master=master,
        leaf=leaf,
        architect_brief=architect_brief,
    )


def compile_architect_brief(repository_root: Path, coordination_root: Path) -> str:
    template_path = (
        repository_root / "skills" / "l-01-agent-lifecycles" / "templates" / "architect-brief.md"
    )
    template = template_path.read_text(encoding="utf-8")
    try:
        body = template.split("```md\n", 1)[1].split("\n```", 1)[0]
    except IndexError as exc:
        raise RuntimeError(
            f"architect brief template has no canonical md body: {template_path}"
        ) from exc
    replacements = {
        "<sprint-id>": "ARSPAWN-E2E-SPRINT",
        "<sprint title>": "Ambient role-chat acceptance",
        "<repo-id>": "repo",
        "<repository>": "repo",
        "<repo-relative canonical sprint task.json path>": "sprint/task.json",
        "<status plus exact master document refs>": "inProgress; repo:master/task.json",
        "<first leaf ref and status, or exact reason no leaf exists yet>": (
            "repo:master/leaf-1.json; inProgress"
        ),
        "<canonical requirement index and developer approval citation | none\n"
        "  yet; compile and obtain approval before task decomposition>": (
            "L5-R1..R7 in the approved ambient-role-chat master"
        ),
        "<executionGraph/nature/priority judgment refs | not yet ruled>": (
            "sprint executionGraph; organizational master; approved fixture priority"
        ),
        "<the developer's exact current objective, without inventing scope>": (
            "Prove ambient dispatch and canonical replacement-aware messaging end to end"
        ),
        "<durable sprint/task decision citations | none>": "approved L5 task document",
        "<durable open-question refs | none>": "none",
        "<canonical task/contract/report refs | none>": "fixture task tree and leaf enclosure",
        "<approved boundaries with citations>": (
            "real Codex and public MCP tools; deterministic model responses only"
        ),
        "<context facts and paths>": coordination_root.as_posix(),
        "<current result or exact blocker>": "fresh fixture; no drift",
        "<configured status/stack key | not configured>": "not configured",
        "<read_ar_files / semantic / relationship / intent evidence>": (
            "canonical fixture task documents"
        ),
        "<exact work | none>": "none",
    }
    for placeholder, value in replacements.items():
        body = body.replace(placeholder, value)
    unresolved = re.findall(r"<.*?>", body, flags=re.DOTALL)
    if unresolved:
        raise RuntimeError(f"architect brief has unresolved placeholders: {unresolved!r}")
    return body


def _task_doc(**values: object) -> TaskDocument:
    return TaskDocument.model_validate(
        {
            "id": values.pop("id"),
            "slug": values.pop("slug"),
            "title": values.pop("title"),
            "kind": values.pop("kind"),
            "repo": "repo",
            "createdAt": "2026-08-30T00:00",
            **values,
        }
    )


def _write_topology(
    coordination_root: Path,
) -> tuple[TaskDocumentRef, TaskDocumentRef, TaskDocumentRef]:
    task_root = coordination_root / "tasks" / "repo"
    write_task_doc(
        task_root / "sprint",
        _task_doc(
            id="ARSPAWN-E2E-SPRINT",
            slug="sprint",
            title="Ambient role-chat acceptance",
            kind="master",
            status="inProgress",
            orchestrates=["master"],
            integrationBranch="ar/super",
            executionGraph={
                "nodes": [{"repository": "repo", "path": "master/task.json"}],
                "edges": [],
            },
        ),
    )
    write_task_doc(
        task_root / "master",
        _task_doc(
            id="MASTER",
            slug="master",
            title="Fixture master",
            kind="master",
            status="inProgress",
            executionNature="organizational",
            subTasks=[
                {
                    "number": "leaf-1",
                    "name": "Fixture leaf",
                    "file": "leaf-1.md",
                    "status": "inProgress",
                }
            ],
        ),
    )
    write_task_doc(
        task_root / "master",
        _task_doc(
            id="leaf-1",
            slug="leaf-1",
            title="Fixture leaf",
            kind="subTask",
            status="inProgress",
            master="task.md",
        ),
    )
    return (
        TaskDocumentRef(repository="repo", path="sprint/task.json"),
        TaskDocumentRef(repository="repo", path="master/task.json"),
        TaskDocumentRef(repository="repo", path="master/leaf-1.json"),
    )


def _initialize_repository(repo: Path) -> str:
    _git(repo, "init", "-b", "ar/super")
    _git(repo, "config", "user.email", "arspawn-e2e@example.invalid")
    _git(repo, "config", "user.name", "ARSPAWN E2E")
    (repo / "README.md").write_text("# ARSPAWN E2E\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "fixture base")
    commit = _git(repo, "rev-parse", "HEAD")
    _git(repo, "branch", "ar/leaf-1", "ar/super")
    _git(repo, "update-ref", "refs/remotes/origin/ar/super", commit)
    _git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/ar/super")
    return commit


def _write_agentic_settings(coordination_root: Path) -> None:
    settings_path = coordination_root / "system" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    roles = {
        role: {"harness": "codex", "model": CODEX_MODEL, "effort": "low"}
        for role in ("architect", "orchestrator", "manager", "worker")
    }
    settings_path.write_text(
        json.dumps(
            {
                "orchestration": {
                    "roles": roles,
                    "agentNotifier": {
                        "enabled": True,
                        "intervalSeconds": 0.25,
                        "staleCutoffSeconds": 120,
                        "redeliverBudget": 20,
                        "escalationBudget": 20,
                    },
                }
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_codex_config(
    codex_home: Path,
    *,
    authority_path: Path,
    responses_base_url: str,
) -> None:
    quote = json.dumps
    config = "\n".join(
        [
            f'model = "{CODEX_MODEL}"',
            'model_provider = "ar_e2e"',
            'model_reasoning_effort = "low"',
            'approval_policy = "never"',
            'sandbox_mode = "danger-full-access"',
            "disable_response_storage = true",
            "",
            "[model_providers.ar_e2e]",
            'name = "ARSPAWN deterministic Responses"',
            f"base_url = {quote(responses_base_url)}",
            'wire_api = "responses"',
            "requires_openai_auth = false",
            "request_max_retries = 0",
            "stream_max_retries = 0",
            "supports_websockets = false",
            "",
            "[mcp_servers.agents_remember_candidate]",
            f"command = {quote('/opt/ar-venv/bin/python')}",
            "args = ["
            + ", ".join(
                quote(value)
                for value in ("-m", "agents_remember.mcp", "--config", authority_path.as_posix())
            )
            + "]",
            'env_vars = ["AR_HOSTED_SESSION_ID", "AR_SPAWN_ROLE", "TMUX_TMPDIR"]',
            "startup_timeout_sec = 120",
            "tool_timeout_sec = 120",
            "",
            "[mcp_servers.agents_remember_candidate.env]",
            f"CODEX_HOME = {quote(codex_home.as_posix())}",
            'OPENAI_API_KEY = "arspawn-e2e-non-secret"',
            'PYTHONIOENCODING = "utf-8"',
            "",
        ]
    )
    (codex_home / "config.toml").write_text(config, encoding="utf-8")


def _unused_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()
