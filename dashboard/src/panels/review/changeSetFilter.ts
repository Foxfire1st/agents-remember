import type { SubjectNode, LocationNode, FoldedGroup, FilteredSubject, FilteredGroup, ChangeSetModel, TreeFilterResult } from './changeSetTypes';
const drawsItself = (node: LocationNode) => node.draws.length > 0;

// ---- the filter (W12: id, title, symbol, path, role, effect; the tree only) ----

const needleOf = (query: string) => query.trim().toLowerCase();

function subjectText(node: SubjectNode): string {
  return `${node.row.id} ${node.row.title} ${node.effects.join(' ')}`.toLowerCase();
}

function locationText(node: LocationNode): string {
  return `${node.path} ${node.symbol ?? ''} ${node.role} ${node.roleLabel}`.toLowerCase();
}

const FOLDED: Readonly<Record<FoldedGroup, (node: SubjectNode) => LocationNode[]>> = {
  unchanged: (node) => node.unchanged,
  undetermined: (node) => node.notComparable,
};

// A subject's rows: one per drawn location, and each group row whose count is above 0 (W13).
function unfiltered(node: SubjectNode): FilteredSubject {
  return {
    node,
    locations: node.changed.filter(drawsItself),
    groups: (['unchanged', 'undetermined'] as const).filter((group) => (group === 'unchanged' ? node.counts?.unchanged ?? node.unchanged.length : node.counts?.not_comparable ?? node.notComparable.length) > 0),
  };
}

// A subject under the filter: its rows without the group rows when its id, title or effect matches;
// else the rows of the locations that match: a changed one by its row (a row matches when an entry it
// draws does), an unchanged or not comparable one by its group's row, which opens that group in the
// reading column, so a folded location stays reachable without a disclosure in the tree (MIK-R89 v2
// rules 3 and 5; MIK-R86 v2 rules 4 and 9).
function filteredSubject(node: SubjectNode, needle: string): FilteredSubject | undefined {
  const rows = unfiltered(node);
  if (subjectText(node).includes(needle)) return { ...rows, groups: [] };
  const matches = (location: LocationNode) => locationText(location).includes(needle);
  const found = new Set(node.changed.filter(matches).map((location) => location.key));
  const locations = rows.locations.filter((location) => location.draws.some((key) => found.has(key)));
  const groups = rows.groups.filter((group) => FOLDED[group](node).some(matches));
  return locations.length || groups.length ? { node, locations, groups } : undefined;
}

export function filterTree(model: ChangeSetModel, query: string): TreeFilterResult {
  const needle = needleOf(query);
  if (needle === '')
    return {
      query,
      groups: model.groups.map((group) => ({ group, subjects: group.subjects.map(unfiltered) })),
      empty: false,
    };
  const groups = model.groups.flatMap((group): FilteredGroup[] => {
    const family = group.family?.row;
    // A family that matches is shown with its members of the view (MIK-R86 v1 rule 9).
    if (family && `${family.id} ${family.title}`.toLowerCase().includes(needle))
      return [{ group, subjects: group.subjects.map((node) => ({ ...unfiltered(node), groups: [] })) }];
    const subjects = group.subjects.flatMap((node) => filteredSubject(node, needle) ?? []);
    return subjects.length ? [{ group, subjects }] : [];
  });
  return { query, groups, empty: groups.length === 0 };
}

// A lane file row matches on its path and on "code without invariant" (mockup app.js:227).
export function laneFileMatches(path: string, query: string): boolean {
  const needle = needleOf(query);
  return needle === '' || `${path} code without invariant`.toLowerCase().includes(needle);
}
