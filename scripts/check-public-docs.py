#!/usr/bin/env python3
"""Check the public documents against the repository tree, offline.

The public documents are ``README.md``, ``ROADMAP.md``, ``CONTRIBUTING.md``, ``SECURITY.md``,
``CODE_OF_CONDUCT.md``, ``mcp/README.md`` and every ``.md`` and ``.txt`` file under ``docs/``.
The check reads the files Git tracks, fetches nothing, and prints each finding as
``file:line: kind: target``:

- ``broken-link``: a relative link, or an absolute link to this repository, names a path that
  is not in the tree.
- ``missing-anchor``: a link names an anchor that is no heading of its target. The target is a
  public document or another Markdown file of the tree.
- ``missing-path``: a document names a repository path that does not exist. A path that Git
  ignores is a build product and is not judged. Paths under ``mcp/src/agents_remember/`` and
  ``skills/`` belong to the instruction consistency test.
- ``outside-docs``: a page under ``docs/`` links by a relative path to a file outside ``docs/``.
- ``unreached``: no link from ``docs/README.md`` reaches a file under ``docs/``; a page must be
  reached directly or through one index page.
- ``entry-link``: an entry point lacks a link that the entry points owe each other, the front
  page does not link the roadmap, or the package description links by a relative path.
- ``install-steps``: an entry point differs from ``docs/getting-started.md`` in the number, the
  order or the titles of the install steps, or shows a command line that page does not show.
- ``identifier``: a document holds an identifier of a task, a leaf, a packet or a direction, or
  a name of that shape that the product itself shows: a rule code such as
  ``R22.3-unresolved-target``, or the sandbox script's name. A line that shows such a name
  carries the marker.
- ``marker``: a marker without a reason, of an unknown kind, or with nothing to allow.

A line that names a missing path or an identifier on purpose carries
``<!-- docs-check: path: <reason> -->`` or ``<!-- docs-check: identifier: <reason> -->``. A
marker alone on its line covers the next line, or the fenced block that the next line opens.

The install steps of an entry point stand under the heading ``Install steps``. A step is a list
item ``1. **Title.**`` or a deeper heading ``### 1. Title``; its command lines are the lines of
its ``bash`` blocks.
"""

from __future__ import annotations

import argparse
import posixpath
import re
import subprocess
import sys
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from itertools import zip_longest
from pathlib import Path
from urllib.parse import unquote

ROOT_DOCUMENTS = (
    "README.md",
    "ROADMAP.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "CODE_OF_CONDUCT.md",
)
ROADMAP = "ROADMAP.md"
PACKAGE_DESCRIPTION = "mcp/README.md"
INDEX = "docs/README.md"
STEPS_SOURCE = "docs/getting-started.md"
INSTALL_INDEX = "docs/install/README.md"
ENTRY_POINTS = (
    "README.md",
    PACKAGE_DESCRIPTION,
    INDEX,
    STEPS_SOURCE,
    INSTALL_INDEX,
    "docs/llms.txt",
)
OTHER_CHECK = ("mcp/src/agents_remember/", "skills/")
PAGE_SUFFIXES = (".md", ".txt", ".html")
SHELLS = frozenset({"bash", "sh", "shell", "console"})
MARKER_KINDS = {"path": "missing-path", "identifier": "identifier"}

REPOSITORY_LINK = re.compile(
    r"https://(?:github\.com/Foxfire1st/agents-remember/(?:blob|tree|raw)"
    r"|raw\.githubusercontent\.com/Foxfire1st/agents-remember)/[^/#?\s)\"'>]+/?([^#?\s)\"'>]*)",
    re.IGNORECASE,
)
FENCE = re.compile(r"^(`{3,}|~{3,})\s*([^`]*)$")
HEADING = re.compile(r"^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
CODE_SPAN = re.compile(r"`+[^`]*`+")
INLINE_LINK = re.compile(r"\]\(\s*<?([^()\s<>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
REFERENCE_LINK = re.compile(r"^ {0,3}\[[^\]]+\]:\s*<?([^\s<>]+)>?")
HTML_LINK = re.compile(r"\b(?:href|src)=[\"']([^\"']+)[\"']")
HTML_ANCHOR = re.compile(r"\b(?:id|name)=[\"']([^\"']+)[\"']")
SCHEME = re.compile(r"^(?:[A-Za-z][A-Za-z0-9+.-]*:|//)")
PATH_TOKEN = re.compile(r"(?<![\w./<>{}$~@:\\-])[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.*<>{}$@-]+)*/?")
PLACEHOLDER = re.compile(r"[*<>{}$]")
MARKER = re.compile(r"<!--\s*docs-check:\s*([a-z-]*)\s*:?\s*(.*?)\s*-->")
STEP_ITEM = re.compile(r"^ {0,3}(\d+)\.\s+\*\*(.+?)\*\*")
STEP_HEADING = re.compile(r"^ {0,3}#{2,6}\s+(\d+)\.\s+(.+?)\s*$")
IDENTIFIERS = (
    re.compile(r"\b[A-Z][A-Z0-9]{1,7}-R\d+\b"),
    re.compile(r"\b\d{6}-[A-Z][A-Z0-9]{0,7}(?:-[A-Z]*\d+)?\b"),
    re.compile(r"\b\d{6}_[a-z0-9]+(?:-[a-z0-9]+)+\b"),
    re.compile(r"(?<![\w.#-])[LD]\d{1,3}\b(?!\.\w)"),
)
# Names of the same shape that the product itself shows: they stay, on a marked line.
PRODUCT_NAMES = (
    re.compile(r"\bR\d{1,3}\.\d{1,2}-[a-z][a-z0-9-]*"),
    re.compile(r"\b(?:ar-pnt|pnt[-_]sandbox)\b"),
)


@dataclass(frozen=True, order=True)
class Finding:
    path: str
    line: int
    kind: str
    target: str
    detail: str

    def render(self) -> str:
        return f"{self.path}:{self.line}: {self.kind}: {self.target} ({self.detail})"


@dataclass(frozen=True)
class Tree:
    """Every path of the tree, the text of each public document, and what Git ignores.

    ``read`` gives the text of another file of the tree, for the anchors of a link's target.
    """

    files: frozenset[str]
    texts: Mapping[str, str]
    ignored: Callable[[Iterable[str]], frozenset[str]]
    read: Callable[[str], str]


@dataclass(frozen=True)
class Line:
    number: int
    text: str
    fence: str | None  # the info string inside fenced code, None outside it


@dataclass(frozen=True)
class Link:
    line: int
    target: str
    path: str
    anchor: str
    relative: bool


@dataclass(frozen=True)
class Marker:
    line: int
    kind: str
    reason: str
    covers: frozenset[int]


@dataclass(frozen=True)
class Step:
    line: int
    number: int
    title: str
    commands: tuple[tuple[int, str], ...]


@dataclass(frozen=True)
class Document:
    path: str
    lines: tuple[Line, ...]
    anchors: frozenset[str]
    links: tuple[Link, ...]
    markers: tuple[Marker, ...]


@dataclass(frozen=True)
class Index:
    tree: Tree
    folders: frozenset[str]
    documents: Mapping[str, Document]

    def exists(self, path: str) -> bool:
        return path in ("", ".") or path in self.tree.files or path in self.folders

    def page(self, path: str) -> str:
        """The file a link to ``path`` opens: a folder opens its ``README.md``."""
        return posixpath.join(path, "README.md") if path in self.folders else path

    def anchors(self, path: str) -> frozenset[str] | None:
        """The anchors of the Markdown file a link to ``path`` opens; None for another target."""
        page = self.page(path)
        document = self.documents.get(page)
        if document is not None:
            return document.anchors
        if page.endswith(".md") and page in self.tree.files:
            return anchors_of(lines_of(self.tree.read(page)))
        return None


def is_document(path: str) -> bool:
    if path in ROOT_DOCUMENTS or path == PACKAGE_DESCRIPTION:
        return True
    return path.startswith("docs/") and path.endswith((".md", ".txt"))


def lines_of(text: str) -> tuple[Line, ...]:
    lines: list[Line] = []
    closing, info = "", ""
    for number, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        opened = FENCE.match(stripped) if not closing else None
        if opened:
            closing, info = opened.group(1), opened.group(2).strip().lower()
        elif not closing:
            lines.append(Line(number, raw, None))
        elif stripped.startswith(closing) and not stripped.strip(closing[0]):
            closing = ""
        else:
            lines.append(Line(number, raw, info))
    return tuple(lines)


def slug_of(title: str) -> str:
    plain = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", title)
    return re.sub(r"[^\w\- ]", "", plain.lower()).replace(" ", "-")


def anchors_of(lines: Iterable[Line]) -> frozenset[str]:
    anchors: set[str] = set()
    seen: dict[str, int] = {}
    for line in lines:
        if line.fence is not None:
            continue
        anchors.update(HTML_ANCHOR.findall(line.text))
        heading = HEADING.match(line.text)
        if heading:
            slug = slug_of(heading.group(2))
            count = seen.get(slug, 0)
            seen[slug] = count + 1
            anchors.add(f"{slug}-{count}" if count else slug)
    return frozenset(anchors)


def link_of(document: str, line: int, target: str) -> Link | None:
    absolute = REPOSITORY_LINK.match(target)
    if absolute:
        return Link(line, target, unquote(absolute.group(1)).rstrip("/"), "", False)
    if SCHEME.match(target):
        return None
    bare, _, anchor = target.partition("#")
    bare = unquote(bare.partition("?")[0])
    if not bare:
        return Link(line, target, document, anchor, True)
    path = posixpath.normpath(posixpath.join(posixpath.dirname(document), bare))
    return Link(line, target, path, anchor, True)


def links_of(document: str, lines: Iterable[Line]) -> tuple[Link, ...]:
    links: list[Link] = []
    for line in lines:
        if line.fence is not None:
            continue
        text = CODE_SPAN.sub(lambda span: " " * len(span.group()), line.text)
        targets = [*INLINE_LINK.findall(text), *REFERENCE_LINK.findall(text)]
        targets += HTML_LINK.findall(text)
        written = set(targets)
        targets += [found.group() for found in REPOSITORY_LINK.finditer(text)]
        for target in dict.fromkeys(targets):
            link = link_of(document, line.number, target)
            if link and (link.relative or target in written or not _within(target, written)):
                links.append(link)
    return tuple(links)


def _within(target: str, written: set[str]) -> bool:
    return any(target in other for other in written)


def markers_of(lines: tuple[Line, ...]) -> tuple[Marker, ...]:
    by_number = {line.number: line for line in lines}
    markers: list[Marker] = []
    for line in lines:
        found = MARKER.search(line.text) if line.fence is None else None
        if not found:
            continue
        covers = {line.number}
        if line.text.strip() == found.group():
            covers |= _next_block(by_number, line.number + 1)
        markers.append(Marker(line.number, found.group(1), found.group(2), frozenset(covers)))
    return tuple(markers)


def _next_block(by_number: Mapping[int, Line], number: int) -> set[int]:
    if number in by_number:
        return {number}
    block: set[int] = set()
    number += 1
    while number in by_number and by_number[number].fence is not None:
        block.add(number)
        number += 1
    return block


def document_of(path: str, text: str) -> Document:
    lines = lines_of(text)
    return Document(path, lines, anchors_of(lines), links_of(path, lines), markers_of(lines))


def link_findings(index: Index) -> Iterator[Finding]:
    for document in index.documents.values():
        for link in document.links:
            yield from _one_link(index, document, link)


def _one_link(index: Index, document: Document, link: Link) -> Iterator[Finding]:
    here = document.path
    if not index.exists(link.path):
        yield Finding(here, link.line, "broken-link", link.target, "the tree holds no such path")
        return
    if link.relative and here.startswith("docs/") and not link.path.startswith("docs/"):
        yield Finding(here, link.line, "outside-docs", link.target, "the site holds docs/ only")
    if link.relative and here == PACKAGE_DESCRIPTION and link.path != here:
        detail = "the package description links by absolute links"
        yield Finding(here, link.line, "entry-link", link.target, detail)
    anchors = index.anchors(link.path) if link.anchor else None
    if anchors is not None and link.anchor not in anchors:
        detail = f"no heading of {index.page(link.path)} has this anchor"
        yield Finding(here, link.line, "missing-anchor", link.target, detail)


def mentions_of(line: Line) -> Iterator[str]:
    """Path-shaped words: all of them in code, in prose only those that end like a file or folder."""
    if line.fence is not None:
        spans, prose = [line.text], ""
    else:
        spans = CODE_SPAN.findall(line.text)
        prose = CODE_SPAN.sub(" ", line.text)
    for span in spans:
        yield from (found.group().rstrip(".,;:") for found in PATH_TOKEN.finditer(span))
    for found in PATH_TOKEN.finditer(prose):
        word = found.group().rstrip(".,;:")
        if "/" in word and (word.endswith("/") or "." in word.rsplit("/", 1)[-1]):
            yield word


def named_path(word: str, roots: frozenset[str]) -> str | None:
    """The repository path a word names, cut before its first placeholder; None when it names none."""
    parts = word.rstrip("/").split("/")
    if parts[0] not in roots:
        return None
    kept: list[str] = []
    for part in parts:
        if PLACEHOLDER.search(part):
            break
        kept.append(part)
    path = "/".join(kept)
    return None if f"{path}/".startswith(OTHER_CHECK) else path


def path_findings(index: Index) -> Iterator[Finding]:
    roots = frozenset(path.split("/", 1)[0] for path in index.tree.files)
    missing: list[tuple[str, int, str, str]] = []
    for document in index.documents.values():
        for line in document.lines:
            for word in dict.fromkeys(mentions_of(line)):
                path = named_path(word, roots)
                if path and not index.exists(path):
                    missing.append((document.path, line.number, word, path))
    built = index.tree.ignored(path for _, _, _, path in missing)
    for here, number, word, path in missing:
        if path not in built:
            yield Finding(here, number, "missing-path", word, "the tree holds no such path")


def reach_findings(index: Index) -> Iterator[Finding]:
    depth = {INDEX: 0}
    queue = [INDEX]
    while queue:
        here = queue.pop(0)
        for link in index.documents[here].links:
            target = index.page(link.path)
            if link.relative and target.startswith("docs/") and target not in depth:
                depth[target] = depth[here] + 1
                if target in index.documents:
                    queue.append(target)
    for path in sorted(index.tree.files):
        if not path.startswith("docs/") or posixpath.basename(path).startswith("."):
            continue
        if path not in depth:
            yield Finding(INDEX, 1, "unreached", path, "no link from this page reaches the file")
        elif path.endswith(PAGE_SUFFIXES) and depth[path] > 2:
            yield Finding(INDEX, 1, "unreached", path, "reached through more than one index page")


def harness_pages(index: Index) -> list[str]:
    return sorted(
        path
        for path in index.documents
        if posixpath.dirname(path) == "docs/install" and path != INSTALL_INDEX
    )


def entry_link_findings(index: Index) -> Iterator[Finding]:
    front = (INDEX, STEPS_SOURCE, INSTALL_INDEX)
    owed: dict[str, tuple[str, ...]] = {
        "README.md": (*front, ROADMAP),
        PACKAGE_DESCRIPTION: front,
    }
    owed.update(dict.fromkeys(harness_pages(index), (INSTALL_INDEX, STEPS_SOURCE)))
    for here, targets in owed.items():
        document = index.documents.get(here)
        linked = {index.page(link.path) for link in document.links} if document else set()
        for target in targets:
            if target not in linked:
                yield Finding(here, 1, "entry-link", target, "this entry point does not link it")


def steps_of(document: Document) -> tuple[int, list[Step]] | None:
    """The line of the heading ``Install steps`` and the steps under it; None without the heading."""
    start, level = 0, 0
    found: list[tuple[int, int, str, list[tuple[int, str]]]] = []
    for line in document.lines:
        heading = HEADING.match(line.text) if line.fence is None else None
        if not start:
            if heading and heading.group(2).strip().lower() == "install steps":
                start, level = line.number, len(heading.group(1))
            continue
        if heading and len(heading.group(1)) <= level:
            break
        step = None if line.fence is not None else _step_start(line.text)
        if step:
            found.append((line.number, step[0], step[1], []))
        elif found and line.fence is not None and line.fence.split(" ")[0] in SHELLS:
            command = line.text.strip().removeprefix("$ ")
            if command and not command.startswith("#"):
                found[-1][3].append((line.number, command))
    if not start:
        return None
    return start, [Step(line, number, title, tuple(cmds)) for line, number, title, cmds in found]


def _step_start(text: str) -> tuple[int, str] | None:
    found = STEP_ITEM.match(text) or STEP_HEADING.match(text)
    if not found:
        return None
    return int(found.group(1)), found.group(2).strip().rstrip(".:").strip()


def step_findings(index: Index) -> Iterator[Finding]:
    source = index.documents.get(STEPS_SOURCE)
    told = steps_of(source) if source else None
    for here in (*ENTRY_POINTS, *harness_pages(index)):
        document = index.documents.get(here)
        shown = steps_of(document) if document else None
        if shown is None:
            detail = "the entry point has no section of this title"
            yield Finding(here, 1, "install-steps", "Install steps", detail)
        elif told is not None:
            yield from _compare_steps(here, shown, told[1])


def _compare_steps(here: str, shown: tuple[int, list[Step]], told: list[Step]) -> Iterator[Finding]:
    start, steps = shown
    if not steps or len(steps) != len(told):
        detail = f"{STEPS_SOURCE} names {len(told)} steps"
        yield Finding(here, start, "install-steps", f"{len(steps)} steps", detail)
    for position, (step, full) in enumerate(zip_longest(steps, told), 1):
        if step is None or full is None:
            continue
        if step.number != position:
            detail = f"step {position} is numbered {step.number}"
            yield Finding(here, step.line, "install-steps", f"{step.number}. {step.title}", detail)
        if step.title != full.title:
            detail = f"{STEPS_SOURCE} titles step {position} '{full.title}'"
            yield Finding(here, step.line, "install-steps", step.title, detail)
        known = {command for _, command in full.commands}
        for number, command in step.commands:
            if command not in known:
                detail = f"{STEPS_SOURCE} shows no such command line in step {position}"
                yield Finding(here, number, "install-steps", command, detail)


def identifier_findings(index: Index) -> Iterator[Finding]:
    for document in index.documents.values():
        for line in document.lines:
            for pattern in IDENTIFIERS:
                for found in pattern.finditer(line.text):
                    detail = "an identifier of a task, a leaf, a packet or a direction"
                    yield Finding(document.path, line.number, "identifier", found.group(), detail)
            for pattern in PRODUCT_NAMES:
                for found in pattern.finditer(line.text):
                    detail = "a name with the shape of an identifier; mark the line if the product shows it"
                    yield Finding(document.path, line.number, "identifier", found.group(), detail)


def apply_markers(index: Index, raw: Iterable[Finding]) -> Iterator[Finding]:
    """Drop each finding that a marker allows; report every marker that is wrong or allows nothing."""
    used: set[tuple[str, int]] = set()
    for finding in raw:
        allowing = [
            marker
            for marker in index.documents[finding.path].markers
            if finding.line in marker.covers and MARKER_KINDS.get(marker.kind) == finding.kind
        ]
        if not allowing:
            yield finding
        used.update((finding.path, marker.line) for marker in allowing)
    for document in index.documents.values():
        for marker in document.markers:
            problem = _marker_problem(marker, (document.path, marker.line) in used)
            if problem:
                target = marker.kind or "docs-check"
                yield Finding(document.path, marker.line, "marker", target, problem)


def _marker_problem(marker: Marker, used: bool) -> str | None:
    if marker.kind not in MARKER_KINDS:
        return "the marker names no known kind (path, identifier)"
    if not marker.reason:
        return "the marker gives no reason"
    return None if used else "the marker allows nothing on the lines it covers"


def check(tree: Tree) -> list[Finding]:
    folders = {
        "/".join(parts[:end])
        for parts in (path.split("/") for path in tree.files)
        for end in range(1, len(parts))
    }
    documents = {path: document_of(path, text) for path, text in sorted(tree.texts.items())}
    index = Index(tree, frozenset(folders), documents)
    if INDEX not in documents:
        return [Finding(INDEX, 1, "unreached", INDEX, "the index of the documentation is missing")]
    marked = [*link_findings(index), *path_findings(index), *identifier_findings(index)]
    allowed = apply_markers(index, marked)
    findings = [
        *reach_findings(index),
        *entry_link_findings(index),
        *step_findings(index),
        *allowed,
    ]
    return sorted(set(findings))


def _git(root: Path, *arguments: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments], cwd=root, input=stdin, capture_output=True, text=True, check=False
    )


def load_tree(root: Path) -> Tree:
    """The files Git tracks under ``root``, without the ones the checkout has lost."""
    listed = _git(root, "ls-files", "-z", "--cached")
    if listed.returncode != 0:
        raise SystemExit(f"check-public-docs: git ls-files failed in {root}: {listed.stderr}")
    files = frozenset(
        path for path in listed.stdout.split("\0") if path and (root / path).is_file()
    )
    texts = {path: (root / path).read_text(encoding="utf-8") for path in files if is_document(path)}

    def ignored(paths: Iterable[str]) -> frozenset[str]:
        asked: dict[str, set[str]] = {}
        for path in paths:
            for form in _forms_git_can_judge(root, path):
                asked.setdefault(form, set()).add(path)
        if not asked:
            return frozenset()
        answer = _git(root, "check-ignore", "-z", "--stdin", stdin="\0".join(sorted(asked)) + "\0")
        if answer.returncode not in (0, 1):
            raise SystemExit(f"check-public-docs: git check-ignore failed: {answer.stderr}")
        named = (form for form in answer.stdout.split("\0") if form)
        return frozenset(path for form in named for path in asked.get(form, ()))

    def read(path: str) -> str:
        return (root / path).read_text(encoding="utf-8")

    return Tree(files, texts, ignored, read)


def _forms_git_can_judge(root: Path, path: str) -> tuple[str, ...]:
    """The path as Git is asked about it.

    Git refuses a path that runs through a symbolic link, so such a path is cut at the link. A
    path that is no file is also asked as a folder: a folder-only ignore rule matches a missing
    folder only in that form.
    """
    parts = path.split("/")
    for count in range(1, len(parts) + 1):
        prefix = "/".join(parts[:count])
        if (root / prefix).is_symlink():
            return (prefix,)
    return (path,) if (root / path).is_file() else (path, f"{path}/")


def main() -> None:
    parser = argparse.ArgumentParser(description="Check the public documents against the tree.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    root = parser.parse_args().root.resolve()
    tree = load_tree(root)
    findings = check(tree)
    for finding in findings:
        print(finding.render())
    print(f"check-public-docs: {len(tree.texts)} documents, {len(findings)} findings")
    if findings:
        sys.exit(1)


if __name__ == "__main__":
    main()
