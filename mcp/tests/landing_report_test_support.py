"""Constants, helpers and scratch fixtures of the landing-report cases (MIK-R120@v1).

``test_landing_report.py`` holds the report itself -- what a card over the measure cuts,
what an addition outside the knowledge is, the role texts that carry the rules-4/5
sentences -- and ``test_landing_report_closeout.py`` holds the real gate, closeout and
checklist populations. Both import this module, so the scratch leaf pairs and the harmless
probes have one home.
"""

from __future__ import annotations

import hashlib
import sys
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

from agents_remember.application.knowledge_gate.gate import (
    GateTrees,
)
from agents_remember.application.knowledge_worklist.leaf import CandidateTrees
from agents_remember.application.memory_quality.converted_base import (
    leaf_base_port,
)
from agents_remember.memory_quality import landing_report
from agents_remember.memory_quality.check import DriftCheckContext
from agents_remember.memory_quality.curator_checklist import (
    CuratorChecklist,
)
from agents_remember.memory_quality.landing_report import LeafBaseRequest
from agents_remember.models.knowledge_files.canonical import canonical_text
from agents_remember.models.lifecycles.memory_candidate import (
    MemoryCandidatePairIdentity,
)
from agents_remember.worktrees.worktree_contract import load_contract
from test_worktree_support import git, init_repo

LAYOUT_MARKER = '{\n  "conversion": "1",\n  "schema": "ar-memory-layout/v2"}\n'
SIDECAR = '{"schema": "ar-onboarding-file/v1", "path": "%s", "references": {}}\n'

CURATOR_SENTENCES = (
    "the handover names the change of this task that made the removed text untrue",
    "Text is never removed to clear a finding of the quality check",
    "never into the memory worktree",
)
CURATOR_SENTENCE_EQUIVALENTS = {
    CURATOR_SENTENCES[
        0
    ]: "For every quality-check cut card, name in the handover this task's change that made its removed text untrue.",
}


def _curator_landing_missing(text: str) -> list[str]:
    reading = " ".join(text.replace("**", "").split())
    return [
        sentence
        for sentence in CURATOR_SENTENCES
        if not any(
            " ".join(variant.split()) in reading
            for variant in (sentence, CURATOR_SENTENCE_EQUIVALENTS.get(sentence, sentence))
        )
    ]


REVIEWER_SENTENCES = (
    "text removed\n   without a named change is a finding",
    "an added file outside the knowledge without a place in the task's requirement",
)


def _report(
    candidate_cards: Mapping[str, bytes],
    base_cards: Mapping[str, bytes] | None,
    **fields: Any,
) -> dict[str, Any]:
    return landing_report.check_landing_report(
        landing_report.LandingReportInputs(
            candidate_cards=candidate_cards, base_cards=base_cards, **fields
        )
    )


def _commit_all(repo: Path, message: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", message)
    return git(repo, "rev-parse", "HEAD")


def _checklist(
    report_path: Path,
    report_only: list[dict[str, Any]],
    *,
    repair_findings: list[dict[str, Any]] | None = None,
    commit_owned_findings: list[dict[str, Any]] | None = None,
    finding_count: int = 0,
) -> CuratorChecklist:
    return CuratorChecklist(
        report_path=report_path,
        repo_id="repo-a",
        code_root=report_path.parent / "code",
        onboarding_root=report_path.parent / "memory" / "onboarding",
        pair_identity=MemoryCandidatePairIdentity(
            repoId="repo-a",
            contractPath="/coordination/tasks/repo-a/task/series-contract.md",
            contractDigest="7" * 64,
            codeRoot="/code",
            memoryRoot="/memory",
            codeSourceBranch="ar/series",
            codeWorkBranch="ar/leaf",
            codeBaseCommit="1" * 40,
            memorySourceBranch="ar/series",
            memoryWorkBranch="ar/leaf",
            memoryBaseCommit="2" * 40,
            onboardingRoot="/memory/onboarding",
            ledgerPath="memory.md",
        ),
        code_candidate_tree="c" * 40,
        memory_candidate_tree="d" * 40,
        quality={"findingCount": finding_count},
        repair_findings=[] if repair_findings is None else repair_findings,
        commit_owned_findings=[] if commit_owned_findings is None else commit_owned_findings,
        missing_onboarding={"missing": [], "missingCount": 0},
        stale_route_indexes=[],
        source_candidates=(),
        drift_rows=[],
        report_only_findings=report_only,
    )


def _reviewer_landing_roots() -> list[Path]:
    roots = {REPO_ROOT / "skills"}
    roots.update(
        path.parent.parent.parent
        for path in REPO_ROOT.glob("**/skills/l-01-agent-lifecycles/roles/reviewer.md")
        if ".venv" not in path.parts
    )
    # MIK-R196 rule 7: the canonical tree and its packaged serving copy are the only two
    # deliveries of the corpus. The eight harness mirrors are gone, so the equality this
    # test can still claim is that both surviving copies carry the sentences.
    assert len(roots) == 2
    return sorted(roots)


def _leaf_pair(root: Path) -> dict[str, Any]:
    """A converted scratch leaf pair: memory and code git repos at one base."""

    memory = root / "memory"
    code = root / "code"
    (memory / "knowledge").mkdir(parents=True)
    (memory / "onboarding").mkdir(parents=True)
    code.mkdir(parents=True)
    (memory / "knowledge" / "layout.json").write_text(LAYOUT_MARKER, encoding="utf-8")
    (memory / "onboarding" / "a.py.md").write_text(
        "# Card\n\n" + "".join(f"line {index}\n" for index in range(40)), encoding="utf-8"
    )
    (memory / "onboarding" / "a.py.json").write_text(SIDECAR % "a.py", encoding="utf-8")
    (memory / "notes").mkdir(exist_ok=True)
    (memory / "notes" / "kept.txt").write_text("older base file\n", encoding="utf-8")
    (code / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
    init_repo(memory)
    init_repo(code)
    _commit_all(memory, "base")
    _commit_all(code, "base")
    return {
        "memory": memory,
        "code": code,
        "memory_base": git(memory, "rev-parse", "HEAD").strip(),
        "code_base": git(code, "rev-parse", "HEAD").strip(),
    }


def _leaf_port(pair: dict[str, Any], memory_base: str | None = None):  # type: ignore[no-untyped-def]
    return leaf_base_port(
        LeafBaseRequest(
            memory_repository=pair["memory"],
            base_commit=pair["memory_base"] if memory_base is None else memory_base,
            code_repository=pair["code"],
            code_base_commit=pair["code_base"],
            cache_directory=None,
        )
    )


def _landing_counts(result: dict[str, Any]) -> tuple[int, int]:
    return int(result["cutCardCount"]), int(result["addedFileCount"])


def _rename_pair(root: Path) -> dict[str, Any]:
    """A scratch leaf pair whose code worktree renames a source with an edit."""

    pair = _leaf_pair(root)
    code = pair["code"]
    old_source = "\n".join(f"VALUE_{index} = {index}" for index in range(20)) + "\n"
    (code / "old.py").write_text(old_source, encoding="utf-8")
    memory = pair["memory"]
    (memory / "onboarding" / "old.py.md").write_text(
        "# Card\n\n" + "".join(f"line {index}\n" for index in range(40)), encoding="utf-8"
    )
    (memory / "onboarding" / "old.py.json").write_text(SIDECAR % "old.py", encoding="utf-8")
    _commit_all(code, "add old source")
    _commit_all(memory, "add old card")
    pair["code_base"] = git(code, "rev-parse", "HEAD").strip()
    pair["memory_base"] = git(memory, "rev-parse", "HEAD").strip()
    edited = old_source.replace("VALUE_0 = 0", "VALUE_0 = 1")
    git(code, "mv", "old.py", "new.py")
    (code / "new.py").write_text(edited, encoding="utf-8")
    (memory / "onboarding" / "old.py.md").unlink()
    (memory / "onboarding" / "old.py.json").unlink()
    (memory / "onboarding" / "new.py.md").write_text("# Card\n\nshort\n", encoding="utf-8")
    (memory / "onboarding" / "new.py.json").write_text(SIDECAR % "new.py", encoding="utf-8")
    return pair


def _operation_context(root: Path, memory: Path) -> Any:
    coordination = root / "coordination"
    temporary = root / "temp"
    coordination.mkdir(parents=True, exist_ok=True)
    temporary.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        onboarding_root=memory / "onboarding",
        memory_root=memory,
        coordination_root=coordination,
        temp_root=temporary,
    )


def _operation_pair(root: Path, landed: bool) -> tuple[dict[str, Any], Any]:
    """A converted pair with (or without) one cut and one added file."""

    pair = _leaf_pair(root)
    memory = pair["memory"]
    if landed:
        (memory / "onboarding" / "a.py.md").write_text("# Card\n\nshort\n", encoding="utf-8")
        (memory / "notes" / "working.txt").write_text("scratch\n", encoding="utf-8")
    context = _operation_context(root, memory)
    drift = DriftCheckContext(
        code_repository_root=pair["code"],
        context=context,
        knowledge_base=None,
        landing_base=_leaf_port(pair),
    )
    return pair, drift


def _operation_contract(root: Path, pair: dict[str, Any]) -> Path:
    """A minimal loadable leaf contract binding the fixture pair's bases."""

    path = root / "series-contract.md"
    path.write_text(
        "---\n"
        "schema: ar-series-contract/v1\n"
        "schemaVersion: 1.0\n"
        "kind: leaf\n"
        "task_id: TEST_LANDING\n"
        "task_name: test_landing\n"
        "repo_name: agents-remember\n"
        "workflow_kind: light-task\n"
        "memory_mode: external\n"
        "coordination:\n"
        f"  root: {root / 'coordination'}\n"
        f"  task_root: {root}\n"
        f"  task_artifact: {root / 'task.md'}\n"
        f"  worktree_group: {root}\n"
        "  leaf_id: TEST-L120\n"
        "code:\n"
        f"  repo_path: {pair['code']}\n"
        "  source_branch: main\n"
        "  work_branch: leaf\n"
        f"  base_commit: {pair['code_base']}\n"
        f"  worktree: {pair['code']}\n"
        "memory:\n"
        "  mode: external\n"
        f"  repo_path: {pair['memory']}\n"
        "  source_branch: main\n"
        "  work_branch: leaf\n"
        f"  base_commit: {pair['memory_base']}\n"
        f"  worktree: {pair['memory']}\n"
        f"  ledger: {pair['memory'] / 'memory.md'}\n"
        "human_review:\n"
        "  status: pending-review\n"
        "  approved_for_commit: no\n"
        "closeout:\n"
        "  status: not-started\n"
        "integration:\n"
        "  status: not-started\n"
        "  cleanup: pending\n"
        "---\n",
        encoding="utf-8",
    )
    return path


def _gate_trees(pair: dict[str, Any]) -> Any:
    return GateTrees(
        code_repository=pair["code"],
        code_tree=git(pair["code"], "rev-parse", "HEAD").strip(),
        memory_repository=pair["memory"],
        memory_tree=git(pair["memory"], "rev-parse", "HEAD").strip(),
        validation_bases=(pair["memory_base"],),
        base_code_commit=pair["code_base"],
        cache_directory=None,
    )


def _clean_pair(root: Path) -> dict[str, Any]:
    """A validator-clean scratch leaf pair: canonical files, anchored sidecar."""

    pair = _leaf_pair(root)
    memory, code = pair["memory"], pair["code"]
    source = (code / "a.py").read_bytes()
    blob = git(code, "hash-object", "a.py")
    sidecar = {
        "schema": "ar-onboarding-file/v1",
        "path": "a.py",
        "references": {},
        "realizes": [
            {
                "id": "RLZ-000001",
                "role": "support",
                "rationale": ("The card documents the module whose value anchors the fixture."),
                "invariant": "INV-000001",
                "anchor": {
                    "locator": {"kind": "file"},
                    "blob": blob,
                    "content": "sha256:" + hashlib.sha256(source).hexdigest(),
                },
            }
        ],
    }
    (memory / "onboarding" / "a.py.json").write_text(canonical_text(sidecar))
    (memory / "knowledge" / "layout.json").write_text(
        canonical_text({"conversion": "1", "schema": "ar-memory-layout/v2"})
    )
    (memory / "knowledge" / "invariants").mkdir(exist_ok=True)
    record = {
        "schema": "ar-invariant/v1",
        "id": "INV-000001",
        "origin": {"task": "fixture"},
        "revision": 1,
        "status": "accepted",
        "statement": "The fixture module holds its value.",
        "applicability": "The scratch fixture only.",
        "conditions": [],
        "exclusions": [],
        "supersedes": [],
        "admission": {
            "criteria": ["prevents_costly_mistake"],
            "justification": (
                "Without this record a worker could delete the value the card documents."
            ),
        },
    }
    (memory / "knowledge" / "invariants" / "INV-000001-fixture.json").write_text(
        canonical_text(record)
    )
    _commit_all(memory, "clean base")
    pair["memory_base"] = git(memory, "rev-parse", "HEAD").strip()
    return pair


def _land(pair: dict[str, Any]) -> None:
    """Cut the fixture card and add one scratch file, uncommitted."""

    memory = pair["memory"]
    (memory / "onboarding" / "a.py.md").write_text("# Card\n\nshort\n", encoding="utf-8")
    (memory / "notes" / "working.txt").write_text("scratch\n", encoding="utf-8")


def _paired_gate_contract(root: Path, pair: dict[str, Any]) -> Any:
    """A loadable leaf contract whose memory line pairs the fixture code base."""

    git(
        pair["memory"],
        "commit",
        "--allow-empty",
        "-qm",
        f"pair code base\n\nCode-Commit: {pair['code_base']}",
    )
    pair["memory_base"] = git(pair["memory"], "rev-parse", "HEAD").strip()
    return load_contract(_operation_contract(root, pair))


def _gate_candidate(pair: dict[str, Any]) -> Any:
    return CandidateTrees(
        code=git(pair["code"], "rev-parse", "HEAD^{tree}").strip(),
        memory=git(pair["memory"], "rev-parse", "HEAD^{tree}").strip(),
    )
