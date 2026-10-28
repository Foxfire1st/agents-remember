"""Explicit comparison witness for tests whose native producer is an injected fixture."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agents_remember.models.knowledge.review_trees import (
    ReviewTrees,
    ReviewTreesResult,
    TreeKnowledge,
)
from agents_remember.serving.review_trees import ReviewTreesQuery


@dataclass(frozen=True)
class FixtureWitness:
    read: Callable[[], ReviewTreesResult]
    expected: str

    def current(self) -> bool:
        return self.read().model_dump_json() == self.expected


@dataclass(frozen=True)
class FixturePreparation:
    result: ReviewTreesResult
    witness: FixtureWitness
    native: tuple[object, ReviewTrees]


def prepared_from_port(port: Callable[[ReviewTreesQuery], ReviewTreesResult]):
    def prepare(query: ReviewTreesQuery) -> FixturePreparation | ReviewTreesResult:
        result = port(query)
        if result.state != "trees":
            return result
        record = result.comparison
        assert record is not None and len(result.knowledge_sides) == 2
        trees = ReviewTrees(
            record,
            None,
            Path(record.memory_candidate.repository),
            TreeKnowledge(result.knowledge_sides[0], None),
            TreeKnowledge(result.knowledge_sides[1], None),
            code_sides=result.code_sides,
        )
        return FixturePreparation(
            result, FixtureWitness(lambda: port(query), result.model_dump_json()), (None, trees)
        )

    return prepare
