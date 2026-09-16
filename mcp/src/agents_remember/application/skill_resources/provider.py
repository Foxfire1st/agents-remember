"""Where the served skills tree comes from.

The served tree is the package's own runtime skills copy. That copy is generated
from the repository's canonical ``skills/`` tree by the repository's sync script,
and publishing the packaged copy is what makes a served revision reproducible: the
bytes a client reads belong to exactly one installed build, so a digest recorded in
one exchange still names the same content in the next.

An operator can serve a different tree by supplying the root explicitly, which is
how a test drives a synthetic tree without touching the shipped corpus.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from agents_remember.install.assets import packaged_source_root

from .catalog import SkillSourceTree

#: The server identity that publishes the shipped skills tree. It is the first two
#: skill-path segments of every shipped skill URI and the ``origin`` half of a
#: skill's distinct identity, so two servers serving one skill name stay distinct.
SHIPPED_SKILL_ORIGIN = "agents-remember/skills"

#: The packaged runtime skills copy, relative to the package-data root.
PACKAGED_SKILLS_DIRECTORY = "runtime/skills"

#: The admitted corpus inside that copy: the lifecycle tree whose own directory is
#: the base of every source path the composition manifest declares.
PACKAGED_COMPOSITION_ROOT = "l-01-agent-lifecycles"

#: The composition manifest inside that corpus.
PACKAGED_COMPOSITION_MANIFEST = "composition-manifest.json"


@contextmanager
def shipped_skill_tree() -> Iterator[SkillSourceTree]:
    """Yield the shipped skills tree for the duration of one server's registration."""

    with packaged_source_root() as root:
        yield SkillSourceTree(
            root=Path(root) / PACKAGED_SKILLS_DIRECTORY,
            origin=SHIPPED_SKILL_ORIGIN,
        )


@contextmanager
def shipped_composition_corpus() -> Iterator[tuple[Path, str]]:
    """Yield the shipped corpus root and its manifest, both admitted together.

    The two are one value because they are one admission: a corpus root without its
    manifest, or a manifest read from another root, is a pairing that cannot select
    a source set.
    """

    with packaged_source_root() as root:
        yield (
            Path(root) / PACKAGED_SKILLS_DIRECTORY / PACKAGED_COMPOSITION_ROOT,
            PACKAGED_COMPOSITION_MANIFEST,
        )


__all__ = [
    "PACKAGED_COMPOSITION_MANIFEST",
    "PACKAGED_COMPOSITION_ROOT",
    "PACKAGED_SKILLS_DIRECTORY",
    "SHIPPED_SKILL_ORIGIN",
    "shipped_composition_corpus",
    "shipped_skill_tree",
]
