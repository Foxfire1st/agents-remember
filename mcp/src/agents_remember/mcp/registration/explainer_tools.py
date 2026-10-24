"""The explainer's authoring declarations, in a light owner (MIK-R204 @ v1 rule 1, slice 1 of 3).

These eight tools are declared here with nothing behind them: the module imports the answer boundary, the
tool framework and the argument vocabulary, and resolves `explainer_authoring`, `_written` and `read_source`
from `canvas.explainer_server` and `canvas.presenter_evidence` **on the call**. Declaring them therefore
does not load a reader, a writer or a renderer.

The declarations are moved, not rewritten: names, descriptions, argument schemas, defaults and bounds are
byte-for-byte the ones `canvas.explainer_server` declared, and the acceptance for the move is that both
seat digests stay identical to A6.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

from mcp.server.fastmcp import FastMCP, Image
from pydantic import Field

from agents_remember.canvas.explainer_answers import answered
from agents_remember.canvas.explainer_inputs import ExplainerInputs, coordination_of, now_iso

if TYPE_CHECKING:  # annotations only; nothing here loads a service
    from agents_remember.canvas.presenter_tools import AuthoringTools

MAX_ASSET_CHARACTERS = 1_048_576


def _explainer_authoring(workspace: Path) -> AuthoringTools:
    """The authoring class, resolved on the call (slice 0 made its home light; this keeps it lazy)."""

    return import_module("agents_remember.canvas.explainer_server").explainer_authoring(workspace)


def _written(workspace: Path, relative: str, body: bytes) -> dict[str, Any]:
    return import_module("agents_remember.canvas.explainer_server")._written(
        workspace, relative, body
    )


def _read_source(*arguments: Any, **keywords: Any) -> Any:
    return import_module("agents_remember.canvas.presenter_evidence").source(*arguments, **keywords)


def _render(
    workspace: Path,
    *,
    task_root: Path | None = None,
    seat: str | None = None,
    now: Callable[[], str] | None = None,
) -> tuple[dict[str, Any], bytes | None]:
    return import_module("agents_remember.canvas.explainer_server").render_and_publish(
        workspace, task_root=task_root, seat=seat, now=now
    )


def register_authoring_tools(
    server: FastMCP,
    workspace: Path,
    *,
    task_root: Path | None = None,
    seat: str | None = None,
    now: Callable[[], str] | None = None,
) -> None:
    """The eight tools kept from the presenter, over this environment's own folders."""

    @server.tool()
    def inputs() -> dict:
        """List the admitted input names and their digests. There is no arbitrary file reader."""
        return answered(lambda: {**_explainer_authoring(workspace).grants["inputs"]})

    @server.tool()
    def read_input(name: str) -> str:
        """Read evidence.json or assignment.txt, with its admitted digest checked."""
        return _explainer_authoring(workspace).read(name)

    @server.tool()
    def source(side: str, path: str, start: int = 1, end: int = 100) -> dict:
        """Read a relative path at one of the packet's four exact trees, through bounded R102 IO.

        The packet is re-read on every call, so an Update's new packet needs no restart. No
        repository, HEAD or revision argument exists. Never writes or executes compared code.
        """
        evidence_packet = import_module(
            "agents_remember.models.knowledge.review_evidence"
        ).EvidencePacket
        packet = evidence_packet.model_validate_json(
            _explainer_authoring(workspace).read("evidence.json")
        )
        return answered(lambda: _read_source(packet, side, path, start=start, end=end))

    @server.tool()
    def assets(relative: str | None = None, offset: int = 0) -> dict:
        """List/read this environment's assets; read chunks are bounded to 12000 characters."""
        return answered(lambda: _explainer_authoring(workspace).assets(relative, offset))

    @server.tool()
    def write_asset(relative: str, text: Annotated[str, Field(max_length=1048576)]) -> dict:
        """Write UTF-8 page output relative to drafts/assets, never an absolute/protected path."""
        body = text.encode()
        return answered(lambda: _written(workspace, relative, body))

    @server.tool()
    def carry_kit(paths: list[str]) -> dict:
        """Carry kit-relative files (e.g. shiki/4.5.0/shiki-kit.js) and their notices."""
        return answered(lambda: _explainer_authoring(workspace).carry(paths))

    @server.tool()
    def documentation(url: str, offset: int = 0) -> dict:
        """Fetch bounded authoring documentation. This surface is NEVER review evidence."""
        # `documentation` is the class's own static reader: resolved on the call, never a TYPE_CHECKING name
        # (the A16 review's AST check caught it as an unbound runtime name).
        authoring = import_module("agents_remember.canvas.presenter_tools").AuthoringTools
        return answered(lambda: authoring.documentation(url, offset))

    @server.tool()
    def render() -> list:
        """Load the exact draft in Chromium with reduced motion; return diagnostics and its picture."""
        report, picture = _render(workspace, task_root=task_root, seat=seat, now=now)
        result: list[Any] = [json.dumps(report)]
        if picture is not None:
            result.append(Image(data=picture, format="png"))
        return result


def _explainer_access() -> Any:
    """The readers' owner, resolved on the call: declaring these four must not load it."""

    return import_module("agents_remember.canvas.explainer_access")


def register_read_tools(
    server: FastMCP,
    workspace: Path,
    *,
    task_root: Path | None,
    leaf: str,
) -> None:
    """``list``, ``search`` and ``read`` — declarations only, readers on the call.

    The readers are built per call from the same three arguments the product always gave them, so the
    move changes where a declaration lives and nothing about what it answers.
    """

    @server.tool()
    def list(surface: str, path: str = "", depth: int = 1) -> dict:
        """List a folder of the bound snapshot, the retained pages or notes/reference/<leaf>/.

        Surfaces: 'code' and 'memory' (the bound snapshot), 'snapshot-<n>/code' and
        'snapshot-<n>/memory' (an older one a spanned turn may still read), 'pages' (the leaf's
        retained page revisions) and 'reference' (notes/reference/<leaf>/). A path outside those
        is refused by name. Bounded: 400 entries, depth 4.
        """
        reads = _explainer_access().ExplainerReads(workspace, task_root, leaf)
        return answered(lambda: reads.listing(surface, path, depth))

    @server.tool()
    def search(surface: str, query: str, path: str = "", limit: int = 30) -> dict:
        """Find a literal string in one admitted surface; answers path, line and the line.

        Bounded: 200 files, 4 MiB, 50 matches. A regex is not admitted; a substring is.
        """
        reads = _explainer_access().ExplainerReads(workspace, task_root, leaf)
        return answered(lambda: reads.searching(surface, query, path, limit))

    @server.tool()
    def read(surface: str, path: str, offset: int = 0) -> dict:
        """Read one admitted file: 12000 characters at a time, with the whole file's SHA-256.

        Binary content comes back base64. The answer names the binding; the retained pages and the
        reference folder are read as they are now and are not part of it.
        """
        reads = _explainer_access().ExplainerReads(workspace, task_root, leaf)
        return answered(lambda: reads.reading(surface, path, offset))

    # `knowledge_read` is registered by `register_knowledge_tool`, after the git declarations, so the
    # seat's discovery order is the one it has always published (R204 rule 2 covers order, not just names).


def register_knowledge_tool(
    server: FastMCP, workspace: Path, *, memory_repository: Path | None, coordination: Path
) -> None:
    """``knowledge_read``, declared after the git tools so discovery order is unchanged (rule 2)."""

    @server.tool()
    def knowledge_read(
        view: str,
        recordId: str = "",
        sourcePath: str = "",
        limit: int = 32,
        continuation: str | None = None,
    ) -> dict:
        """Name records of one view at the bound memory tree.

        Views: record, source_context, invariant, family, dependent, weakness. `recordId` takes a
        text ID (`INV-…`, `FAM-…`, `DEC-…`); `sourcePath` names a code path. Rows name ID, kind,
        state and location, never text. `limit` is at most 200; `continuation` is the answer's own.
        """
        access = _explainer_access()
        query = access.KnowledgeQuery(
            view=view,
            record_id=recordId,
            source_path=sourcePath,
            limit=limit,
            continuation=continuation,
        )
        knowledge = access.ExplainerKnowledge(workspace, memory_repository, coordination)
        return answered(lambda: knowledge.read(query))


def register_git_tools(server: FastMCP, workspace: Path, *, repositories: dict[str, Path]) -> None:
    """The six read-only Git subcommands on the code and memory repositories."""

    def git() -> Any:
        return _explainer_access().ExplainerGit(repositories, workspace)

    @server.tool()
    def git_log(repository: str, ref: str = "HEAD", limit: int = 20, path: str = "") -> dict:
        """Commits of the 'code' or 'memory' repository. History informs; it is never evidence."""
        return answered(lambda: git().log(repository, ref, limit, path))

    @server.tool()
    def git_show(repository: str, target: str, path: str = "") -> dict:
        """One commit or tree as text, by its own object name, never today's checkout."""
        return answered(lambda: git().show(repository, target, path))

    @server.tool()
    def git_diff(repository: str, base: str, candidate: str, path: str = "") -> dict:
        """The diff between two named trees or commits in the 'code' or 'memory' repository."""
        return answered(lambda: git().diff(repository, base, candidate, path))

    @server.tool()
    def git_ls_tree(repository: str, tree: str, recursive: bool = False, path: str = "") -> dict:
        """The entries of one Git tree, with sizes."""
        return answered(lambda: git().ls_tree(repository, tree, recursive, path))

    @server.tool()
    def git_cat_file(repository: str, target: str, mode: str = "pretty") -> dict:
        """One object's content ('pretty'), type ('type') or size ('size')."""
        return answered(lambda: git().cat_file(repository, target, mode))

    @server.tool()
    def git_rev_parse(repository: str, expression: str = "HEAD") -> dict:
        """Resolve one object name to its id. A worktree path is never an answer."""
        return answered(lambda: git().rev_parse(repository, expression))


def register_judgment_tools(
    server: FastMCP,
    workspace: Path,
    *,
    task_root: Path | None,
    seat: str | None,
    fingerprints: Any,
    now: Any,
) -> None:
    """MIK-R187 rule 2's three grade tools."""

    # One instance per registration, built on the first call and kept: the judgment carries the seat's
    # identity and fingerprints, and a per-call reconstruction is a different object (the A14 failure).
    kept: list[Any] = []

    def judgment() -> Any:
        if not kept:
            kept.append(
                import_module("agents_remember.canvas.explainer_server").ExplainerJudgment(
                    workspace, task_root, seat, fingerprints, now
                )
            )
        return kept[0]

    @server.tool()
    def define_category(
        name: str, scale: list[dict[str, str]], binding: str, meaning: str = ""
    ) -> dict:
        """Define a judgment category: 2 to 5 named grades, highest first, each with its meaning.

        `binding` is the claim token or digest the answers name ('#7', 'planned@...', or the
        comparison digest). A name with another scale or meaning is refused `category-exists`; at
        most 12 live per leaf.
        """
        return answered(lambda: judgment().define(name, scale, binding, meaning=meaning))

    @server.tool()
    def remove_category(name: str, binding: str) -> dict:
        """Remove one category and, with it, its grades. `binding` is the label it was made at."""
        return answered(lambda: judgment().remove(name, binding))

    @server.tool()
    def grade(references: list[str], category: str, grade: str, reason: str, binding: str) -> dict:
        """Grade review blocks; answer counts and the refused references with their reasons.

        `references` name blocks by L89's identity; `reason` is one line. The grade carries the
        binding and the block's fingerprint, and is refused `binding-moved` when the environment
        has left that binding. Bounds: 200 references, 160 characters per reason.
        """
        return answered(lambda: judgment().grade(references, category, grade, reason, binding))


def register_report_tool(
    server: FastMCP,
    workspace: Path,
    *,
    task_root: Path | None,
    coordination: Path,
    seat: str | None,
    sender: Any,
    now: Any,
) -> None:
    """MIK-R189 rule 7's one report per filed tray message."""

    # Same rule as the judgment: the sender's tray paths are construction-time state, so the instance is
    # built once on the first call and kept rather than rebuilt for each report (the A14 failure).
    kept: list[Any] = []

    def reports() -> Any:
        if not kept:
            kept.append(
                import_module("agents_remember.canvas.explainer_server").ExplainerReports(
                    workspace, task_root, coordination, seat, sender, now
                )
            )
        return kept[0]

    @server.tool()
    def report_to_architect(messageId: str, perspective: str) -> dict:
        """Report one filed tray message to the master's architect, with your perspective.

        `messageId` is taken from the delivered developer message's `Message file: …_<messageId>.md`
        line (or its `messageId:` header): its selections and words are copied byte for byte with
        their SHA-256, and there is no argument for them. `perspective` is at most 4000 characters,
        marked presenter inference. One report per tray message; the copy goes back to his tray when
        no master-to-architect record names a seat.
        """
        return answered(lambda: reports().report(messageId, perspective))


def register_bound_explainer_tools(
    server: FastMCP,
    *,
    workspace: Path,
    seat: str | None = None,
    inputs: ExplainerInputs | None = None,
) -> None:
    """Register the explainer's tools on the server the normal seat path already built.

    MIK-R186 @ v2 rule 3: there is no separate explainer or presenter server. The normal
    `agents-remember-task` server serves the explainer, and this supplies exactly its surface on the
    instance the launch already created -- the same implementations, one copy, no parallel entry point.

    ``seat`` is the caller identity the launch binding names; a tool only the leaf's explainer may call
    refuses every other identity. Authority comes from that binding, not from ``AR_EXPLAINER_WORKSPACE``,
    which survives only as the retirement scanner's cleanup handle.
    """

    given = inputs or ExplainerInputs()
    workspace = Path(workspace)
    leaf = workspace.name
    repositories = {
        side: path
        for side, path in (("code", given.code_repository), ("memory", given.memory_repository))
        if path is not None
    }
    coordination = given.coordination_root or coordination_of(workspace)
    now = given.now or now_iso
    seat = seat or os.environ.get(
        import_module("agents_remember.canvas.explainer_state").AGENT_MARKER
    )
    # MIK-R204 @ v1 rule 1, slice 1: the eight authoring declarations live in their own light owner.
    register_authoring_tools(server, workspace, task_root=given.task_root, seat=seat, now=now)
    # MIK-R204 @ v1 rule 1, slice 2: the four read declarations live in the same light owner.
    register_read_tools(
        server,
        workspace,
        task_root=given.task_root,
        leaf=leaf,
    )
    register_git_tools(server, workspace, repositories=repositories)
    register_knowledge_tool(
        server, workspace, memory_repository=given.memory_repository, coordination=coordination
    )
    register_judgment_tools(
        server,
        workspace,
        task_root=given.task_root,
        seat=seat,
        fingerprints=given.fingerprints,
        now=now,
    )
    register_report_tool(
        server,
        workspace,
        task_root=given.task_root,
        coordination=coordination,
        seat=seat,
        sender=given.sender,
        now=now,
    )
