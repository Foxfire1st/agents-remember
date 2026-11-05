"""Read every producer handoff entry against a named candidate without writing it."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agents_remember.application.knowledge_writer import case_proof
from agents_remember.application.knowledge_writer.code_anchors import (
    AnchorResolutionError,
    CodeSnapshot,
)
from agents_remember.application.knowledge_writer.handoff import (
    CoverRequest,
    EntryRequest,
    HandoffDocument,
    LocatorRequest,
    TestRangeReference,
    read_handoff_shapes,
    read_locator,
)
from agents_remember.errors import GrammarUnavailableError
from agents_remember.kernel.git_command import (
    GIT_METADATA_TIMEOUT_SECONDS,
    GitRunnerOptions,
    run_git,
)
from agents_remember.memory_quality.knowledge_validator.trees import knowledge_tree_from_directory
from agents_remember.models.knowledge_files import RECORD_DIRECTORIES, parse_document_text


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("list", type=Path, help="The producer handoff JSON list or document.")
    parser.add_argument("--code", required=True, type=Path, help="The code repository to read.")
    parser.add_argument("--tree", required=True, help="The exact candidate commit or tree.")
    parser.add_argument(
        "--memory", type=Path, help="Read stored record links from this memory tree."
    )


@dataclass
class _Memory:
    records: dict[str, Mapping[str, Any]] = field(default_factory=dict)
    entries: dict[str, tuple[str, Mapping[str, Any]]] = field(default_factory=dict)

    @classmethod
    def read(cls, root: Path) -> _Memory:
        if not root.is_dir():
            raise ValueError(f"memory directory does not exist: {root}")
        found = cls()
        record_roots = tuple(f"knowledge/{name}/" for name in RECORD_DIRECTORIES.values())
        for path, data in knowledge_tree_from_directory(root).files.items():
            if not path.endswith(".json"):
                continue
            if not path.startswith((*record_roots, "onboarding/")):
                continue
            document = parse_document_text(data.decode("utf-8")).to_document()
            record_id = document.get("id")
            if path.startswith(record_roots) and isinstance(record_id, str):
                found.records[record_id] = document
            source_path = document.get("path")
            if not isinstance(source_path, str):
                continue
            for section in ("realizes", "proves"):
                for entry in document.get(section, []):
                    anchor = entry.get("anchor")
                    if isinstance(entry.get("id"), str) and isinstance(anchor, Mapping):
                        found.entries[entry["id"]] = (source_path, anchor)
        return found


@dataclass
class _Check:
    code: CodeSnapshot
    memory: _Memory | None
    problems: list[str] = field(default_factory=list)
    record_links: int = 0
    unchecked_records: int = 0

    def anchor(self, path: str, locator: LocatorRequest, where: str) -> None:
        try:
            self.code.resolve(path, locator.document)
        except (AnchorResolutionError, GrammarUnavailableError) as error:
            self.problems.append(f"{where}: {path} ({locator.describe()}): {error}")

    def written_anchor(self, raw: Any, where: str) -> None:
        if not isinstance(raw, Mapping):
            self.problems.append(f"{where}: an anchor names path and locator")
            return
        path = raw.get("path")
        locator = read_locator(raw.get("locator"))
        if not isinstance(path, str) or not path or isinstance(locator, str):
            self.problems.append(
                f"{where}: " + (locator if isinstance(locator, str) else "an anchor names its path")
            )
            return
        self.anchor(path, locator, where)

    def file(self, path: Any, where: str) -> None:
        if not isinstance(path, str) or not self.code.has_file(path):
            self.problems.append(f"{where}: the candidate holds no tracked file {path!r}")

    def entry(self, entry: EntryRequest) -> None:
        where = f"entry {entry.entry_id}"
        for target in entry.targets:
            self.anchor(target.path, target.locator, f"{where}.target[{target.position}]")
        for proof in entry.proofs:
            at = f"{where}.proofs[{proof.position}]"
            if isinstance(proof.test, TestRangeReference):
                result = case_proof.range_proof(self.code, proof.test)
                if result.refusal is not None:
                    self.problems.append(f"{at}: {result.refusal}")
            else:
                self.anchor(
                    proof.test.path,
                    LocatorRequest({"kind": "symbol", "name": proof.test.name}),
                    at,
                )

    def link(self, raw: Any, where: str) -> None:
        if not isinstance(raw, Mapping):
            self.problems.append(f"{where}: a link names kind and target")
            return
        kind = raw.get("kind")
        if kind == "anchor":
            self.written_anchor(raw.get("anchor"), where)
        elif kind == "file":
            self.file(raw.get("path"), where)
        elif kind == "record":
            self.record_links += 1
            if self.memory is None:
                self.unchecked_records += 1
                self.problems.append(
                    f"{where}: record link {raw.get('id')!r} unchecked without --memory"
                )
            else:
                record_id = raw.get("id")
                record = self.memory.records.get(record_id) if isinstance(record_id, str) else None
                if record is None:
                    self.problems.append(f"{where}: no stored record {record_id!r}")
                elif record.get("status") == "retired":
                    self.problems.append(f"{where}: record {record_id!r} is retired")
        else:
            self.problems.append(f"{where}: unknown link kind {kind!r}")

    def cover(self, cover: CoverRequest, entries: Mapping[str, EntryRequest], where: str) -> None:
        if cover.remove:
            return
        if cover.handoff is not None:
            entry = entries.get(cover.handoff)
            if entry is None or not entry.targets:
                self.problems.append(
                    f"{where}: cover target {cover.handoff!r} unchecked: no handoff target"
                )
            # Every target is checked by entry(), including targets named through a cover.
            return
        path, locator = cover.path, cover.locator
        if self.memory is not None and cover.entry_id in self.memory.entries:
            stored_path, anchor = self.memory.entries[cover.entry_id]
            path = path if path is not None else stored_path
            if locator is None:
                decoded = read_locator(anchor.get("locator"))
                if isinstance(decoded, LocatorRequest):
                    locator = decoded
        if path is None or locator is None:
            self.problems.append(
                f"{where}: cover {cover.entry_id!r} unchecked: path/locator need its stored entry (--memory)"
            )
        else:
            self.anchor(path, locator, where)

    def document(self, document: HandoffDocument) -> None:
        all_entries = (*document.entries, *document.rulings)
        entries = {entry.entry_id: entry for entry in all_entries}
        for entry in all_entries:
            self.entry(entry)
        for record in document.records:
            where = f"record {record.key}"
            if record.kind == "weakness":
                self.written_anchor(record.fields.get("anchor"), f"{where}.anchor")
            elif record.kind == "dependent" and not record.remove:
                self.file(record.fields.get("document"), f"{where}.document")
                links = record.fields.get("stands_on", [])
                if not isinstance(links, list):
                    self.problems.append(f"{where}.stands_on: links are a list")
                else:
                    for position, link in enumerate(links, 1):
                        self.link(link, f"{where}.stands_on[{position}]")
        for row in document.rows:
            where = f"history[{row.position}] ({row.subject})"
            for position, cover in enumerate(row.covers, 1):
                self.cover(cover, entries, f"{where}.covers[{position}]")
            for repoint in row.repoint:
                self.anchor(repoint.path, repoint.locator, f"{where}.repoint[{repoint.position}]")


def run(args: argparse.Namespace) -> int:
    try:
        raw = json.loads(args.list.read_text(encoding="utf-8"))
        document, problems = read_handoff_shapes(raw)
        root = args.code.resolve()
        resolved = run_git(
            root,
            ["rev-parse", "--verify", "--quiet", "--end-of-options", f"{args.tree}^{{tree}}"],
            GitRunnerOptions(timeout=GIT_METADATA_TIMEOUT_SECONDS),
        )
        if resolved.returncode or not resolved.stdout.strip():
            raise ValueError(f"{args.tree!r} names no candidate tree in {root}")
        code = CodeSnapshot.of_tree(root, resolved.stdout.strip())
        memory = None if args.memory is None else _Memory.read(args.memory.resolve())
    except (OSError, ValueError, TypeError) as error:
        print(f"cannot read handoff-check inputs: {error}")
        return 2
    check = _Check(code, memory, [problem.render() for problem in problems])
    check.document(document)
    print("\n".join(check.problems) if check.problems else "handoff-check: clean")
    print("fired link: checked by the writer")
    print(
        "record links: not run" if memory is None else f"record links: checked {check.record_links}"
    )
    return 1 if check.problems else 0
