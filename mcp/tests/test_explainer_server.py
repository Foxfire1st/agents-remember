"""The explainer's four tools: grading, render and its publication, and their answers (MIK-R211 v5)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from agents_remember.application.review_evidence_packet import (
    comparison_reference,
)
from agents_remember.application.review_frame_store import frame_scope, reopen_frame
from agents_remember.canvas.explainer_access import binding_claim
from agents_remember.canvas.explainer_answer_bounds import (
    EXPLAINER_ANSWER_BOUNDS,
    answer_tokens,
)
from agents_remember.canvas.explainer_grades import grades_path, read_grades
from agents_remember.canvas.explainer_inputs import ExplainerInputs  # the canonical owner
from agents_remember.canvas.explainer_paths import (
    EXPLAINER_FRAME,
    GENERATION_SUFFIX,
    leaf_lock_resource,
)
from agents_remember.canvas.explainer_staleness import BlockIdentity
from agents_remember.canvas.explainer_state import (
    ExplainerBinding,
    agent_id,
    binding_digest,
    read_binding,
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
from agents_remember.mcp.registration.explainer_surface import EXPLAINER_SURFACE
from agents_remember.mcp.registration.explainer_tools import (
    register_bound_explainer_tools,
)  # the entry
from agents_remember.mcp.tools import PUBLIC_TOOLS
from agents_remember.models.knowledge.review_frame import FrameBinding
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS
from canvas_host_test_support import LEAF, REPO, TASK, config, packet, task
from explainer_server_test_support import (
    AT,
    REFUSED_FOREIGN,
    SCALE,
    _call,
    _fingerprints,
    _record,
    _server,
    _server_only,
    _world,
    _write,
)
from knowledge_index_test_support import (
    REVIEW_INVARIANT,
    commit_all,
    init_repository,
    write_review_tree,
)
from mcp.server.fastmcp import FastMCP
from mcp.types import ImageContent

# ---------------------------------------------------------------------------------------------
# 1-4. The grade tools
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
    # ``exclusive_file_lock`` guards ``<resource>.lock``: the resource is the workspace's state/leaf,
    # the same resource the service's build, Update and Retire hold (MIK-R211 v5 rule 12).
    held = leaf_lock_resource(world.workspace)
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
    # MIK-R211 v5 rule 11: the category and its grade names in order, and nothing else.
    assert defined == {"state": "defined", "category": "risk", "grades": ["sound", "thin"]}
    # The lock the seat takes is the leaf's own -- the same file a build, an Update and a Retire hold,
    # and the only one: `exclusive_file_lock` guards `<resource>.lock`, so no `.lock.lock` is made.
    leaf_lock = leaf_lock_resource(world.workspace).with_name("leaf.lock")
    assert leaf_lock.is_file() and not (world.task_root / f"{LEAF}.lock").exists()
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
    assert graded == {"state": "graded", "graded": 2, "refused": 0}
    store = read_grades(grades_path(world.task_root, LEAF), leaf_id=LEAF)
    assert [one.grade for one in store.grades] == ["thin", "thin"]
    assert all(one.binding.comparison_number == 7 for one in store.grades)
    removed = _call(server, "remove_category", {"name": "risk", "binding": "#7"})
    assert removed == {"state": "removed", "category": "risk", "gradesRemoved": 2}


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
    # The refusal names the mismatch and where the binding is read, and carries no binding block.
    assert stale["detail"].startswith("the environment is bound to #8 (sha256:")
    assert str(world.workspace / "binding.json") in stale["detail"] and "'#7'" in stale["detail"]
    assert set(stale) == {"state", "refusal", "detail"}
    store = read_grades(grades_path(world.task_root, LEAF), leaf_id=LEAF)
    assert store.grades == ()
    unknown = _call(server, "remove_category", {"name": "risk", "binding": "whenever"})
    assert unknown["refusal"] == "binding-unknown"
    landable = _call(server, "remove_category", {"name": "risk", "binding": "#8"})
    assert landable["state"] == "removed"
    # A removed environment stays removed: a late grade call is refused before the leaf's lock, so
    # it makes no state/ folder again (MIK-R211 v5 rule 12, removal is complete).
    shutil.rmtree(world.workspace)
    late = _call(server, "define_category", {"name": "risk", "scale": SCALE, "binding": "#8"})
    assert "refusal" in late, late
    assert not world.workspace.exists()


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
            inputs=ExplainerInputs(task_root=world.task_root, fingerprints=_fingerprints),
        ),
        "define_category",
        {"name": "risk", "scale": SCALE, "binding": "#7"},
    )
    assert anonymous["refusal"] == REFUSED_FOREIGN
    assert not grades_path(world.task_root, LEAF).exists()


def test_the_grade_tools_keep_the_scale_shape_and_bound_no_count_or_reason(tmp_path: Path) -> None:
    """The scale's shape stays; no category count, reason length or reference count is refused.

    MIK-R211 v4 rule 3, D567: the explainer is refused no size or count for being the explainer.
    """

    world = _world(tmp_path)
    server = _server(world)
    short_scale = _call(
        server,
        "define_category",
        {"name": "risk", "scale": [SCALE[0]], "binding": "#7"},
    )
    assert short_scale["refusal"] == "category-scale"
    for index in range(13):  # past the old twelve
        defined = _call(
            server, "define_category", {"name": f"cat-{index}", "scale": SCALE, "binding": "#7"}
        )
        assert "refusal" not in defined
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
    assert long_reason.get("refusal") != "grade-reason"
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
    assert too_many.get("refusal") != "grade-limit"


# ---------------------------------------------------------------------------------------------
# 5. The four tools, and AR's general tools beside them
# ---------------------------------------------------------------------------------------------


def test_each_tool_answer_is_held_to_its_bound_and_what_passes_it_is_a_named_file(
    tmp_path: Path,
) -> None:
    """MIK-R211 v5 rule 11: each of the four answers within its own bound (render 1,000, define and
    remove 150, grade 400 -- never above 1,000). An answer that would pass its bound is not cut: the
    reply keeps the outcome and names the file in scratch/answers/ that holds the whole answer."""

    assert EXPLAINER_ANSWER_BOUNDS == {
        "render": 1000,
        "define_category": 150,
        "remove_category": 150,
        "grade": 400,
    }
    world = _world(tmp_path)
    server = _server(world)
    names = [f"{index}-" + "g" * 157 for index in range(5)]  # five grade names at the label length
    scale = [{"grade": name, "meaning": "one point"} for name in names]
    whole = {"state": "defined", "category": "wide", "grades": names}
    assert answer_tokens(whole) > EXPLAINER_ANSWER_BOUNDS["define_category"]
    defined = _call(server, "define_category", {"name": "wide", "scale": scale, "binding": "#7"})
    assert defined["state"] == "defined" and "grades" not in defined
    assert defined["answer"] == "scratch/answers/define_category-001.json"
    assert answer_tokens(defined) <= EXPLAINER_ANSWER_BOUNDS["define_category"]
    kept = json.loads((world.workspace / defined["answer"]).read_text(encoding="utf-8"))
    assert kept == whole  # the next grade call's names, whole, where the reply says
    small = _call(server, "define_category", {"name": "risk", "scale": SCALE, "binding": "#7"})
    assert "answer" not in small and small["grades"] == ["sound", "thin"]


def test_the_explainer_registration_adds_exactly_its_four_tools(tmp_path: Path) -> None:
    """MIK-R211 v5 rule 3: the explainer's own registration is render and the three grade tools.

    AR's general tools come from the normal server's own families beside it
    (`test_explainer_surface.py`); this registration adds none of them and none of the eighteen.
    """

    server = FastMCP("review-explainer")
    register_bound_explainer_tools(server, workspace=tmp_path, seat="seat-1")
    names = {tool.name for tool in asyncio.run(server.list_tools())}
    assert (
        names == set(EXPLAINER_SURFACE) == {"render", "define_category", "remove_category", "grade"}
    )
    assert names.isdisjoint(PUBLIC_TOOLS)


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
    # MIK-R211 v5 rule 11: each refused reference with its reason, inline while it is short ...
    assert refused["refused"] == 1 and refused["refusedReferences"] == [
        {
            "reference": "inv:INV-NOPE/code-candidate/src/missing.py",
            "reason": "the bound comparison has no fingerprint for this block",
        }
    ]
    # ... and past about 300 tokens, the total and the file that holds every one, whole.
    unknown = [f"inv:INV-NOPE/code-candidate/src/missing-{index:03d}.py" for index in range(60)]
    many = _call(
        server,
        "grade",
        {
            "references": unknown,
            "category": "risk",
            "grade": "thin",
            "reason": "absent blocks",
            "binding": "#7",
        },
    )
    assert many["refused"] == 60 and "refusedReferences" not in many
    assert many["refusedFile"] == "scratch/grades/refused-001.json"
    kept = json.loads((world.workspace / many["refusedFile"]).read_text(encoding="utf-8"))
    assert [one["reference"] for one in kept] == unknown and all(one["reason"] for one in kept)
    assert answer_tokens(many) <= EXPLAINER_ANSWER_BOUNDS["grade"]


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


def _bound(world: Any) -> dict[str, str]:
    """What the agent reads in its workspace's binding.json: the claim, the digest and the label.

    No tool answers the binding (MIK-R211 v5 rule 11); the agent reads the file with its own tools.
    """

    held = read_binding(world.workspace)
    assert held is not None
    label = "planned" if held.planned is not None else f"#{held.binding.comparison_number}"
    return {"claim": binding_claim(held), "digest": binding_digest(held), "label": label}


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

    face_old = _bound(world)
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
    face_new = _bound(world)
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
    face_recorded = _bound(world)
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
    assert defined["state"] == "defined"
    stored = read_grades(grades_path(world.task_root, LEAF), leaf_id=LEAF).category("risk")
    assert stored is not None and stored.meaning == "first meaning"  # kept in the store, not echoed
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


#: The runner's own words for the one bound on an interaction run (canvas-interactive-report.mjs).
WINDOW = (
    "the browser load process's own 180 s window"
    " (canvas/prospective.py; not a limit on pages or a product cap)"
)


def _picturing_load(errors: tuple[str, ...], passes: list[dict[str, Any]]) -> Any:
    """A browser load that writes the draft picture and two interaction pictures, as the real one."""

    def load(self, **kwargs):
        self._screenshot.write_bytes(b"\x89PNG the page")
        self._interactive_directory.mkdir()
        for name in ("1280-click-initial.png", "1280-click-control-0.png"):
            (self._interactive_directory / name).write_bytes(b"\x89PNG" + name.encode())
        # The runner's own report: every pass it left incomplete is named with what it did not reach.
        unvisited = [
            {
                "pass": f"{one['width']}-{one['method']}",
                "controls": sum(c["status"] == "unvisited" for c in one["controls"]),
                "started": True,
            }
            for one in passes
            if not one.get("complete")
        ]
        return ProspectiveResult(
            state="loaded",
            kind="browser",
            errors=errors,
            interactive={
                "reportOnly": True,
                "passes": [dict(one) for one in passes],
                "errors": [],
                "unvisited": unvisited,
                "window": WINDOW,
            },
        )

    return load


def _assert_run_folder(run: Path) -> None:
    """One render's run folder holds its draft picture and every interaction picture, whole."""

    assert (run / "page.png").read_bytes() == b"\x89PNG the page"
    assert sorted(item.name for item in (run / "interactions").iterdir()) == [
        "1280-click-control-0.png",
        "1280-click-initial.png",
    ]


def test_render_replies_with_its_report_file_and_publishes_rereading_the_binding_under_the_lock(
    tmp_path: Path, monkeypatch
) -> None:
    """MIK-R211 v5 rule 11 (D597): each render writes renders/run-NNN/ -- report.json, page.png and
    interactions/ -- and its answer names them with the publication state, revision and coverage,
    and holds the summary and whole errors within 1,000 tokens in total with the true error count.
    No picture is in the answer; the file holds every error and the derived part map. A revision
    states its interaction coverage, and a resumed run that changes it publishes a successor rather
    than answering "unchanged" (the Manager's 15:43Z ruling). Each new revision is copied into the
    workspace's own revisions/<n>/ from the task, and the answer says so (the Manager's 16:40Z ruling).
    A moved binding still refuses publication."""

    world = _world(tmp_path)
    errors = tuple(f"console: failure {index:02d} " + "x" * 200 for index in range(40))
    passes = [
        {
            "width": 1280,
            "method": "click",
            "effects": True,
            "complete": False,
            "errors": [],
            "controls": [
                {"index": 0, "status": "exercised"},
                {"index": 1, "status": "error", "error": "the control threw"},
                {"index": 2, "status": "unvisited"},
            ],
        }
    ]

    monkeypatch.setattr(BrowserProspectiveLoad, "load", _picturing_load(errors, passes))
    # A clock that moves: rendering the same draft again later (to resume a run, or to read it again)
    # is the same page, answered "unchanged", never refused as another authoring draft.
    ticks = iter(f"2026-10-22T09:{minute:02d}:00+00:00" for minute in range(60))
    server = _server(world, now=lambda: next(ticks))
    html = (
        "<!doctype html><html><body>"
        '<section data-part="overview" data-part-title="Overview">'
        '<p data-evidence-claim="c-present">Summary</p>'
        '<p data-evidence-claim="c-gone">Earlier</p>'
        "</section></body></html>"
    )
    page = world.workspace / "drafts" / "assets" / "index.html"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(html, encoding="utf-8")  # the explainer writes its page with its own tools
    blocks = asyncio.run(server.call_tool("render", {}))
    blocks = blocks[0] if isinstance(blocks, tuple) else blocks
    assert not any(isinstance(block, ImageContent) for block in blocks)  # no picture in the answer
    rendered = _call(server, "render")
    assert rendered["state"] == "loaded"
    assert rendered["publication"] == {
        "state": "unchanged",
        "revision": 1,
        "coverage": f"0 of 1 pass — stopped before {WINDOW}",
    }
    assert rendered["run"] == "renders/run-002/"
    assert rendered["report"] == "renders/run-002/report.json"
    assert rendered["picture"] == "renders/run-002/page.png"
    assert rendered["interactions"] == {"folder": "renders/run-002/interactions", "count": 2}
    _assert_run_folder(world.workspace / "renders" / "run-002")
    assert not (world.workspace / "drafts" / "host").exists()
    total = len(errors) + 1  # the forty load errors and the one failed control
    shown = len(rendered["errors"])
    assert rendered["errorsTotal"] == total > shown > 0
    assert answer_tokens(rendered) <= EXPLAINER_ANSWER_BOUNDS["render"] == 1000
    assert rendered["moved"] == [
        {"part": "errors", "shown": shown, "total": total, "to": rendered["report"]}
    ]
    report = json.loads((world.workspace / rendered["report"]).read_text(encoding="utf-8"))
    assert len(report["allErrors"]) == total  # the file holds every error, whole
    assert report["allErrors"][:shown] == rendered["errors"]
    assert (
        rendered["summary"]
        == report["summary"]
        == {
            "passes": 1,
            "passesIncomplete": 1,
            "controlsVisited": 2,
            "controlsFailed": 1,
            "checksPassed": report["readiness"]["checks"]["passed"],
            "checksFailed": 0,
            "errors": total,
        }
    )
    assert report["readiness"]["ready"] is True
    assert report["publication"]["partMap"] == [
        {"part": "overview", "title": "Overview", "claims": ["c-present", "c-gone"]}
    ]
    scope = frame_scope(world.task_root, LEAF, EXPLAINER_FRAME)
    opened = reopen_frame(scope)
    assert opened.state == "ready" and opened.manifest is not None
    assert len(opened.manifest.part_map) == 1
    assert opened.manifest.part_map[0].part == "overview"
    first = opened.manifest
    assert first.interaction_coverage is not None
    assert first.interaction_coverage.window == WINDOW
    assert _tree_digest(world.workspace / "revisions" / "1") == _tree_digest(
        scope.frame_dir / "revisions" / "1"
    )

    _assert_a_resumed_run_publishes_a_successor(world, server, scope, passes, first)
    page.write_text(html.replace("Summary", "V2"), encoding="utf-8")
    _assert_a_moved_binding_refuses_and_keeps_the_revision(world, server, scope)


def _assert_a_resumed_run_publishes_a_successor(
    world: Any, server: FastMCP, scope: Any, passes: list[dict[str, Any]], first: Any
) -> None:
    """The resumed run reaches what was left: the same page with new coverage is a successor
    revision that says so, never the old revision quietly patched; the same again is unchanged.
    The successor is in the workspace's revisions/2/ at once, byte for byte the task's."""

    passes[0]["controls"][2]["status"] = "exercised"
    passes[0]["complete"] = True
    resumed = _call(server, "render")
    assert resumed["publication"] == {
        "state": "published",
        "revision": 2,
        "coverage": "all 1 pass",
        "workspace": "refreshed revisions/2/ from the task's published revision 2",
    }
    copied = world.workspace / "revisions" / "2"
    assert (copied / "manifest.json").is_file() and (copied / "assets" / "index.html").is_file()
    assert _tree_digest(copied) == _tree_digest(scope.frame_dir / "revisions" / "2")
    successor = reopen_frame(scope).manifest
    assert successor is not None and successor.revision == 2
    assert successor.artifact_digest == first.artifact_digest
    assert successor.interaction_coverage is not None and successor.interaction_coverage.complete
    again = _call(server, "render")
    assert again["publication"] == {"state": "unchanged", "revision": 2, "coverage": "all 1 pass"}
    # A reopened explainer is a new session (rule 12): its render of the same bytes is the same
    # request, answered unchanged, with nothing published and the published revision untouched.
    published = _tree_digest(scope.frame_dir / "revisions")
    reopened = _call(_server(world, seat="seat-reopened", now=lambda: AT), "render")
    assert reopened["publication"] == again["publication"], reopened.get("publication")
    assert _tree_digest(scope.frame_dir / "revisions") == published
    assert reopen_frame(scope).manifest == successor


def _tree_digest(folder: Path) -> str:
    """One digest of a folder's files: each relative path and its bytes, in path order."""

    digest = hashlib.sha256()
    for item in sorted(path for path in folder.rglob("*") if path.is_file()):
        digest.update(item.relative_to(folder).as_posix().encode() + b"\0" + item.read_bytes())
    return digest.hexdigest()


def _assert_a_moved_binding_refuses_and_keeps_the_revision(
    world: Any, server: FastMCP, scope: Any
) -> None:
    """binding.json moved before the next publication: refused `binding-moved` in its own run, and
    the published revision keeps its original binding (#7), not #9."""

    current = json.loads((world.workspace / "binding.json").read_text())
    moved = ExplainerBinding.model_validate(
        {**current, "binding": {**current["binding"], "comparison_number": 9}}
    )
    write_binding(world.workspace, moved)
    refused_render = _call(server, "render")
    assert refused_render["state"] == "refused"
    assert refused_render["publication"]["refusal"] == "binding-moved"
    assert refused_render["report"] == "renders/run-006/report.json"  # its own run (the sixth)
    reopened = reopen_frame(scope)
    assert reopened.manifest is not None and reopened.manifest.binding.comparison_number == 7
