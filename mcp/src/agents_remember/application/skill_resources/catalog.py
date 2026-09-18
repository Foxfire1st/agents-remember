"""Build the host's skill discovery registry from one canonical skills tree.

Discovery and delivery are two different jobs and this module keeps them apart.
Building the catalog walks each skill directory once, reads the frontmatter the
Agent Skills specification requires and records a revision per file; serving a
skill later re-reads exactly the file a URI addresses. A skill's body is therefore
never reachable from the metadata the catalog hands out.

The tree is the repository's canonical skills source. A skill directory whose
``SKILL.md`` cannot be read as a conforming skill is not silently dropped: it is
recorded as an unreadable skill and the delivery entry points refuse while one
exists, because serving the remaining skills would let a tree edit quietly delete a
skill from the surface.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from agents_remember.models.skill_resources import (
    MARKDOWN_MIME,
    OPAQUE_MIME,
    SKILL_META_PREFIX,
    SKILL_ROOT_FILE,
    SkillResourceCatalog,
    SkillResourceEntry,
    SkillResourceFile,
    UnreadableSkill,
    declared_allowed_tools,
)

from .frontmatter import SkillFrontmatterError, parse_skill_frontmatter

#: How deep the walk descends below the corpus root. SEP-2640 lets a skill path nest
#: to arbitrary depth and requires a nested skill to be published flat, like any
#: other; this bound exists so a pathological tree cannot make one registration
#: unbounded, and it is stated rather than implied. 32 levels is far beyond any
#: namespace a corpus here could carry.
_MAX_SKILL_DEPTH = 32


class SkillCatalogError(ValueError):
    """A skill could not be served without guessing its identity or its bytes."""


@dataclass(frozen=True, slots=True)
class SkillSourceTree:
    """One canonical skills tree and the server identity that publishes it."""

    root: Path
    origin: str


@dataclass(frozen=True, slots=True)
class ServedSkillFile:
    """One skill file resolved for delivery, with the entry and record it came from."""

    entry: SkillResourceEntry
    record: SkillResourceFile
    content: bytes

    @property
    def text(self) -> str | None:
        """The content as text when it is UTF-8, else ``None`` for a byte delivery."""

        try:
            return self.content.decode("utf-8")
        except UnicodeDecodeError:
            return None


def build_skill_catalog(tree: SkillSourceTree) -> SkillResourceCatalog:
    """Read ``tree`` into a discovery catalog, recording every unreadable skill."""

    root = Path(tree.root)
    if not root.is_dir():
        raise SkillCatalogError(f"skills tree does not exist as a directory: {root}")
    entries: list[SkillResourceEntry] = []
    unreadable: list[UnreadableSkill] = []
    for skill_path in _skill_paths(root):
        try:
            entries.append(_read_skill(root, tree.origin, skill_path))
        except SkillCatalogError as error:
            unreadable.append(UnreadableSkill(skill_path=skill_path, reason=str(error)))
    # The unreadable set rides on the catalog so one build produces one verdict;
    # the delivery entry points read it from there rather than re-walking the tree.
    return SkillResourceCatalog(
        origin=tree.origin,
        source_root=root.as_posix(),
        entries=tuple(sorted(entries, key=lambda item: (item.name, item.origin))),
        unreadable=tuple(sorted(unreadable, key=lambda item: item.skill_path)),
    )


def unreadable_skills(catalog: SkillResourceCatalog) -> tuple[UnreadableSkill, ...]:
    """The unreadable skills recorded when ``catalog`` was built."""

    return catalog.unreadable


def require_servable(catalog: SkillResourceCatalog, *, action: str) -> None:
    """Refuse ``action`` while any discovered skill directory cannot be served."""

    unreadable = unreadable_skills(catalog)
    if not unreadable:
        return
    listed = "; ".join(f"{item.skill_path}: {item.reason}" for item in unreadable)
    raise SkillCatalogError(
        f"{action} refused because {len(unreadable)} skill(s) under {catalog.source_root} "
        f"cannot be served: {listed}"
    )


def read_served_file(catalog: SkillResourceCatalog, uri: str) -> ServedSkillFile:
    """The bytes one catalog URI addresses, re-read from its source file.

    The file is re-read rather than cached so the revision the catalog recorded and
    the bytes a caller receives are checked against the same tree in the same call;
    a tree that moved under the catalog is a refusal, not a stale delivery.
    """

    found = catalog.entry_for_uri(uri)
    if found is None:
        raise SkillCatalogError(f"{uri!r} is not a skill resource this catalog publishes")
    entry, record = found
    skill_directory = Path(catalog.source_root).joinpath(*entry.skill_path.split("/")).resolve()
    target = skill_directory.joinpath(*record.relative_path.split("/"))
    # Containment is proven before any byte is read: a catalog record that could
    # address a file outside its own skill directory is refused, never served.
    if not target.resolve().is_relative_to(skill_directory):
        raise SkillCatalogError(
            f"skill resource {uri!r} resolves outside its skill directory; refused"
        )
    try:
        content = target.read_bytes()
    except OSError as error:
        raise SkillCatalogError(f"skill resource {uri!r} is unreadable: {error}") from error
    observed = _digest(content)
    if observed != record.revision:
        raise SkillCatalogError(
            f"skill resource {uri!r} changed since the catalog was built "
            f"(recorded {record.revision}, observed {observed}); "
            f"the served revision is not the revision on disk"
        )
    return ServedSkillFile(entry=entry, record=record, content=content)


def index_resource_meta(catalog: SkillResourceCatalog) -> dict[str, object]:
    """The ``_meta`` block for ``skill://index.json``: the publishing identity."""

    return {
        f"{SKILL_META_PREFIX}origin": catalog.origin,
        f"{SKILL_META_PREFIX}skills": len(catalog.entries),
    }


def _skill_paths(root: Path) -> tuple[str, ...]:
    """Every directory below ``root`` that carries a ``SKILL.md``.

    A ``SKILL.md`` MAY appear in a descendant directory of another skill, so a nested
    skill is discovered like any other and is published flat: its own entry, whose
    ``uri`` merely shares a path prefix with the enclosing skill's, with nothing in
    the listing marking the nesting. The walk therefore does not stop at a skill
    directory, and only the stated depth bound truncates it.
    """

    found: list[str] = []
    for current, directories, files in os.walk(root):
        directories[:] = sorted(name for name in directories if not name.startswith("."))
        here = Path(current)
        if SKILL_ROOT_FILE in files:
            found.append(here.relative_to(root).as_posix())
        if len(here.relative_to(root).parts) >= _MAX_SKILL_DEPTH:
            directories[:] = []
    return tuple(sorted(found))


def _read_skill(root: Path, origin: str, skill_path: str) -> SkillResourceEntry:
    directory = root.joinpath(*skill_path.split("/"))
    root_file = directory / SKILL_ROOT_FILE
    try:
        text = root_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise SkillCatalogError(f"{skill_path}/{SKILL_ROOT_FILE} is unreadable: {error}") from error
    try:
        frontmatter = parse_skill_frontmatter(text)
    except SkillFrontmatterError as error:
        raise SkillCatalogError(f"{skill_path}/{SKILL_ROOT_FILE}: {error}") from error
    declared_name = frontmatter.name
    if skill_path.rsplit("/", maxsplit=1)[-1] != declared_name:
        raise SkillCatalogError(
            f"{skill_path}/{SKILL_ROOT_FILE} declares name {declared_name!r}, which SEP-2640 "
            f"requires to equal the final skill-path segment {skill_path.rsplit('/', maxsplit=1)[-1]!r}"
        )
    files = tuple(
        _file_record(origin, skill_path, directory, relative)
        for relative in _relative_files(directory)
    )
    if not any(record.relative_path == SKILL_ROOT_FILE for record in files):
        raise SkillCatalogError(f"{skill_path} has no {SKILL_ROOT_FILE} record")
    return SkillResourceEntry(
        name=declared_name,
        origin=origin,
        uri=_skill_root_file_uri(origin, skill_path),
        skill_path=skill_path,
        description=frontmatter.description,
        declared_allowed_tools=declared_allowed_tools(frontmatter.fields),
        files=files,
        frontmatter=frontmatter.fields,
    )


def _relative_files(directory: Path) -> tuple[str, ...]:
    """Every file of one skill, including any nested skill's files.

    SEP-2640 requires an entry's ``resources`` to be complete, and completeness
    extends to nested skills: from the enclosing skill's perspective their files are
    ordinary supporting files, so they are listed here too. The same file therefore
    appears in both the enclosing and the nested skill's entries, which the
    specification states explicitly.
    """

    relatives: list[str] = []
    for current, directories, files in os.walk(directory):
        directories[:] = sorted(name for name in directories if name != "__pycache__")
        here = Path(current)
        for name in sorted(files):
            if name.endswith((".pyc", ".pyo")):
                continue
            relatives.append((here / name).relative_to(directory).as_posix())
    return tuple(sorted(relatives))


def _file_record(
    origin: str, skill_path: str, directory: Path, relative_path: str
) -> SkillResourceFile:
    target = directory.joinpath(*relative_path.split("/"))
    try:
        content = target.read_bytes()
    except OSError as error:
        raise SkillCatalogError(f"{skill_path}/{relative_path} is unreadable: {error}") from error
    return SkillResourceFile(
        relative_path=relative_path,
        uri=f"{_skill_directory_uri(origin, skill_path)}/{relative_path}",
        mime_type=_mime_type(relative_path, content),
        revision=_digest(content),
        byte_length=len(content),
    )


def _skill_directory_uri(origin: str, skill_path: str) -> str:
    """The skill's directory URI: the address every file in it hangs off.

    SEP-2640 makes ``SKILL.md`` explicit rather than implied, so the directory is
    the root URI with the required file stripped, and each file's resource URI is
    the directory plus its own path below the skill root.
    """

    return f"skill://{origin}/{skill_path}"


def _skill_root_file_uri(origin: str, skill_path: str) -> str:
    return f"{_skill_directory_uri(origin, skill_path)}/{SKILL_ROOT_FILE}"


def _mime_type(relative_path: str, content: bytes) -> str:
    if relative_path.endswith(".md"):
        try:
            content.decode("utf-8")
        except UnicodeDecodeError:
            return OPAQUE_MIME
        return MARKDOWN_MIME
    return OPAQUE_MIME


def _digest(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


__all__ = [
    "ServedSkillFile",
    "SkillCatalogError",
    "SkillSourceTree",
    "build_skill_catalog",
    "index_resource_meta",
    "read_served_file",
    "require_servable",
    "unreadable_skills",
]
