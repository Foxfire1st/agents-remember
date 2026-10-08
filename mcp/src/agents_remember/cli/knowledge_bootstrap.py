"""CLI adapter: the taskless knowledge bootstrap, written as a wave of knowledge files.

    agents-remember knowledge-bootstrap --repo <repo_id> --list <hand-off list> --wave <wave>
        --authorization-ref <ref> [--commit] [--config <MCP settings>] [--json]

This is the **taskless** entry of the knowledge write plane. The ordinary ``knowledge-ingest``
subcommand requires ``--contract`` -- a leaf enclosure contract -- and a repository whose foundation
knowledge is being written has no leaf and no enclosure. This subcommand resolves the second *real*
admission instead: the repository entry the MCP settings document declares, the memory layer the
ordinary read route resolves, and the exact code and memory revisions the real checkouts stand at.

``--repo`` is the repository id the MCP settings document declares. It is resolved through
:func:`~agents_remember.application.knowledge_bootstrap_admission.admit_bootstrap_context`, so a
repository the document does not list, a repository with no external memory root, a coordination root
that does not exist, an unreadable revision and a memory root that is not the one the ordinary read
route selects are each refused by name before anything is read or written.

``--config`` names the MCP settings document and defaults to the umbrella CLI's own trusted-settings
discovery, so the command runs from anywhere under the workspace exactly as ``memory-citations`` does.

``--wave`` names the wave the bootstrap writes as (MIK-R07 rule 8): a bootstrap has no leaf, so its
judgment rows go to ``knowledge/history/<wave>.json``.

PLANNING IS THE DEFAULT, AND PLANNING IS ALSO THE DRY RUN. Without ``--commit`` the list is read, the
tree the run would produce is validated and reported, and **nothing is written**. ``--commit`` is the
commit word and the whole of the write act: the curator file writer
(:mod:`agents_remember.cli.knowledge_write_route`) writes records, sidecar entries and the wave's
history file into the admitted memory root.

KNOWLEDGE IS WRITTEN AS FILES (MIK-R12 rule 7). The admitted memory root must be converted (it holds
``knowledge/layout.json``). The canonical knowledge database, its staging candidate and its
publication are retired (MIK-R26): an unconverted memory root is refused by name, and the refusal
says how it converts.

Exit status: 0 when the operation wrote or planned; 1 when the writer refused it (nothing was
written); 2 when the invocation itself is refused (a missing or unreadable list, a blank
authorization reference, a context that cannot be admitted, an undecidable settings path, an
unconverted memory root).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from agents_remember.application.knowledge_bootstrap_admission import (
    AdmittedKnowledgeBootstrap,
    BootstrapRefusal,
    admit_bootstrap_context,
)
from agents_remember.cli.discovery import ConfigDiscoveryError, discover_config
from agents_remember.cli.knowledge_write_route import is_converted, run_wave_write
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
    load_config,
    require_config_path,
)
from agents_remember.worktrees.cutover_lock import cutover_lock_refusal, legacy_format_refusal

EXIT_REFUSED = 2


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo", required=True, help="The repository id the MCP settings declare.")
    parser.add_argument(
        "--list",
        dest="hand_off_list",
        required=True,
        help="Path to the curator's hand-off list (JSON).",
    )
    parser.add_argument(
        "--authorization-ref",
        required=True,
        help="The authorization this bootstrap is admitted under; the report records it.",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Write the files into the admitted memory root. Without it this plans, validates and "
        "reports, and writes nothing.",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to the MCP authority settings file. Omit to use the umbrella CLI's trusted "
        "settings discovery from the current directory.",
    )
    parser.add_argument(
        "--wave",
        default=None,
        help="The wave this bootstrap writes as (MIK-R07 rule 8); its judgment rows go to "
        "knowledge/history/<wave>.json.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Print the whole report as JSON instead of the human-readable summary.",
    )


def _invocation_refusal(args: argparse.Namespace) -> str | None:
    """Why this invocation has no list to read or no authority to write under, or ``None``.

    Each is a fact about the argument list, so it is answered before a settings document, a
    context, a list or a byte is touched.
    """

    if not Path(args.hand_off_list).is_file():
        return f"the hand-off list {args.hand_off_list} is not a file this command can read"
    if not str(args.authorization_ref or "").strip():
        return "--authorization-ref must not be blank: an admitted write needs an authorization"
    return None


def _settings(args: argparse.Namespace) -> McpRuntimeConfig | str:
    """The MCP authority settings path this invocation resolved, or the reason it could not."""

    try:
        return load_config(
            require_config_path(args.config) if args.config else discover_config(Path.cwd())
        )
    except ConfigDiscoveryError as error:
        return (
            f"no MCP authority settings could be resolved ({error}); pass --config with the "
            "document the harness registers"
        )
    except (OSError, ValueError) as error:
        return f"the MCP authority settings could not be read: {error}"


def _admitted(args: argparse.Namespace) -> AdmittedKnowledgeBootstrap | BootstrapRefusal | str:
    """The admitted context this invocation runs under, or the refusal that stopped it."""

    config = _settings(args)
    if isinstance(config, str):
        return config
    return admit_bootstrap_context(config, args.repo)


def _refusal_payload(refusal: BootstrapRefusal) -> dict[str, Any]:
    return {
        "state": "refused",
        "code": refusal.code,
        "detail": refusal.detail,
        "nextAction": refusal.next_action,
    }


def _print_refusal(as_json: bool, refusal: BootstrapRefusal) -> None:
    payload = _refusal_payload(refusal)
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    print(
        f"knowledge-bootstrap REFUSED ({payload['code']}): {payload['detail']}\n"
        f"  next action: {payload['nextAction']}"
    )


def run(args: argparse.Namespace) -> int:
    """Run one bootstrap and print its report; the report IS the result."""

    refusal = _invocation_refusal(args)
    if refusal is not None:
        print(refusal)
        return EXIT_REFUSED
    admitted = _admitted(args)
    if isinstance(admitted, str):
        print(admitted)
        return EXIT_REFUSED
    if isinstance(admitted, BootstrapRefusal):
        _print_refusal(args.as_json, admitted)
        return EXIT_REFUSED
    return _run(args, admitted)


def _run(args: argparse.Namespace, admitted: AdmittedKnowledgeBootstrap) -> int:
    """Write the admitted memory root through the file writer, or refuse unconverted memory."""

    memory = admitted.admission.memory_worktree
    if memory is not None and is_converted(memory):
        return run_wave_write(
            args,
            memory_root=memory,
            code_root=admitted.admission.code_worktree,
            task=admitted.admission.scope,
            coordination_root=admitted.authority.coordination_root,
        )
    line = "no memory root" if memory is None else memory.as_posix()
    # MIK-R09 rule 6: unconverted memory in a repository that holds converted memory is only read.
    # Any other unconverted root is memory in the legacy format, whose writer is retired (MIK-R26).
    print(
        cutover_lock_refusal(memory, operation="knowledge-bootstrap", line=line)
        or legacy_format_refusal(
            memory, operation="knowledge-bootstrap", subject=f"the memory root {line}"
        )
    )
    return EXIT_REFUSED
