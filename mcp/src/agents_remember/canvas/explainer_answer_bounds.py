"""Each explainer tool's answer bound, and where an answer goes that passes it (MIK-R211 v5 rule 11).

A tool answer carries what the agent needs to know what to do next; everything else is a file in the
explainer's workspace and the answer names it (D598, D599, D608). The bounds are the explainer's own
table, counted with the product's counter (``models/tokens.py``) the way AR's general table counts
its answers (``models/tools/tool_answer.py``, which this does not change). An answer over its bound is
not cut: it is written whole to ``scratch/answers/`` and the reply names that file.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from importlib import import_module
from pathlib import Path
from typing import Any

from agents_remember.canvas.explainer_paths import SCRATCH_DIRECTORY
from agents_remember.canvas.presenter_files import write_output

#: Each kept tool's whole answer, in tokens. render's summary and errors share what its identity
#: items leave of 1,000 (D597); define_category answers its names (about 50), remove_category its
#: count (about 30), and grade its counts with each refused reference and reason (about 300, D608).
EXPLAINER_ANSWER_BOUNDS: dict[str, int] = {
    "render": 1000,
    "define_category": 150,
    "remove_category": 150,
    "grade": 400,
}
#: grade's refused references stay inline up to this many tokens; past it, the total and the file
#: that holds every one (D608).
GRADE_REFUSED_INLINE_TOKENS = 300
#: Where an answer, or a list, that passes its bound is written: inside the workspace, removed with it.
ANSWERS_FOLDER = f"{SCRATCH_DIRECTORY}/answers"
GRADES_FOLDER = f"{SCRATCH_DIRECTORY}/grades"


def answer_tokens(answer: Mapping[str, Any]) -> int:
    """One answer's size in tokens, counted as AR counts every tool answer.

    The counter (and its tokenizer) loads on the first count, so declaring the tools loads neither.
    """

    return import_module("agents_remember.models.tokens").DEFAULT_TOKEN_COUNTER.count(answer)


def write_scratch(workspace: Path, folder: str, stem: str, payload: Any) -> str:
    """Write ``payload`` whole to the next free ``<folder>/<stem>-NNN.json``; answer its path."""

    base = workspace / folder
    base.mkdir(parents=True, exist_ok=True)
    taken = [
        int(item.stem.removeprefix(f"{stem}-"))
        for item in base.glob(f"{stem}-*.json")
        if item.stem.removeprefix(f"{stem}-").isdecimal()
    ]
    relative = f"{folder}/{stem}-{max(taken, default=0) + 1:03d}.json"
    write_output(workspace, relative, json.dumps(payload, indent=1, default=str).encode())
    return relative


def bounded(tool: str, answer: dict[str, Any], workspace: Path) -> dict[str, Any]:
    """``answer`` when it is within ``tool``'s bound; otherwise its outcome and the file holding it.

    The outcome (state, refusal) stays in the reply, so the agent still knows what happened; the
    whole answer is written to ``scratch/answers/`` and named, never cut.
    """

    bound = EXPLAINER_ANSWER_BOUNDS[tool]
    size = answer_tokens(answer)
    if size <= bound:
        return answer
    path = write_scratch(workspace, ANSWERS_FOLDER, tool, answer)
    outcome = {key: answer[key] for key in ("state", "refusal") if key in answer}
    return {
        **outcome,
        "answer": path,
        "moved": f"the whole answer ({size} tokens) is in {path}; this tool answers {bound}",
    }


__all__ = [
    "ANSWERS_FOLDER",
    "EXPLAINER_ANSWER_BOUNDS",
    "GRADES_FOLDER",
    "GRADE_REFUSED_INLINE_TOKENS",
    "answer_tokens",
    "bounded",
    "write_scratch",
]
