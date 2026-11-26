"""Master-end dependent-document staleness classification over real Git repositories.

Covers standing-base sync walks, own-leaf attribution and confirmation clearing, post-landing
parent repair checks, fail-closed uncomputable/unverifiable edges, checkpoints, and per-kind output.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from agents_remember.application.knowledge_gate import document_staleness as ds
from agents_remember.application.worktree_services import build_default_worktree_services
from agents_remember.models.knowledge_files import canonical_text
from agents_remember.models.knowledge_files import documents as kf_docs
from agents_remember.models.knowledge_files.records import DependentRecord
from agents_remember.models.task_document import DocStatus
from agents_remember.tasks import SubTaskRef, TaskDocument, write_task_doc
from agents_remember.worktrees import knowledge_gate as kg
from agents_remember.worktrees import services as wt_services
from agents_remember.worktrees import worktree_contract as wc
from agents_remember.worktrees.integration import integration_ref_transaction as irt
from agents_remember.worktrees.modules import args as wt_args
from agents_remember.worktrees.modules import integration_gate, record_landing
from agents_remember.worktrees.modules import models as wt_models

type ObjMap = Mapping[str, object]
type DocTuple = tuple[str, str, str]
type Pair4 = tuple[str, str, str, str]
type LinkMap = Mapping[str, Sequence[ObjMap]] | None
type EvalOut = tuple[ds.DocumentStaleness, str | None, dict[str, object] | None]

DOC_A, ID_A, SRC_A = "docs/guide.md", "DPD-7K3F9Q", "src/tool.py"
WORK, TS = "ar/master", "2026-10-01T00:00:00+00:00"
_LOC = {"kind": "symbol", "name": "missing_symbol"}
_BAD = {"path": "notes.txt", "blob": "a" * 40, "content": "sha256:" + "b" * 64, "locator": _LOC}
BAD_ANCHOR: dict[str, object] = {"kind": "anchor", "anchor": _BAD}


@pytest.fixture(autouse=True)
def _bound_services() -> Iterator[None]:
    wt_services.bind_worktree_services(build_default_worktree_services())
    yield
    wt_services.reset_worktree_services()


def _git(repo: Path, *args: str) -> str:
    cmd = ["git", "-c", "user.email=test@example.invalid", "-c", "user.name=Test", *args]
    return subprocess.run(cmd, cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def _write_json(root: Path, rel: str, payload: ObjMap) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(canonical_text(dict(payload)), encoding="utf-8")


def _commit_memory(repo: Path, msg: str) -> str:
    mp = repo / kf_docs.LAYOUT_MARKER_PATH
    marker: dict[str, object] = json.loads(mp.read_bytes())
    links = kf_docs.dependent_link_index(
        (p.relative_to(repo).as_posix(), DependentRecord.model_validate_json(p.read_bytes()))
        for p in sorted(repo.glob("knowledge/dependents/*.json"))
    )
    marker.pop("dependent_links", None)
    if links is not None:
        marker["dependent_links"] = links
    mp.write_text(canonical_text(marker), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", msg)
    return _git(repo, "rev-parse", "HEAD")


def _dep(rec_id: str, doc: str, links: Sequence[ObjMap], conf: ObjMap) -> dict[str, object]:
    head: dict[str, object] = {"schema": "ar-dependent/v1", "id": rec_id, "revision": 1}
    tail: dict[str, object] = {"confirmed": dict(conf), "origin": {"task": "MASTER", "leaf": "L0"}}
    return {**head, "document": doc, "stands_on": [dict(x) for x in links], **tail}


def _sec(ans: ObjMap, key: str) -> dict[str, object]:
    raw = ans[key]
    assert isinstance(raw, dict)
    return dict(raw)


def _note(memory: Path, branch: str, msg: str) -> str:
    _git(memory, "switch", branch)
    (memory / "memory.md").write_text(f"# {msg}\n", encoding="utf-8")
    return _commit_memory(memory, msg)


def _init_repos(
    root: Path, src_branch: str, docs: Sequence[DocTuple], links_map: LinkMap
) -> tuple[Path, Path, str, str]:
    code, mem = root / "code", root / "memory"
    for r in (code, mem):
        r.mkdir(parents=True, exist_ok=True)
        _git(r, "init", "-q", "-b", src_branch)
    for _, doc, src in docs:
        for rel, body in ((src, "v1\n"), (doc, f"# {doc}\n")):
            (code / rel).parent.mkdir(parents=True, exist_ok=True)
            (code / rel).write_text(body, encoding="utf-8")
    _git(code, "add", "-A")
    _git(code, "commit", "-q", "-m", "initial code")
    cb = _git(code, "rev-parse", src_branch)
    _git(code, "branch", WORK, cb)
    (mem / "memory.md").write_text("# Memory\n", encoding="utf-8")
    layout = {"schema": "ar-memory-layout/v2", "conversion": "1"}
    _write_json(mem, kf_docs.LAYOUT_MARKER_PATH, layout)
    conf = {"task": "L0", "role": "curator", "commit": cb}
    for rec_id, doc, src in docs:
        default = [{"kind": "file", "path": src, "blob": _git(code, "hash-object", src)}]
        links = list(links_map[rec_id]) if links_map and rec_id in links_map else default
        rel = kf_docs.record_path("dependent", rec_id, rec_id.lower())
        _write_json(mem, rel, _dep(rec_id, doc, links, conf))
    mb = _commit_memory(mem, "initial memory")
    _git(mem, "branch", WORK, mb)
    return code, mem, cb, mb


def _write_enclosure(h: _Harness, leaf_id: str, pair: Pair4, integrated: bool) -> None:
    slug, (cb, mb, cl, ml) = leaf_id.lower(), pair
    coord, cp = h.task_root.parents[2], h.contract.contract_path
    task = wc.ContractTask("master", "repo", coord, "light-task", "external", "master", cp)
    c_plan = wc.RepoBranchPlan(h.code, WORK, f"ar/{slug}", cb)
    m_plan = wc.RepoBranchPlan(h.memory, WORK, f"ar/{slug}", mb)
    leaf = wc.default_contract(
        task, leaf=wc.LeafIdentity(slug, leaf_id), code=c_plan, memory=m_plan
    )
    if integrated:
        leaf = replace(
            leaf,
            closeout_status="completed",
            code_commit=cl,
            memory_content_commit=ml,
            integration_status="completed",
            integrated_code_commit=cl,
            integrated_memory_content_commit=ml,
        )
    wc.write_contract(leaf.contract_path, leaf)


def _eval(h: _Harness, c: str, m: str) -> EvalOut:
    req = h.end_req(c, m)
    ans = kg.landing_document_answer(h.contract, code_commit=c, memory_commit=m, master_end=True)
    return ds.document_staleness(req, full=True), kg.landing_gate_refusal(req), ans


def _leaf_then_sync(h: _Harness, *, delivery: str = "merge") -> tuple[str, str]:
    h.land_leaf("L1", {SRC_A: "v2-leaf\n"}, "L1 edits source")
    h.merge_pair(h.src_branch, WORK, "parent takes L1", squash=(delivery == "squash"))
    return h.sync()


def _land_and_repair(h: _Harness, origin: str = "since_base") -> tuple[str, str]:
    lc, lm = h._work({SRC_A: "v2\n"}, "master") if origin == "since_base" else _leaf_then_sync(h)
    pc, _ = h.merge_pair(h.src_branch, WORK, "land master")
    h.confirm(h.src_branch, pc)
    return lc, lm


@dataclass
class _Harness:
    """Paired Git repositories, canonical task documents, and contracts for one master."""

    code: Path
    memory: Path
    task_root: Path
    src_branch: str
    contract: wc.WorktreeContract
    subtasks: list[SubTaskRef]

    @classmethod
    def create(
        cls,
        root: Path,
        *,
        src_branch: str = "main",
        docs: Sequence[DocTuple] = ((ID_A, DOC_A, SRC_A),),
        links: LinkMap = None,
    ) -> _Harness:
        code, mem, cb, mb = _init_repos(root, src_branch, docs, links)
        coord = root / "coordination"
        task_root = coord / "tasks" / "repo" / "master"
        task_root.mkdir(parents=True)
        task = wc.ContractTask("master", "repo", coord, "light-task", "external")
        c_plan = wc.RepoBranchPlan(code, src_branch, WORK, cb)
        m_plan = wc.RepoBranchPlan(mem, src_branch, WORK, mb)
        series = wc.default_series_contract(task, code=c_plan, memory=m_plan, task_root=task_root)
        wc.write_contract(series.contract_path, series)
        h = cls(code, mem, task_root, src_branch, series, [])
        h.add_leaf("L0", status="inProgress")
        return h

    def _heads(self, branch: str = WORK) -> tuple[str, str]:
        return _git(self.code, "rev-parse", branch), _git(self.memory, "rev-parse", branch)

    def add_leaf(
        self,
        leaf_id: str,
        *,
        status: DocStatus = "Completed",
        enclosure: Pair4 | None = None,
        integrated: bool = True,
    ) -> None:
        cell = "" if status == "abandoned" else f"{leaf_id.lower()}.json"
        row = SubTaskRef(number=leaf_id, name=f"Leaf {leaf_id}", file=cell, status=status)
        self.subtasks = [*(r for r in self.subtasks if r.number != leaf_id), row]
        rows = [r.model_dump(mode="json") for r in self.subtasks]
        m = {
            "id": "MASTER",
            "slug": "master",
            "title": "MASTER",
            "kind": "master",
            "status": "inProgress",
        }
        write_task_doc(
            self.task_root,
            TaskDocument.model_validate({**m, "repo": "repo", "createdAt": TS, "subTasks": rows}),
        )
        if status != "abandoned":
            lf = {
                "id": leaf_id,
                "slug": leaf_id.lower(),
                "title": leaf_id,
                "kind": "subTask",
                "status": status,
            }
            extra = {
                "repo": "repo",
                "createdAt": TS,
                "objective": "Leaf.",
                "requirements": ["Paired."],
            }
            write_task_doc(self.task_root, TaskDocument.model_validate({**lf, **extra}))
        if enclosure is not None:
            _write_enclosure(self, leaf_id, enclosure, integrated)

    def edit_code(self, branch: str, files: Mapping[str, str], msg: str) -> str:
        _git(self.code, "switch", branch)
        for rel, body in files.items():
            (self.code / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.code / rel).write_text(body, encoding="utf-8")
        _git(self.code, "add", "-A")
        _git(self.code, "commit", "-q", "-m", msg)
        return _git(self.code, "rev-parse", branch)

    def _work(self, files: Mapping[str, str], msg: str) -> tuple[str, str]:
        return self.edit_code(WORK, files, msg), _note(self.memory, WORK, f"{msg} mem")

    def write_dep(
        self, branch: str, links: Sequence[ObjMap], conf: ObjMap, extra: ObjMap | None = None
    ) -> str:
        _git(self.memory, "switch", branch)
        rec_id, doc = str((extra or {}).get("id", ID_A)), str((extra or {}).get("document", DOC_A))
        rel = kf_docs.record_path("dependent", rec_id, rec_id.lower())
        body = {**_dep(rec_id, doc, links, conf), "revision": 2, **(extra or {})}
        _write_json(self.memory, rel, body)
        return _commit_memory(self.memory, f"update {rec_id}")

    def confirm(
        self,
        branch: str,
        ref: str,
        spec: DocTuple = (ID_A, DOC_A, SRC_A),
        *,
        uncommitted: bool = False,
    ) -> str:
        rec_id, doc, src = spec
        blob = _git(self.code, "rev-parse", f"{ref}:{src}")
        conf: dict[str, object] = {
            "task": "L2",
            "role": "curator",
            "commit": ref,
            "statement": "OK.",
        }
        if uncommitted:
            conf["uncommitted"] = True
        link = {"kind": "file", "path": src, "blob": blob}
        return self.write_dep(branch, [link], conf, {"id": rec_id, "document": doc})

    def merge_pair(
        self, target: str, source: str, msg: str, *, squash: bool = False
    ) -> tuple[str, str]:
        out: list[str] = []
        for repo in (self.code, self.memory):
            _git(repo, "switch", target)
            if squash:
                _git(repo, "merge", "--squash", source)
                _git(repo, "commit", "-q", "-m", msg)
            else:
                _git(repo, "merge", "--no-ff", "-m", msg, source)
            out.append(_git(repo, "rev-parse", target))
        return out[0], out[1]

    def sync(self, *, sync_src: str = "contract-line", skip_mem: bool = False) -> tuple[str, str]:
        cs, ms = self._heads(self.src_branch)
        ct, mt = self.merge_pair(WORK, self.src_branch, "sync from parent")
        c, is_def = self.contract, sync_src == "repository-default"
        cb, mb = c.code_base_commit, c.memory_base_commit
        nc, nm = (cb, mb) if is_def else (cs, ms)
        entry = {"syncSource": sync_src, "codeBaseFrom": cb, "memoryBaseFrom": mb}
        entry |= {"codeBaseTo": nc, "memoryBaseTo": nm}
        if is_def:
            entry |= {"codeSourceCommit": cs, "memorySourceCommit": "" if skip_mem else ms}
        log = (*c.sync_log, entry)
        self.contract = replace(c, code_base_commit=nc, memory_base_commit=nm, sync_log=log)
        wc.write_contract(self.contract.contract_path, self.contract)
        return ct, mt

    def land_leaf(self, leaf_id: str, files: Mapping[str, str], msg: str) -> tuple[str, str]:
        cb, mb = self._heads()
        ct, mt = self._work(files, msg)
        self.add_leaf(leaf_id, enclosure=(cb, mb, ct, mt))
        return ct, mt

    def end_req(self, ct: str, mt: str) -> wt_services.LandingGateRequest:
        c = self.contract
        assert c.memory_repo_path is not None
        base = kg.master_document_base(c, code_commit=ct, memory_commit=mt)
        return wt_services.LandingGateRequest(
            memory_repository=c.memory_repo_path,
            memory_commit=mt,
            memory_bases=(c.memory_base_commit,),
            code_repository=self.code,
            code_commit=ct,
            code_base=c.code_base_commit,
            series_contract=c.contract_path.as_posix(),
            master_end=True,
            document_base=base,
        )

    def gate_block(self, ct: str, mt: str, op: str) -> wt_models.WorktreeCommandResult | None:
        pc, pm = self._heads(self.src_branch)
        src = irt.IntegrationSources(pc, pm, False, False)
        return integration_gate.integration_gate_block(
            self.contract, wt_args.WorktreeArgs(), irt.IntegratedCommits(ct, mt), src, operation=op
        )


def test_unlanded_master_work_refuses_since_base(tmp_path: Path) -> None:
    h = _Harness.create(tmp_path)
    ct, mt = h._work({SRC_A: "v2\n"}, "master edit")
    s, refusal, ans = _eval(h, ct, mt)
    assert (s.made_stale_since_base, s.made_stale_by_leaf, s.already_stale) == ((DOC_A,), (), ())
    assert ans is not None and ans["count"] == 1 and "refusedByOwnLeaf" not in ans
    sec = _sec(ans, "refusedSinceBase")
    expected_read = {
        "view": "dependent",
        "staleOnly": True,
        "memoryRoot": "the landing's memory tree",
    }
    assert (
        sec["documents"] == [DOC_A]
        and sec["read"] == expected_read
        and isinstance(sec["remedy"], str)
    )
    assert refusal is not None and "Made stale since the base" in refusal
    assert "The remedy is a task of this master" in refusal


def test_parent_staleness_brought_by_sync_is_named_only(tmp_path: Path) -> None:
    h = _Harness.create(tmp_path)
    h.edit_code(h.src_branch, {SRC_A: "parent-v2\n"}, "parent edit")
    _note(h.memory, h.src_branch, "parent mem")
    ct, mt = h.sync()
    s, refusal, ans = _eval(h, ct, mt)
    assert s.documents == () and s.already_stale == (DOC_A,) and refusal is None
    assert ans is not None and ans["count"] == 0 and ans["alreadyStale"] == [DOC_A]
    named = _sec(ans, "namedOnly")
    assert named["documents"] == [DOC_A] and "read" in named and "remedy" not in named


@pytest.mark.parametrize("delivery", ["merge", "squash"])
def test_own_leaf_staleness_refuses_after_sync(tmp_path: Path, delivery: str) -> None:
    h = _Harness.create(tmp_path)
    ct, mt = _leaf_then_sync(h, delivery=delivery)
    s, refusal, ans = _eval(h, ct, mt)
    assert s.made_stale_since_base == () and s.made_stale_by_leaf_ids == ((DOC_A, ("L1",)),)
    assert ans is not None and ans["count"] == 1
    by_leaf = _sec(ans, "refusedByOwnLeaf")
    assert by_leaf["documents"] == [DOC_A] and by_leaf["leaves"] == {DOC_A: ["L1"]}
    assert "read" in by_leaf and "confirmed.commit" in str(by_leaf["remedy"])
    assert refusal is not None and f"Made stale by this master's own leaf: {DOC_A} (L1)" in refusal
    assert "confirmed.commit descends from the leaf's landed code commit" in refusal


def test_own_leaf_document_confirmed_after_landing_passes(tmp_path: Path) -> None:
    h = _Harness.create(tmp_path)
    ct, _ = _leaf_then_sync(h)
    s, refusal, ans = _eval(h, ct, h.confirm(WORK, ct))
    assert s.count == 0 and s.answer(name_already_stale=True) is None
    assert ans is None and refusal is None


def test_confirmed_after_leaf_then_restaled_by_sync_is_named_only(tmp_path: Path) -> None:
    h = _Harness.create(tmp_path)
    l1_c, l1_m = h.land_leaf("L1", {SRC_A: "v2-leaf\n"}, "L1 edit")
    l2_c = h.edit_code(WORK, {"other.py": "ok\n"}, "L2 code")
    l2_m = h.confirm(WORK, l2_c)
    h.add_leaf("L2", enclosure=(l1_c, l1_m, l2_c, l2_m))
    h.merge_pair(h.src_branch, WORK, "parent merges master")
    h.edit_code(h.src_branch, {SRC_A: "v3-parent\n"}, "parent edits again")
    ct, mt = h.sync()
    s, refusal, ans = _eval(h, ct, mt)
    assert s.documents == () and s.already_stale == (DOC_A,) and refusal is None
    assert ans is not None and ans["count"] == 0 and _sec(ans, "namedOnly")["documents"] == [DOC_A]


def test_deferral_only_update_after_own_leaf_still_refuses(tmp_path: Path) -> None:
    h = _Harness.create(tmp_path)
    oc = h.contract.code_base_commit
    ob = _git(h.code, "rev-parse", f"{oc}:{SRC_A}")
    ct, _ = _leaf_then_sync(h)
    dfr: dict[str, object] = {
        "id": "DFR-7K3F9Q",
        "passage": "Usage",
        "reason": "Deferred.",
        "condition": {"task": "L99"},
    }
    dfr |= {"owner": "docs", "createdAt": TS, "createdByLeaf": "L1"}
    conf = {"task": "L0", "role": "curator", "commit": oc}
    link = {"kind": "file", "path": SRC_A, "blob": ob}
    s, refusal, _ = _eval(h, ct, h.write_dep(WORK, [link], conf, {"deferrals": [dfr]}))
    assert s.made_stale_by_leaf_ids == ((DOC_A, ("L1",)),) and refusal is not None


def test_unreadable_confirmed_commit_makes_end_uncomputable(tmp_path: Path) -> None:
    h = _Harness.create(tmp_path)
    ob = _git(h.code, "rev-parse", f"{h.contract.code_base_commit}:{SRC_A}")
    ct, _ = _leaf_then_sync(h)
    link = {"kind": "file", "path": SRC_A, "blob": ob}
    mt = h.write_dep(WORK, [link], {"task": "L2", "role": "curator", "commit": "deadbeef" * 5})
    s, refusal, ans = _eval(h, ct, mt)
    assert s.uncomputable is not None and ans is not None
    assert ans["state"] == "uncomputable" and refusal is not None


@pytest.mark.parametrize("origin", ["since_base", "own_leaf"])
def test_repaired_on_parent_after_landing_refuses_nothing(tmp_path: Path, origin: str) -> None:
    h = _Harness.create(tmp_path, src_branch="release/next")
    lc, lm = _land_and_repair(h, origin=origin)
    req = record_landing._landing_request(h.contract, h.memory, lc, lm)
    assert not isinstance(req, str), req
    s = ds.document_staleness(req, full=True)
    assert s.documents == () and s.repaired_on_parent == (DOC_A,)
    assert kg.landing_gate_refusal(req) is None
    _, _, ans = _eval(h, lc, lm)
    assert ans is not None and ans["count"] == 0
    read = {"view": "dependent", "memoryRoot": "the parent line's present memory tip"}
    assert _sec(ans, "repairedOnParent") == {"documents": [DOC_A], "read": read}


def test_integration_gate_before_landing_refuses_even_when_parent_current(tmp_path: Path) -> None:
    h = _Harness.create(tmp_path, src_branch="release/next")
    ct, mt = h._work({SRC_A: "v2\n"}, "unlanded edit")
    blocked = h.gate_block(ct, mt, "worktree_integrate")
    assert blocked is not None and blocked.returncode == 2
    assert blocked.payload["state"] == "knowledge-gate-refused"
    assert "Made stale since the base" in str(blocked.payload["reason"])
    assert ds.document_staleness(h.end_req(ct, mt), full=True).repaired_on_parent == ()


def test_unresolvable_parent_ref_fails_closed(tmp_path: Path) -> None:
    h = _Harness.create(tmp_path, src_branch="release/next")
    lc, lm = _land_and_repair(h)
    h.contract = replace(h.contract, code_source_branch="release/does-not-exist")
    wc.write_contract(h.contract.contract_path, h.contract)
    s, refusal, _ = _eval(h, lc, lm)
    assert s.repaired_on_parent == () and s.made_stale_since_base == (DOC_A,)
    assert refusal is not None


def test_next_leaf_confirmation_at_previous_landed_commit_clears_debt(tmp_path: Path) -> None:
    h = _Harness.create(tmp_path)
    l1_c, l1_m = h.land_leaf("L1", {SRC_A: "v2-l1\n"}, "L1 code")
    l2_m = h.confirm(WORK, l1_c, uncommitted=True)
    h.add_leaf("L2", enclosure=(l1_c, l1_m, l1_c, l2_m))
    assert kg.landing_gate_refusal(h.end_req(l1_c, l2_m)) is None
    h.merge_pair(h.src_branch, WORK, "parent merges master")
    h.edit_code(h.src_branch, {SRC_A: "v3-parent\n"}, "parent re-stales")
    ct, mt = h.sync()
    s, refusal, _ = _eval(h, ct, mt)
    assert s.made_stale_by_leaf == () and s.already_stale == (DOC_A,) and refusal is None


@pytest.mark.parametrize(
    "mode", ["open_and_abandoned", "missing_pair", "nonexistent_commit", "code_only", "memory_only"]
)
def test_leaf_pair_integrity_and_uncomputable_refusals(tmp_path: Path, mode: str) -> None:
    h = _Harness.create(tmp_path)
    cb, mb = h._heads()
    ct, mt = h._work({"other.py": "v1\n"}, "unrelated")
    if mode == "open_and_abandoned":
        h.add_leaf("L-OPEN", status="inProgress", enclosure=(cb, mb, "", ""), integrated=False)
        h.add_leaf("L-ABANDONED", status="abandoned")
        s, refusal, _ = _eval(h, ct, mt)
        assert s.uncomputable is None and refusal is None
        return
    if mode == "missing_pair":
        pair: Pair4 = (cb, mb, "", mt)
    elif mode == "nonexistent_commit":
        pair = (cb, mb, "deadbeef" * 5, mt)
    elif mode == "code_only":
        _git(h.memory, "branch", "side-mem", mb)
        pair = (cb, mb, ct, _note(h.memory, "side-mem", "side mem"))
    else:
        _git(h.code, "branch", "side-code", cb)
        pair = (cb, mb, h.edit_code("side-code", {"side.py": "x\n"}, "side code"), mt)
    h.add_leaf("L-BAD", enclosure=pair, integrated=True)
    s, refusal, ans = _eval(h, ct, mt)
    assert s.uncomputable is not None and ans is not None and ans["state"] == "uncomputable"
    assert refusal is not None and "cannot be computed" in refusal


@pytest.mark.parametrize(
    ("base_bad", "tip_bad", "refuse"),
    [(True, False, False), (True, True, False), (False, True, True)],
)
def test_unreadable_links_refuse_only_when_introduced_after_base(
    tmp_path: Path, base_bad: bool, tip_bad: bool, refuse: bool
) -> None:
    init = {ID_A: [BAD_ANCHOR]} if base_bad else None
    h = _Harness.create(tmp_path, docs=((ID_A, DOC_A, "notes.txt"),), links=init)
    ct, _ = h._heads()
    blob = _git(h.code, "rev-parse", f"{ct}:notes.txt")
    ok: dict[str, object] = {"kind": "file", "path": "notes.txt", "blob": blob}
    conf = {"task": "L1", "role": "curator", "commit": ct}
    mt = h.write_dep(WORK, [BAD_ANCHOR if tip_bad else ok], conf)
    s, refusal, ans = _eval(h, ct, mt)
    if refuse:
        assert s.unverifiable and ans is not None and ans["state"] == "unverifiable"
        assert refusal is not None and "cannot all be observed" in refusal
    else:
        assert s.unverifiable == () and refusal is None


def test_repository_default_sync_moves_base_and_ignores_post_landing_sync(tmp_path: Path) -> None:
    h, base_of = _Harness.create(tmp_path), kg.master_document_base
    orig_mem = h.contract.memory_base_commit
    pc1 = h.edit_code(h.src_branch, {SRC_A: "parent-v2\n"}, "parent stale 1")
    pm1 = _note(h.memory, h.src_branch, "parent mem 1")
    c1, m1 = h.sync(sync_src="repository-default")
    assert base_of(h.contract, code_commit=c1, memory_commit=m1) == (pc1, pm1)
    _, refusal, ans = _eval(h, c1, m1)
    assert ans is not None and ans["count"] == 0 and ans["alreadyStale"] == [DOC_A]
    assert refusal is None
    pc2 = h.edit_code(h.src_branch, {SRC_A: "parent-v3\n"}, "parent stale 2")
    _note(h.memory, h.src_branch, "parent mem 2")
    c2, m2 = h.sync(sync_src="repository-default", skip_mem=True)
    assert base_of(h.contract, code_commit=c2, memory_commit=m2) == (pc2, orig_mem)
    assert base_of(h.contract, code_commit=c1, memory_commit=m1) == (pc1, pm1)


def test_checkpoint_landing_reports_debt_without_refusing(tmp_path: Path) -> None:
    h = _Harness.create(tmp_path)
    l1_c, l1_m = h.land_leaf("L1", {SRC_A: "v2-leaf\n"}, "L1 edit")
    assert h.gate_block(l1_c, l1_m, "worktree_checkpoint_landing") is None
    rep = kg.landing_document_answer(
        h.contract, code_commit=l1_c, memory_commit=l1_m, master_end=False
    )
    assert rep is not None and rep["count"] == 1 and rep["documents"] == [DOC_A]
    final = h.gate_block(l1_c, l1_m, "worktree_integrate")
    assert final is not None and final.payload["state"] == "knowledge-gate-refused"


def test_answer_and_refusal_message_name_all_four_kinds_apart(tmp_path: Path) -> None:
    docs = (
        ("DPD-7K3F9Q", "docs/since.md", "src/since.py"),
        ("DPD-8M4G0R", "docs/leaf.md", "src/leaf.py"),
        ("DPD-9N5H1S", "docs/repaired.md", "src/repaired.py"),
        ("DPD-2P6J3T", "docs/named.md", "src/named.py"),
    )
    h = _Harness.create(tmp_path, src_branch="release/next", docs=docs)
    h.land_leaf("L1", {"src/leaf.py": "v2\n"}, "L1 edits leaf")
    h.merge_pair("release/next", WORK, "parent takes L1")
    h.edit_code("release/next", {"src/named.py": "v2\n"}, "parent edits named")
    h.sync()
    lc, lm = h._work({"src/since.py": "v2\n", "src/repaired.py": "v2\n"}, "master work")
    pc, _ = h.merge_pair("release/next", WORK, "land master")
    h.confirm("release/next", pc, ("DPD-9N5H1S", "docs/repaired.md", "src/repaired.py"))

    s, _, ans = _eval(h, lc, lm)
    assert ans is not None and ans["count"] == 2
    assert _sec(ans, "refusedSinceBase")["documents"] == ["docs/since.md"]
    assert _sec(ans, "refusedByOwnLeaf")["leaves"] == {"docs/leaf.md": ["L1"]}
    assert _sec(ans, "repairedOnParent")["documents"] == ["docs/repaired.md"]
    assert _sec(ans, "namedOnly")["documents"] == ["docs/named.md"]
    for key in ("refusedSinceBase", "refusedByOwnLeaf", "repairedOnParent", "namedOnly"):
        assert "read" in _sec(ans, key)
    assert "remedy" in _sec(ans, "refusedSinceBase") and "remedy" in _sec(ans, "refusedByOwnLeaf")
    msg = s.refusal_message()
    for phrase in (
        "Made stale since the base: docs/since.md",
        "Made stale by this master's own leaf: docs/leaf.md (L1)",
        "Repaired on the parent line and refusing nothing: docs/repaired.md",
        "Already stale at the base and refusing nothing: docs/named.md",
    ):
        assert phrase in msg
