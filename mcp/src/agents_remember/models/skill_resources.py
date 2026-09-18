"""Value types for serving canonical reusable skills over SEP-2640.

These are the model-owned half of the skills transport: the host discovery
registry, the per-file revisions, and the SEP-2640 ``skills/list`` /
``skills/get`` entry shape. They are pure values plus their rendering, so the
``application`` layer that owns the reading and the ``mcp`` layer that owns the
registration both import one definition instead of keeping two copies of the wire
shape in step.

Identity is ``origin`` (the publishing server's identity) plus the resource URI.
A resource read is data delivery: nothing here activates a skill, grants a tool
permission, or marks server-supplied text as trusted, and there is deliberately no
field through which it could. The ``allowed-tools`` key a skill's frontmatter may
carry is recorded as an observed fact and never applied (§ SEP-2640 Security
Implications: hosts MUST NOT honor mechanisms in skill content).

The entry shape is the final specification's: ``{uri, frontmatter, resources}``,
where ``frontmatter`` is the verbatim ``SKILL.md`` frontmatter rendered as JSON and
``resources`` enumerates every file of the skill with its digest and size. A
nested skill is published flat: an ordinary entry whose ``uri`` happens to share a
path prefix with the enclosing skill's, with nothing marking the nesting.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

#: The SEP-2640 extension identifier, declared in the ``initialize`` response.
SKILLS_EXTENSION_ID = "io.modelcontextprotocol/skills"

#: This server's own well-known discovery index resource, served as an ordinary
#: resource. It is deliberately *not* presented as the extension's enumeration
#: surface: SEP-2640 enumerates through the ``skills/list`` method.
SKILL_INDEX_URI = "skill://index.json"

#: The Agent Skills well-known discovery index schema this server's own index emits.
SKILL_INDEX_SCHEMA = "https://schemas.agentskills.io/discovery/0.2.0/schema.json"

#: Reverse-domain prefix the extension reserves for skill ``_meta`` keys.
SKILL_META_PREFIX = "io.modelcontextprotocol.skills/"

#: The frontmatter key a skill uses to declare behavior a host must not honor.
ALLOWED_TOOLS_KEY = "allowed-tools"

#: The one file every skill must carry at its root (Agent Skills specification).
SKILL_ROOT_FILE = "SKILL.md"

MARKDOWN_MIME = "text/markdown"
JSON_MIME = "application/json"
OPAQUE_MIME = "application/octet-stream"

#: The SEP-2640 result type every entry-bearing result declares.
RESULT_TYPE_COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class SkillResourceFile:
    """One addressable file inside a skill directory.

    ``revision`` is the digest of this file's bytes, so a reader can tell whether
    what it holds is still what the server publishes without re-reading the tree.
    ``byte_length`` is the same file's raw length, which the entry's ``size`` field
    carries so a host can budget a skill before fetching anything.
    """

    relative_path: str
    uri: str
    mime_type: str
    revision: str
    byte_length: int


@dataclass(frozen=True, slots=True)
class SkillResourceEntry:
    """One skill in the host's discovery registry.

    ``frontmatter`` is the verbatim ``SKILL.md`` frontmatter, every field the author
    wrote, which is what the specification requires an entry to publish. ``origin``
    is the publishing server's identity and is part of the skill's identity, so two
    servers serving a skill of the same name stay two distinct entries rather than
    collapsing into one.

    ``declared_allowed_tools`` records the frontmatter's ``allowed-tools`` value when
    present. It is an observation, never a grant: no code path turns it into a tool
    permission.
    """

    name: str
    origin: str
    uri: str
    skill_path: str
    description: str
    declared_allowed_tools: tuple[str, ...]
    files: tuple[SkillResourceFile, ...]
    frontmatter: Mapping[str, Any] = field(default_factory=dict)

    @property
    def identity(self) -> str:
        """The distinct identity: which server publishes which skill."""

        return f"{self.origin}#{self.name}"

    @property
    def root(self) -> SkillResourceFile | None:
        """The skill's ``SKILL.md`` record, or ``None`` when it is absent."""

        for record in self.files:
            if record.relative_path == SKILL_ROOT_FILE:
                return record
        return None

    def entry_document(self) -> dict[str, Any]:
        """The SEP-2640 entry: ``{uri, frontmatter, resources}``.

        ``resources`` is complete by construction — the catalog enumerates every file
        of the skill, each exactly once, and refuses to publish a skill whose root
        file is missing from that set.
        """

        return {
            "uri": self.uri,
            "frontmatter": dict(self.frontmatter),
            "resources": [
                {"uri": record.uri, "digest": record.revision, "size": record.byte_length}
                for record in self.files
            ],
        }


@dataclass(frozen=True, slots=True)
class UnreadableSkill:
    """One discovered skill directory that cannot be served, with its cause.

    An unreadable skill is a recorded fact of one build, never a silent omission:
    the delivery entry points refuse while one exists, so a tree edit cannot make a
    published skill disappear from the surface without saying so.
    """

    skill_path: str
    reason: str


@dataclass(frozen=True, slots=True)
class SkillResourceCatalog:
    """Every skill one server publishes, plus the tree they were read from.

    The catalog is host bookkeeping for discovery. It is deliberately not the
    model-visible channel: listing metadata for a skill does not read its body, and
    reading one skill's body does not read the others.
    """

    origin: str
    source_root: str
    entries: tuple[SkillResourceEntry, ...]
    unreadable: tuple[UnreadableSkill, ...] = ()

    def entry(self, identity: str) -> SkillResourceEntry | None:
        """The skill whose ``origin#name`` identity is ``identity``."""

        for candidate in self.entries:
            if candidate.identity == identity:
                return candidate
        return None

    def entry_for_uri(self, uri: str) -> tuple[SkillResourceEntry, SkillResourceFile] | None:
        """The skill and file record one resource URI addresses."""

        for candidate in self.entries:
            for record in candidate.files:
                if record.uri == uri:
                    return candidate, record
        return None

    def skill_for_root_uri(self, uri: str) -> SkillResourceEntry | None:
        """The skill whose ``SKILL.md`` resource URI is ``uri``.

        This is what ``skills/get`` answers for: the specification requires an entry
        for every skill the server serves, listed or not.
        """

        for candidate in self.entries:
            if candidate.uri == uri:
                return candidate
        return None

    def names(self) -> tuple[str, ...]:
        return tuple(entry.name for entry in self.entries)

    def entry_documents(self) -> tuple[dict[str, Any], ...]:
        """One SEP-2640 entry per skill, in stable order, carrying no file body."""

        return tuple(
            entry.entry_document()
            for entry in sorted(self.entries, key=lambda item: (item.name, item.origin))
        )

    def discovery_metadata(self) -> tuple[dict[str, object], ...]:
        """One metadata row per skill, carrying no file body.

        This is what this server's own listing surfaces hand out. It is the whole of
        the discovery surface: name, description, origin, the ``SKILL.md`` URI, the
        root revision and the file paths. Bodies are not reachable from here.
        """

        rows: list[dict[str, object]] = []
        for entry in sorted(self.entries, key=lambda item: (item.name, item.origin)):
            root = entry.root
            rows.append(
                {
                    "name": entry.name,
                    "description": entry.description,
                    "origin": entry.origin,
                    "uri": entry.uri,
                    "revision": "" if root is None else root.revision,
                    "skillPath": entry.skill_path,
                }
            )
        return tuple(rows)

    def index_document(self) -> dict[str, object]:
        """This server's own ``skill://index.json`` payload.

        This is the Agent Skills well-known discovery index shape, which is *this
        server's* convenience surface. It is not the extension's enumeration result:
        SEP-2640 enumerates through ``skills/list``, whose entries carry verbatim
        frontmatter and per-file digests.
        """

        skills: list[dict[str, object]] = []
        for entry in sorted(self.entries, key=lambda item: (item.name, item.origin)):
            root = entry.root
            skills.append(
                {
                    "name": entry.name,
                    "type": "skill-md",
                    "description": entry.description,
                    "url": entry.uri,
                    "_meta": {
                        f"{SKILL_META_PREFIX}origin": entry.origin,
                        f"{SKILL_META_PREFIX}revision": "" if root is None else root.revision,
                    },
                }
            )
        return {"$schema": SKILL_INDEX_SCHEMA, "skills": skills}

    def index_bytes(self) -> bytes:
        return json.dumps(self.index_document(), indent=2, sort_keys=False).encode("utf-8") + b"\n"


def skill_meta(entry: SkillResourceEntry, record: SkillResourceFile) -> dict[str, object]:
    """The ``_meta`` block a skill resource carries on read and on listing.

    Provenance travels with the bytes: which server published them, the skill's
    distinct identity, and the revision of exactly this file. The server-supplied
    nature of the content is stated where a consumer reads it, so no consumer has to
    infer trust from the transport.
    """

    return {
        f"{SKILL_META_PREFIX}origin": entry.origin,
        f"{SKILL_META_PREFIX}identity": entry.identity,
        f"{SKILL_META_PREFIX}revision": record.revision,
        f"{SKILL_META_PREFIX}contentTrust": "server-supplied-data",
    }


def declared_allowed_tools(frontmatter: Mapping[str, Any]) -> tuple[str, ...]:
    """The observed ``allowed-tools`` declaration, normalized and never applied.

    A host MUST NOT honor a skill's tool declaration (§ SEP-2640 Security
    Implications), so this value is carried as an observation for a reader that wants
    to refuse or gate the skill. It is not a permission channel.
    """

    raw = frontmatter.get(ALLOWED_TOOLS_KEY)
    if raw is None:
        return ()
    if isinstance(raw, str):
        return tuple(part.strip() for part in raw.split(",") if part.strip())
    if isinstance(raw, (list, tuple)):
        return tuple(str(item).strip() for item in raw if str(item).strip())
    return ()


__all__ = [
    "ALLOWED_TOOLS_KEY",
    "JSON_MIME",
    "MARKDOWN_MIME",
    "OPAQUE_MIME",
    "RESULT_TYPE_COMPLETE",
    "SKILLS_EXTENSION_ID",
    "SKILL_INDEX_SCHEMA",
    "SKILL_INDEX_URI",
    "SKILL_META_PREFIX",
    "SKILL_ROOT_FILE",
    "SkillResourceCatalog",
    "SkillResourceEntry",
    "SkillResourceFile",
    "UnreadableSkill",
    "declared_allowed_tools",
    "skill_meta",
]
