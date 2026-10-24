"""MIK-R186 rule 3's tool server: the kept tools, the reads, the grade tools and the report.

Each case protects one distinct operation of the seat's own server, at the mounted boundary
(``FastMCP.call_tool``), against a real environment: :func:`build_environment` writes the
workspace, its snapshot of two real Git trees, the packet and the grants, and the assertion is on
what came back and what lies on disk.

1. the eight kept authoring tools still answer, over this environment's instructions and drafts;
2. ``list``/``read`` name a file of the bound snapshot and the binding they were read at;
3. a path outside the four admitted surfaces is refused by name, and a symlink is held;
4. the retained page revisions and ``notes/reference/<leaf>/`` are readable, and are not the
   binding;
5. an environment with no binding refuses every read rather than inventing one;
6. ``search`` finds a literal string, and its answer is bounded;
7. ``git_log``/``git_show``/``git_diff`` read the bound repositories and are never evidence;
8. ``git_ls_tree``/``git_cat_file``/``git_rev_parse`` name exact objects;
9. the Git surfaces refuse an option, a range, a worktree path and an unknown repository;
10. ``knowledge_read`` names records of the bound memory tree through the tree reader;
11. a planned binding is labelled ``planned`` with its two tip commits;
12. ``define_category`` and ``grade`` write the store under the leaf's lock;
13. a grade at a binding the environment has left is refused and writes nothing;
14. a foreign caller cannot call the grade tools;
15. the grade tools' bounds are the store's, named;
16. ``report_to_architect`` copies his words, delivers once and logs the state;
17. with no master-to-architect record the report is kept back for his tray;
18. the report refuses a message that is not his, and a perspective over its bound.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from agents_remember.application.review_evidence_packet import (
    comparison_reference,
    planned_reference,
)
from agents_remember.application.review_frame_store import frame_scope, frames_area, reopen_frame
from agents_remember.canvas import explainer_server
from agents_remember.canvas.explainer_grades import MAX_CATEGORIES, grades_path, read_grades
from agents_remember.canvas.explainer_inputs import ExplainerInputs  # the canonical owner
from agents_remember.canvas.explainer_paths import EXPLAINER_FRAME, GENERATION_SUFFIX
from agents_remember.canvas.explainer_server import page_revision, report_state, settle_reports
from agents_remember.canvas.explainer_staleness import BlockIdentity
from agents_remember.canvas.explainer_state import (
    ExplainerBinding,
    PlannedIdentity,
    agent_id,
    write_binding,
)
from agents_remember.canvas.explainer_target import ExplainerTargetReads
from agents_remember.canvas.explainer_workspace import (
    EnvironmentInputs,
    EnvironmentRef,
    EnvironmentRun,
    build_environment,
)
from agents_remember.canvas.prospective import BrowserProspectiveLoad, ProspectiveResult
from agents_remember.cli import developer_message_files as developer_messages
from agents_remember.cli.paseo_bridge import SEND_OUTCOME_UNKNOWN, PaseoBridgeFailure
from agents_remember.cli.role_message_files import (
    MessageContent,
    file_line,
    render_message_file,
    store_message_file,
)
from agents_remember.mcp.registration.explainer_surface import EXPLAINER_SURFACE
from agents_remember.mcp.registration.explainer_tools import (
    register_bound_explainer_tools,  # the entry
)
from agents_remember.mcp.tools import PUBLIC_TOOLS
from agents_remember.models.knowledge.review_frame import FrameBinding
from agents_remember.models.knowledge.review_trees import (
    ReviewTreeComparisonRecord,
    ReviewTreeSide,
)
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS
from canvas_host_test_support import LEAF, REPO, TASK, config, packet, publish, task
from knowledge_index_test_support import (
    FAMILY,
    REVIEW_INVARIANT,
    commit_all,
    init_repository,
    write_review_tree,
)
from mcp.server.fastmcp import FastMCP

AT = "2026-10-22T09:00:00+00:00"
MESSAGE_ID = "8d1b0f5e-0f4a-4a57-9c2e-4f4b1f2a6c11"
ARCHITECT = "architect-7d1f"
SCALE = [
    {"grade": "sound", "meaning": "the claim holds against the recorded evidence"},
    {"grade": "thin", "meaning": "the claim rests on less than it says"},
]
REFUSED_FOREIGN = "not-this-explainer"


# ---------------------------------------------------------------------------------------------
# One environment: two real repositories, a record, a packet, a built workspace and its seat
# ---------------------------------------------------------------------------------------------


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments], check=True, capture_output=True, text=True
    )
    return completed.stdout.strip()


def _write(root: Path, files: Mapping[str, str]) -> None:
    for name, text in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


@dataclass(frozen=True)
class World:
    """One leaf's environment, its two repositories and the task's records area."""

    root: Path
    code: Path
    memory: Path
    task_root: Path
    workspace: Path
    seat: str
    record: ReviewTreeComparisonRecord
    base_commit: str
    candidate_commit: str

    @property
    def code_tree(self) -> str:
        return self.record.code_candidate.tree


def _record(
    code: Path, memory: Path, *, number: int
) -> tuple[ReviewTreeComparisonRecord, str, str]:
    base_commit, candidate_commit = (
        _git(code, "rev-parse", "HEAD~1"),
        _git(code, "rev-parse", "HEAD"),
    )
    comparison = ReviewTreeComparisonRecord(
        task_id=TASK,
        leaf_id=LEAF,
        number=number,
        code_base=ReviewTreeSide(
            repository=str(code), tree=_git(code, "rev-parse", "HEAD~1^{tree}"), commit=base_commit
        ),
        code_candidate=ReviewTreeSide(
            repository=str(code),
            tree=_git(code, "rev-parse", "HEAD^{tree}"),
            commit=candidate_commit,
        ),
        memory_base=ReviewTreeSide(
            repository=str(memory), tree=_git(memory, "rev-parse", "HEAD~1^{tree}")
        ),
        memory_candidate=ReviewTreeSide(
            repository=str(memory), tree=_git(memory, "rev-parse", "HEAD^{tree}")
        ),
        recorded_at=AT,
    )
    return comparison, base_commit, candidate_commit


def _world(tmp_path: Path, *, number: int = 7, planned: bool = False) -> World:
    """Build one environment whose snapshot is two real trees, then raise its generation."""

    settings = config(tmp_path)
    code = tmp_path / "code"
    memory = tmp_path / "memory"
    init_repository(code)
    _write(
        code,
        {
            "src/app.py": "def run():\n    return 1\n",
            "README.md": "the fixture repository\n",
        },
    )
    commit_all(code, "the base")
    _write(code, {"src/app.py": "def run():\n    return 2\n", "src/new.py": "NEW = True\n"})
    commit_all(code, "the candidate")
    init_repository(memory)
    write_review_tree(memory)
    commit_all(memory, "the memory base")
    _write(memory, {"notes/added.md": "# added after the base\n"})
    commit_all(memory, "the memory candidate")
    record, base_commit, candidate_commit = _record(code, memory, number=number)
    reference = planned_reference(TASK, LEAF) if planned else comparison_reference(record)
    binding = FrameBinding.from_record(
        record,
        comparison_owner="planned" if planned else REPO,
        comparison_reference=reference,
    )
    identity = (
        PlannedIdentity(
            code_tip=candidate_commit,
            memory_tip=_git(memory, "rev-parse", "HEAD"),
            code_tree=record.code_candidate.tree,
            memory_tree=record.memory_candidate.tree,
        )
        if planned
        else None
    )
    root = task(settings)
    (_reference_folder(root)).mkdir(parents=True, exist_ok=True)
    (root / "notes" / "reference" / LEAF / "agreed.md").write_text(
        "# the agreed page\n\nthis wording was agreed\n", encoding="utf-8"
    )
    publish(root, record)
    built = build_environment(
        EnvironmentRun(
            ref=EnvironmentRef(settings, REPO, TASK, LEAF),
            inputs=EnvironmentInputs(
                binding=binding,
                packet=packet(record),
                capsule="the capsule for this leaf's explainer\n",
                code_repository=code,
                memory_repository=memory,
                planned=identity,
            ),
            now=AT,
        )
    )
    built.workspace.with_name(built.workspace.name + GENERATION_SUFFIX).write_text("1\n")
    return World(
        root=tmp_path,
        code=code,
        memory=memory,
        task_root=root,
        workspace=built.workspace,
        seat=agent_id(built.workspace, 1),
        record=record,
        base_commit=base_commit,
        candidate_commit=candidate_commit,
    )


def _reference_folder(root: Path) -> Path:
    return root / "notes" / "reference" / LEAF


def _fingerprints(references: tuple[str, ...]) -> dict[str, str]:
    """A deterministic stand-in for L89's per-block fingerprint (the product hands one in)."""

    return {
        reference: "sha256:" + hashlib.sha256(reference.encode()).hexdigest()
        for reference in references
    }


def _server(world: World, **overrides: Any):
    inputs = {
        "task_root": world.task_root,
        "coordination_root": world.root,
        "code_repository": world.code,
        "memory_repository": world.memory,
        "fingerprints": _fingerprints,
        "now": lambda: AT,
    }
    inputs.update(overrides)
    seat = inputs.pop("seat", world.seat)
    return _server_only(world.workspace, seat=seat, inputs=ExplainerInputs(**inputs))


def _server_only(
    workspace: Path, *, seat: str | None = None, inputs: ExplainerInputs | None = None
) -> FastMCP:
    """The explainer's tools on a bare server: what a launch would register, without a launch."""

    server = FastMCP("review-explainer")
    register_bound_explainer_tools(server, workspace=workspace, seat=seat, inputs=inputs)
    return server


def _content(server: Any, tool: str, arguments: Mapping[str, Any] | None = None) -> str:
    """The text one mounted tool call answered, whichever shape the SDK returns it in."""

    result = asyncio.run(server.call_tool(tool, dict(arguments or {})))
    if isinstance(result, tuple):  # (content blocks, structured body) on a non-object return
        result = result[0]
    first = result[0]
    return first.text if hasattr(first, "text") else str(first)


def _call(server: Any, tool: str, arguments: Mapping[str, Any] | None = None) -> Any:
    """One mounted tool call, decoded: the JSON body the tool answered."""

    return json.loads(_content(server, tool, arguments))


# ---------------------------------------------------------------------------------------------
# 1. The kept tools
# ---------------------------------------------------------------------------------------------


def test_the_kept_authoring_tools_answer_over_this_environment(tmp_path: Path) -> None:
    """Kept names and behaviour: admitted inputs, the packet, a written asset and its digest."""

    world = _world(tmp_path)
    server = _server(world)
    inputs = _call(server, "inputs")
    assert set(inputs) == {"evidence.json", "assignment.txt"}
    assert json.loads(_content(server, "read_input", {"name": "evidence.json"}))["comparison"]
    written = _call(server, "write_asset", {"relative": "index.html", "text": "<p>page</p>"})
    assert written["sha256"] == hashlib.sha256(b"<p>page</p>").hexdigest()
    assert (world.workspace / "drafts" / "assets" / "index.html").is_file()
    listed = _call(server, "assets")
    assert "index.html" in listed


def test_the_packet_is_re_read_so_an_update_needs_no_restart(tmp_path: Path) -> None:
    """Rule 3's re-read: ``source`` answers from the packet on disk at the moment of the call."""

    world = _world(tmp_path)
    server = _server(world)
    packet_path = world.workspace / "instructions" / "evidence.json"
    payload = json.loads(packet_path.read_text())
    payload["scope"] = "a later scope for the same comparison"
    packet_path.write_text(json.dumps(payload))
    assert (
        _call(server, "inputs")["evidence.json"]
        == hashlib.sha256(packet_path.read_bytes()).hexdigest()
    )


# ---------------------------------------------------------------------------------------------
# 2-6. The reads
# ---------------------------------------------------------------------------------------------


def test_reads_name_the_snapshot_and_the_binding_they_were_read_at(tmp_path: Path) -> None:
    """``list`` and ``read`` over the bound snapshot, every answer naming ``#7``."""

    world = _world(tmp_path)
    server = _server(world)
    listing = _call(server, "list", {"surface": "code"})
    assert listing["state"] == "listed"
    assert {"src", "README.md"} <= {one["path"] for one in listing["entries"]}
    assert listing["binding"]["label"] == "#7" and listing["partOfBinding"] is True
    one = _call(server, "read", {"surface": "code", "path": "src/app.py"})
    assert one["content"] == "def run():\n    return 2\n"
    assert one["encoding"] == "utf-8" and one["nextOffset"] is None
    assert one["sha256"] == "sha256:" + hashlib.sha256(b"def run():\n    return 2\n").hexdigest()
    assert one["binding"]["label"] == "#7"


def test_a_path_outside_the_admitted_surfaces_is_refused_by_name(tmp_path: Path) -> None:
    """Traversal, an absolute path, an unserved surface and a symlink are all held."""

    world = _world(tmp_path)
    server = _server(world)
    traversal = _call(server, "read", {"surface": "code", "path": "../instructions/grants.json"})
    assert traversal["refusal"] == "path-refused"
    absolute = _call(server, "read", {"surface": "code", "path": "/etc/passwd"})
    assert absolute["refusal"] == "path-refused"
    surface = _call(server, "list", {"surface": "worktrees"})
    assert surface["refusal"] == "surface-unknown"
    outside = _call(server, "read", {"surface": "reference", "path": "../../task.json"})
    assert outside["refusal"] == "path-refused"
    snapshot = world.workspace / "snapshot-1" / "code"
    (snapshot / "linked").symlink_to("/etc")
    held = _call(server, "list", {"surface": "code"})
    assert held["refusal"] == "path-held"


def test_the_retained_pages_and_the_reference_folder_are_readable(tmp_path: Path) -> None:
    """MIK-R189 rule 9: the agreed page stays readable, and it is not part of the binding."""

    world = _world(tmp_path)
    server = _server(world)
    pages = _call(server, "list", {"surface": "pages"})
    assert pages["state"] == "listed" and pages["partOfBinding"] is False
    reference = _call(server, "read", {"surface": "reference", "path": "agreed.md"})
    assert "this wording was agreed" in reference["content"]
    assert reference["partOfBinding"] is False and reference["binding"]["label"] == "#7"
    assert page_revision(world.task_root, LEAF) == 1
    frame = (frames_area(world.task_root, LEAF) / "host-and-presenter").name
    assert any(frame in one["path"] for one in pages["entries"])


def test_an_environment_with_no_binding_refuses_every_read(tmp_path: Path) -> None:
    """No binding, no read: the answer names it instead of inventing a snapshot."""

    world = _world(tmp_path)
    server = _server(world)
    (world.workspace / "binding.json").unlink()
    assert _call(server, "list", {"surface": "code"})["refusal"] == "explainer-missing"
    assert _call(server, "read", {"surface": "code", "path": "README.md"})["refusal"] == (
        "explainer-missing"
    )


def test_search_finds_a_literal_string_and_bounds_its_answer(tmp_path: Path) -> None:
    """A literal search answers path, line and text; an empty query and a small limit are bounded."""

    world = _world(tmp_path)
    server = _server(world)
    found = _call(server, "search", {"surface": "code", "query": "return"})
    assert found["state"] == "searched"
    assert [(one["path"], one["line"]) for one in found["matches"]] == [("src/app.py", 2)]
    assert found["filesRead"] >= 1
    empty = _call(server, "search", {"surface": "code", "query": ""})
    assert empty["refusal"] == "search-query"
    capped = _call(server, "search", {"surface": "code", "query": "e", "limit": 1})
    assert len(capped["matches"]) <= 1 and capped["truncated"] is True


# ---------------------------------------------------------------------------------------------
# 7-9. The six Git subcommands
# ---------------------------------------------------------------------------------------------


def test_git_log_show_and_diff_read_the_bound_repositories(tmp_path: Path) -> None:
    """The history reads answer from the repository's objects, and are never evidence."""

    world = _world(tmp_path)
    server = _server(world)
    log = _call(server, "git_log", {"repository": "code", "limit": 5})
    assert log["state"] == "git" and world.candidate_commit in log["text"]
    assert log["evidence"] is False and log["history"] is True
    assert log["binding"]["label"] == "#7"
    show = _call(server, "git_show", {"repository": "code", "target": world.candidate_commit})
    assert "the candidate" in show["text"]
    diff = _call(
        server,
        "git_diff",
        {"repository": "code", "base": world.base_commit, "candidate": world.candidate_commit},
    )
    assert "src/new.py" in diff["text"] and "+NEW = True" in diff["text"]
    memory = _call(server, "git_log", {"repository": "memory", "limit": 2})
    assert "the memory candidate" in memory["text"]


def test_git_ls_tree_cat_file_and_rev_parse_name_exact_objects(tmp_path: Path) -> None:
    """The object reads answer exact ids and sizes, with the kernel runner's environment."""

    world = _world(tmp_path)
    server = _server(world)
    tree = _call(
        server, "git_ls_tree", {"repository": "code", "tree": world.code_tree, "recursive": True}
    )
    assert "src/app.py" in tree["text"]
    blob = _git(world.code, "rev-parse", f"{world.code_tree}:src/app.py")
    size = _call(server, "git_cat_file", {"repository": "code", "target": blob, "mode": "size"})
    assert size["text"].strip() == str(len("def run():\n    return 2\n"))
    pretty = _call(server, "git_cat_file", {"repository": "code", "target": blob})
    assert pretty["text"] == "def run():\n    return 2\n"
    resolved = _call(server, "git_rev_parse", {"repository": "code", "expression": "HEAD"})
    assert resolved["text"].strip() == world.candidate_commit


def test_git_refuses_an_option_a_range_a_worktree_path_and_a_foreign_repository(
    tmp_path: Path,
) -> None:
    """Nothing here can ask for another program, a range, a worktree path or another repository."""

    world = _world(tmp_path)
    server = _server(world)
    option = _call(server, "git_log", {"repository": "code", "ref": "--textconv"})
    assert option["refusal"] == "revision-refused"
    diff_option = _call(server, "git_show", {"repository": "code", "target": "--output=/tmp/x"})
    assert diff_option["refusal"] == "revision-refused"
    worktree = _call(
        server, "git_diff", {"repository": "code", "base": "/etc/passwd", "candidate": "HEAD"}
    )
    assert worktree["refusal"] == "revision-refused"
    no_index = _call(
        server, "git_diff", {"repository": "code", "base": "a..b", "candidate": "HEAD"}
    )
    assert no_index["refusal"] == "revision-refused"
    filter_path = _call(
        server, "git_ls_tree", {"repository": "code", "tree": "HEAD", "path": "../x"}
    )
    assert filter_path["refusal"] == "path-refused"
    foreign = _call(server, "git_log", {"repository": "worktrees"})
    assert foreign["refusal"] == "repository-unknown"
    toplevel = _call(
        server, "git_rev_parse", {"repository": "code", "expression": "--show-toplevel"}
    )
    assert toplevel["refusal"] == "revision-refused"


# ---------------------------------------------------------------------------------------------
# 10-11. The tree-addressed knowledge read
# ---------------------------------------------------------------------------------------------


def test_knowledge_read_names_records_of_the_bound_memory_tree(tmp_path: Path) -> None:
    """The product's views at the bound tree, through the tree reader, rows without text."""

    world = _world(tmp_path)
    server = _server(world)
    invariant = _call(server, "knowledge_read", {"view": "invariant", "recordId": REVIEW_INVARIANT})
    assert invariant["state"] == "named"
    assert invariant["memoryTree"] == world.record.memory_candidate.tree
    assert invariant["binding"]["label"] == "#7"
    kinds = {one["kind"] for one in invariant["rows"]}
    assert {"invariant", "realization", "proof"} <= kinds
    assert all("statement" not in one for one in invariant["rows"])
    family = _call(server, "knowledge_read", {"view": "family", "recordId": FAMILY})
    assert {"member", "route"} <= {one["kind"] for one in family["rows"]}
    paged = _call(
        server, "knowledge_read", {"view": "invariant", "recordId": REVIEW_INVARIANT, "limit": 1}
    )
    assert len(paged["rows"]) == 1 and paged["continuation"] == "1"
    assert _call(server, "knowledge_read", {"view": "record", "recordId": "INV-NOPE"})[
        "refusal"
    ] == ("record-unknown")
    assert _call(server, "knowledge_read", {"view": "timeline"})["refusal"] == "view-unknown"


def test_a_planned_binding_is_labelled_planned_with_its_tips(tmp_path: Path) -> None:
    """MIK-R189 rule 4: a planned binding is never ``#0``; it names the master's two tips."""

    world = _world(tmp_path, number=0, planned=True)
    server = _server(world)
    listing = _call(server, "list", {"surface": "memory"})
    face = listing["binding"]
    assert face["label"] == "planned" and face["number"] == 0
    assert face["planned"]["codeTip"] == world.candidate_commit
    assert face["planned"]["memoryTip"] == _git(world.memory, "rev-parse", "HEAD")
    assert face["reference"] == planned_reference(TASK, LEAF)


# ---------------------------------------------------------------------------------------------
# 12-15. The grade tools
# ---------------------------------------------------------------------------------------------


def test_a_grade_write_waits_on_the_lock_a_build_holds(tmp_path: Path) -> None:
    """MIK-R187 rule 6: the seat's writer and the service's writer share one file, so neither loses keys.

    A build holds the leaf's lock while it rewrites the store. The seat's `define_category` meets that
    same lock -- it waits for it rather than writing underneath it -- and the grade file is written only
    after the holder lets go. The holder is a separate process: a lock taken in this one would be
    re-entrant and prove nothing.
    """

    world = _world(tmp_path)
    server = _server(world)
    # ``exclusive_file_lock`` guards ``<resource>.lock``: the resource is the bare leaf name.
    held = world.task_root / LEAF
    holder = _hold_leaf_lock(held, seconds=3)
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "held"
        before = time.monotonic()
        defined = _call(
            server, "define_category", {"name": "risk", "scale": SCALE, "binding": "#7"}
        )
        waited = time.monotonic() - before
    finally:
        holder.kill()
        holder.communicate(timeout=HANG_GUARD_SECONDS)
    assert defined["state"] == "defined", defined
    # It met the build's lock: the write waited for the holder, not around it.
    # load-independent: the 3 s lock hold makes the lower bound the lock's property, not a race.
    assert waited >= 2.0, waited


def _hold_leaf_lock(path: Path, *, seconds: int) -> subprocess.Popen[str]:
    """A second process holding the leaf's lock, printing `held` once it really has it."""

    script = (
        "import sys, time\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from pathlib import Path\n"
        "from agents_remember.kernel.file_lock import exclusive_file_lock\n"
        "with exclusive_file_lock(Path(sys.argv[2]), 'explainer leaf'):\n"
        "    print('held', flush=True)\n"
        f"    time.sleep({seconds})\n"
    )
    return subprocess.Popen(
        [sys.executable, "-c", script, str(Path(__file__).resolve().parents[1] / "src"), str(path)],
        stdout=subprocess.PIPE,
        text=True,
    )


def test_define_category_and_grade_write_the_store_under_the_leaf_lock(tmp_path: Path) -> None:
    """MIK-R187 rule 2: a category's scale is its meaning, and a grade carries its binding."""

    world = _world(tmp_path)
    server = _server(world)
    defined = _call(server, "define_category", {"name": "risk", "scale": SCALE, "binding": "#7"})
    assert defined["state"] == "defined" and defined["grades"] == ["sound", "thin"]
    assert defined["binding"]["label"] == "#7"
    # The store lies with the task's records, so the lock the seat takes is the leaf's own -- the same
    # file a build, an Update and a Retire hold, and the only one: `exclusive_file_lock` guards
    # `<resource>.lock`, and the resource is the bare leaf name, so no `.lock.lock` is made.
    leaf_lock = world.task_root / f"{LEAF}.lock"
    assert leaf_lock.is_file()
    again = _call(server, "define_category", {"name": "risk", "scale": SCALE, "binding": "#7"})
    assert again["state"] == "unchanged"
    graded = _call(
        server,
        "grade",
        {
            "references": ["inv:INV-RVW001/code-candidate/dashboard/src/data/review.ts", "block-b"],
            "category": "risk",
            "grade": "thin",
            "reason": "one hunk only",
            "binding": "#7",
        },
    )
    assert graded["state"] == "graded" and graded["graded"] == 2
    store = read_grades(grades_path(world.task_root, LEAF), leaf_id=LEAF)
    assert [one.grade for one in store.grades] == ["thin", "thin"]
    assert all(one.binding.comparison_number == 7 for one in store.grades)
    removed = _call(server, "remove_category", {"name": "risk", "binding": "#7"})
    assert removed["state"] == "removed" and removed["gradesRemoved"] == 2


def test_a_grade_at_a_binding_the_environment_left_is_refused(tmp_path: Path) -> None:
    """``binding-moved``: the store is not written at a binding the environment has left."""

    world = _world(tmp_path)
    server = _server(world)
    _call(server, "define_category", {"name": "risk", "scale": SCALE, "binding": "#7"})
    current = json.loads((world.workspace / "binding.json").read_text())
    moved = ExplainerBinding.model_validate(
        {**current, "binding": {**current["binding"], "comparison_number": 8}}
    )
    write_binding(world.workspace, moved)
    stale = _call(
        server,
        "grade",
        {
            "references": ["block-a"],
            "category": "risk",
            "grade": "sound",
            "reason": "checked",
            "binding": "#7",
        },
    )
    assert stale["refusal"] == "binding-moved"
    store = read_grades(grades_path(world.task_root, LEAF), leaf_id=LEAF)
    assert store.grades == ()
    unknown = _call(server, "remove_category", {"name": "risk", "binding": "whenever"})
    assert unknown["refusal"] == "binding-unknown"
    landable = _call(server, "remove_category", {"name": "risk", "binding": "#8"})
    assert landable["state"] == "removed"


def test_only_the_leaf_explainer_may_call_the_grade_tools(tmp_path: Path) -> None:
    """A foreign identity, and a server built with none, are both refused by name."""

    world = _world(tmp_path)
    foreign = _call(
        _server(world, seat="someone-else"),
        "define_category",
        {"name": "risk", "scale": SCALE, "binding": "#7"},
    )
    assert foreign["refusal"] == REFUSED_FOREIGN
    anonymous = _call(
        _server_only(
            world.workspace,
            inputs=ExplainerInputs(
                task_root=world.task_root,
                coordination_root=world.root,
                fingerprints=_fingerprints,
            ),
        ),
        "define_category",
        {"name": "risk", "scale": SCALE, "binding": "#7"},
    )
    assert anonymous["refusal"] == REFUSED_FOREIGN
    assert not grades_path(world.task_root, LEAF).exists()


def test_the_grade_tools_carry_the_store_bounds(tmp_path: Path) -> None:
    """Rule 2's bounds, named: the scale, the count of categories, the reason and the references."""

    world = _world(tmp_path)
    server = _server(world)
    short_scale = _call(
        server,
        "define_category",
        {"name": "risk", "scale": [SCALE[0]], "binding": "#7"},
    )
    assert short_scale["refusal"] == "category-scale"
    for index in range(MAX_CATEGORIES):
        _call(server, "define_category", {"name": f"cat-{index}", "scale": SCALE, "binding": "#7"})
    full = _call(server, "define_category", {"name": "one-more", "scale": SCALE, "binding": "#7"})
    assert full["refusal"] == "category-limit"
    clash = _call(
        server,
        "define_category",
        {"name": "cat-0", "scale": [SCALE[1], SCALE[0]], "binding": "#7"},
    )
    assert clash["refusal"] == "category-exists"
    long_reason = _call(
        server,
        "grade",
        {
            "references": ["block-a"],
            "category": "cat-0",
            "grade": "sound",
            "reason": "x" * 161,
            "binding": "#7",
        },
    )
    assert long_reason["refusal"] == "grade-reason"
    too_many = _call(
        server,
        "grade",
        {
            "references": [f"block-{index}" for index in range(201)],
            "category": "cat-0",
            "grade": "sound",
            "reason": "checked",
            "binding": "#7",
        },
    )
    assert too_many["refusal"] == "grade-limit"


# ---------------------------------------------------------------------------------------------
# 16-18. The report
# ---------------------------------------------------------------------------------------------


def _file_tray_message(world: World, *, message_id: str = MESSAGE_ID) -> Path:
    """His filed tray message, written the way the product files one (MIK-R188)."""

    folder = developer_messages.target_folder(_coordination(world.root), REPO, TASK, world.seat)
    content = render_message_file(
        MessageContent(
            when=datetime.fromisoformat(AT),
            sender_line="developer",
            sender_task=TASK,
            recipient_role="review-presenter",
            recipient_id=world.seat,
            recipient_task=TASK,
            message_id=message_id,
            text="Prefer the shorter page.\nKeep the comparison table.",
        )
    )
    stem = developer_messages.message_stem(
        datetime.fromisoformat(AT), "review-presenter", world.seat, message_id
    )
    return store_message_file(folder, stem, content)


def _coordination(root: Path) -> Any:
    """The one field the developer-message reader uses; the server casts its own."""

    class _Root:
        coordination_root = root

    return _Root()


def _record_architect(world: World, *, agent: str = ARCHITECT) -> None:
    path = (
        world.root
        / "notes"
        / "reports"
        / "master-architect"
        / REPO
        / f"{world.task_root.name}.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"architectAgentId": agent, "act": "manager-start", "at": AT}, sort_keys=True)
    )


def test_report_to_architect_copies_his_words_and_delivers_once(tmp_path: Path) -> None:
    """Rule 7: byte-for-byte copy, the perspective under its heading, one delivery, one log line."""

    world = _world(tmp_path)
    message = _file_tray_message(world)
    _record_architect(world)
    sent: list[tuple[str, str]] = []

    def sender(architect: str, text: str) -> str:
        sent.append((architect, text))
        return "reported"

    server = _server(world, sender=sender)
    answer = _call(
        server, "report_to_architect", {"messageId": MESSAGE_ID, "perspective": "the page is sound"}
    )
    assert answer["state"] == "reported" and answer["architect"] == ARCHITECT
    assert (
        answer["copiedSha256"]
        == hashlib.sha256(b"Prefer the shorter page.\nKeep the comparison table.").hexdigest()
    )
    assert answer["pageRevision"] == 1 and answer["binding"]["label"] == "#7"
    report = Path(answer["file"])
    body = report.read_text()
    assert "Prefer the shorter page.\nKeep the comparison table." in body
    assert "## The explainer's perspective (presenter inference)" in body
    assert "page revision: 1" in body
    assert len(sent) == 1 and sent[0][0] == ARCHITECT
    assert sent[0][1].splitlines()[0] == f"From explainer · {TASK} · agent {world.seat}"
    assert sent[0][1].strip().endswith(f"(SHA-256 {answer['sha256']})")
    log = (
        _coordination(world.root).coordination_root
        / "notes/reports/developer-messages"
        / REPO
        / TASK
    )
    lines = [json.loads(one) for one in (log / "reports.jsonl").read_text().splitlines()]
    assert [(one["messageId"], one["state"]) for one in lines] == [(MESSAGE_ID, "reported")]
    assert message.name.endswith(".md")
    repeat = _call(server, "report_to_architect", {"messageId": MESSAGE_ID, "perspective": "again"})
    assert repeat["refusal"] == "already-reported" and len(sent) == 1


def test_a_report_with_no_architect_record_is_kept_for_his_tray(tmp_path: Path) -> None:
    """Rule 7 step 5: no usable record keeps the file back, ``not reported``."""

    world = _world(tmp_path)
    _file_tray_message(world)
    server = _server(world, sender=lambda architect, text: "reported")
    answer = _call(
        server, "report_to_architect", {"messageId": MESSAGE_ID, "perspective": "a view"}
    )
    assert answer["state"] == "not reported" and answer["refusal"] == "architect-unaddressed"
    assert answer["architect"] is None
    assert answer["file"].endswith(".undelivered.md")
    assert "delivery: refused as architect-unaddressed" in Path(answer["file"]).read_text()


def test_the_report_refuses_a_message_that_is_not_his_and_a_long_perspective(
    tmp_path: Path,
) -> None:
    """Step 1 and its bounds: only a filed message to this explainer, and 4000 characters."""

    world = _world(tmp_path)
    _file_tray_message(world)
    _record_architect(world)
    server = _server(world, sender=lambda architect, text: "reported")
    foreign = _call(
        server,
        "report_to_architect",
        {"messageId": "00000000-0000-4000-8000-000000000000", "perspective": "a view"},
    )
    assert foreign["refusal"] == "not-a-developer-message"
    long_view = _call(
        server, "report_to_architect", {"messageId": MESSAGE_ID, "perspective": "x" * 4001}
    )
    assert long_view["refusal"] == "perspective-bounds"


def test_the_server_bound_as_explainer_exposes_no_other_roles_tool(tmp_path: Path) -> None:
    """A roster claim, not proof that the normal AR server selects this roster for an explainer.

    What this exercises, stated so nobody has to infer it: **`FastMCP.list_tools` on the object returned by
    `canvas.explainer_server.register_bound_explainer_tools`** on a bare `FastMCP`, with a
    stand-in environment directory. It shows that server's roster and that no tool the task server mutates
    through appears in it. `knowledge_read` is shared by both roles and is a read, which is not a fault.

    What it does not show, and must not be read as showing: that the normal AR task server, under explainer
    identity, selects this roster. That binding is the bound-server facet, and it can only be shown by
    enumerating the normal server for that identity at the registered successor. Nor does disjointness from
    `MUTATING_TOOLS` exclude another role's *reads* from appearing, or the seat being bound to the wrong
    identity altogether -- both are acceptance checks for that successor enumeration, not claims of this test.
    """

    server = FastMCP("review-explainer")
    register_bound_explainer_tools(server, workspace=tmp_path, seat="seat-1")
    names = {tool.name for tool in asyncio.run(server.list_tools())}
    # One source of truth for the roster: the surface the bound server selects from.
    assert names == set(EXPLAINER_SURFACE)
    # And no AR task-server tool appears. `knowledge_read` is shared by both and is a read, so it is the
    # one name allowed to overlap; a shared read is not a leak, and this test says so rather than hiding
    # it inside set arithmetic.
    task_server_tools = set(PUBLIC_TOOLS) - {"knowledge_read"}
    assert names & task_server_tools == set(), sorted(names & task_server_tools)


def test_production_server_grades_real_blocks_without_injected_fingerprints(tmp_path: Path) -> None:
    """Production server (`fingerprints=None`) computes real fingerprints from bound Git trees."""

    world = _world(tmp_path)
    server = _server(world, fingerprints=None)
    defined = _call(
        server,
        "define_category",
        {"name": "risk", "scale": SCALE, "binding": "#7"},
    )
    assert defined["state"] == "defined"
    card_ref = "inv:INV-RVW001/code-candidate/src/app.py"
    inv_ref = f"invariant:{REVIEW_INVARIANT}@1"
    graded = _call(
        server,
        "grade",
        {
            "references": [card_ref, inv_ref],
            "category": "risk",
            "grade": "sound",
            "reason": "verified against bound trees",
            "binding": "#7",
        },
    )
    assert graded["state"] == "graded" and graded["graded"] == 2
    refused = _call(
        server,
        "grade",
        {
            "references": ["inv:INV-NOPE/code-candidate/src/missing.py"],
            "category": "risk",
            "grade": "thin",
            "reason": "absent block",
            "binding": "#7",
        },
    )
    assert refused["state"] == "not-graded" and refused["refusal"] == "fingerprint-unavailable"


def test_production_server_grades_a_native_long_path_hunk_and_a_deleted_binary_file(
    tmp_path: Path,
) -> None:
    """Rule 3's admitted fingerprint kinds fit the store: a two-sided hunk at a realistic long
    repository path and a deleted unexplained binary file, fingerprinted from the bound trees,
    grade through the production server with the full admitted fingerprints retained.
    """

    settings = config(tmp_path)
    code = tmp_path / "code"
    memory = tmp_path / "memory"
    long_path = (
        "mcp/src/agents_remember/application/review_tree_comparison/review_tree_comparison_suite.py"
    )
    binary_path = "assets/binary/logo.png"
    init_repository(code)
    _write(
        code,
        {
            "src/app.py": "def run():\n    return 1\n",
            long_path: "def f():\n    return 1\n",
            binary_path: "\x89PNG\r\n\x1a\n\x00binary",
        },
    )
    commit_all(code, "the base")
    _write(code, {long_path: "def f():\n    return 2\n"})
    (code / binary_path).unlink()
    commit_all(code, "the candidate")
    init_repository(memory)
    write_review_tree(memory)
    commit_all(memory, "the memory base")
    _write(memory, {"notes/added.md": "# added after the base\n"})
    commit_all(memory, "the memory candidate")
    record, _base_commit, _candidate_commit = _record(code, memory, number=7)
    binding = FrameBinding.from_record(
        record, comparison_owner=REPO, comparison_reference=comparison_reference(record)
    )
    root = task(settings)
    built = build_environment(
        EnvironmentRun(
            ref=EnvironmentRef(settings, REPO, TASK, LEAF),
            inputs=EnvironmentInputs(
                binding=binding,
                packet=packet(record),
                capsule="the capsule for this leaf's explainer\n",
                code_repository=code,
                memory_repository=memory,
            ),
            now=AT,
        )
    )
    built.workspace.with_name(built.workspace.name + GENERATION_SUFFIX).write_text("1\n")
    seat = agent_id(built.workspace, 1)
    server = _server_only(
        built.workspace,
        seat=seat,
        inputs=ExplainerInputs(
            task_root=root,
            coordination_root=tmp_path,
            code_repository=code,
            memory_repository=memory,
            fingerprints=None,
            now=lambda: AT,
        ),
    )

    reads = ExplainerTargetReads(binding=binding)
    hunks = reads.file_hunks(long_path)
    assert hunks is not None and len(hunks) == 1
    hunk_ref = hunks[0].identity().block_key()
    binary_ref = BlockIdentity.non_text(
        path=binary_path, trees=(record.code_base.tree, record.code_candidate.tree)
    ).reference()

    defined = _call(server, "define_category", {"name": "risk", "scale": SCALE, "binding": "#7"})
    assert defined["state"] == "defined"
    graded = _call(
        server,
        "grade",
        {
            "references": [hunk_ref, binary_ref],
            "category": "risk",
            "grade": "thin",
            "reason": "both admitted fingerprints fit storage",
            "binding": "#7",
        },
    )
    assert graded["state"] == "graded" and graded["graded"] == 2

    by_ref = {
        one.reference: one for one in read_grades(grades_path(root, LEAF), leaf_id=LEAF).grades
    }
    assert by_ref[hunk_ref].fingerprint.startswith(f"hunk:{long_path}@sha256:")
    assert "..sha256:" in by_ref[hunk_ref].fingerprint
    assert by_ref[binary_ref].fingerprint == f"file:{binary_path}@absent"
    # The admitted fingerprints are retained whole, not re-hashed or capped.
    assert len(by_ref[hunk_ref].fingerprint) > len(long_path)


def test_planned_and_same_number_claims_bind_exact_identity_and_refuse_after_update(
    tmp_path: Path,
) -> None:
    """Planned claims require tip identity; old tips or bare aliases after Update refuse `binding-moved`."""

    world = _world(tmp_path, number=0, planned=True)
    server = _server(world, fingerprints=None)

    # Reusable alias without tips is refused on a planned binding
    alias_refused = _call(
        server, "define_category", {"name": "risk", "scale": SCALE, "binding": "planned"}
    )
    assert alias_refused.get("refusal") == "binding-moved", alias_refused

    face_old = _call(server, "list", {"surface": "code"})["binding"]
    assert "claim" in face_old and face_old["claim"].startswith("planned@")

    # Exact current planned identity succeeds
    defined = _call(
        server,
        "define_category",
        {"name": "risk", "scale": SCALE, "binding": face_old["claim"]},
    )
    assert defined["state"] == "defined"

    # Simulate planned Update to new tips
    current = json.loads((world.workspace / "binding.json").read_text())
    new_code_tip = "f" * 40
    moved_planned = ExplainerBinding.model_validate(
        {
            **current,
            "planned": {**current["planned"], "code_tip": new_code_tip},
        }
    )
    write_binding(world.workspace, moved_planned)

    # Delayed grade/define/remove with old planned tips refuses `binding-moved`
    for tool_name, args in (
        (
            "grade",
            {
                "references": ["inv:INV-RVW001/code-candidate/src/app.py"],
                "category": "risk",
                "grade": "sound",
                "reason": "delayed",
                "binding": face_old["claim"],
            },
        ),
        ("define_category", {"name": "other", "scale": SCALE, "binding": face_old["digest"]}),
        ("remove_category", {"name": "risk", "binding": face_old["claim"]}),
    ):
        res = _call(server, tool_name, args)
        assert res["refusal"] == "binding-moved", (tool_name, res)
    assert read_grades(grades_path(world.task_root, LEAF), leaf_id=LEAF).grades == ()

    # Current exact planned identity after Update succeeds
    face_new = _call(server, "list", {"surface": "code"})["binding"]
    graded_now = _call(
        server,
        "grade",
        {
            "references": ["inv:INV-RVW001/code-candidate/src/app.py"],
            "category": "risk",
            "grade": "sound",
            "reason": "at new tips",
            "binding": face_new["claim"],
        },
    )
    assert graded_now["state"] == "graded"

    # The go: the leaf lands. Same number 0, the owner and the reference change, the trees do not.
    landed = json.loads((world.workspace / "binding.json").read_text())
    landed["planned"] = None
    landed["binding"]["comparison_owner"] = REPO
    landed["binding"]["comparison_reference"] = comparison_reference(world.record)
    write_binding(world.workspace, ExplainerBinding.model_validate(landed))

    # The same-number owner transition refuses the old planned identity, and writes nothing.
    before = read_grades(grades_path(world.task_root, LEAF), leaf_id=LEAF).grades
    stale_planned = _call(
        server,
        "grade",
        {
            "references": ["inv:INV-RVW001/code-candidate/src/app.py"],
            "category": "risk",
            "grade": "thin",
            "reason": "a planned identity after the go",
            "binding": face_new["claim"],
        },
    )
    assert stale_planned["refusal"] == "binding-moved"
    assert read_grades(grades_path(world.task_root, LEAF), leaf_id=LEAF).grades == before

    # The current recorded identity at number 0 succeeds.
    face_recorded = _call(server, "list", {"surface": "code"})["binding"]
    assert face_recorded["label"] == "#0"
    landed_ok = _call(
        server, "remove_category", {"name": "risk", "binding": face_recorded["claim"]}
    )
    assert landed_ok["state"] == "removed"


def test_category_meaning_scale_name_bound_and_one_line_reason_contracts(tmp_path: Path) -> None:
    """Category meaning equality, overbound scale name refusal, and newline/CR reason refusal."""

    world = _world(tmp_path)
    server = _server(world)
    overbound_scale = [
        {"grade": "g" * 161, "meaning": "too long grade name"},
        {"grade": "thin", "meaning": "ok"},
    ]
    refused_scale = _call(
        server,
        "define_category",
        {"name": "risk", "scale": overbound_scale, "binding": "#7"},
    )
    assert refused_scale["refusal"] == "category-scale"
    assert not grades_path(world.task_root, LEAF).exists()

    defined = _call(
        server,
        "define_category",
        {"name": "risk", "scale": SCALE, "meaning": "first meaning", "binding": "#7"},
    )
    assert defined["state"] == "defined" and defined["meaning"] == "first meaning"
    noop = _call(
        server,
        "define_category",
        {"name": "risk", "scale": SCALE, "meaning": "first meaning", "binding": "#7"},
    )
    assert noop["state"] == "unchanged"
    diff_meaning = _call(
        server,
        "define_category",
        {"name": "risk", "scale": SCALE, "meaning": "second meaning", "binding": "#7"},
    )
    assert diff_meaning["refusal"] == "category-exists"

    for bad_reason in ("line 1\nline 2", "line 1\rline 2"):
        bad = _call(
            server,
            "grade",
            {
                "references": ["block-a"],
                "category": "risk",
                "grade": "sound",
                "reason": bad_reason,
                "binding": "#7",
            },
        )
        assert bad["refusal"] == "grade-reason"
    assert read_grades(grades_path(world.task_root, LEAF), leaf_id=LEAF).grades == ()


def test_render_publishes_ready_revision_with_derived_part_map_and_rereads_binding_under_frames_lock(
    tmp_path: Path, monkeypatch
) -> None:
    """`render` publishes ready draft with derived `part_map` and lock-time `binding.json` reread."""

    world = _world(tmp_path)
    monkeypatch.setattr(
        BrowserProspectiveLoad,
        "load",
        lambda self, **kwargs: ProspectiveResult(state="loaded", kind="browser"),
    )
    server = _server(world)
    html = (
        "<!doctype html><html><body>"
        '<section data-part="overview" data-part-title="Overview">'
        '<p data-evidence-claim="c-present">Summary</p>'
        '<p data-evidence-claim="c-gone">Earlier</p>'
        "</section></body></html>"
    )
    _call(server, "write_asset", {"relative": "index.html", "text": html})
    rendered = _call(server, "render")
    assert rendered["readiness"]["ready"] is True
    assert rendered["publication"]["state"] == "published"
    assert rendered["publication"]["partMap"] == [
        {"part": "overview", "title": "Overview", "claims": ["c-present", "c-gone"]}
    ]
    scope = frame_scope(world.task_root, LEAF, EXPLAINER_FRAME)
    opened = reopen_frame(scope)
    assert opened.state == "ready" and opened.manifest is not None
    assert len(opened.manifest.part_map) == 1
    assert opened.manifest.part_map[0].part == "overview"

    # Move binding.json before next render publication -> refuses `binding-moved`
    current = json.loads((world.workspace / "binding.json").read_text())
    moved = ExplainerBinding.model_validate(
        {**current, "binding": {**current["binding"], "comparison_number": 9}}
    )
    write_binding(world.workspace, moved)
    _call(server, "write_asset", {"relative": "index.html", "text": html.replace("Summary", "V2")})
    refused_render = _call(server, "render")
    assert refused_render["refusal"] == "binding-moved"
    assert refused_render["publication"]["refusal"] == "binding-moved"
    # Earlier published revision retains its original binding (#7), not #9
    reopened = reopen_frame(scope)
    assert reopened.manifest is not None and reopened.manifest.binding.comparison_number == 7


def test_report_preserves_markdown_headings_crlf_non_ascii_and_seals_copied_byte_range(
    tmp_path: Path,
) -> None:
    """Developer message with `## ` headings, CRLF, trailing newline and UTF-8 round-trips with byte seal."""

    world = _world(tmp_path)
    _record_architect(world)
    complex_body = (
        "First line with café — naïve\r\n"
        "## Developer Heading Inside Message\n"
        "Keep this section.\n"
        "## Second Heading\n"
        "Final line with trailing newline\n"
    )
    folder = developer_messages.target_folder(_coordination(world.root), REPO, TASK, world.seat)
    content = render_message_file(
        MessageContent(
            when=datetime.fromisoformat(AT),
            sender_line="developer",
            sender_task=TASK,
            recipient_role="review-presenter",
            recipient_id=world.seat,
            recipient_task=TASK,
            message_id=MESSAGE_ID,
            text=complex_body,
        )
    )
    stem = developer_messages.message_stem(
        datetime.fromisoformat(AT), "review-presenter", world.seat, MESSAGE_ID
    )
    filed_path = store_message_file(folder, stem, content)
    delivered_to_explainer = f"From developer · {TASK}\n{complex_body}\n{file_line(filed_path, hashlib.sha256(filed_path.read_bytes()).hexdigest())}"
    # Explainer extracts messageId from the delivered `Message file: ..._<messageId>.md` line
    extracted_id = (
        delivered_to_explainer.splitlines()[-1]
        .split("Message file: ")[1]
        .split(" (SHA-256")[0]
        .rsplit("_", 1)[-1]
        .removesuffix(".md")
    )
    assert extracted_id == MESSAGE_ID

    sent: list[tuple[str, str]] = []
    server = _server(world, sender=lambda arch, txt: sent.append((arch, txt)) or "reported")
    answer = _call(
        server,
        "report_to_architect",
        {"messageId": extracted_id, "perspective": "headings and bytes preserved"},
    )
    assert answer["state"] == "reported"
    expected_bytes = complex_body.encode("utf-8")
    assert answer["copiedSha256"] == hashlib.sha256(expected_bytes).hexdigest()
    assert answer["sourceFile"] == filed_path.as_posix()
    start_b, end_b = answer["copiedByteRange"]
    assert filed_path.read_bytes()[start_b:end_b] == expected_bytes
    report_bytes = Path(answer["file"]).read_bytes()
    assert expected_bytes in report_bytes
    assert f"copiedSource: {filed_path.as_posix()}".encode() in report_bytes
    assert (
        f"copiedBytes: {start_b}..{end_b} (SHA-256 {answer['copiedSha256']})".encode()
        in report_bytes
    )
    assert f"copiedSource: {filed_path.as_posix()}" in sent[0][1]


def test_production_report_delivery_routes_per_master_holds_limits_and_settles_transitions(
    tmp_path: Path,
) -> None:
    """Two masters, busy/limit hold, timeout-unknown no-resend, and terminal settlement in reports.jsonl."""

    world = _world(tmp_path)
    arch_a = "11111111-1111-4111-8111-111111111111"
    arch_b = "22222222-2222-4222-8222-222222222222"
    _record_architect(world, agent=arch_a)

    # Create launch receipts and workspaces for arch_a and arch_b
    exec_dir = world.root / "notes" / "reports" / "role-executions"
    exec_dir.mkdir(parents=True, exist_ok=True)
    ws_a = world.root / "workspace" / "arch-a"
    ws_b = world.root / "workspace" / "arch-b"
    ws_a.mkdir(parents=True, exist_ok=True)
    ws_b.mkdir(parents=True, exist_ok=True)
    receipt_a = exec_dir / "arch-a.json"
    receipt_b = exec_dir / "arch-b.json"
    receipt_a.write_text(
        json.dumps({"agentId": arch_a, "status": "held", "workspace": {"path": ws_a.as_posix()}}),
        encoding="utf-8",
    )
    receipt_b.write_text(
        json.dumps(
            {"agentId": arch_b, "status": "running", "workspace": {"path": ws_b.as_posix()}}
        ),
        encoding="utf-8",
    )

    msg_1 = "10000000-0000-4000-8000-000000000001"
    msg_2 = "10000000-0000-4000-8000-000000000002"
    _file_tray_message(world, message_id=msg_1)
    _file_tray_message(world, message_id=msg_2)

    delivered: list[tuple[str, str]] = []
    server = _server(world, sender=lambda arch, txt: delivered.append((arch, txt)) or "reported")

    # 1. arch_a is held -> report is held in arch_a's held store
    ans_1 = _call(server, "report_to_architect", {"messageId": msg_1, "perspective": "p1"})
    assert ans_1["state"] == "held" and ans_1["architect"] == arch_a

    log_path = (
        world.root / "notes" / "reports" / "developer-messages" / REPO / TASK / "reports.jsonl"
    )
    state_1 = report_state(log_path, msg_1)
    assert state_1 is not None and state_1["state"] == "held"

    # 2. arch_a is archived before delivery, and master-architect record now names arch_b -> re-addresses once to arch_b!
    receipt_a.write_text(
        json.dumps(
            {
                "agentId": arch_a,
                "status": "archived",
                "archived": True,
                "workspace": {"path": ws_a.as_posix()},
                "heldMessageLines": [
                    {"messageId": ans_1["reportId"], "how": "not-delivered-archived-or-gone"}
                ],
            }
        ),
        encoding="utf-8",
    )
    _record_architect(world, agent=arch_b)
    transitions = settle_reports(
        world.root,
        world.task_root,
        seat=world.seat,
        sender=lambda arch, txt: delivered.append((arch, txt)) or "reported",
    )
    assert len(transitions) == 1
    assert transitions[0]["state"] == "reported" and transitions[0]["architect"] == arch_b
    assert transitions[0]["readdressed"] is True
    state_1_after = report_state(log_path, msg_1)
    assert state_1_after is not None and state_1_after["state"] == "reported"
    assert delivered[-1][0] == arch_b

    # 3. Timeout / SEND_OUTCOME_UNKNOWN -> `sent, outcome unknown`, never resent, never in tray-picks
    def timeout_sender(_arch: str, _txt: str) -> str:
        raise PaseoBridgeFailure(SEND_OUTCOME_UNKNOWN, "bridge timed out")

    server_timeout = _server(world, sender=timeout_sender)
    ans_2 = _call(server_timeout, "report_to_architect", {"messageId": msg_2, "perspective": "p2"})
    assert ans_2["state"] == "sent, outcome unknown"
    assert not ans_2["file"].endswith(".undelivered.md")
    state_2 = report_state(log_path, msg_2)
    assert state_2 is not None and state_2["state"] == "sent, outcome unknown"
    assert not (log_path.parent / "tray-picks.jsonl").exists()


def test_the_architect_receipt_is_read_by_its_own_path_and_a_task_decoy_is_not_consulted(
    tmp_path: Path,
) -> None:
    """B6: the receipt is the coordination root's own file, never a receipt found by walking tasks."""

    world = _world(tmp_path)
    architect = "33333333-3333-4333-8333-333333333333"
    _record_architect(world, agent=architect)
    receipts = world.root / "notes" / "reports" / "paseo-native-executions"
    receipts.mkdir(parents=True, exist_ok=True)
    workspace = world.root / "workspace" / "architect"
    workspace.mkdir(parents=True, exist_ok=True)
    (receipts / "architect-exact.json").write_text(
        json.dumps(
            {
                "agentId": architect,
                "status": "held",
                "workspace": {"path": workspace.as_posix()},
            }
        ),
        encoding="utf-8",
    )
    # A decoy with the same agent id, deep under tasks: the old walk read this one.
    decoy_folder = (
        world.root
        / "tasks"
        / REPO
        / world.task_root.name
        / "notes"
        / "reports"
        / "paseo-native-executions"
    )
    decoy_folder.mkdir(parents=True, exist_ok=True)
    (decoy_folder / "architect-decoy.json").write_text(
        json.dumps(
            {
                "agentId": architect,
                "status": "running",
                "workspace": {"path": (world.root / "workspace" / "decoy").as_posix()},
            }
        ),
        encoding="utf-8",
    )
    message_id = "10000000-0000-4000-8000-000000000009"
    _file_tray_message(world, message_id=message_id)
    delivered: list[tuple[str, str]] = []
    server = _server(world, sender=lambda arch, txt: delivered.append((arch, txt)) or "reported")

    answer = _call(server, "report_to_architect", {"messageId": message_id, "perspective": "p1"})

    assert answer["state"] == "held"
    assert answer["architect"] == architect
    assert delivered == []


def test_report_delivery_without_an_injected_sender_uses_the_production_bridge(
    tmp_path: Path, monkeypatch
) -> None:
    """I10: the normal server composition delivers through the Paseo bridge, not only a fake sender."""

    world = _world(tmp_path)
    architect = "44444444-4444-4444-8444-444444444444"
    _record_architect(world, agent=architect)
    calls: list[tuple[str, dict[str, object]]] = []

    def _bridge(_config, command: str, payload: dict[str, object]) -> dict[str, object]:
        calls.append((command, dict(payload)))
        return {"delivery": {"delivered": True}}

    monkeypatch.setattr(explainer_server, "bridge_call", _bridge)
    message_id = "10000000-0000-4000-8000-000000000011"
    _file_tray_message(world, message_id=message_id)
    server = _server(world)  # no sender: the normal composition

    answer = _call(server, "report_to_architect", {"messageId": message_id, "perspective": "p1"})

    assert answer["state"] == "reported"
    assert answer["architect"] == architect
    assert calls and calls[0][0] == "agent-send"
    assert calls[0][1]["agentId"] == architect
    assert calls[0][1]["messageId"]
