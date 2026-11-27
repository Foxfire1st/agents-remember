"""The list of a consolidation pass: the families and rules a task reads again against the code.

A task whose document names a non-empty ``consolidationList`` is a consolidation task
(:mod:`.consolidation_task` answers that from the document file). This module is the list's schema.

The task plane checks the shape only: ids shaped ``FAM-…`` and ``INV-…``, at least one id in total,
no id twice in one list, and no size limit. It never reads knowledge, so an id the memory does not
hold is the worklist's fact, not a refusal here.
"""

from __future__ import annotations

from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents_remember.models.knowledge_files.ids import RECORD_PREFIXES, id_pattern

__all__ = ["ConsolidationList", "check_consolidation_list"]

ListedFamily = Annotated[str, Field(pattern=id_pattern(RECORD_PREFIXES["family"]))]
ListedRule = Annotated[str, Field(pattern=id_pattern(RECORD_PREFIXES["invariant"]))]


class ConsolidationList(BaseModel):
    """``{families?, rules?}``: the families and the rules of one pass, by their ids."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    families: list[ListedFamily] | None = None
    rules: list[ListedRule] | None = None

    @model_validator(mode="after")
    def _require_ids(self) -> Self:
        if not self.listed_ids():
            raise ValueError(
                "consolidationList names at least one family (FAM-…) or rule (INV-…); "
                "clear it with null instead"
            )
        for name, ids in (("families", self.families or ()), ("rules", self.rules or ())):
            repeated = sorted({one for one in ids if ids.count(one) > 1})
            if repeated:
                raise ValueError(
                    f"consolidationList.{name} names an id twice: {', '.join(repeated)}"
                )
        return self

    def listed_ids(self) -> tuple[str, ...]:
        """Every listed id, families first, each as listed."""

        return (*(self.families or ()), *(self.rules or ()))


def check_consolidation_list(kind: str, listing: ConsolidationList | None) -> None:
    """A master names no list: a consolidation runs as a task of its own master."""

    if listing is not None and kind == "master":
        raise ValueError("consolidationList belongs to a task document, not a master")
