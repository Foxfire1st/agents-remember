"""The explainer's grades: categories, bounds, binding and pruning (MIK-R187 rules 2, 7).

Each case protects one promise the store makes: a category's scale is its meaning, the same name
with another scale is refused, a grade carries the binding and fingerprint it was made at, an
uncomputable fingerprint is refused rather than graded, and the store survives a Retire by living
with the task's records.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from agents_remember.canvas.explainer_grades import (
    MAX_CATEGORIES,
    GradeStore,
    ScalePoint,
    grades_path,
    read_grades,
)
from agents_remember.canvas.explainer_state import ExplainerRefused
from agents_remember.models.knowledge.review_frame import FrameBinding
from canvas_host_test_support import LEAF, TASK, packet, record

AT = "2026-10-22T09:00:00+00:00"
SCALE = (
    ScalePoint(grade="sound", meaning="the claim holds against the recorded evidence"),
    ScalePoint(grade="thin", meaning="the claim rests on less than it says"),
)


def _binding(number: int = 7) -> FrameBinding:
    return FrameBinding.from_packet(packet(record(number)).comparison)


def _store(tmp_path: Path) -> GradeStore:
    task_root = tmp_path / "tasks" / "agents-remember" / TASK
    task_root.mkdir(parents=True)
    return GradeStore(path=grades_path(task_root, LEAF), leaf_id=LEAF)


def test_a_grade_is_the_judgment_it_was_made_as(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.define("risk", SCALE)
    _grades, refused = store.grade(
        references=("inv:INV-AAA/code-candidate/src/app.py",),
        category="risk",
        grade="thin",
        reason="the claim rests on one hunk",
        fingerprints={"inv:INV-AAA/code-candidate/src/app.py": "sha256:abc"},
        binding=_binding(),
        at=AT,
    )
    assert refused == ()
    stored = read_grades(store.path, leaf_id=LEAF)
    assert stored.categories[0].scale == SCALE
    one = stored.grades[0]
    assert (one.grade, one.reason, one.fingerprint) == (
        "thin",
        "the claim rests on one hunk",
        "sha256:abc",
    )
    assert one.binding.same_comparison(_binding())
    assert one.stale is False


def test_the_same_category_name_with_another_scale_is_refused(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.define("risk", SCALE, meaning="overall change risk")
    assert store.define("risk", SCALE, meaning="overall change risk").categories[0].meaning == (
        "overall change risk"
    )
    with pytest.raises(ExplainerRefused) as diff_meaning:
        store.define("risk", SCALE, meaning="different meaning")
    assert diff_meaning.value.reason == "category-exists"
    with pytest.raises(ExplainerRefused) as refused:
        store.define(
            "risk",
            (ScalePoint(grade="high", meaning="bad"), ScalePoint(grade="low", meaning="fine")),
            meaning="overall change risk",
        )
    assert refused.value.reason == "category-exists"


def test_the_category_bound_is_named_and_removal_takes_its_grades(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for index in range(MAX_CATEGORIES):
        store.define(f"cat-{index}", SCALE)
    with pytest.raises(ExplainerRefused) as refused:
        store.define("one-more", SCALE)
    assert refused.value.reason == "category-limit"
    store.grade(
        references=("block-a",),
        category="cat-0",
        grade="sound",
        reason="checked",
        fingerprints={"block-a": "sha256:a"},
        binding=_binding(),
        at=AT,
    )
    after = store.remove("cat-0")
    assert all(item.name != "cat-0" for item in after.categories)
    assert all(item.category != "cat-0" for item in after.grades)


def test_a_reference_without_a_fingerprint_is_refused_not_graded(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.define("risk", SCALE)
    grades, refused = store.grade(
        references=("block-a", "block-b"),
        category="risk",
        grade="sound",
        reason="checked",
        fingerprints={"block-a": "sha256:a"},
        binding=_binding(),
        at=AT,
    )
    assert refused == ("block-b",)
    assert grades.grades == ()


def test_an_unknown_category_or_grade_is_refused_by_name(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(ExplainerRefused) as no_category:
        store.grade(
            references=("block-a",),
            category="risk",
            grade="sound",
            reason="checked",
            fingerprints={"block-a": "sha256:a"},
            binding=_binding(),
            at=AT,
        )
    assert no_category.value.reason == "category-unknown"
    store.define("risk", SCALE)
    with pytest.raises(ExplainerRefused) as no_grade:
        store.grade(
            references=("block-a",),
            category="risk",
            grade="excellent",
            reason="checked",
            fingerprints={"block-a": "sha256:a"},
            binding=_binding(),
            at=AT,
        )
    assert no_grade.value.reason == "grade-unknown"


def test_a_gone_block_is_pruned_and_counted(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.define("risk", SCALE)
    store.grade(
        references=("block-a", "block-b"),
        category="risk",
        grade="sound",
        reason="checked",
        fingerprints={"block-a": "sha256:a", "block-b": "sha256:b"},
        binding=_binding(),
        at=AT,
    )
    grades, removed = store.prune(("block-b",))
    assert removed == 1
    assert [item.reference for item in grades.grades] == ["block-a"]


def test_multiline_reasons_are_refused_and_long_path_or_absent_fingerprints_fit_storage(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.define("risk", SCALE)
    for bad_reason in ("first line\nsecond line", "first line\rsecond line"):
        with pytest.raises(ExplainerRefused) as bad:
            store.grade(
                references=("block-a",),
                category="risk",
                grade="sound",
                reason=bad_reason,
                fingerprints={"block-a": "sha256:a"},
                binding=_binding(),
                at=AT,
            )
        assert bad.value.reason == "grade-reason"
    assert store.load().grades == ()
    long_path = "mcp/src/agents_remember/application/review_tree_comparison_long_module_name.py"
    long_hunk_fp = f"hunk:{long_path}@sha256:{'a' * 64}..sha256:{'b' * 64}"
    assert len(long_hunk_fp) > 200
    deleted_file_fp = "file:assets/binary/logo.png@absent"
    written, refused = store.grade(
        references=("hunk-long", "file-deleted"),
        category="risk",
        grade="thin",
        reason="both admitted fingerprints fit storage",
        fingerprints={"hunk-long": long_hunk_fp, "file-deleted": deleted_file_fp},
        binding=_binding(),
        at=AT,
    )
    assert refused == ()
    by_ref = {item.reference: item.fingerprint for item in written.grades}
    assert by_ref["hunk-long"] == long_hunk_fp
    assert by_ref["file-deleted"] == deleted_file_fp
