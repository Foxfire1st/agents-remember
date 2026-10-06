"""Canonical form of the two test-evidence catalogs, and the one command that writes it.

``mcp/tests/test-evidence-lanes.toml`` and ``mcp/tests/evidence-lifecycle.toml`` are edited by almost
every change that adds a test file, and Git merges them line by line (``merge=union``, see
``.gitattributes``). That only works when the catalogs are in one canonical form: contract rows
ordered by ``id``, artifact rows ordered by ``path``, every list in ascending byte order without
duplicates, one path per line. Both loaders refuse a catalog that is not canonical and name
:data:`WRITE_COMMAND`; this module is the code both loaders and the command share.

The command makes no decision. It orders, removes duplicate lines and the lines of files that no
longer exist, and sets the ``consumers`` of an ``exact`` or ``exact-source`` row to the set the
dependency oracle derives from the source tree. It adds and removes no row, never assigns a lane,
keeps comments, and writes nothing when it cannot read a catalog or the dependency facts are
incomplete.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any

from agents_remember_test_support.code_quality.scope import ScopeError
from agents_remember_test_support.testing.dependency_facts import RepositoryDependencyFacts
from agents_remember_test_support.testing.evidence_governance import LIFECYCLE_CATALOG_PATH

LANE_CATALOG_PATH = "mcp/tests/test-evidence-lanes.toml"
WRITE_COMMAND = (
    "python -m agents_remember_test_support.testing.evidence_lifecycle --project-root . --write"
)
INTERLEAVED_ROWS_HINT = (
    "two rows added by different leaves may have been interleaved by the merge; "
    "restore the file from the landed commit and add this leaf's row again"
)
WRITTEN_FORM = "with one path per line, in double quotes, with a comma after each path"
ONE_PATH_PER_LINE = (
    f"is not written {WRITTEN_FORM} (`key = [`, then the paths, then `]` on its own line)"
)
_DERIVED_SCOPES = frozenset({"exact", "exact-source"})
_HEADER = re.compile(r"^\[\[?([A-Za-z0-9_.-]+)\]\]?\s*(?:#.*)?$")
_LIST_START = re.compile(r"^([A-Za-z0-9_-]+) = \[(.*)$")


class CatalogWriteError(RuntimeError):
    """A catalog cannot be rewritten; nothing was written."""


def unreadable_catalog(what: str, path: object, error: Exception) -> str:
    """The refusal every reader of a catalog gives for a file it cannot read or parse."""

    hint = f"; {INTERLEAVED_ROWS_HINT}" if isinstance(error, tomllib.TOMLDecodeError) else ""
    return f"cannot read {what} {path}: {error}{hint}"


def read_catalog(what: str, path: Path, refusal: type[Exception]) -> tuple[str, dict[str, Any]]:
    """Read and parse one catalog; a file that cannot be read or parsed is refused by name.

    Both loaders, the start of a test run and the helper test read through here, each with its
    own ``refusal``, so none of them shows a bare parser error in place of the sentence that
    says what to do. The command reads the files itself, because it also refuses CRLF.
    """

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise refusal(unreadable_catalog(what, path, error)) from error
    return text, parse_catalog(what, path, text, refusal)


def parse_catalog(what: str, name: object, text: str, refusal: type[Exception]) -> dict[str, Any]:
    """Parse the text of one catalog; text that does not parse is refused by ``name``.

    For a reader that holds the text and not the file, such as test selection with the catalog
    of the base revision.
    """

    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise refusal(unreadable_catalog(what, name, error)) from error


def lane_files(
    document: Mapping[str, Any], path: object, refusal: type[Exception]
) -> Mapping[str, Any]:
    """The ``[files]`` table of a parsed lane manifest; a manifest without it is refused by name."""

    files = document.get("files")
    if not isinstance(files, Mapping):
        raise refusal(
            f"evidence lane manifest {path} has no [files] table; write the lane lists under "
            "the header [files]"
        )
    return files


def list_defect(values: Sequence[str]) -> str | None:
    """Name what keeps a list from canonical form, or ``None`` when it is canonical."""

    out_of_order = any(left > right for left, right in pairwise(values))
    duplicated = len(set(values)) != len(values)
    if out_of_order and duplicated:
        return "is out of order and holds duplicates"
    if out_of_order:
        return "is out of order"
    if duplicated:
        return "holds duplicates"
    return None


def row_order_defects(keys: Sequence[object], label: str, order_field: str) -> list[str]:
    """One finding per row that sorts before the row above it."""

    findings: list[str] = []
    for previous, current in pairwise(keys):
        if isinstance(previous, str) and isinstance(current, str) and current < previous:
            findings.append(
                f"{current}: {label} row is out of order (rows are ordered by {order_field}); "
                f"run {WRITE_COMMAND}"
            )
    return findings


def list_findings(owner: str, kind: str, values: object) -> list[str]:
    """The finding for one list of paths, or nothing when it is canonical or not a string list."""

    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        return []
    defect = list_defect(values)
    return [] if defect is None else [f"{owner}: {kind} {defect}; run {WRITE_COMMAND}"]


def layout_findings(text: str, document: Mapping[str, object], label: str) -> list[str]:
    """One finding per list that is not written as the command writes it: one path per line.

    Layout is a property of the lines, so each table the TOML parser sees is paired with the
    block of lines under its header. A header the line scan does not find is the one finding.
    """

    _, blocks = _split(text)
    try:
        _require_tables_read(document, blocks, label)
    except CatalogWriteError as error:
        return [str(error)]
    artifacts, files = document.get("artifact"), document.get("files")
    rows = iter(artifacts if isinstance(artifacts, list) else [])
    findings: list[str] = []
    for block in blocks:
        lists: list[tuple[str, str, object]] = []
        if block.kind == "artifact":
            row = next(rows, None)
            if isinstance(row, Mapping):
                lists = [(f"{row.get('path')}: consumers", "consumers", row.get("consumers"))]
        elif block.kind == "files" and isinstance(files, Mapping):
            lists = [(f"{lane}: lane list", lane, paths) for lane, paths in files.items()]
        findings.extend(
            f"{name} {ONE_PATH_PER_LINE}; run {WRITE_COMMAND}"
            for name, key, values in lists
            if not _one_path_per_line(block.lines, key, values)
        )
    return findings


def _one_path_per_line(lines: Sequence[str], key: str, values: object) -> bool:
    """Whether a block writes the list ``key`` exactly as the command renders it."""

    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        return True  # not a list of paths: the loader's own checks name that
    rendered = _render(key, values)
    at = lines.index(rendered[0]) if rendered[0] in lines else len(lines)
    return list(lines[at : at + len(rendered)]) == rendered


@dataclass
class _Block:
    kind: str
    lines: list[str]


def write_canonical_catalogs(root: Path) -> list[str]:
    """Rewrite both catalogs to canonical form and return one report line per change.

    Everything is computed before anything is written, so a refusal leaves both files alone.
    """

    root = root.resolve()
    lifecycle_path = root / LIFECYCLE_CATALOG_PATH
    lane_path = root / LANE_CATALOG_PATH
    lifecycle_text = _read(lifecycle_path)
    lane_text = _read(lane_path)
    try:
        facts = RepositoryDependencyFacts.build(root)
    except ScopeError as error:
        raise CatalogWriteError(f"cannot list the repository's files ({error})") from error
    if facts.parse_error is not None:
        raise CatalogWriteError(f"source-derived consumer graph is incomplete: {facts.parse_error}")
    if facts.ambiguous_modules:
        raise CatalogWriteError(
            f"source-derived consumer graph has ambiguous modules: {sorted(facts.ambiguous_modules)}"
        )

    def derive(path: str, scope: str) -> list[str]:
        observed = (
            facts.observed_source_consumers(Path(path))
            if scope == "exact-source"
            else facts.observed_test_consumers(Path(path))
        )
        return sorted(item.as_posix() for item in observed)

    def exists(path: str) -> bool:
        return (root / path).is_file()

    report: list[str] = []
    new_lifecycle = canonical_lifecycle_text(
        lifecycle_text, derive, exists, report, LIFECYCLE_CATALOG_PATH
    )
    new_lanes = canonical_lane_text(lane_text, exists, report, LANE_CATALOG_PATH)
    for label, before, after in (
        (LIFECYCLE_CATALOG_PATH, lifecycle_text, new_lifecycle),
        (LANE_CATALOG_PATH, lane_text, new_lanes),
    ):
        if before != after and not any(line.startswith(f"{label}: ") for line in report):
            report.append(f"{label}: whitespace normalised")
    if new_lifecycle != lifecycle_text:
        lifecycle_path.write_text(new_lifecycle, encoding="utf-8", newline="")
    if new_lanes != lane_text:
        lane_path.write_text(new_lanes, encoding="utf-8", newline="")
    return report


def canonical_lifecycle_text(
    text: str,
    derive: Callable[[str, str], list[str]],
    exists: Callable[[str], bool],
    report: list[str],
    label: str,
) -> str:
    """Canonical form of the lifecycle catalog.

    ``derive(path, scope)`` is the oracle's set; ``exists(path)`` tells whether the tree has a file.
    """

    document = _require_parseable(text, label)
    preamble, blocks = _split(text)
    _require_tables_read(document, blocks, label)
    keyed: dict[str, list[tuple[str, _Block]]] = {}
    for block in blocks:
        row = _row_of(block, label)
        key_name = "id" if block.kind == "contract" else "path"
        if block.kind in {"contract", "artifact"} and any(
            existing == str(row.get(key_name)) for existing, _ in keyed.get(block.kind, [])
        ):
            raise CatalogWriteError(
                f"{label}: the {block.kind} row {row.get(key_name)!r} is listed twice; the command "
                "removes no row: delete one copy by hand. If a merge with a side whose catalogs "
                "were not in canonical order doubled the rows, restore the file from the landed "
                "commit and add that side's own lines again"
            )
        if block.kind == "artifact":
            _canonicalize_consumers(block, row, derive, report, label)
            _drop_gone_consumers(block, row, exists, report, label)
        keyed.setdefault(block.kind, []).append((str(row.get(key_name, "")), block))
    ordered: list[_Block] = []
    for kind, rows in keyed.items():
        if kind in {"contract", "artifact"}:
            after = sorted(rows, key=lambda item: item[0])
            if [item[0] for item in after] != [item[0] for item in rows]:
                report.append(f"{label}: {kind} rows reordered")
            ordered.extend(block for _, block in after)
        else:
            ordered.extend(block for _, block in rows)
    return _join(preamble, ordered)


def canonical_lane_text(
    text: str, exists: Callable[[str], bool], report: list[str], label: str
) -> str:
    """Canonical form of the lane manifest: every ``[files]`` list sorted without duplicates."""

    document = _require_parseable(text, label)
    lane_files(document, label, CatalogWriteError)
    preamble, blocks = _split(text)
    _require_tables_read(document, blocks, label)
    for block in blocks:
        if block.kind == "files":
            read = _rewrite_lists(block, None, report, label)
            _require_lists_read(document.get("files"), read, label)
            _drop_gone(block, None, exists, report, label)
    return _join(preamble, blocks)


def _read(path: Path) -> str:
    try:
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeError) as error:
        raise CatalogWriteError(f"cannot read {path}: {error}") from error
    if "\r" in text:
        raise CatalogWriteError(
            f"{path} has CRLF line endings; the catalogs are LF files (the repository sets no "
            "other convention): convert it to LF, then run the command again"
        )
    return text


def _require_parseable(text: str, label: str) -> dict[str, object]:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise CatalogWriteError(
            f"cannot parse {label}: {error}. A catalog is never guessed at: repair it by hand; "
            f"{INTERLEAVED_ROWS_HINT}"
        ) from error


def _require_tables_read(
    document: Mapping[str, object], blocks: Sequence[_Block], label: str
) -> None:
    """Refuse when the TOML parser sees a table whose header the line scan did not find.

    The rewrite works on lines. A row under such a header would ride in the block above it and
    be neither ordered nor derived, while the command reported that nothing needed changing.
    """

    for kind, value in document.items():
        tables = value if isinstance(value, list) else [value]
        if not tables or not all(isinstance(table, dict) for table in tables):
            continue
        if len(tables) != sum(block.kind == kind for block in blocks):
            header = f"[[{kind}]]" if isinstance(value, list) else f"[{kind}]"
            raise CatalogWriteError(
                f"{label}: a {header} table header is written in a form the command does not "
                f"read; write it at the start of its own line as {header} (a comment may follow)"
            )


def _require_lists_read(
    table: object, read: Sequence[str], label: str, only: str | None = None
) -> None:
    """Refuse when the TOML parser sees a list (or the key ``only``) the line scan did not find."""

    if not isinstance(table, Mapping):
        return
    for key, value in table.items():
        wanted = isinstance(value, list) if only is None else key == only
        if wanted and key not in read:
            raise CatalogWriteError(
                f"{label}: the list {key} is written in a form the command does not read; write "
                f"it as `{key} = [` at the start of a line, one path per line, closed by `]` on "
                "its own line"
            )


def _split(text: str) -> tuple[list[str], list[_Block]]:
    """Cut a catalog into the lines before its first table and one block per table."""

    lines = text.splitlines()
    headers = [index for index, line in enumerate(lines) if _HEADER.match(line)]
    starts = []
    for index in headers:
        start = index
        while start > 0 and lines[start - 1].startswith("#"):
            start -= 1
        starts.append(start)
    first = starts[0] if starts else len(lines)
    preamble = _trim(lines[:first])
    blocks: list[_Block] = []
    for number, header in enumerate(headers):
        end = starts[number + 1] if number + 1 < len(starts) else len(lines)
        match = _HEADER.match(lines[header])
        assert match is not None
        blocks.append(_Block(match.group(1), _trim(lines[starts[number] : end])))
    return preamble, blocks


def _trim(lines: list[str]) -> list[str]:
    start, end = 0, len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return lines[start:end]


def _join(preamble: list[str], blocks: Sequence[_Block]) -> str:
    parts = [preamble] if preamble else []
    parts.extend(block.lines for block in blocks)
    return "\n\n".join("\n".join(part) for part in parts) + "\n"


def _row_of(block: _Block, label: str) -> dict[str, object]:
    document = tomllib.loads("\n".join(block.lines))
    rows = document.get(block.kind)
    if isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], dict):
        return rows[0]
    if isinstance(rows, dict):
        return rows
    raise CatalogWriteError(f"cannot read a [{block.kind}] block of {label}")


def _canonicalize_consumers(
    block: _Block,
    row: Mapping[str, object],
    derive: Callable[[str, str], list[str]],
    report: list[str],
    label: str,
) -> None:
    path, scope = row.get("path"), row.get("consumer_scope")
    target: list[str] | None = None
    if isinstance(path, str) and scope in _DERIVED_SCOPES:
        derived = derive(path, str(scope))
        if derived:
            target = derived
        else:
            report.append(
                f"{label}: {path}: the source tree shows no consumer; consumers kept as written "
                "(remove the row if the artifact is retired)"
            )
    read = _rewrite_lists(block, "consumers", report, f"{label}: {path}", target)
    _require_lists_read(row, read, f"{label}: {path}", "consumers")
    if not read and target is not None:
        block.lines.extend(_render("consumers", target))
        report.append(f"{label}: {path}: consumers added: {len(target)} path(s)")


def _drop_gone_consumers(
    block: _Block,
    row: Mapping[str, object],
    exists: Callable[[str], bool],
    report: list[str],
    label: str,
) -> None:
    """Drop the consumers of a row whose files are gone; never the row, never its last consumer.

    Whether a row whose artifact is gone, or that no existing file consumes, is retired is a
    decision: the command names the first and leaves both as they are.
    """

    path, consumers = row.get("path"), row.get("consumers")
    if isinstance(path, str) and not exists(path):
        report.append(
            f"{label}: {path}: the artifact file does not exist; the command removes no row "
            "(delete the row if the artifact is retired)"
        )
    if isinstance(consumers, list) and any(exists(str(item)) for item in consumers):
        _drop_gone(block, "consumers", exists, report, f"{label}: {path}")


def _drop_gone(
    block: _Block,
    only: str | None,
    exists: Callable[[str], bool],
    report: list[str],
    label: str,
) -> None:
    """Remove each line of a rewritten list whose file does not exist, and report it.

    A union merge keeps a list line that one side deleted when the other side changed the lines
    next to it, so the line of a deleted file can come back. That the file is gone is a fact of
    the tree, not a decision.
    """

    kept: list[str] = []
    key: str | None = None
    for line in block.lines:
        opened = _LIST_START.match(line)
        if key is not None and line == "]":
            key = None
        elif key is not None:
            value = str(tomllib.loads(f"value = [{line}]")["value"][0])
            if not exists(value):
                name = label if only is not None else f"{label}: {key}"
                report.append(f"{name}: removed {value}: the file does not exist")
                continue
        elif opened is not None and not opened.group(2) and only in (None, opened.group(1)):
            key = opened.group(1)
        kept.append(line)
    block.lines = kept


def _rewrite_lists(
    block: _Block,
    only: str | None,
    report: list[str],
    label: str,
    target: list[str] | None = None,
) -> list[str]:
    """Rewrite each list of a block (or the list ``only``) in canonical form; return their keys."""

    lines = block.lines
    result: list[str] = []
    read: list[str] = []
    index = 0
    while index < len(lines):
        match = _LIST_START.match(lines[index])
        if match is None or (only is not None and match.group(1) != only):
            result.append(lines[index])
            index += 1
            continue
        key = match.group(1)
        read.append(key)
        end = index if "]" in match.group(2) else _closing_line(lines, index, label, key)
        region = lines[index : end + 1]
        if any("#" in line for line in region):
            raise CatalogWriteError(
                f"{label}: the list {key} holds a comment, which the command cannot keep; "
                "move the comment above the list"
            )
        current = _values_of(region, label, key)
        wanted = sorted(set(current)) if target is None else target
        rendered = _render(key, wanted)
        name = f"{label}: {key}" if only is None else label
        _report_list(report, name, current, wanted, target is not None)
        if current == wanted and _squeezed(region) != _squeezed(rendered):
            # More than spacing differs: quotes or commas were not as the command writes them.
            report.append(f"{name}: rewritten {WRITTEN_FORM}")
        result.extend(rendered)
        index = end + 1
    block.lines = result
    return read


def _values_of(region: list[str], label: str, key: str) -> list[str]:
    try:
        document = tomllib.loads("\n".join(region))
    except tomllib.TOMLDecodeError as error:
        raise CatalogWriteError(f"{label}: cannot read the list {key}: {error}") from error
    if set(document) != {key}:
        raise CatalogWriteError(
            f"{label}: the list {key} is not closed by `]` on its own line, so the command cannot "
            "tell where it ends; write one path per line, then `]` on a line of its own"
        )
    parsed = document[key]
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise CatalogWriteError(f"{label}: the list {key} must hold only strings")
    return parsed


def _squeezed(lines: Sequence[str]) -> str:
    """The lines without any whitespace, to tell a change of spacing from any other change."""

    return "".join("".join(lines).split())


def _render(key: str, values: Sequence[str]) -> list[str]:
    return [f"{key} = [", *(f"  {_quote(value)}," for value in values), "]"]


def _quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _closing_line(lines: list[str], start: int, label: str, key: str) -> int:
    for position in range(start + 1, len(lines)):
        if lines[position].strip() == "]":
            return position
    raise CatalogWriteError(f"{label}: the list {key} has no closing bracket")


def _report_list(
    report: list[str],
    label: str,
    current: list[str],
    wanted: list[str],
    derived: bool,
) -> None:
    if current == wanted:
        return
    parts: list[str] = []
    added = sorted(set(wanted) - set(current))
    removed = sorted(set(current) - set(wanted))
    if derived and (added or removed):
        parts.append(f"derived from source (added {added}, removed {removed})")
    duplicates = len(current) - len(set(current))
    if duplicates:
        parts.append(f"removed {duplicates} duplicate line(s)")
    distinct = list(dict.fromkeys(current))
    if distinct != sorted(distinct):
        parts.append("reordered")
    report.append(f"{label}: {'; '.join(parts) or 'rewritten'}")
