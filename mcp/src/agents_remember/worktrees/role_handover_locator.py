"""The bounded, product-owned locator of one launch's hand-over artifact.

The hand-over artifact is written beside the report the agent is told to write, in whatever
folder the task's enclosure, the execution folder or the worktree group has. The path to it
therefore grows with that folder, while the recovery note that names it has a fixed budget of
characters (PNT-R04 rule 6). The product publishes one directory alias per launch request under
a durable root of its own -- ``~/projects/.agents-remember/handover-locators/<request id>`` --
and names the artifact through it: ``<request id>/<artifact name>``. The alias is a link of one
directory; the artifact keeps its own name, its own bytes and its write-once mode, and no link is
ever followed at the artifact's own name (:mod:`agents_remember.cli.immutable_artifact`).

The locator belongs to its request: a repeat of the same request reuses it, one that would lead
to another folder is refused before anything is written, a lost one is rebuilt at the place its
receipt recorded, and the end of resumability removes the alias alone, never the report folder
it leads to.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

# Under the developer's projects folder, beside the product's other durable records: not in a
# worktree, an enclosure or a scratch folder, all of which are removed when a task ends.
LOCATOR_ROOT_PARTS = ("projects", ".agents-remember", "handover-locators")


def locator_root() -> Path:
    """The durable folder this product owns every launch's locator under."""

    return Path.home().joinpath(*LOCATOR_ROOT_PARTS)


def locator_directory(request_id: str) -> Path:
    """The alias of one launch request; its name is the request's own id."""

    if not request_id or request_id in {".", ".."} or "/" in request_id or "\\" in request_id:
        raise ValueError(f"'{request_id}' cannot name the handover locator of a request.")
    return locator_root() / request_id


def is_locator(directory: Path) -> bool:
    """Whether ``directory`` is where this product puts the locator of a request."""

    return directory.parent == locator_root()


def ensure_locator(directory: Path, target: Path) -> None:
    """Make ``directory`` lead to ``target``: reuse the right alias, create a lost one, refuse another.

    A target that is already pointed at is reused, so a repeat, a resume and a restarted process
    keep one locator; an absent one is created, so a lost locator is rebuilt at the same place.
    Anything else at the name -- a link to another folder, a real directory, a file -- is refused
    with what has to be put right, before the launch writes any of its files.
    """

    resolved = target.resolve(strict=False)
    try:
        mode = directory.lstat().st_mode
    except FileNotFoundError:
        directory.parent.mkdir(parents=True, exist_ok=True)
        try:
            directory.symlink_to(resolved, target_is_directory=True)
        except FileExistsError:
            # Another launch of the same request published it in the meantime.
            _leads_to(directory, resolved)
        return
    except OSError as error:
        raise ValueError(locator_refusal(directory, resolved)) from error
    if not stat.S_ISLNK(mode):
        raise ValueError(locator_refusal(directory, resolved))
    _leads_to(directory, resolved)


def locator_refusal(directory: Path, target: Path) -> str:
    """What a launch or a restore says about a name that belongs to another folder."""

    return (
        f"The handover locator {directory} does not lead to this request's report folder "
        f"{target}. A locator belongs to the request that made it: remove what stands at that "
        "name and retry the same request."
    )


def remove_locator(directory: Path, *, within: Path, dry_run: bool = False) -> dict[str, Any]:
    """Remove one alias, and only it, when the folder it leads to is inside ``within``.

    The report folder itself is never touched, and a name that is not this product's link -- or
    one that leads outside the folders being reclaimed -- is left as it stands and reported.
    """

    if not directory.is_symlink():
        return {
            "path": directory.as_posix(),
            "removed": False,
            "reason": "no-locator",
        }
    target = Path(os.readlink(directory))
    if not target.is_absolute():
        target = directory.parent / target
    if not target.resolve(strict=False).is_relative_to(within.resolve(strict=False)):
        return {
            "path": directory.as_posix(),
            "removed": False,
            "reason": "leads-outside",
        }
    if not dry_run:
        directory.unlink()
    return {
        "path": directory.as_posix(),
        "would_remove" if dry_run else "removed": True,
        "reason": None,
    }


def _leads_to(directory: Path, target: Path) -> None:
    if directory.resolve(strict=False) != target:
        raise ValueError(locator_refusal(directory, target))
