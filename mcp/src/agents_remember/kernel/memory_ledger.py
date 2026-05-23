"""Parse, validate, and write Agents Remember external-memory ledgers.

The canonical ledger format is a fenced JSON metadata block followed by the
first markdown table with exactly two semantic columns: Code commit and Memory
commit. The module deliberately uses only the Python standard library.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path


LEDGER_SCHEMA = "ar-memory-ledger/v1"
LEGACY_LEDGER_SCHEMA = "ar-memory-branch-ledger/v1"
LEDGER_FENCE_RE = re.compile(r"```json\s+ar-memory-ledger\s*\n(.*?)\n```", re.DOTALL)


@dataclass(frozen=True)
class LedgerRow:
    code_commit: str
    memory_commit: str


@dataclass(frozen=True)
class MemoryLedger:
    repo_name: str
    base_code_commit: str
    base_memory_commit: str
    last_verified_code_commit: str
    last_memory_content_commit: str
    sort_order: str
    rows: list[LedgerRow]
    schema: str = LEDGER_SCHEMA


class LedgerError(ValueError):
    """Raised when a memory ledger is missing or invalid."""


def _metadata_get(metadata: dict[str, object], *names: str) -> str:
    for name in names:
        value = metadata.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def parse_ledger_text(text: str) -> MemoryLedger:
    match = LEDGER_FENCE_RE.search(text)
    if not match:
        raise LedgerError("memory.md is missing a fenced `json ar-memory-ledger` metadata block")
    try:
        metadata = json.loads(match.group(1))
    except json.JSONDecodeError as error:
        raise LedgerError(f"memory.md ledger metadata is invalid JSON: {error}") from error
    if not isinstance(metadata, dict):
        raise LedgerError("memory.md ledger metadata must be a JSON object")

    schema = _metadata_get(metadata, "schema")
    repo_name = _metadata_get(metadata, "repoName", "repo_name")
    base_code_commit = _metadata_get(metadata, "baseCodeCommit", "base_code_commit")
    base_memory_commit = _metadata_get(metadata, "baseMemoryCommit", "base_memory_commit")
    last_verified_code_commit = _metadata_get(metadata, "lastVerifiedCodeCommit", "last_verified_code_commit")
    last_memory_content_commit = _metadata_get(metadata, "lastMemoryContentCommit", "last_memory_content_commit")
    sort_order = _metadata_get(metadata, "sortOrder", "sort_order")

    required = {
        "schema": schema,
        "repoName": repo_name,
        "baseCodeCommit": base_code_commit,
        "baseMemoryCommit": base_memory_commit,
        "lastVerifiedCodeCommit": last_verified_code_commit,
        "lastMemoryContentCommit": last_memory_content_commit,
        "sortOrder": sort_order,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise LedgerError(f"memory.md ledger metadata is missing required fields: {', '.join(missing)}")
    if schema not in {LEDGER_SCHEMA, LEGACY_LEDGER_SCHEMA}:
        raise LedgerError(f"unsupported memory ledger schema: {schema}")

    rows = parse_ledger_rows(text[match.end() :])
    ledger = MemoryLedger(
        schema=schema,
        repo_name=repo_name,
        base_code_commit=base_code_commit,
        base_memory_commit=base_memory_commit,
        last_verified_code_commit=last_verified_code_commit,
        last_memory_content_commit=last_memory_content_commit,
        sort_order=sort_order,
        rows=rows,
    )
    validate_ledger(ledger)
    return ledger


def parse_ledger_rows(text: str) -> list[LedgerRow]:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        cells = _table_cells(line)
        if [cell.lower() for cell in cells] != ["code commit", "memory commit"]:
            continue
        if index + 1 >= len(lines) or not _is_separator_row(lines[index + 1]):
            raise LedgerError("memory.md ledger table header is not followed by a separator row")
        rows: list[LedgerRow] = []
        for row_line in lines[index + 2 :]:
            row_cells = _table_cells(row_line)
            if not row_cells:
                if rows:
                    break
                continue
            if len(row_cells) != 2:
                raise LedgerError("memory.md ledger table rows must have exactly two columns")
            rows.append(LedgerRow(row_cells[0], row_cells[1]))
        if not rows:
            raise LedgerError("memory.md ledger table must contain at least one mapping row")
        return rows
    raise LedgerError("memory.md is missing the `Code commit | Memory commit` table")


def _table_cells(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return []
    return [cell.strip() for cell in stripped.strip("|").split("|")]


def _is_separator_row(line: str) -> bool:
    cells = _table_cells(line)
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def validate_ledger(ledger: MemoryLedger) -> None:
    if ledger.sort_order != "newest-first":
        raise LedgerError("memory.md ledger sortOrder must be `newest-first`")
    if not ledger.rows:
        raise LedgerError("memory.md ledger must contain at least one row")
    first = ledger.rows[0]
    if first.code_commit != ledger.last_verified_code_commit:
        raise LedgerError("memory.md first Code commit row must match lastVerifiedCodeCommit")
    if first.memory_commit != ledger.last_memory_content_commit:
        raise LedgerError("memory.md first Memory commit row must match lastMemoryContentCommit")


def ledger_to_text(ledger: MemoryLedger) -> str:
    validate_ledger(ledger)
    metadata = {
        "schema": LEDGER_SCHEMA,
        "repoName": ledger.repo_name,
        "baseCodeCommit": ledger.base_code_commit,
        "baseMemoryCommit": ledger.base_memory_commit,
        "lastVerifiedCodeCommit": ledger.last_verified_code_commit,
        "lastMemoryContentCommit": ledger.last_memory_content_commit,
        "sortOrder": ledger.sort_order,
    }
    lines = [
        "# Memory Ledger",
        "",
        "```json ar-memory-ledger",
        json.dumps(metadata, indent=2),
        "```",
        "",
        "Newest entries are always inserted at the top.",
        "",
        "| Code commit | Memory commit |",
        "| ----------- | ------------- |",
    ]
    lines.extend(f"| {row.code_commit} | {row.memory_commit} |" for row in ledger.rows)
    lines.append("")
    return "\n".join(lines)


def load_ledger(path: Path) -> MemoryLedger:
    if not path.exists():
        raise LedgerError(f"memory ledger does not exist: {path}")
    return parse_ledger_text(path.read_text(encoding="utf-8"))


def write_ledger(path: Path, ledger: MemoryLedger) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ledger_to_text(ledger), encoding="utf-8")


def prepend_mapping(ledger: MemoryLedger, code_commit: str, memory_commit: str) -> MemoryLedger:
    code_commit = code_commit.strip()
    memory_commit = memory_commit.strip()
    if not code_commit or not memory_commit:
        raise LedgerError("code_commit and memory_commit are required")
    rows = [LedgerRow(code_commit, memory_commit), *ledger.rows]
    return replace(
        ledger,
        last_verified_code_commit=code_commit,
        last_memory_content_commit=memory_commit,
        rows=rows,
    )


def find_mapping(ledger: MemoryLedger, code_commit: str) -> LedgerRow | None:
    wanted = code_commit.strip()
    return next((row for row in ledger.rows if row.code_commit == wanted), None)


def create_initial_ledger(
    repo_name: str,
    code_commit: str,
    memory_commit: str,
) -> MemoryLedger:
    return MemoryLedger(
        repo_name=repo_name,
        base_code_commit=code_commit,
        base_memory_commit=memory_commit,
        last_verified_code_commit=code_commit,
        last_memory_content_commit=memory_commit,
        sort_order="newest-first",
        rows=[LedgerRow(code_commit, memory_commit)],
    )


def find_ledger_anchor_commit(memory_repo: Path, code_commit: str, memory_commit: str) -> str | None:
    row_text = f"| {code_commit} | {memory_commit} |"
    result = subprocess.run(
        ["git", "-C", str(memory_repo), "log", "--format=%H", "-S", row_text, "--", "memory.md"],
        text=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.splitlines()[0].strip() if result.stdout.splitlines() else None
