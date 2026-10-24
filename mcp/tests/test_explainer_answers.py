"""MIK-R204 rule 1: the answer boundary was extracted, not relocated — the identity must hold.

`answered` and `refusal` live in a light module now so a declaration owner can reach them without loading
the readers behind them. `explainer_access` re-exports them, and this pins that both paths are the *same
function object*: a caller's import and a declaration owner's import cannot diverge into two behaviours.
"""

from __future__ import annotations

import subprocess
import sys

from agents_remember.canvas import explainer_access, explainer_answers


def test_the_two_paths_reach_the_same_function_objects() -> None:
    assert explainer_access.answered is explainer_answers.answered
    assert explainer_access.refusal is explainer_answers.refusal


def test_the_light_module_imports_no_service() -> None:
    """The point of the extraction: importing it must not pull a reader, a writer or a service."""

    probe = (
        "import sys, agents_remember.canvas.explainer_answers;"
        "print('|'.join(sorted(m for m in sys.modules if m.startswith('agents_remember'))))"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=False
    ).stdout
    loaded = set(out.strip().split("|"))
    for forbidden in (
        "agents_remember.canvas.explainer_access",
        "agents_remember.canvas.explainer_server",
        "agents_remember.mcp.tools",
        "fastapi",
        "tiktoken",
    ):
        assert forbidden not in loaded, forbidden
