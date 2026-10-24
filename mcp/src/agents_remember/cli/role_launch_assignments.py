"""The assignment text of the two roles a launch admits without a canonical task.

MIK-R196 rule 2 puts Bootstrap outside the task tree, and an Investigator's concern arrives from
its parent rather than from a document. Their assignment sentences are procedure, not preparation
logic, so they live here and :mod:`agents_remember.cli.role_launch_preparation` keeps the one call
that asks for them — which is also what keeps that already-large module from growing.
"""

from __future__ import annotations

from typing import Any


def architect_assignment(report_path: str) -> str:
    """A project-scoped launch: the developer names the repository before any trust-packet work."""

    return (
        "Project-scoped architect launch. Ask the developer what outcome they want and which "
        "registered repository it concerns. The shared Projects workspace is an execution "
        "location, not a repository selection. Until the developer names a repository, do no "
        "repository-specific trust-packet or task-corpus work; do not infer a target from the "
        "workspace name or from a singleton repository registry. Then orient from the selected "
        "repository's existing AR task portfolio. Do not invent a sprint or task identity. "
        f"Write this session's report to {report_path}."
    )


def investigator_assignment(report_path: str, *, started_by: Any | None) -> str:
    """One scoped concern, one report, no writes outside the report."""

    concern = (
        "The concern arrives in the parent's first role_message. Ask that parent once for a "
        "missing concern or scope and wait; do not invent one."
        if started_by
        else "Ask the developer for the scoped concern before acting."
    )
    return (
        f"Investigator launch: investigate one scoped concern of any kind. {concern} Write one "
        f"report to {report_path}. Change no code checkout or leaf enclosure, memory, task "
        "document or status; make no commit, closeout or integration and start no role. Change "
        "provider or system state only after the report and an explicit authorized order."
    )


def bootstrap_assignment(report_path: str, *, started_by: Any | None) -> str:
    """MIK-R196 rule 2: parentless and taskless, nothing invented, one repository set up.

    Nothing here names a sprint, master, leaf, task document, worktree or enclosure, and no
    repository is selected for the user: the setup conversation chooses one.
    """

    made = (
        "The user started this seat with one call from their own chat; that chat needs no "
        "further identity."
        if started_by is None
        else "Your parent started this seat."
    )
    return (
        "Bootstrap launch: set one repository up through the surfaces that own each step "
        "and report what actually happened. Ask the developer which code repository they "
        "mean, which branch is its memory's foundation, whether the memory repository is new "
        "or existing, and whether providers should index now or be deferred; invent no "
        "repository, branch, task or identity, and create no task, worktree or role. "
        f"Write the setup report to {report_path} and name on its first line where it "
        f"stands. {made}"
    )
