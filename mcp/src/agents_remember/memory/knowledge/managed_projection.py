"""``ProjectionWriter`` as a managed, staged, non-destructive write into a destination we do not own.

Everything here exists because the destination belongs to the user. ``Doc13:271`` says a projection
target is never "a fallback source of knowledge authority", and ``Doc13:269`` supplies the eight
clauses that keep a naive writer from destroying a file the substrate never created. Each clause is
one behaviour in this module, and each is a refusal rather than a best effort:

1. **The manifest is the only authority on ownership.** :func:`_prior_manifest` reads it and every
   deletion decision is taken from it. The destination is never enumerated, so a file the manifest
   does not list -- ``user-notes/`` in the packet's own example -- is not reachable by any code path
   here. That is also clause 7: there is no recursive clean, and there is no ``rmtree`` in this
   module at all.
2. **Confinement is resolved, not string-matched.** :func:`resolve_inside_destination` resolves the
   real path of the output's parent directory and proves it is inside the resolved destination root,
   so a ``..`` segment, a symlinked directory and a case-folded alias are all caught by one
   comparison. A path that escapes is ``destination_escape``, naming the path and the resolved root,
   and nothing is staged.
3. **Collisions are reported before either is written.** Two records whose destinations differ only
   by case or normalization are refused with both paths and both identities; the substrate does not
   pick one, does not overwrite one with the other, and does not invent a disambiguating suffix.
4. **An escaping link is never followed, for a write or for a delete.** It is reported and the
   projection continues with the remaining outputs, so one hostile entry does not block the rest.
5. **Publication is a rename.** Every artifact is rendered into a staging directory inside the
   destination and moved into place, so an interruption leaves the prior generation intact rather
   than a half-written mixture.
6. **A deletion needs both halves of the unchanged test.** The path must be in the prior manifest
   *and* the file on disk must still match what that manifest recorded.
7. **No recursive clean**, above.
8. **An externally edited file is reported and preserved.** The four discrepancy kinds are
   distinguished because they call for different caller action, and the only route to an overwrite
   is an explicit per-path caller authorization, which is recorded in the resulting manifest entry.

**Two hooks exist so the safety contract is testable rather than asserted.** ``before_publish`` lets
a test interrupt publication at the exact instant between staging and the first rename -- the
packet's Open Truth Gap records that whether checkpoint 4 can be induced deterministically was
unverified, and this is the answer: it can, without a sleep, a thread or a signal.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from pathlib import Path

from agents_remember.models.knowledge.projection_manifest import (
    DIGEST_ALGORITHM,
    PROJECTION_MANIFEST_FORMAT,
    PROJECTION_MANIFEST_NAME,
    STAGING_DIRECTORY_NAME,
    Collision,
    DestinationProfile,
    Discrepancy,
    DiscrepancyKind,
    ManagedOutput,
    ProjectionManifest,
    ProjectionOutcome,
    ProjectionPlan,
    ProjectionRefusal,
    ProjectionRefusalCode,
    ProjectionReport,
    RenderedOutput,
    RetainedOutput,
    RetentionReason,
    detect_destination_collisions,
    require_confined_relative_path,
)

__all__ = [
    "ManagedProjectionWriter",
    "ProjectionHooks",
    "refusal_report",
    "resolve_inside_destination",
]


def _no_hook() -> None:
    """The default publication hook: nothing to do, and no place to interrupt."""


@dataclass(frozen=True)
class ProjectionHooks:
    """The one seam a test uses to interrupt publication at a deterministic instant."""

    before_publish: Callable[[], None] = field(default=_no_hook)


def refusal_report(profile: DestinationProfile, refusal: ProjectionRefusal) -> ProjectionReport:
    """The report of a projection that wrote nothing, with the destination named."""

    return ProjectionReport(
        state="refused",
        destination_root=profile.destination_root,
        renderer_version=profile.renderer_version,
        manifest_generation=None,
        refusal=refusal,
    )


def resolve_inside_destination(destination_root: Path, relative_path: str) -> Path | None:
    """The real path one destination-relative path resolves to, or ``None`` when it escapes.

    Two comparisons are made and both must hold. The *resolved parent* must be inside the resolved
    root, which is what catches a symlinked intermediate directory; and the joined path's own
    resolved form must still be inside, which is what catches a final component that is itself a
    link pointing out of the destination. Doing this with ``Path.resolve()`` rather than a string
    prefix is requirement 5.2's whole point -- ``/vault/agents-remember-backup`` has the configured
    root's text as a prefix and is not inside it.
    """

    root = Path(os.path.realpath(destination_root))
    candidate = root / relative_path
    parent = Path(os.path.realpath(candidate.parent))
    if parent != root and root not in parent.parents:
        return None
    resolved = Path(os.path.realpath(candidate))
    if resolved != root and root not in resolved.parents:
        # The final component is a link that leaves the destination, or the destination root itself
        # is a link: either way this path is not inside the root we resolved.
        if not resolved.exists() and not candidate.is_symlink():
            return candidate
        return None
    return resolved


def _digest_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_staged(staging: Path, relative_path: str, payload: bytes) -> Path:
    """Write one staged artifact under the staging directory, creating only its own parents."""

    staged = staging / relative_path
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_bytes(payload)
    return staged


def _manifest_bytes(manifest: ProjectionManifest) -> bytes:
    """The manifest's own bytes: canonical JSON, so two runs at one state are byte-identical."""

    body = manifest.model_dump(mode="json")
    return (json.dumps(body, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


class _DestinationState:
    """One description of the destination as it stands, computed once and read by every decision."""

    def __init__(self, root: Path, prior: ProjectionManifest | None) -> None:
        self.root = root
        self.prior = prior
        self._observations: dict[str, _Observation] = {}

    def observe(self, relative_path: str) -> _Observation:
        """How one prior-manifest path stands on disk right now, computed at most once."""

        if relative_path not in self._observations:
            self._observations[relative_path] = _observe(self.root / relative_path)
        return self._observations[relative_path]


@dataclass(frozen=True)
class _Observation:
    """What one recorded output looks like on disk: its kind and the digest of its bytes."""

    state: str
    digest: str | None
    byte_count: int | None
    detail: str

    @property
    def unchanged_from(self) -> Callable[[ManagedOutput], bool]:
        """Whether this observation still matches what one prior manifest entry recorded."""

        def matches(recorded: ManagedOutput) -> bool:
            return self.state == "file" and self.digest == recorded.digest

        return matches


def _observe(path: Path) -> _Observation:
    """Classify one path without following a link and without mutating anything."""

    if path.is_symlink():
        return _Observation("link", None, None, "the destination entry is a symbolic link")
    if not path.exists():
        return _Observation("absent", None, None, "the recorded output is no longer on disk")
    if not path.is_file():
        return _Observation(
            "replaced", None, None, "the recorded output is no longer a regular file"
        )
    try:
        payload = path.read_bytes()
    except OSError as error:
        return _Observation(
            "unreadable", None, None, f"the recorded output could not be read: {error}"
        )
    return _Observation(
        "file", _digest_bytes(payload), len(payload), "the recorded output is readable"
    )


def _read_prior_manifest(root: Path) -> tuple[ProjectionManifest | None, ProjectionRefusal | None]:
    """The prior manifest, or a refusal naming why it could not be read.

    An unreadable manifest is refused rather than treated as absent. Treating it as absent would
    make every prior output an unowned file, which is safe but silently abandons the ownership
    record; refusing makes the caller repair it, and requirement 5.1's "only authority" is only
    meaningful if its unavailability is loud.
    """

    path = root / PROJECTION_MANIFEST_NAME
    if path.is_symlink():
        return None, ProjectionRefusal(
            code=ProjectionRefusalCode.ESCAPING_LINK,
            detail="the destination's manifest is a symbolic link and is not followed",
            offending_path=PROJECTION_MANIFEST_NAME,
            resolved_root=str(root),
            next_action="replace the link with the manifest the substrate wrote",
        )
    if not path.exists():
        return None, None
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return None, ProjectionRefusal(
            code=ProjectionRefusalCode.MANIFEST_UNREADABLE,
            detail=(
                "the destination carries a projection manifest that could not be read, so what "
                f"the substrate owns there is unknown: {error}"
            ),
            offending_path=PROJECTION_MANIFEST_NAME,
            resolved_root=str(root),
            next_action="repair or remove the manifest; no output is written while it is unreadable",
        )
    if (
        not isinstance(decoded, Mapping)
        or decoded.get("manifest_format") != PROJECTION_MANIFEST_FORMAT
    ):
        return None, ProjectionRefusal(
            code=ProjectionRefusalCode.MANIFEST_UNREADABLE,
            detail="the destination's manifest does not declare the expected manifest format",
            offending_path=PROJECTION_MANIFEST_NAME,
            resolved_root=str(root),
            expected=PROJECTION_MANIFEST_FORMAT,
            next_action="repair or remove the manifest; no output is written while it is unreadable",
        )
    try:
        return ProjectionManifest.model_validate(decoded), None
    except ValueError as error:
        return None, ProjectionRefusal(
            code=ProjectionRefusalCode.MANIFEST_UNREADABLE,
            detail=f"the destination's manifest is not a valid manifest: {error}",
            offending_path=PROJECTION_MANIFEST_NAME,
            resolved_root=str(root),
            next_action="repair or remove the manifest; no output is written while it is unreadable",
        )


class ManagedProjectionWriter:
    """The shipped :class:`~agents_remember.models.knowledge.projection_manifest.ProjectionWriter`.

    One instance is stateless between calls: every fact it needs about the destination is read from
    the destination's own manifest at the start of a write, so a writer cannot carry "what it wrote
    last time" as a second, unrecorded authority.
    """

    def __init__(self, hooks: ProjectionHooks | None = None) -> None:
        self._hooks = hooks or ProjectionHooks()

    def write(self, plan: ProjectionPlan) -> ProjectionReport:
        """Stage every output and publish it, or refuse and leave the destination as it was."""

        profile = plan.destination
        root = Path(profile.destination_root)
        unavailable = self._require_destination(root, profile)
        if unavailable is not None:
            return refusal_report(profile, unavailable)
        prior, unreadable = _read_prior_manifest(root)
        if unreadable is not None:
            return refusal_report(profile, unreadable)
        collision = self._first_collision(plan)
        if collision is not None:
            return refusal_report(profile, collision)
        escaped = self._first_escape(plan, root)
        if escaped is not None:
            return refusal_report(profile, escaped)
        return self._stage_and_publish(plan, root, prior)

    # -- guards ------------------------------------------------------------

    def _require_destination(
        self, root: Path, profile: DestinationProfile
    ) -> ProjectionRefusal | None:
        """Prove the configured destination is a usable directory before anything else runs."""

        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            return ProjectionRefusal(
                code=ProjectionRefusalCode.DESTINATION_UNAVAILABLE,
                detail=f"the configured destination could not be created: {error}",
                offending_path=str(root),
                resolved_root=str(root),
                next_action="configure a destination this process can create and write",
            )
        if not root.is_dir():
            return ProjectionRefusal(
                code=ProjectionRefusalCode.DESTINATION_UNAVAILABLE,
                detail="the configured destination exists and is not a directory",
                offending_path=str(root),
                resolved_root=str(root),
                next_action="configure a directory as the projection destination",
            )
        if profile.renderer_version == "":
            return ProjectionRefusal(
                code=ProjectionRefusalCode.UNRESOLVED_PROJECTION_INPUT,
                detail="the destination profile carries no renderer/profile version",
                offending_path=str(root),
                resolved_root=str(root),
                next_action="name the renderer/profile version the projection is produced by",
            )
        return None

    def _first_collision(self, plan: ProjectionPlan) -> ProjectionRefusal | None:
        """Refuse the whole plan when two of its outputs would land on one destination."""

        collisions: tuple[Collision, ...] = detect_destination_collisions(plan.outputs)
        if not collisions:
            return None
        collision = collisions[0]
        return ProjectionRefusal(
            code=ProjectionRefusalCode.DESTINATION_COLLISION,
            detail=(
                "two outputs would land on destinations that are equivalent by case or "
                f"normalization; neither is written: {collision.canonical_form} from "
                f"{list(collision.destination_relative_paths)}"
            ),
            offending_path=collision.destination_relative_paths[0],
            resolved_root=str(Path(os.path.realpath(plan.destination.destination_root))),
            expected="unique destinations per projected record",
            observed=", ".join(collision.stable_identities),
            next_action=(
                "change a destination configuration value or a record name; the substrate does not "
                "invent a disambiguating suffix"
            ),
        )

    def _first_escape(self, plan: ProjectionPlan, root: Path) -> ProjectionRefusal | None:
        """Refuse the whole plan when any output path is not confined to the destination root."""

        for output in plan.outputs:
            syntactic = require_confined_relative_path(output.destination_relative_path)
            if syntactic is not None:
                # The syntactic refusal names the same resolved root the resolving one does, so a
                # caller always learns both halves requirement 5.2 asks for.
                return syntactic.model_copy(
                    update={"resolved_root": str(Path(os.path.realpath(root)))}
                )
            if _crosses_a_link(
                root, output.destination_relative_path, root / output.destination_relative_path
            ):
                # Clause 5.4's case, not clause 5.2's. A clean destination-relative request that
                # happens to land on a link is a hostile *destination entry*, and the packet's own
                # acceptance case requires it to be reported while the remaining outputs continue.
                # Only a request that is itself unconfined is a plan-level escape.
                continue
            if resolve_inside_destination(root, output.destination_relative_path) is None:
                return ProjectionRefusal(
                    code=ProjectionRefusalCode.DESTINATION_ESCAPE,
                    detail=(
                        "the requested output path resolves outside the configured destination "
                        "root; the comparison is a real path resolution, not a string prefix"
                    ),
                    offending_path=output.destination_relative_path,
                    resolved_root=str(Path(os.path.realpath(root))),
                    next_action="name a destination-relative path that resolves inside the root",
                )
        return None

    # -- publication -------------------------------------------------------

    def _stage_and_publish(
        self, plan: ProjectionPlan, root: Path, prior: ProjectionManifest | None
    ) -> ProjectionReport:
        """Stage every writable output, then publish, then record the new generation."""

        state = _DestinationState(root, prior)
        authorized = frozenset(plan.authorized_overwrites)
        writable, reported, reported_discrepancies = self._partition(plan, state, authorized)
        staged, links = self._stage(root, writable)
        if links:
            # A link in the way of a write is skipped for that output only; the rest continue.
            writable = tuple(
                output for output in writable if output.destination_relative_path not in links
            )
            reported = reported + tuple(links.values())
        try:
            outcomes, published = self._publish(root, staged, state)
        except BaseException:
            # Clause 5.5: an interruption leaves the destination in its prior state, so the staged
            # bytes are discarded rather than left beside the published generation. Recovery is to
            # re-run the projection, which re-derives them.
            _discard_staging(root)
            raise
        removed, retained = self._retire(
            state,
            frozenset(output.destination_relative_path for output in plan.outputs),
            published,
        )
        preserved = frozenset(outcome.destination_relative_path for outcome in reported)
        manifest = _next_manifest(
            prior,
            _ManifestInputs(
                renderer_version=plan.destination.renderer_version,
                published=tuple(published),
                retained=tuple(retained),
                preserved=preserved,
                authorized=authorized,
            ),
        )
        _publish_manifest(root, manifest)
        return ProjectionReport(
            state="projected",
            destination_root=str(root),
            renderer_version=plan.destination.renderer_version,
            manifest_generation=manifest.generation,
            outcomes=tuple(outcomes) + reported + tuple(removed),
            retained=tuple(retained),
            discrepancies=reported_discrepancies,
            manifest=manifest,
        )

    def _partition(
        self,
        plan: ProjectionPlan,
        state: _DestinationState,
        authorized: frozenset[str],
    ) -> tuple[tuple[RenderedOutput, ...], tuple[ProjectionOutcome, ...], tuple[Discrepancy, ...]]:
        """Split the plan into what may be written and what must be reported and preserved."""

        writable: list[RenderedOutput] = []
        reported: list[ProjectionOutcome] = []
        discrepancies: list[Discrepancy] = []
        for output in plan.outputs:
            path = output.destination_relative_path
            recorded_digest = None if state.prior is None else state.prior.recorded_digest_for(path)
            if recorded_digest is None:
                writable.append(output)
                continue
            observation = state.observe(path)
            if (observation.state == "file" and observation.digest == recorded_digest) or (
                path in authorized
            ):
                writable.append(output)
                continue
            kind = _discrepancy_kind(observation)
            reported.append(
                ProjectionOutcome(
                    destination_relative_path=path,
                    subject=output.stable_identity,
                    state="reported",
                    detail=(
                        f"the file on disk is {kind} since the substrate last wrote it; it is "
                        "preserved and not overwritten"
                    ),
                )
            )
            discrepancies.append(
                Discrepancy(
                    destination_relative_path=path,
                    kind=kind,
                    recorded_digest=recorded_digest,
                    observed_digest=observation.digest,
                    detail=observation.detail,
                )
            )
        return tuple(writable), tuple(reported), tuple(discrepancies)

    def _stage(
        self, root: Path, writable: Sequence[RenderedOutput]
    ) -> tuple[dict[str, tuple[Path, RenderedOutput]], dict[str, ProjectionOutcome]]:
        """Render every writable output into staging, skipping one that would go through a link."""

        staging = root / STAGING_DIRECTORY_NAME
        staged: dict[str, tuple[Path, RenderedOutput]] = {}
        linked: dict[str, ProjectionOutcome] = {}
        for output in writable:
            path = output.destination_relative_path
            existing = root / path
            if _crosses_a_link(root, path, existing):
                linked[path] = ProjectionOutcome(
                    destination_relative_path=path,
                    subject=output.stable_identity,
                    state="reported",
                    detail=(
                        "a destination entry on this path is a symbolic link or resolves outside "
                        "the root; it is not followed for a write and the projection continues"
                    ),
                )
                continue
            staged[path] = (
                _write_staged(staging, path, output.text.encode("utf-8")),
                output,
            )
        return staged, linked

    def _publish(
        self,
        root: Path,
        staged: Mapping[str, tuple[Path, RenderedOutput]],
        state: _DestinationState,
    ) -> tuple[list[ProjectionOutcome], list[ManagedOutput]]:
        """Move every staged artifact into place, one rename per output."""

        self._hooks.before_publish()
        outcomes: list[ProjectionOutcome] = []
        published: list[ManagedOutput] = []
        for path, (staged_path, output) in staged.items():
            final = root / path
            final.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged_path, final)
            payload = output.text.encode("utf-8")
            published.append(
                ManagedOutput(
                    destination_relative_path=path,
                    stable_identity=output.stable_identity,
                    record_kind=output.record_kind,
                    format=output.format,
                    source_snapshot=output.source_snapshot,
                    renderer_version=output.renderer_version,
                    digest_algorithm=DIGEST_ALGORITHM,
                    digest=_digest_bytes(payload),
                    byte_count=len(payload),
                )
            )
            outcomes.append(
                ProjectionOutcome(
                    destination_relative_path=path,
                    subject=output.stable_identity,
                    state="unchanged"
                    if state.prior and _unchanged_prior(state, path, output)
                    else "published",
                    detail="the artifact is in place and its digest is recorded in the new manifest",
                )
            )
        _discard_staging(root)
        return outcomes, published

    def _retire(
        self,
        state: _DestinationState,
        still_projected: frozenset[str],
        published: Sequence[ManagedOutput],
    ) -> tuple[list[ProjectionOutcome], list[RetainedOutput]]:
        """Delete prior outputs the new projection dropped, but only when they are unchanged.

        A path the new projection still produces is not retired at all, even when the write to it
        was withheld: it keeps its prior manifest entry, which is what makes the next run detect the
        same external edit instead of forgetting it. Retention is therefore exactly the set of
        prior-owned paths this generation no longer produces.
        """

        produced = {output.destination_relative_path for output in published}
        outcomes: list[ProjectionOutcome] = []
        retained: list[RetainedOutput] = []
        if state.prior is None:
            return outcomes, retained
        for recorded in state.prior.outputs:
            path = recorded.destination_relative_path
            if path in produced or path in still_projected:
                continue
            observation = state.observe(path)
            if observation.unchanged_from(recorded):
                removed = _remove_unchanged(state.root / path)
                outcomes.append(
                    ProjectionOutcome(
                        destination_relative_path=path,
                        subject=recorded.stable_identity,
                        state="removed" if removed else "retained-with-reason",
                        detail=(
                            "the prior manifest owned this path and the file was unchanged, so it "
                            "is removed"
                            if removed
                            else "the unchanged path could not be removed and is retained"
                        ),
                    )
                )
                if not removed:
                    retained.append(
                        RetainedOutput(
                            destination_relative_path=path,
                            reason="unreadable",
                            detail="the file was unchanged but could not be removed",
                            recorded=recorded,
                        )
                    )
                continue
            retained.append(
                RetainedOutput(
                    destination_relative_path=path,
                    reason=(
                        "edited-since-last-projection"
                        if observation.state == "file"
                        else _retention_reason(observation)
                    ),
                    detail=observation.detail,
                    recorded=recorded,
                )
            )
            outcomes.append(
                ProjectionOutcome(
                    destination_relative_path=path,
                    subject=recorded.stable_identity,
                    state="retained-with-reason",
                    detail=(
                        "the new projection no longer produces this path and the file on disk has "
                        "changed since the substrate wrote it, so it is retained"
                    ),
                )
            )
        return outcomes, retained + _carried_retentions(state, still_projected, produced)


def _carried_retentions(
    state: _DestinationState, still_projected: AbstractSet[str], produced: AbstractSet[str]
) -> list[RetainedOutput]:
    """Prior retentions the new generation does not produce, still on disk, carried forward.

    A retained file is not a file the substrate forgot. If it were dropped from the next manifest it
    would become unowned, and the generation after that would be free to write over a user's edit --
    which is the file loss the retention state exists to prevent. A retention whose file has since
    gone is dropped, because there is nothing left to preserve.
    """

    if state.prior is None:
        return []
    carried: list[RetainedOutput] = []
    for entry in state.prior.retained:
        path = entry.destination_relative_path
        if path in produced or path in still_projected:
            continue
        if state.observe(path).state == "absent":
            continue
        carried.append(entry)
    return carried


def _unchanged_prior(state: _DestinationState, path: str, output: RenderedOutput) -> bool:
    """Whether a published artifact is byte-identical to the content the prior manifest recorded."""

    if state.prior is None:
        return False
    recorded = state.prior.output_for(path)
    if recorded is None:
        return False
    return recorded.digest == _digest_bytes(output.text.encode("utf-8"))


def _discrepancy_kind(observation: _Observation) -> DiscrepancyKind:
    """The caller-facing kind of one discrepancy, from the observation taken of the file."""

    if observation.state == "absent":
        return "deleted"
    if observation.state == "unreadable":
        return "unreadable"
    if observation.state == "replaced":
        return "replaced"
    return "modified"


def _retention_reason(observation: _Observation) -> RetentionReason:
    """Why a changed prior output is retained rather than removed."""

    if observation.state == "absent":
        return "not-owned-by-prior-manifest"
    if observation.state == "replaced":
        return "replaced"
    return "unreadable"


def _crosses_a_link(root: Path, relative_path: str, existing: Path) -> bool:
    """Whether writing this path would pass through a symbolic link or leave the root.

    Both are checked: a link *inside* the destination that points inside it is still not followed
    for a write (requirement 5.4 makes no exception for a benign target), and a resolved path that
    leaves the root is refused for the same reason it is refused at confinement time.
    """

    if existing.is_symlink():
        return True
    current = root
    for segment in relative_path.split("/")[:-1]:
        current = current / segment
        if current.is_symlink():
            return True
    return resolve_inside_destination(root, relative_path) is None


def _remove_unchanged(path: Path) -> bool:
    """Remove one unchanged file, without following a link and without touching a directory."""

    try:
        if path.is_symlink() or not path.is_file():
            return False
        path.unlink()
    except OSError:
        return False
    return True


def _discard_staging(root: Path) -> None:
    """Remove the staging directory's own files and then the directory, never recursively.

    This is not the recursive clean requirement 5.7 forbids: the staging directory is the
    substrate's own scratch space inside the destination, it is created by this writer, and its
    contents are exactly the staged files this call wrote. The destination itself is never
    enumerated and never swept.
    """

    staging = root / STAGING_DIRECTORY_NAME
    if not staging.is_dir() or staging.is_symlink():
        return
    for child in sorted(staging.rglob("*")):
        if child.is_file() and not child.is_symlink():
            child.unlink(missing_ok=True)
    for child in sorted(staging.rglob("*"), reverse=True):
        if child.is_dir() and not child.is_symlink():
            child.rmdir()
    staging.rmdir()


@dataclass(frozen=True)
class _ManifestInputs:
    """Everything the next manifest generation is composed from, as one value."""

    renderer_version: str
    published: tuple[ManagedOutput, ...]
    retained: tuple[RetainedOutput, ...]
    preserved: frozenset[str]
    authorized: frozenset[str]


def _next_manifest(prior: ProjectionManifest | None, inputs: _ManifestInputs) -> ProjectionManifest:
    """The new manifest generation: what this run published, plus what it preserved.

    ``preserved`` is exactly the set of paths the projection still produces but did not overwrite.
    Each keeps the digest the substrate last wrote, so the file on disk remains detectably different
    from what the manifest records and the next run reports it again rather than adopting the edit.
    """

    carried: dict[str, ManagedOutput] = {}
    if prior is not None:
        for output in prior.outputs:
            if output.destination_relative_path in inputs.preserved:
                carried[output.destination_relative_path] = output
        for entry in prior.retained:
            recorded = entry.recorded
            if recorded is not None and entry.destination_relative_path in inputs.preserved:
                # A path that was retained and is produced again keeps the *prior* entry, so the
                # file on disk stays detectably different from what the substrate last wrote and the
                # next run reports the same edit rather than adopting it.
                carried[entry.destination_relative_path] = recorded
    entries: list[ManagedOutput] = [
        output.model_copy(
            update={"authorized_overwrite": output.destination_relative_path in inputs.authorized}
        )
        for output in inputs.published
    ]
    published_paths = {output.destination_relative_path for output in inputs.published}
    for path, carried_entry in carried.items():
        if path not in published_paths:
            entries.append(carried_entry)
    return ProjectionManifest(
        generation=1 if prior is None else prior.generation + 1,
        renderer_version=inputs.renderer_version,
        outputs=tuple(sorted(entries, key=lambda entry: entry.destination_relative_path)),
        retained=tuple(sorted(inputs.retained, key=lambda entry: entry.destination_relative_path)),
    )


def _publish_manifest(root: Path, manifest: ProjectionManifest) -> None:
    """Write the manifest through the same staging discipline every other artifact uses."""

    staging = root / STAGING_DIRECTORY_NAME
    staging.mkdir(parents=True, exist_ok=True)
    staged = staging / PROJECTION_MANIFEST_NAME
    staged.write_bytes(_manifest_bytes(manifest))
    os.replace(staged, root / PROJECTION_MANIFEST_NAME)
    _discard_staging(root)
