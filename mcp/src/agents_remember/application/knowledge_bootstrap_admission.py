"""The admitted bootstrap context: a taskless knowledge write derived from real setup authority.

The knowledge write plane has always needed an admission, and the only admission that existed was a
**leaf enclosure contract**: a document created by a worktree operation, naming a code worktree, a
memory worktree and a recorded base pair. That shape is correct for a task's own ingest and wrong for
the thing this module provides, which is the first write a repository ever makes -- a repository that
by definition has no task, no leaf and no enclosure yet. The bootstrap handover refuses the shortcut
of minting a contract to satisfy the shape (``BOOTSTRAP-HANDOVER.md`` line 11), and a second write
path is the parallel-store defect, so what is left is the honest third option: **a second admission,
derived from an authority that really exists for a repository with no task.**

That authority is the setup and repository authority the product already has:

* the MCP runtime settings document (``McpRuntimeConfig``) -- the same document
  :func:`~agents_remember.kernel.memory_init.initialize_memory` reads -- declares which repositories
  exist, where their checkouts are and where their external memory root is. A repository the document
  does not list is refused by name, exactly as the memory initializer refuses it;
* the coordination resolver (``kernel/coordination_context/resolver.py``), invoked with **no
  enclosure selector**, is the owner of "which memory layer does this repository's ordinary read
  select". Resolving the context here rather than computing a path is what makes the bootstrap write
  to the location ordinary readers resolve instead of to a second convention that agrees today;
* the two real Git checkouts supply the **exact source inputs**: the code line's commit and the memory
  line's commit, read at admission time rather than asserted by a caller.

**Provenance, not a flag.** Nothing on this path accepts "is this a bootstrap?" as input. The
admission this resolver returns is a :class:`~agents_remember.application.knowledge_write_admission.
KnowledgeWriteAdmission` whose provenance names the settings document, the repository entry in it and
the exact roots and revisions that were read. A caller cannot assert an admission; it can only be
handed one this resolver built after its own checks, which is what the handover's "a client-supplied
boolean is not authority" clause requires.

**Every way out is a named state, and none of them is a fallback.** A repository that is not declared,
a memory layer that cannot be resolved, an enclosure that is somehow in scope, a memory root that is
not the one the ordinary read route selects, a root that is not a Git checkout, an unreadable
revision: each is a :class:`BootstrapRefusal` carrying a stable code, the fact that produced it and
the route that re-observes it. There is deliberately no branch that borrows another repository's
memory root, another branch's commit or another dataset's namespace -- the packet's "no silent
fallback to another repository, branch or dataset" is a property of this function's control flow
rather than a promise in a docstring.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agents_remember.application.knowledge_write_admission import (
    BOOTSTRAP_ADMISSION_KIND,
    AdmissionProvenance,
    KnowledgeWriteAdmission,
)
from agents_remember.application.published_intent import published_dataset_path
from agents_remember.kernel import coordination_context_resolver as resolver
from agents_remember.kernel.coordination_context.models import (
    CoordinationContext,
    CoordinationRequest,
)
from agents_remember.kernel.coordination_context_resolver import (
    CoordinationHints,
    EnclosureSelector,
)
from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.worktrees.modules.contract_reader import WorktreeContractReader

__all__ = [
    "BOOTSTRAP_STAGING_DIRECTORY",
    "AdmittedKnowledgeBootstrap",
    "BootstrapAuthority",
    "BootstrapRefusal",
    "admit_bootstrap_context",
    "bootstrap_scope",
    "bootstrap_staging_root",
]

# The one directory name a bootstrap's temporary staging lives under, inside the coordination
# context's own named temp root. It is a constant rather than a caller argument so the cleanup owner
# and the writer name the same place by construction.
BOOTSTRAP_STAGING_DIRECTORY = "knowledge-bootstrap"

# The one prefix a bootstrap operation's retry scope carries. A repository's bootstrap and any leaf
# of that repository therefore never share an allocation-journal key: the scope is joined with the
# entry's own id to form the key, and this prefix is what keeps "the repository's first knowledge"
# and "this task's knowledge" two different operations rather than one that aliases the other.
_SCOPE_PREFIX = "knowledge-bootstrap"


@dataclass(frozen=True)
class BootstrapAuthority:
    """The authority a taskless bootstrap was admitted under, named exactly.

    ``config_path`` is the settings document the repository entry came from and
    ``authority_entry`` is the entry inside it, because those two facts together are what a reader
    needs to re-open the admission and check it -- a report that said only "the settings file" would
    name a document that may declare several repositories. ``declared_memory_root`` is what the
    document says the memory root is and ``resolved_memory_root`` is what the ordinary read route
    resolved; the pair is carried rather than one of them because the equality between them is the
    check, and a reader has to be able to see it rather than trust it.
    """

    repo_id: str
    config_path: Path
    authority_entry: str
    code_repository_root: Path
    declared_memory_root: Path
    resolved_memory_root: Path
    coordination_root: Path
    topology: str
    settings_path: Path

    @property
    def source(self) -> str:
        """The authority reference in one stable spelling, for a report or a progress record."""

        return f"{self.config_path}#repositories.{self.repo_id}"


@dataclass(frozen=True)
class BootstrapRefusal:
    """Why a repository could not be given a bootstrap context, and what re-observes it.

    ``code`` is the stable vocabulary a caller branches on, ``detail`` states the fact that produced
    it (with the paths or ids that fact is about), and ``next_action`` names the route that changes
    it. A refusal is a fact about the repository or the setup, never a defect of this module.
    """

    code: str
    detail: str
    next_action: str


@dataclass(frozen=True)
class AdmittedKnowledgeBootstrap:
    """One admitted bootstrap context: the authority, the admission, and the two places it uses.

    ``destination_path`` is the location this bootstrap publishes to, resolved through the ordinary
    read route's own owner (:func:`~agents_remember.application.published_intent.
    published_dataset_path`), so the writer and a later task's planner cannot disagree about where a
    repository's knowledge lives. ``staging_root`` is the bounded temporary area this bootstrap's
    candidate and its retained progress record live in, derived from the context's own temp root.
    """

    authority: BootstrapAuthority
    admission: KnowledgeWriteAdmission
    context: CoordinationContext
    destination_path: Path
    staging_root: Path

    @property
    def repo_id(self) -> str:
        return self.authority.repo_id


def bootstrap_scope(repo_id: str) -> str:
    """The retry scope one repository's bootstrap operations are found by.

    It is a function of the repository alone on purpose: an exact retry of an interrupted bootstrap
    must ask the same question it asked before, whatever commit the code line has advanced to in the
    meantime, or a resumed run would mint a second set of identities for knowledge it already holds.
    """

    return f"{_SCOPE_PREFIX}:{repo_id}"


def bootstrap_staging_root(context: CoordinationContext, repo_id: str) -> Path:
    """Where a repository's bootstrap stages its candidate and its retained progress.

    Derived from the resolved context's own named temp root rather than from an argument, so the
    writer, the resume path and the cleanup owner all name one directory by construction.
    """

    return context.temp_root / BOOTSTRAP_STAGING_DIRECTORY / repo_id


def admit_bootstrap_context(
    config: McpRuntimeConfig, repo_id: str
) -> AdmittedKnowledgeBootstrap | BootstrapRefusal:
    """Resolve the admitted bootstrap context for one repository, or name why it cannot be had.

    The order below is the order the facts depend on, and no step is skipped because a later one
    might have answered: the repository entry, then the coordination root, then the code checkout,
    then the memory layer the ordinary read route resolves, then the equality of that memory root
    with the one the setup document declares, then the two exact revisions.
    """

    repo = config.repositories.get(repo_id)
    if repo is None:
        return _refusal(
            "repository_not_allowed",
            (
                f"repo_id {repo_id!r} is not declared by the MCP settings document "
                f"{config.config_path}; declared: {', '.join(config.allowed_repo_ids) or '<none>'}"
            ),
            "declare the repository in the MCP settings document and reload the runtime",
        )
    if repo.memory_root is None:
        return _refusal(
            "repository_has_no_external_memory_root",
            (
                f"repo_id {repo_id!r} declares no external memory root, so there is no memory line "
                "for a knowledge bootstrap to publish on"
            ),
            "declare the repository's memory root and run the c-00-initialize-memory-repo route",
        )
    root_refusal = _coordination_root_refusal(config, repo_id)
    if root_refusal is not None:
        return root_refusal
    checkout_refusal = _code_checkout_refusal(repo)
    if checkout_refusal is not None:
        return checkout_refusal
    resolved = _resolved_context(config, repo_id, repo)
    if isinstance(resolved, BootstrapRefusal):
        return resolved
    return _context_admission(config, repo_id, repo, resolved)


def _coordination_root_refusal(config: McpRuntimeConfig, repo_id: str) -> BootstrapRefusal | None:
    """Why the coordination root cannot host this bootstrap's staging, or ``None``.

    Git would happily create a missing parent, so an absent coordination root would silently stage a
    repository's knowledge somewhere nothing else in the product reads. This is the same check the
    memory initializer makes, for the same reason and with the same named route.
    """

    if config.coordination_root.is_dir():
        return None
    return _refusal(
        "coordination_root_unavailable",
        (
            f"the coordination root {config.coordination_root.as_posix()} does not exist or is not a "
            f"directory, so repo_id {repo_id!r} has no workspace to bootstrap knowledge in"
        ),
        "run runtime_install to create the coordinator scaffold first (c-13-install-and-onboard)",
    )


def _code_checkout_refusal(repo: RepositoryScope) -> BootstrapRefusal | None:
    """Why the declared code root cannot supply exact source inputs, or ``None``."""

    if not repo.path.is_dir():
        return _refusal(
            "code_checkout_unavailable",
            (
                f"the declared code root {repo.path.as_posix()} does not exist or is not a "
                "directory, so no source revision can be observed"
            ),
            "correct the repository's declared path and reload the runtime",
        )
    if not (repo.path / ".git").exists():
        return _refusal(
            "code_checkout_is_not_a_git_checkout",
            (
                f"the declared code root {repo.path.as_posix()} is not a Git checkout, so the exact "
                "source revision a bootstrap binds to cannot be observed"
            ),
            "point the repository entry at the Git checkout it really is",
        )
    return None


def _resolved_context(
    config: McpRuntimeConfig, repo_id: str, repo: RepositoryScope
) -> CoordinationContext | BootstrapRefusal:
    """The context the ordinary read route resolves for this repository, with no enclosure in scope.

    The selector is deliberately empty. A bootstrap is a repository-scoped act, and an enclosure
    selector would make the effective memory root this task's memory worktree -- a line that will be
    cleaned up -- rather than the repository's memory layer. Anything the resolver cannot answer is a
    named refusal carrying the resolver's own sentence rather than a path this module guessed.
    """

    try:
        return resolver.resolve_coordination_context(
            code_repository_name=repo_id,
            workspace_root=config.workspace_root,
            code_repository_root=repo.path,
            request=CoordinationRequest(
                hints=CoordinationHints(coordination_root=config.coordination_root),
                selector=EnclosureSelector(),
                contract_reader=WorktreeContractReader(),
            ),
        )
    except (ValueError, OSError) as error:
        return _refusal(
            "memory_layer_not_resolved",
            (
                f"the coordination context for repo_id {repo_id!r} could not be resolved, so the "
                f"memory layer a bootstrap would publish on is unknown: {error}"
            ),
            "repair the coordination settings or the memory root, then re-observe this context",
        )


def _context_admission(
    config: McpRuntimeConfig,
    repo_id: str,
    repo: RepositoryScope,
    context: CoordinationContext,
) -> AdmittedKnowledgeBootstrap | BootstrapRefusal:
    """The resolved context as an admission, once the four facts about it hold."""

    if context.contract_path is not None:
        return _refusal(
            "enclosure_in_scope",
            (
                "the resolved context is bound to the enclosure "
                f"{context.contract_path.as_posix()}, so this is a task's own memory line and not "
                "the repository's; a bootstrap must not publish onto a line that is about to be "
                "cleaned up"
            ),
            "resolve the bootstrap context with no enclosure selector in scope",
        )
    declared = repo.memory_root
    if declared is None:  # pragma: no cover - the caller refused this case already
        raise ValueError("a bootstrap context requires a declared memory root")
    if context.memory_root.resolve() != declared.resolve():
        return _refusal(
            "memory_line_moved",
            (
                f"the setup document declares the memory root {declared.as_posix()} while the "
                f"ordinary read route resolves {context.memory_root.as_posix()} for repo_id "
                f"{repo_id!r}, so the location a reader selects is not the location this bootstrap "
                "would publish on"
            ),
            "re-observe the repository entry and the coordination settings, then admit again",
        )
    revisions = _source_revisions(repo, context)
    if isinstance(revisions, BootstrapRefusal):
        return revisions
    code_commit, memory_commit = revisions
    authority = BootstrapAuthority(
        repo_id=repo_id,
        config_path=config.config_path,
        authority_entry=f"repositories.{repo_id}",
        code_repository_root=repo.path,
        declared_memory_root=declared,
        resolved_memory_root=context.memory_root,
        coordination_root=context.coordination_root,
        topology=context.topology,
        settings_path=context.settings_path,
    )
    return AdmittedKnowledgeBootstrap(
        authority=authority,
        admission=_admission(authority, code_commit, memory_commit),
        context=context,
        destination_path=published_dataset_path(context),
        staging_root=bootstrap_staging_root(context, repo_id),
    )


def _admission(
    authority: BootstrapAuthority, code_commit: str, memory_commit: str
) -> KnowledgeWriteAdmission:
    """The one write admission this bootstrap context confers."""

    return KnowledgeWriteAdmission(
        provenance=AdmissionProvenance(
            kind=BOOTSTRAP_ADMISSION_KIND,
            authority="the MCP settings document's repository entry",
            reference=authority.source,
            detail=(
                f"repository {authority.repo_id!r} bootstrapped at code revision {code_commit} and "
                f"memory revision {memory_commit} on the {authority.topology} memory layer"
            ),
        ),
        scope=bootstrap_scope(authority.repo_id),
        repository_name=authority.repo_id,
        coordination_root=authority.coordination_root,
        code_repo_path=authority.code_repository_root,
        code_worktree=authority.code_repository_root,
        memory_repo_path=authority.resolved_memory_root,
        memory_worktree=authority.resolved_memory_root,
        code_base_commit=code_commit,
        memory_base_commit=memory_commit,
        code_work_branch=_current_branch(authority.code_repository_root),
        source_ref=authority.config_path,
    )


def _current_branch(root: Path) -> str:
    """The checkout's own branch name, or ``""`` for a detached HEAD.

    An empty answer is not a substitute for a branch: the operation then reads the checkout's
    ``HEAD`` and, failing that, the admitted base commit, and reports which of the three it used. The
    value is a *hint about which line to read*, never an identity input.
    """

    symbolic = run_git(root, ["symbolic-ref", "--quiet", "--short", "HEAD"])
    return symbolic.stdout.strip() if symbolic.returncode == 0 else ""


def _source_revisions(
    repo: RepositoryScope, context: CoordinationContext
) -> tuple[str, str] | BootstrapRefusal:
    """The exact code and memory revisions this bootstrap binds to, read from the real checkouts.

    Both are read here rather than accepted as arguments, because "exact source inputs" that a caller
    typed would be exactly the client-asserted authority the handover refuses. A checkout that cannot
    answer is a refusal naming it, never a silently empty revision: an admission with no commit would
    make the snapshot and candidate references meaningless while still looking admitted.
    """

    code = _revision(repo.path, "HEAD")
    if code is None:
        return _refusal(
            "code_revision_unavailable",
            (
                f"the code checkout {repo.path.as_posix()} answered no commit for HEAD, so the exact "
                "source revision this bootstrap would bind to is unknown"
            ),
            "make the code checkout resolve HEAD, then admit again",
        )
    memory = _revision(context.memory_root, "HEAD")
    if memory is None:
        return _refusal(
            "memory_revision_unavailable",
            (
                f"the memory root {context.memory_root.as_posix()} answered no commit for HEAD, so "
                "the exact memory revision this bootstrap would bind to is unknown"
            ),
            "make the memory line resolve HEAD, then admit again",
        )
    return code, memory


def _revision(root: Path, reference: str) -> str | None:
    """One commit id the checkout answers for ``reference``, or ``None`` when it cannot."""

    result = run_git(root, ["rev-parse", "--verify", f"{reference}^{{commit}}"])
    value = result.stdout.strip() if result.returncode == 0 else ""
    return value or None


def _refusal(code: str, detail: str, next_action: str) -> BootstrapRefusal:
    return BootstrapRefusal(code=code, detail=detail, next_action=next_action)
