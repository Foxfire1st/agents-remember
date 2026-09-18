"""The eve capsule carrier: the one value AR hands a pinned eve runtime before it executes.

The launch environment can carry a reference and a digest, but not the compiled instructions, so
the content travels as a file whose bytes are addressed by that digest. This module owns the
carrier's *format* and nothing else: it parses bytes it is handed and computes digests over them.
Reading the file, writing it, and deciding whether a launch may proceed are the business of the
tiers that own those surfaces, which is what keeps one shape usable from the compiler side
(``application``) and the launch side (``serving``) without either importing the other.

Four properties are load-bearing and are enforced here rather than trusted:

* the carrier is **self-describing** — it names the binding it belongs to, so a carrier left over
  from another seat cannot be applied to this one;
* its **content digest is over the exact bytes on disk**, so a carrier edited after it was written
  is refused instead of applied;
* every field a consumer needs is **required**, so a truncated or hand-written carrier fails at
  parse time with the missing field named rather than contributing an empty instruction block;
* the **workspace it names and the workspace scope it declares are the same root**, so a carrier
  cannot confine writes to one directory while the runtime reads and executes in another.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from agents_remember.errors import HarnessControlError

EVE_CAPSULE_CARRIER_SCHEMA = "ar-eve-capsule-carrier/v1"

BINDING_REF_ENV = "AR_BINDING_REF"
CAPSULE_PATH_ENV = "AR_CAPSULE_PATH"
CAPSULE_DIGEST_ENV = "AR_CAPSULE_DIGEST"
WORKSPACE_ROOT_ENV = "AR_WORKSPACE_ROOT"
"""The launch environment that names one bound carrier.

Declared beside the format they address, because the writer (the binding seam) and the reader (the
launch path) must agree byte for byte about which variables carry which value.
"""

SHA256_PREFIX = "sha256:"

ROLE_INSTRUCTION_CHANNEL = "system"
"""The channel the trusted instruction block is applied in: eve's system-role instructions.

System-role instructions stay outside conversation history and are included on every model call of
the session, which is what makes them the trusted channel rather than one turn's context.
"""

TASK_CONTEXT_CHANNEL = "user"
"""The channel the projected task facts are applied in.

Task facts are content, not authority: they belong in the durable user-role history where
compaction may legitimately summarize them, not in the standing system block.
"""

WORKSPACE_SCOPE_KIND = "workspace"
"""A scope whose root is the admitted workspace: the runtime resolves the path inside it."""

ABSOLUTE_SCOPE_KIND = "absolute"
"""A scope a seat is admitted by its own name, such as its report or memory surface."""


@dataclass(frozen=True, slots=True)
class EveCapsuleIdentity:
    """The admitted seat this carrier belongs to, as the AR task layer named it.

    Exactly the fields whose absence would let a carrier be applied to the wrong seat: the role,
    the task the seat is bound to, the operation, and the capsule's semantic digest. Nothing here
    is derived from a user message or from the session id.
    """

    role: str
    task_reference: str
    operation: str
    binding_ref: str
    semantic_digest: str

    def to_json(self) -> dict[str, str]:
        return {
            "role": self.role,
            "taskReference": self.task_reference,
            "operation": self.operation,
            "bindingRef": self.binding_ref,
            "semanticDigest": self.semantic_digest,
        }

    @classmethod
    def from_json(cls, raw: object) -> EveCapsuleIdentity:
        payload = _object(raw, "carrier identity")
        return cls(
            role=_text(payload, "role"),
            task_reference=_text(payload, "taskReference"),
            operation=_text(payload, "operation"),
            binding_ref=_text(payload, "bindingRef"),
            semantic_digest=_text(payload, "semanticDigest"),
        )


@dataclass(frozen=True, slots=True)
class EveCapsuleWorkspace:
    """The admitted AR worktree, with the git identity the runtime has to find on disk.

    ``root`` is where the runtime's own file tools are confined. The other fields are the worktree
    owner's facts carried through L3's projection: they exist so the runtime can prove the directory
    it was pointed at is *this* worktree on *this* branch, rather than a directory that merely
    exists at the path it was given.
    """

    root: str
    repository_id: str
    work_branch: str
    base_commit: str
    contract_path: str

    def to_json(self) -> dict[str, str]:
        return {
            "root": self.root,
            "repositoryId": self.repository_id,
            "workBranch": self.work_branch,
            "baseCommit": self.base_commit,
            "contractPath": self.contract_path,
        }

    @classmethod
    def from_json(cls, raw: object) -> EveCapsuleWorkspace:
        payload = _object(raw, "carrier workspace")
        return cls(
            root=_text(payload, "root"),
            repository_id=_text(payload, "repositoryId"),
            work_branch=_text(payload, "workBranch"),
            base_commit=_text(payload, "baseCommit"),
            contract_path=_text(payload, "contractPath"),
        )


@dataclass(frozen=True, slots=True)
class EveCapsuleWriteScope:
    """One surface this seat may write, as a root and a path resolved inside it.

    One resolution rule serves both kinds: a target is admitted when it resolves inside
    ``join(root, path)``. The workspace kind names the admitted worktree, whose root is required to
    equal the carrier's own workspace root; an absolute kind names a surface AR admits by its own
    path, such as the seat's report directory. A scope can therefore never be satisfied by a path
    that merely resembles one.
    """

    kind: str
    path: str
    root: str

    def to_json(self) -> dict[str, str]:
        return {"kind": self.kind, "path": self.path, "root": self.root}

    @classmethod
    def from_json(cls, raw: object) -> EveCapsuleWriteScope:
        payload = _object(raw, "write scope")
        kind = _text(payload, "kind")
        if kind not in {WORKSPACE_SCOPE_KIND, ABSOLUTE_SCOPE_KIND}:
            raise HarnessControlError(f"carrier write scope has unknown kind {kind!r}")
        return cls(kind=kind, path=_text(payload, "path"), root=_text(payload, "root"))


@dataclass(frozen=True, slots=True)
class EveCapsuleCarrier:
    """Everything one bound eve runtime needs, and nothing it may infer for itself.

    ``instructions`` is the compiled capsule's own block content in composition order; the runtime
    concatenates it verbatim rather than re-rendering it, which is what keeps AR's compiler the only
    place obligation order exists. ``instruction_identities`` travels beside it so the applied text
    can be attributed block by block without the runtime knowing what a block identity means.
    """

    schema: str
    identity: EveCapsuleIdentity
    workspace: EveCapsuleWorkspace
    instructions: tuple[str, ...]
    instruction_identities: tuple[str, ...]
    instruction_digests: tuple[str, ...]
    task_context_markdown: str
    task_context_digest: str
    write_scopes: tuple[EveCapsuleWriteScope, ...]
    granted_tools: tuple[str, ...]
    carry_forward: tuple[str, ...] = ()

    @property
    def instruction_text(self) -> str:
        """The trusted block exactly as the runtime applies it: one concatenation, in order."""

        return "".join(self.instructions)

    @property
    def workspace_scope(self) -> EveCapsuleWriteScope | None:
        """The workspace scope, when the carrier declares one."""

        for scope in self.write_scopes:
            if scope.kind == WORKSPACE_SCOPE_KIND:
                return scope
        return None

    def to_json(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "identity": self.identity.to_json(),
            "workspace": self.workspace.to_json(),
            "instructions": list(self.instructions),
            "instructionIdentities": list(self.instruction_identities),
            "instructionDigests": list(self.instruction_digests),
            "taskContextMarkdown": self.task_context_markdown,
            "taskContextDigest": self.task_context_digest,
            "writeScopes": [scope.to_json() for scope in self.write_scopes],
            "grantedTools": list(self.granted_tools),
            "carryForward": list(self.carry_forward),
        }

    def to_bytes(self) -> bytes:
        """The exact bytes written to disk, and therefore the bytes the digest covers."""

        return (json.dumps(self.to_json(), indent=2, sort_keys=True) + "\n").encode("utf-8")

    @classmethod
    def from_bytes(cls, payload: bytes) -> EveCapsuleCarrier:
        try:
            raw = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HarnessControlError("the eve capsule carrier is not readable UTF-8 JSON") from exc
        return cls.from_json(raw)

    @classmethod
    def from_json(cls, raw: object) -> EveCapsuleCarrier:
        payload = _object(raw, "carrier")
        schema = _text(payload, "schema")
        if schema != EVE_CAPSULE_CARRIER_SCHEMA:
            raise HarnessControlError(
                f"carrier schema {schema!r} is not {EVE_CAPSULE_CARRIER_SCHEMA!r}"
            )
        instructions = _text_tuple(payload, "instructions")
        identities = _text_tuple(payload, "instructionIdentities")
        digests = _text_tuple(payload, "instructionDigests")
        if not instructions:
            raise HarnessControlError(
                "the eve capsule carrier carries no instruction block; a bound seat always has at "
                "least one, so an empty carrier is a defect rather than an unbound seat"
            )
        if not (len(instructions) == len(identities) == len(digests)):
            raise HarnessControlError(
                "carrier instruction blocks, identities and digests must correspond one to one: "
                f"{len(instructions)} blocks, {len(identities)} identities, {len(digests)} digests"
            )
        scopes_raw = payload.get("writeScopes")
        if not isinstance(scopes_raw, list):
            raise HarnessControlError("carrier writeScopes must be a list")
        workspace = EveCapsuleWorkspace.from_json(payload.get("workspace"))
        scopes = tuple(EveCapsuleWriteScope.from_json(item) for item in scopes_raw)
        _require_workspace_confinement(workspace, scopes)
        return cls(
            schema=schema,
            identity=EveCapsuleIdentity.from_json(payload.get("identity")),
            workspace=workspace,
            instructions=instructions,
            instruction_identities=identities,
            instruction_digests=digests,
            task_context_markdown=_optional_text(payload, "taskContextMarkdown"),
            task_context_digest=_optional_text(payload, "taskContextDigest"),
            write_scopes=scopes,
            granted_tools=_text_tuple(payload, "grantedTools"),
            carry_forward=_text_tuple(payload, "carryForward"),
        )

    def require_identity(self, expected_binding_ref: str) -> None:
        """Refuse a carrier that belongs to another binding.

        A carrier left in an epoch directory by a previous seat is the one realistic way the wrong
        instructions reach a runtime, and it is exactly what the binding reference is for.
        """

        if self.identity.binding_ref != expected_binding_ref:
            raise HarnessControlError(
                f"the eve capsule carrier belongs to binding {self.identity.binding_ref!r}, not "
                f"{expected_binding_ref!r}; refusing to apply another seat's instructions"
            )


def carrier_digest(payload: bytes) -> str:
    """The content address of one carrier's exact bytes."""

    return f"{SHA256_PREFIX}{hashlib.sha256(payload).hexdigest()}"


def instruction_digest(content: str) -> str:
    """The content address of one applied instruction block."""

    return carrier_digest(content.encode("utf-8"))


def _require_workspace_confinement(
    workspace: EveCapsuleWorkspace, scopes: tuple[EveCapsuleWriteScope, ...]
) -> None:
    """The workspace scope must name the carrier's own workspace, and there must be exactly one.

    Without this the two halves of confinement could disagree: the runtime would read and execute
    in one directory while its write rule admitted another, which is precisely the failure a
    confinement claim is supposed to make impossible.
    """

    declared = [scope for scope in scopes if scope.kind == WORKSPACE_SCOPE_KIND]
    if len(declared) != 1:
        raise HarnessControlError(
            "the eve capsule carrier must declare exactly one workspace scope; "
            f"it declares {len(declared)}"
        )
    if declared[0].root != workspace.root:
        raise HarnessControlError(
            f"the carrier's workspace scope root {declared[0].root!r} is not its workspace "
            f"{workspace.root!r}; refusing to confine writes somewhere other than the workspace"
        )


def _object(raw: object, label: str) -> Mapping[str, object]:
    if not isinstance(raw, Mapping):
        raise HarnessControlError(f"carrier {label} must be an object")
    return cast(Mapping[str, object], raw)


def _text(raw: Mapping[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise HarnessControlError(f"carrier requires a non-empty {key}")
    return value


def _optional_text(raw: Mapping[str, object], key: str) -> str:
    value = raw.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise HarnessControlError(f"carrier {key} must be a string when present")
    return value


def _text_tuple(raw: Mapping[str, object], key: str) -> tuple[str, ...]:
    value = raw.get(key)
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise HarnessControlError(f"carrier {key} must be a list of strings")
    return tuple(cast(list[str], value))


__all__ = [
    "ABSOLUTE_SCOPE_KIND",
    "BINDING_REF_ENV",
    "CAPSULE_DIGEST_ENV",
    "CAPSULE_PATH_ENV",
    "EVE_CAPSULE_CARRIER_SCHEMA",
    "ROLE_INSTRUCTION_CHANNEL",
    "SHA256_PREFIX",
    "TASK_CONTEXT_CHANNEL",
    "WORKSPACE_ROOT_ENV",
    "WORKSPACE_SCOPE_KIND",
    "EveCapsuleCarrier",
    "EveCapsuleIdentity",
    "EveCapsuleWorkspace",
    "EveCapsuleWriteScope",
    "carrier_digest",
    "instruction_digest",
]
