"""Multi-file and folder impact view (``view="impact"``) for ``knowledge_read``.

Deduplicates live invariants across queried files/folder, groups them by live family with family
guarantee and 1-line out-of-scope ``familySiblings``, includes open weaknesses and standing
dependent documents, trims statements past 80 words with ``… (full: records <ID>)``, uses uniform
repo-relative paths, reports exact rule/page hints when multi-page, and supports ``compact=True``
one-line-per-rule output within the 3,500-token per-page target.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from agents_remember.application.file_briefs.titles import rule_title
from agents_remember.application.knowledge_currentness import Currentness
from agents_remember.application.knowledge_query_formatting import (
    anchor_counts,
    decode_query_cursor,
    encode_query_cursor,
    estimate_total_pages,
    fit_slice_in_budget,
    normalize_repo_path,
    resolve_indexed_paths,
    scope_digest,
    standing_label,
    trim_statement,
)
from agents_remember.application.knowledge_reader.marks import dependent_list, weakness_list
from agents_remember.application.knowledge_reader.marks_compact import compact_dependent_rows
from agents_remember.application.knowledge_reader.paths import states_at
from agents_remember.application.knowledge_reader.selection import ReaderSelection
from agents_remember.memory.knowledge_index import KnowledgeIndex, Record, RecordStanding

DEFAULT_IMPACT_BUDGET_TOKENS: Final = 3150
_SIBLING_FILE_CAP: Final = 3
_ALSO_AT_FILE_CAP: Final = 3
_MAX_ITEMS_PER_PAGE: Final = 20


@dataclass(frozen=True)
class ImpactQuery:
    """Parameters for one ``view="impact"`` read."""

    files: tuple[str, ...] = ()
    folder: str | None = None
    compact: bool = False
    continuation: str | None = None
    budget_tokens: int = DEFAULT_IMPACT_BUDGET_TOKENS


@dataclass(frozen=True)
class _RuleUnit:
    record_id: str
    family_id: str | None
    overlap: int
    stale: bool
    payload: dict[str, Any] | str
    is_sibling: bool = False


@dataclass(frozen=True)
class _FamilyMeta:
    family_id: str
    title: str
    guarantee: str
    siblings: tuple[str, ...]
    overlap: int


@dataclass(frozen=True)
class _ImpactAssembly:
    live_invs: Mapping[str, Record]
    matched_inv_paths: Mapping[str, set[str]]
    families_by_inv: Mapping[str, list[str]]
    family_metas: Mapping[str, _FamilyMeta]
    standings: Mapping[str, RecordStanding]
    currentness: Currentness | None
    scope_set: set[str]
    compact: bool


@dataclass(frozen=True)
class _PageContext:
    scope_paths: tuple[str, ...]
    unmatched: tuple[str, ...]
    folder: str | None
    compact: bool
    start: int
    digest: str
    units: Sequence[_RuleUnit]
    family_metas: Mapping[str, _FamilyMeta]
    weaknesses: Sequence[dict[str, Any]]
    dependents: Sequence[dict[str, Any]]
    code_pairing: dict[str, Any]


def impact_view(selection: ReaderSelection, query: ImpactQuery) -> dict[str, Any]:
    """Assemble a deduplicated, family-grouped impact answer over ``query.files``/``folder``."""

    if not query.files and (query.folder is None or not query.folder.strip()):
        return {
            "refusalCode": "invalid-input",
            "refusalDetail": "view='impact' requires `files` (list of paths) and/or `folder`",
        }

    index = selection.index
    scope_paths, unmatched = resolve_indexed_paths(index, query.files, folder=query.folder)
    scope_set = set(scope_paths)
    digest = scope_digest(
        {
            "tree": index.state.key,
            "files": list(scope_paths),
            "folder": None if query.folder is None else normalize_repo_path(query.folder),
            "compact": query.compact,
        }
    )
    start = decode_query_cursor(query.continuation, view="impact", digest=digest)
    if isinstance(start, str):
        return {"refusalCode": "bad_continuation", "refusalDetail": start}

    matched_inv_paths = _invariants_for_paths(index, scope_paths)
    live_invs = _load_live_invariants(index, list(matched_inv_paths))
    inv_ids = sorted(live_invs)
    families_by_inv, family_members = _family_memberships(index, inv_ids)
    currentness, currentness_problem = (
        states_at(selection, inv_ids, sorted(family_members)) if inv_ids else (None, None)
    )
    standings = index.standings(inv_ids).value if inv_ids else {}
    family_metas = _build_family_metas(
        index, family_members, set(inv_ids), matched_inv_paths, scope_set
    )
    units = _build_rule_units(
        index,
        _ImpactAssembly(
            live_invs=live_invs,
            matched_inv_paths=matched_inv_paths,
            families_by_inv=families_by_inv,
            family_metas=family_metas,
            standings=standings,
            currentness=currentness,
            scope_set=scope_set,
            compact=query.compact,
        ),
    )

    if start > 0 and start >= len(units):
        return {
            "refusalCode": "bad_continuation",
            "refusalDetail": f"continuation offset {start} is past the {len(units)} matched rules",
        }

    page_ctx = _PageContext(
        scope_paths=scope_paths,
        unmatched=unmatched,
        folder=query.folder,
        compact=query.compact,
        start=start,
        digest=digest,
        units=units,
        family_metas=family_metas,
        weaknesses=_scope_weaknesses(selection, scope_set, query.folder),
        dependents=_scope_dependents(selection, scope_paths, set(inv_ids)),
        code_pairing=_code_pairing_block(selection, currentness_problem),
    )
    return _paginate_impact(page_ctx, budget_tokens=query.budget_tokens)


def _paginate_impact(ctx: _PageContext, *, budget_tokens: int) -> dict[str, Any]:
    def render_page(
        page_units: Sequence[_RuleUnit], has_more: bool, *, total_pages: int | None = None
    ) -> dict[str, Any]:
        return _render_impact_page(ctx, page_units, has_more, total_pages=total_pages)

    chosen, _ = fit_slice_in_budget(
        ctx.units,
        ctx.start,
        render_page,
        budget_tokens=budget_tokens,
        max_items=_MAX_ITEMS_PER_PAGE,
    )
    has_more = ctx.start + len(chosen) < len(ctx.units)
    total_pages = (
        estimate_total_pages(
            ctx.units, render_page, budget_tokens=budget_tokens, max_items=_MAX_ITEMS_PER_PAGE
        )
        if has_more or ctx.start > 0
        else 1
    )
    return _render_impact_page(ctx, chosen, has_more, total_pages=total_pages)


def _render_impact_page(
    ctx: _PageContext,
    page_units: Sequence[_RuleUnit],
    has_more: bool,
    *,
    total_pages: int | None = None,
) -> dict[str, Any]:
    grouped_families, ungrouped = _assemble_page_sections(page_units, ctx.family_metas, ctx.compact)
    rules_total = sum(1 for u in ctx.units if not u.is_sibling)
    rules_returned = sum(1 for u in page_units if not u.is_sibling)
    tail = ctx.units[ctx.start + len(page_units) :]
    rules_remaining = sum(1 for u in tail if not u.is_sibling)
    siblings_total = len(ctx.units) - rules_total
    counts: dict[str, Any] = {
        "rulesTotal": rules_total,
        "rulesReturned": rules_returned,
        "rulesRemaining": rules_remaining,
        "families": len(ctx.family_metas),
        "weaknesses": len(ctx.weaknesses),
        "dependents": len(ctx.dependents),
    }
    if siblings_total > 0:
        counts["siblingsTotal"] = siblings_total
        counts["siblingsReturned"] = len(page_units) - rules_returned
        counts["siblingsRemaining"] = len(tail) - rules_remaining
    body: dict[str, Any] = {
        "scope": {
            "files": list(ctx.scope_paths),
            **({"folder": normalize_repo_path(ctx.folder)} if ctx.folder else {}),
            **({"unmatchedFiles": list(ctx.unmatched)} if ctx.unmatched else {}),
        },
        "compact": ctx.compact,
        "families": grouped_families,
        "ungrouped": ungrouped,
        "counts": counts,
        "codePairing": ctx.code_pairing,
        "enumerationComplete": not has_more,
    }
    if ctx.start == 0:
        if ctx.weaknesses:
            body["weaknesses"] = list(ctx.weaknesses)
        if ctx.dependents:
            body["dependents"] = list(ctx.dependents)
    if total_pages is not None and total_pages > 1:
        body["counts"]["totalPages"] = total_pages
        if not ctx.compact:
            body["hint"] = (
                f"{rules_total} rules, {total_pages} pages; compact=true gives one line per rule"
            )
    if has_more:
        body["continuation"] = encode_query_cursor(
            "impact", ctx.start + len(page_units), ctx.digest
        )
    return body


def _invariants_for_paths(index: KnowledgeIndex, paths: Sequence[str]) -> dict[str, set[str]]:
    by_inv: dict[str, set[str]] = defaultdict(set)
    for path in paths:
        entries = index.entries_at_path(path).value
        for entry in (*entries.realizations, *entries.proofs):
            by_inv[entry.invariant].add(normalize_repo_path(entry.path))
    return by_inv


def _load_live_invariants(index: KnowledgeIndex, invariant_ids: Sequence[str]) -> dict[str, Record]:
    live: dict[str, Record] = {}
    for inv_id in invariant_ids:
        rec = index.record(inv_id).value
        if rec is None or rec.kind != "invariant" or rec.status == "retired":
            continue
        live[inv_id] = rec
    return live


def _family_memberships(
    index: KnowledgeIndex, invariant_ids: Sequence[str]
) -> tuple[dict[str, list[str]], dict[str, tuple[str, ...]]]:
    families_by_inv: dict[str, list[str]] = defaultdict(list)
    family_members: dict[str, tuple[str, ...]] = {}
    for inv_id in invariant_ids:
        inv_knowledge = index.invariant(inv_id).value
        for fam_id in sorted(inv_knowledge.families):
            fam_rec = index.record(fam_id).value
            if fam_rec is None or fam_rec.kind != "family" or fam_rec.status == "retired":
                continue
            families_by_inv[inv_id].append(fam_id)
            if fam_id not in family_members:
                family_members[fam_id] = index.family(fam_id).value.members
    return families_by_inv, family_members


def _build_family_metas(
    index: KnowledgeIndex,
    family_members: Mapping[str, tuple[str, ...]],
    in_scope_invs: set[str],
    matched_inv_paths: Mapping[str, set[str]],
    scope_set: set[str],
) -> dict[str, _FamilyMeta]:
    metas: dict[str, _FamilyMeta] = {}
    for fam_id, members in family_members.items():
        fam_rec = index.record(fam_id).value
        if fam_rec is None:
            continue
        title = str(fam_rec.document.get("title") or "").strip() or fam_id
        guarantee = str(fam_rec.document.get("guarantee") or "").strip()
        covered_files: set[str] = set()
        siblings: list[str] = []
        for member_id in sorted(members):
            if member_id in in_scope_invs:
                covered_files.update(matched_inv_paths.get(member_id, set()) & scope_set)
                continue
            sib_rec = index.record(member_id).value
            if sib_rec is None or sib_rec.kind != "invariant" or sib_rec.status == "retired":
                continue
            siblings.append(_format_sibling_line(index, sib_rec))
        metas[fam_id] = _FamilyMeta(
            family_id=fam_id,
            title=title,
            guarantee=guarantee,
            siblings=tuple(siblings),
            overlap=len(covered_files),
        )
    return metas


def _format_sibling_line(index: KnowledgeIndex, record: Record) -> str:
    title = rule_title(record.document).text
    knowledge = index.invariant(record.id).value
    paths = sorted({normalize_repo_path(e.path) for e in knowledge.realizations})
    shown = paths[:_SIBLING_FILE_CAP]
    extra = len(paths) - len(shown)
    file_text = ", ".join(shown) + (f" (+{extra})" if extra > 0 else "")
    return f"{record.id}: {title} [{file_text}]" if file_text else f"{record.id}: {title}"


def _build_rule_units(index: KnowledgeIndex, assembly: _ImpactAssembly) -> list[_RuleUnit]:
    primary_family = _assign_primary_families(assembly)
    grouped_units: dict[str | None, list[_RuleUnit]] = defaultdict(list)
    for inv_id, rec in assembly.live_invs.items():
        fam_id = primary_family[inv_id]
        in_scope_files = sorted(assembly.matched_inv_paths.get(inv_id, set()) & assembly.scope_set)
        knowledge = index.invariant(inv_id).value
        all_realized = sorted({normalize_repo_path(e.path) for e in knowledge.realizations})
        elsewhere = [p for p in all_realized if p not in assembly.scope_set]
        anchors = anchor_counts(
            inv_id,
            assembly.currentness,
            fallback_total=len(knowledge.realizations) + len(knowledge.proofs),
        )
        other_families = [f for f in (assembly.families_by_inv.get(inv_id) or ()) if f != fam_id]
        label = standing_label(assembly.standings.get(inv_id), anchors)
        payload = _format_rule_payload(
            rec,
            label=label,
            in_scope_files=in_scope_files,
            elsewhere=elsewhere,
            extra=(other_families, assembly.compact),
        )
        grouped_units[fam_id].append(
            _RuleUnit(
                record_id=inv_id,
                family_id=fam_id,
                overlap=len(in_scope_files),
                stale=anchors["stale"] > 0,
                payload=payload,
            )
        )

    ordered: list[_RuleUnit] = []
    fam_order = sorted(
        (fid for fid in grouped_units if fid is not None),
        key=lambda fid: (-assembly.family_metas[fid].overlap, -len(grouped_units[fid]), fid),
    )
    for fid in fam_order:
        units = grouped_units[fid]
        units.sort(key=lambda u: (-u.overlap, -int(u.stale), u.record_id))
        ordered.extend(units)
    ungrouped = grouped_units.get(None, [])
    ungrouped.sort(key=lambda u: (-u.overlap, -int(u.stale), u.record_id))
    ordered.extend(ungrouped)
    if not assembly.compact:
        for fid in fam_order:
            for line in assembly.family_metas[fid].siblings:
                ordered.append(
                    _RuleUnit(
                        record_id=line.split(":", 1)[0],
                        family_id=fid,
                        overlap=0,
                        stale=False,
                        payload=line,
                        is_sibling=True,
                    )
                )
    return ordered


def _assign_primary_families(assembly: _ImpactAssembly) -> dict[str, str | None]:
    primary: dict[str, str | None] = {}
    for inv_id in assembly.live_invs:
        fams = assembly.families_by_inv.get(inv_id) or []
        if not fams:
            primary[inv_id] = None
        else:
            primary[inv_id] = min(fams, key=lambda fid: (-assembly.family_metas[fid].overlap, fid))
    return primary


def _format_rule_payload(
    record: Record,
    *,
    label: str,
    in_scope_files: Sequence[str],
    elsewhere: Sequence[str],
    extra: tuple[Sequence[str], bool],
) -> dict[str, Any] | str:
    other_families, compact = extra
    if compact:
        title = rule_title(record.document).text
        files_text = ", ".join(in_scope_files[:_ALSO_AT_FILE_CAP])
        if len(in_scope_files) > _ALSO_AT_FILE_CAP:
            files_text += f" (+{len(in_scope_files) - _ALSO_AT_FILE_CAP})"
        return f"{record.id} [{label}] {title} [{files_text}]"

    statement = trim_statement(str(record.document.get("statement") or ""), record.id)
    row: dict[str, Any] = {
        "id": record.id,
        "standing": label,
        "statement": statement,
        "at": list(in_scope_files),
    }
    recorded_title = record.document.get("title")
    if isinstance(recorded_title, str) and recorded_title.strip():
        row["title"] = " ".join(recorded_title.split())
    if elsewhere:
        shown = list(elsewhere[:_ALSO_AT_FILE_CAP])
        omitted = len(elsewhere) - len(shown)
        row["alsoAt"] = shown if omitted == 0 else [*shown, f"+{omitted} more"]
    if other_families:
        row["otherFamilies"] = list(other_families)
    return row


def _assemble_page_sections(
    page_units: Sequence[_RuleUnit],
    family_metas: Mapping[str, _FamilyMeta],
    compact: bool,
) -> tuple[list[dict[str, Any]], list[Any]]:
    fam_rules: dict[str, list[Any]] = {}
    fam_siblings: dict[str, list[str]] = {}
    fam_order: list[str] = []
    ungrouped: list[Any] = []
    for unit in page_units:
        if unit.family_id is None:
            ungrouped.append(unit.payload)
            continue
        if unit.family_id not in fam_rules:
            fam_rules[unit.family_id] = []
            fam_siblings[unit.family_id] = []
            fam_order.append(unit.family_id)
        if unit.is_sibling:
            fam_siblings[unit.family_id].append(str(unit.payload))
        else:
            fam_rules[unit.family_id].append(unit.payload)

    families: list[dict[str, Any]] = []
    for fam_id in fam_order:
        meta = family_metas[fam_id]
        entry: dict[str, Any] = {"id": fam_id, "title": meta.title, "rules": fam_rules[fam_id]}
        if not compact:
            entry["guarantee"] = meta.guarantee
            if fam_siblings[fam_id]:
                entry["familySiblings"] = fam_siblings[fam_id]
        families.append(entry)
    return families, ungrouped


def _scope_weaknesses(
    selection: ReaderSelection, scope_set: set[str], folder: str | None
) -> list[dict[str, Any]]:
    norm_folder = None if folder is None else normalize_repo_path(folder)
    rows = weakness_list(selection, folder=norm_folder, include_closed=False, observe=True)
    matched: list[dict[str, Any]] = []
    for row in rows:
        path = normalize_repo_path(str(row.get("file") or ""))
        if path in scope_set or (
            norm_folder is not None and (path == norm_folder or path.startswith(f"{norm_folder}/"))
        ):
            anchor_state = row.get("anchorState")
            state_word = (
                anchor_state.get("state") if isinstance(anchor_state, Mapping) else "unverifiable"
            )
            matched.append(
                {
                    "id": row["id"],
                    "file": path,
                    "kind": row["kind"],
                    "anchorState": state_word,
                    "statement": trim_statement(str(row.get("statement") or ""), str(row["id"])),
                }
            )
    return matched


def _scope_dependents(
    selection: ReaderSelection, scope_paths: Sequence[str], matched_invs: set[str]
) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    if scope_paths:
        for row in dependent_list(selection, code_path=scope_paths):
            by_id[str(row["id"])] = row
    if matched_invs:
        for row in dependent_list(selection):
            rid = str(row["id"])
            if rid in by_id:
                continue
            rec = selection.index.record(rid).value
            if rec is None:
                continue
            stands_on = rec.document.get("stands_on") or ()
            if any(
                isinstance(link, Mapping)
                and link.get("kind") == "record"
                and link.get("id") in matched_invs
                for link in stands_on
            ):
                by_id[rid] = row
    ordered = [
        by_id[k] for k in sorted(by_id, key=lambda key: (str(by_id[key].get("document", "")), key))
    ]
    return compact_dependent_rows(ordered)


def _code_pairing_block(selection: ReaderSelection, problem: str | None) -> dict[str, Any]:
    if selection.code_tree is None:
        return {"paired": False, "reason": problem or "no code tree was resolved"}
    return {
        "paired": True,
        "codeTreeId": selection.code_tree.tree[:12],
        "repositoryRoot": selection.code_tree.repository.as_posix(),
    }


__all__ = ["DEFAULT_IMPACT_BUDGET_TOKENS", "ImpactQuery", "impact_view"]
