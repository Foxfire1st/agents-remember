"""The explainer's judgment: categories, grades and per-block staleness (MIK-R187).

A grade is the explainer's judgment, never a recorded fact, and it is stored as such:

* one JSON file per leaf, beside ``review-frames`` in the task's records area, so it survives Retire
  and leaves with the master's retirement or the sprint's archive;
* written atomically under the leaf's own lock, so the grade tool and an Update never write it at
  once, and no other file lock is taken while that lock is held (MIK-R186 rule 2);
* each grade is keyed by the block identity MIK-R89 v2 defines and carries the *fingerprint* it was
  made against (MIK-R187 rule 3). A moved line inside a symbol keeps its fingerprint; a changed file
  does not. Staleness is that comparison, made against one target comparison
  (:mod:`agents_remember.canvas.explainer_parts`).

The bounds are the packet's: at most :data:`MAX_CATEGORIES` live categories per leaf, at most
:data:`MAX_REFERENCES` references in one call, and 160 characters for a reason. A refusal names the
values it refused rather than silently dropping them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import ConfigDict, Field

from agents_remember.canvas.explainer_state import ExplainerRefused
from agents_remember.kernel.atomic_write import atomic_write_text
from agents_remember.memory.knowledge.durable_evidence import durable_reports_root
from agents_remember.models.knowledge.base import KnowledgeModel
from agents_remember.models.knowledge.review_frame import FrameBinding

__all__ = [
    "GRADES_DIRECTORY",
    "GRADES_NAME",
    "MAX_CATEGORIES",
    "MAX_FINGERPRINT_CHARACTERS",
    "MAX_GRADE_CHARACTERS",
    "MAX_REFERENCES",
    "Category",
    "Grade",
    "GradeStore",
    "Grades",
    "ScalePoint",
    "grades_path",
    "read_grades",
]

GRADES_DIRECTORY = "review-grades"
GRADES_NAME = "grades.json"
GRADES_SCHEMA = "ar-review-grades/v1"
MAX_CATEGORIES = 12
MAX_REFERENCES = 200
MAX_GRADE_CHARACTERS = 160
MAX_FINGERPRINT_CHARACTERS = 2048


class ScalePoint(KnowledgeModel):
    """One named grade of a category's scale, highest first."""

    grade: str = Field(min_length=1, max_length=MAX_GRADE_CHARACTERS)
    meaning: str = Field(min_length=1, max_length=MAX_GRADE_CHARACTERS)


class Category(KnowledgeModel):
    """One judgment category: its name, its two-to-five named grades and what they mean."""

    name: str = Field(min_length=1, max_length=MAX_GRADE_CHARACTERS)
    scale: tuple[ScalePoint, ...] = Field(min_length=2, max_length=5)
    meaning: str = Field(default="", max_length=MAX_GRADE_CHARACTERS)


class Grade(KnowledgeModel):
    """One judgment about one block: its identity, its fingerprint, and the binding it was made at."""

    reference: str = Field(min_length=1, max_length=4096)
    category: str = Field(min_length=1, max_length=MAX_GRADE_CHARACTERS)
    grade: str = Field(min_length=1, max_length=MAX_GRADE_CHARACTERS)
    reason: str = Field(min_length=1, max_length=MAX_GRADE_CHARACTERS)
    fingerprint: str = Field(min_length=1, max_length=MAX_FINGERPRINT_CHARACTERS)
    binding: FrameBinding
    made_at: str = Field(min_length=1, max_length=64)
    stale: bool = False
    stale_reason: str | None = Field(default=None, max_length=MAX_FINGERPRINT_CHARACTERS)


class Grades(KnowledgeModel):
    """One leaf's whole judgment store, as it lies on disk."""

    schema_: Literal["ar-review-grades/v1"] = Field(default=GRADES_SCHEMA, alias="schema")
    leaf_id: str = Field(min_length=1, max_length=128)
    categories: tuple[Category, ...] = ()
    grades: tuple[Grade, ...] = ()

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    def category(self, name: str) -> Category | None:
        return next((item for item in self.categories if item.name == name), None)

    def for_reference(self, reference: str) -> tuple[Grade, ...]:
        return tuple(item for item in self.grades if item.reference == reference)


def grades_path(task_root: Path, leaf_id: str) -> Path:
    """Where one leaf's grades lie: ``<records>/review-grades/<leaf>/grades.json``."""

    return durable_reports_root(task_root) / GRADES_DIRECTORY / leaf_id / GRADES_NAME


def read_grades(path: Path, *, leaf_id: str) -> Grades:
    """The leaf's store, or an empty one when no grade was ever made.

    An unreadable store is an error the page shows as a fault: no grade is invented from it.
    """

    if not path.is_file():
        return Grades(leaf_id=leaf_id)
    try:
        return Grades.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ExplainerRefused(
            "grades-unreadable", f"the grade store at {path} cannot be read: {error}"
        ) from error


@dataclass(frozen=True)
class GradeStore:
    """The store's path and leaf, and the writes the tool and the prune make.

    Every write takes the leaf's lock at the caller (:mod:`agents_remember.canvas.explainer_state`),
    reads the store again under it, applies one change and writes the whole file atomically.
    """

    path: Path
    leaf_id: str

    def load(self) -> Grades:
        return read_grades(self.path, leaf_id=self.leaf_id)

    def write(self, grades: Grades) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.path, json.dumps(grades.model_dump(by_alias=True), indent=2) + "\n")

    def define(self, name: str, scale: tuple[ScalePoint, ...], meaning: str = "") -> Grades:
        """Define one category; the same name with another scale or meaning is refused ``category-exists``."""

        if not scale or len(scale) < 2 or len(scale) > 5:
            raise ExplainerRefused(
                "category-scale", "a category's scale is 2 to 5 named grades, highest first"
            )
        if len({point.grade for point in scale}) != len(scale):
            raise ExplainerRefused("category-scale", "each grade of a scale is named once")
        cleaned_meaning = meaning.strip()
        if len(cleaned_meaning) > MAX_GRADE_CHARACTERS or "\n" in meaning or "\r" in meaning:
            raise ExplainerRefused(
                "category-meaning",
                f"a category meaning is one line of at most {MAX_GRADE_CHARACTERS} characters",
            )
        grades = self.load()
        existing = grades.category(name)
        if existing is not None:
            if existing.scale == scale and existing.meaning == cleaned_meaning:
                return grades
            raise ExplainerRefused(
                "category-exists",
                f"the category {name!r} already has another scale or meaning; remove it before "
                "defining it again, because a grade means what its scale and category say",
            )
        if len(grades.categories) >= MAX_CATEGORIES:
            raise ExplainerRefused(
                "category-limit",
                f"the leaf holds {len(grades.categories)} live categories, its admitted "
                f"{MAX_CATEGORIES}; remove one first",
            )
        updated = grades.model_copy(
            update={
                "categories": (
                    *grades.categories,
                    Category(name=name, scale=scale, meaning=cleaned_meaning),
                )
            }
        )
        self.write(updated)
        return updated

    def remove(self, name: str) -> Grades:
        """Remove one category and, with it, its grades."""

        grades = self.load()
        categories = tuple(item for item in grades.categories if item.name != name)
        kept = tuple(item for item in grades.grades if item.category != name)
        updated = grades.model_copy(update={"categories": categories, "grades": kept})
        self.write(updated)
        return updated

    def grade(  # noqa: PLR0913 - one call states its own judgment
        self,
        *,
        references: tuple[str, ...],
        category: str,
        grade: str,
        reason: str,
        fingerprints: dict[str, str],
        binding: FrameBinding,
        at: str,
    ) -> tuple[Grades, tuple[str, ...]]:
        """Grade one or more blocks; answer the store and the references it refused.

        A reference whose fingerprint the product could not compute is refused rather than graded:
        a grade made without a fingerprint could never be marked stale, and would read as current
        for ever.
        """

        if not references:
            raise ExplainerRefused("grade-empty", "a grade names at least one block")
        if len(references) > MAX_REFERENCES:
            raise ExplainerRefused(
                "grade-limit",
                f"a grade names at most {MAX_REFERENCES} references, not {len(references)}",
            )
        if not reason or not reason.strip() or len(reason) > MAX_GRADE_CHARACTERS:
            raise ExplainerRefused(
                "grade-reason", f"a reason is 1 to {MAX_GRADE_CHARACTERS} characters"
            )
        if "\n" in reason or "\r" in reason:
            raise ExplainerRefused(
                "grade-reason", "a grade reason is one line and may not contain a newline"
            )
        grades = self.load()
        known = grades.category(category)
        if known is None:
            raise ExplainerRefused(
                "category-unknown", f"the leaf has no category {category!r}; define it first"
            )
        if grade not in {point.grade for point in known.scale}:
            raise ExplainerRefused(
                "grade-unknown",
                f"{grade!r} is not a grade of {category!r} ({', '.join(point.grade for point in known.scale)})",
            )
        refused: list[str] = []
        made: list[Grade] = []
        for reference in references:
            fingerprint = fingerprints.get(reference)
            if not fingerprint:
                refused.append(reference)
                continue
            made.append(
                Grade(
                    reference=reference,
                    category=category,
                    grade=grade,
                    reason=reason,
                    fingerprint=fingerprint,
                    binding=binding,
                    made_at=at,
                )
            )
        if refused:
            return grades, tuple(refused)
        replaced = {(item.reference, item.category) for item in made}
        kept = tuple(
            item for item in grades.grades if (item.reference, item.category) not in replaced
        )
        updated = grades.model_copy(update={"grades": kept + tuple(made)})
        self.write(updated)
        return updated, ()

    def prune(self, gone: tuple[str, ...]) -> tuple[Grades, int]:
        """Drop the grades of blocks that no longer exist at the bound comparison."""

        if not gone:
            return self.load(), 0
        grades = self.load()
        removed = tuple(item for item in grades.grades if item.reference in set(gone))
        kept = tuple(item for item in grades.grades if item.reference not in set(gone))
        if not removed:
            return grades, 0
        updated = grades.model_copy(update={"grades": kept})
        self.write(updated)
        return updated, len(removed)
