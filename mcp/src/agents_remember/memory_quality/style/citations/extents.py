"""Resolve symbol bindings shared by conversion, reference reads and the writer."""

from __future__ import annotations

import threading
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from agents_remember.memory_quality.style.citations import grammars
from agents_remember.memory_quality.style.citations.tags_engine import QualifierSpec

DEFINITION = "definition"
_DEFAULT_QUALIFIERS = QualifierSpec()
LEAN_SCOPE_EXPLANATION = (
    "An unclassified token could be a scope name, a command declared in this file, "
    "or an imported command. Cite a line range."
)


# Re-exported so nothing outside :mod:`grammars` has to know a suffix to ask whether a
# definition in a file is distinguishable from a mention of it.


@dataclass(frozen=True)
class Extent:
    """One range an anchor occupies in a file, and how it was found."""

    start: int
    end: int
    kind: str
    # For a DEFINITION extent, the line the declaration itself begins on. It differs from
    # ``start`` for a decorated definition, whose extent is widened to cover its decorator: the
    # anchor names the declaration, and a citation that starts at the declaration's own first
    # line is about that construct. ``None`` for every other kind, where the two coincide.
    declaration: int | None = None
    test_case: bool = False
    full_name: str | None = None
    scope_uncertain: bool = False
    containers: tuple[str, ...] = ()
    qualifier_kind: str = ""


@dataclass
class _DefinitionCache:
    values: dict[tuple[str, str | None, str, tuple[str, ...]], dict[str, list[Extent]]] = field(
        default_factory=dict
    )
    lock: threading.Lock = field(default_factory=threading.Lock)


_definitions_in_call: ContextVar[_DefinitionCache | None] = ContextVar(
    "definitions_in_call", default=None
)


@contextmanager
def definition_scope(language_set: grammars.LanguageSet | None = None) -> Iterator[None]:
    """Join a nested operation, or own definitions until this call finishes."""
    if language_set is not None:
        with grammars.language_set_scope(language_set), definition_scope():
            yield
        return
    if _definitions_in_call.get() is not None:
        yield
        return
    cache = _DefinitionCache()
    token = _definitions_in_call.set(cache)
    try:
        with grammars.parsing_scope():
            yield
    finally:
        _definitions_in_call.reset(token)
        cache.values.clear()


def definitions(path: str, lines: Sequence[str]) -> dict[str, list[Extent]]:
    """Every name this file binds at any depth, and the extent of the construct binding it.

    Assignments and declarators count, not only ``def`` and ``class``: an earlier
    measurement of this surface collected definitions only and read badly, because these
    documents cite module-level constants constantly. A file in an unsupported language,
    and a construct too broken to parse, bind nothing and drop to occurrence matching
    rather than failing the run -- the tree holds source written for other dialects and
    none of it is this check's business. A grammar that will not LOAD is the other case
    entirely and raises; see :mod:`grammars`.
    """
    cache = _definitions_in_call.get()
    if cache is None:
        return _definitions(path, lines)
    grammar = grammars.grammar_of(path)
    provider = grammars.active_language_set().provider_for(grammar) if grammar is not None else None
    version = "" if provider is None else provider.version
    key = (path, grammar, version, tuple(lines))
    # A request's copied ContextVars share this table across its worker threads.
    # Serialize a miss so concurrent anchors still parse their input exactly once.
    with cache.lock:
        if key not in cache.values:
            cache.values[key] = _definitions(path, lines)
        # Callers own their containers, as they did before definitions were reused.
        return {name: list(items) for name, items in cache.values[key].items()}


def _definitions(path: str, lines: Sequence[str]) -> dict[str, list[Extent]]:
    return {
        name: [
            Extent(
                start=binding.span[0],
                end=binding.span[1],
                kind=DEFINITION,
                declaration=binding.declaration,
                test_case=binding.test_case,
                full_name=binding.full_name,
                scope_uncertain=binding.scope_uncertain,
                containers=binding.containers,
                qualifier_kind=binding.qualifier_kind,
            )
            for binding in items
        ]
        for name, items in grammars.bindings(path, lines).items()
    }


def source_lines(data: bytes) -> list[str]:
    """A blob's lines as the symbol readers parse them: UTF-8, undecodable bytes kept as escapes."""

    return data.decode("utf-8", errors="surrogateescape").split("\n")


def uncertain_lean_scope(name: str, bound: Mapping[str, Sequence[Extent]]) -> bool:
    """Whether the exact Lean lookup includes a declaration with uncertain scope."""

    return any(extent.scope_uncertain for extent in bound.get(name, ()))


def qualified_spans(
    name: str, bound: Mapping[str, Sequence[Extent]], *, grammar: str | None = None
) -> tuple[tuple[int, int], ...]:
    """The distinct extents binding ``name`` in one file's :func:`definitions`.

    Lean names resolve through the exact keys supplied by the scanner.
    For other languages, ``Holder.method`` names the ``method`` inside a ``Holder``, so a same-named
    method of another class in the file does not make the name ambiguous. A symbol anchor names one
    construct only when exactly one span comes back; the curator writer (MIK-R12), the recorded
    anchor reader, and the conversion (MIK-R24) all bind symbols through this one rule.
    """
    if grammar == grammars.LEAN and uncertain_lean_scope(name, bound):
        return ()
    if name in bound:
        return _direct_spans(bound[name])
    if _uses_exact_bindings(grammar):
        return ()
    return _enclosed_qualified_spans(name, bound, _qualifier_spec(grammar))


def _direct_spans(entries: Sequence[Extent]) -> tuple[tuple[int, int], ...]:
    spans = tuple((extent.start, extent.end) for extent in entries)
    return spans if any(extent.test_case for extent in entries) else tuple(dict.fromkeys(spans))


def _uses_exact_bindings(grammar: str | None) -> bool:
    if grammar == grammars.LEAN:
        return True
    provider = grammars.active_language_set().provider_for(grammar) if grammar is not None else None
    return provider is not None and provider.uses_exact_bindings


def _qualifier_spec(grammar: str | None) -> QualifierSpec:
    if grammar is None:
        return _DEFAULT_QUALIFIERS
    provider = grammars.active_language_set().provider_for(grammar)
    return _DEFAULT_QUALIFIERS if provider is None else provider.qualifiers


def _split_qualified(
    text: str, separators: tuple[str, ...]
) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
    """Split ``text`` on the configured ``separators`` (longest match first)."""
    if not separators:
        return None
    ordered = sorted(separators, key=len, reverse=True)
    parts: list[str] = []
    used_seps: list[str] = []
    start = 0
    idx = 0
    length = len(text)
    while idx < length:
        matched = next((sep for sep in ordered if text.startswith(sep, idx)), None)
        if matched is not None:
            parts.append(text[start:idx])
            used_seps.append(matched)
            idx += len(matched)
            start = idx
        else:
            idx += 1
    parts.append(text[start:])
    if len(parts) < 2 or any(not part for part in parts):
        return None
    return tuple(parts), tuple(used_seps)


def _expand_containers(containers: tuple[str, ...], separators: tuple[str, ...]) -> tuple[str, ...]:
    expanded: list[str] = []
    for item in containers:
        split = _split_qualified(item, separators)
        if split is not None:
            expanded.extend(split[0])
        elif item:
            expanded.append(item)
    return tuple(expanded)


def _encloses(holder: Extent, inner: Extent) -> bool:
    return holder.start <= inner.start and inner.end <= holder.end


def _is_lexically_enclosed(
    target: Extent,
    parents: tuple[str, ...],
    seps: tuple[str, ...],
    bound: Mapping[str, Sequence[Extent]],
    outer_limit: Extent | None = None,
) -> bool:
    """Check whether ``parents`` form a nested chain of enclosing holders around ``target``."""
    if not parents:
        return True
    for split_end in range(1, len(parents) + 1):
        holder_name = parents[0]
        for index in range(1, split_end):
            holder_name += seps[index - 1] + parents[index]
        holders = bound.get(holder_name)
        if not holders:
            continue
        rem_parents = parents[split_end:]
        rem_seps = seps[split_end:]
        for holder in holders:
            if not _encloses(holder, target):
                continue
            if outer_limit is not None and not _encloses(outer_limit, holder):
                continue
            if _is_lexically_enclosed(target, rem_parents, rem_seps, bound, outer_limit=holder):
                return True
    return False


def _matches_qualifier_chain(
    candidate: Extent,
    parents: tuple[str, ...],
    parent_seps: tuple[str, ...],
    separators: tuple[str, ...],
    bound: Mapping[str, Sequence[Extent]],
) -> bool:
    explicit = _expand_containers(candidate.containers, separators)
    if explicit:
        if len(parents) < len(explicit) or parents[-len(explicit) :] != explicit:
            return False
        rem_count = len(parents) - len(explicit)
        rem_parents = parents[:rem_count]
        rem_seps = parent_seps[: max(0, rem_count - 1)]
        return _is_lexically_enclosed(candidate, rem_parents, rem_seps, bound)
    return _is_lexically_enclosed(candidate, parents, parent_seps, bound)


def _enclosed_qualified_spans(
    name: str,
    bound: Mapping[str, Sequence[Extent]],
    qualifiers: QualifierSpec = _DEFAULT_QUALIFIERS,
) -> tuple[tuple[int, int], ...]:
    split = _split_qualified(name, qualifiers.separators)
    if split is None:
        return ()
    parts, seps = split
    leaf_name = parts[-1]
    candidates = list(bound.get(leaf_name, ()))
    if not candidates:
        return ()
    final_sep = seps[-1]
    allowed_kinds = qualifiers.separator_kinds.get(final_sep)
    if allowed_kinds is not None:
        candidates = [one for one in candidates if one.qualifier_kind in allowed_kinds]
    parents = parts[:-1]
    parent_seps = seps[:-1]
    matched = [
        one
        for one in candidates
        if _matches_qualifier_chain(one, parents, parent_seps, qualifiers.separators, bound)
    ]
    return tuple(dict.fromkeys((extent.start, extent.end) for extent in matched))
