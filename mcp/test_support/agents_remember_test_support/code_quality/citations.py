"""Resolve repository path citations in package comments and docstrings.

Scope is ``mcp/src/agents_remember`` excluding ``package_data`` and tests. A checked
token must be prose, contain a directory separator, end in a source/documentation
extension, not name a memory sidecar, and start at an anchor derived from the repository
or package tree. It resolves against either root; ``:NN`` and ``:NN-MM`` anchors must be
in bounds.

Known false-positive boundaries:

1. String operands are runtime data, not prose citations.
2. Bare filenames, runtime-artifact extensions, sidecar names, and third-party roots are
   outside the grammar.
3. Fenced examples inside docstrings are scanned as prose.
4. A stale citation whose old target still exists passes because this check proves
   existence, not intent.
5. An external path whose first segment collides with a local anchor is reported if it is
   written as prose; qualify it as external.

Failures list every unresolved or out-of-bounds token. Repair the path/line anchor or
rewrite text that is not intended as a repository citation.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from dataclasses import dataclass
from pathlib import Path

from agents_remember_test_support.code_quality.single_owner import Offender, package_modules, report

__all__ = [
    "CITATION_PATTERN",
    "CITATION_REMEDIATION",
    "SIDECAR_PATTERN",
    "SOURCE_EXTENSIONS",
    "Citation",
    "Offender",
    "all_citations",
    "anchors",
    "citations_in_source",
    "module_citation_offenders",
    "prose_spans",
    "report",
    "resolve",
    "unresolved_citations",
]

SOURCE_EXTENSIONS = ("py", "md", "ts", "tsx")
"""Source and documentation extensions, longest-first so the alternation cannot match
``ts`` inside ``tsx``. Data extensions are excluded as a class -- see rule 3 in the module
docstring: every one measured in this package is a runtime artifact."""

_EXTENSIONS = "|".join(sorted(SOURCE_EXTENSIONS, key=len, reverse=True))
CITATION_PATTERN = re.compile(
    r"(?<![\w./-])"
    rf"(?P<path>[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+\.(?P<ext>{_EXTENSIONS}))"
    r"(?::(?P<start>\d+)(?:-(?P<end>\d+))?)?"
    r"(?![\w/])"
)
"""A path with at least one directory separator, a source extension, and an optional line
anchor. Built FROM :data:`SOURCE_EXTENSIONS` so the constant and the grammar cannot
disagree. The leading and trailing guards stop a match starting mid-path or mid-word."""

SIDECAR_PATTERN = re.compile(rf"\.(?:{_EXTENSIONS})\.(?:{_EXTENSIONS})$")
"""Rule 4: a doubled source extension is an external-memory sidecar name by construction."""

CITATION_REMEDIATION = (
    "point the citation at the file that exists now, or delete it -- a prose pointer at a "
    "path this repository does not have costs a reader more than no pointer at all; if the "
    "target legitimately lives outside this repository, cite it in a form that says so "
    "(a distribution or task name) so it falls outside this check's grammar"
)


@dataclass(frozen=True)
class Citation:
    """One prose reference to a path, with the line anchor it carried."""

    module: str
    line: int
    path: str
    start: int | None = None
    end: int | None = None

    @property
    def text(self) -> str:
        if self.start is None:
            return self.path
        if self.end is None:
            return f"{self.path}:{self.start}"
        return f"{self.path}:{self.start}-{self.end}"


def anchors(repo_root: Path, package_root: Path) -> frozenset[str]:
    """Directory names a citation may start with, derived from the tree.

    Top-level directories of the repository plus top-level subpackages of the source
    package. Read off the filesystem rather than listed here so a directory added tomorrow
    is citable tomorrow, for the same reason ``check.derive_scope`` reads its scope from
    ``git ls-files`` instead of carrying a constant that drifts.
    """
    found = {entry.name for entry in repo_root.iterdir() if entry.is_dir()}
    found |= {entry.name for entry in package_root.iterdir() if entry.is_dir()}
    return frozenset(name for name in found if not name.startswith(".") and name != "__pycache__")


def prose_spans(source: str, tree: ast.AST) -> list[tuple[int, str]]:
    """``(line, text)`` for every comment and docstring -- prose, never a string operand.

    Rule 1 of the grammar, and the filter that does the most work: a path in a string
    argument is runtime data (a copy instruction, a settings filename), not a reference.
    """
    spans: list[tuple[int, str]] = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.COMMENT:
                spans.append((token.start[0], token.string))
    except (tokenize.TokenError, IndentationError):
        pass
    spans.extend(_docstrings(tree))
    return spans


def _docstrings(tree: ast.AST) -> list[tuple[int, str]]:
    holders = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    spans: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, holders):
            continue
        first = node.body[0] if node.body else None
        if not isinstance(first, ast.Expr) or not isinstance(first.value, ast.Constant):
            continue
        if isinstance(first.value.value, str):
            spans.append((first.value.lineno, first.value.value))
    return spans


def citations_in_source(
    source: str, tree: ast.AST, module: str, known: frozenset[str]
) -> list[Citation]:
    """Every in-grammar citation in one module's prose."""
    found: list[Citation] = []
    for line, text in prose_spans(source, tree):
        for match in CITATION_PATTERN.finditer(text):
            path = match.group("path")
            if path.split("/", 1)[0] not in known or SIDECAR_PATTERN.search(path):
                continue
            start, end = match.group("start"), match.group("end")
            found.append(
                Citation(
                    module,
                    line,
                    path,
                    int(start) if start else None,
                    int(end) if end else None,
                )
            )
    return sorted(found, key=lambda citation: (citation.line, citation.path))


def resolve(citation: Citation, roots: tuple[Path, ...]) -> Path | None:
    """The file a citation names, or ``None`` when no declared root holds it."""
    for root in roots:
        candidate = root / citation.path
        if candidate.is_file():
            return candidate
    return None


def _anchor_offender(citation: Citation, target: Path) -> Offender | None:
    """A citation whose line anchor points past the end of the file it resolved to."""
    last = max(citation.start or 0, citation.end or 0)
    if last == 0:
        return None
    total = len(target.read_text(encoding="utf-8", errors="replace").splitlines())
    if last <= total:
        return None
    return Offender(
        citation.module,
        citation.line,
        "line past end of file",
        f"cites {citation.text} but that file has {total} lines",
    )


def module_citation_offenders(
    source: str, tree: ast.AST, module: str, *, roots: tuple[Path, ...], known: frozenset[str]
) -> list[Offender]:
    """Every citation in one module that does not resolve, or overruns its target."""
    offenders: list[Offender] = []
    for citation in citations_in_source(source, tree, module, known):
        target = resolve(citation, roots)
        if target is None:
            offenders.append(
                Offender(
                    citation.module,
                    citation.line,
                    "unresolved citation",
                    f"cites {citation.text}, which no declared root holds",
                )
            )
            continue
        overrun = _anchor_offender(citation, target)
        if overrun is not None:
            offenders.append(overrun)
    return offenders


def unresolved_citations(repo_root: Path, package_root: Path) -> list[Offender]:
    """Every prose citation in the package that names a path this repository lacks."""
    roots = (repo_root, package_root)
    known = anchors(repo_root, package_root)
    offenders: list[Offender] = []
    for path in package_modules(package_root):
        module = path.relative_to(package_root).as_posix()
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        offenders.extend(module_citation_offenders(source, tree, module, roots=roots, known=known))
    return offenders


def all_citations(repo_root: Path, package_root: Path) -> list[Citation]:
    """Every in-grammar citation in the package -- what the check is actually watching."""
    known = anchors(repo_root, package_root)
    found: list[Citation] = []
    for path in package_modules(package_root):
        module = path.relative_to(package_root).as_posix()
        source = path.read_text(encoding="utf-8")
        found.extend(citations_in_source(source, ast.parse(source), module, known))
    return found
