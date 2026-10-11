// Explicit synthetic index adapter for immutable pre-R86 marker captures. It carries their
// recorded change facts into the new tree, without interpreting a missing roster as removal.
import type { ReviewResult } from '../../data/review';
import { memberChanges } from './changeTriage';
import { syntheticCatalogue } from './outline.test-utils';
export function syntheticMarkerCatalogue(result: ReviewResult) {
  const catalogue = syntheticCatalogue([result]);
  for (const entry of result.payload!.family_context!.entries) {
    const family = catalogue.outline.families.find((one) => one.selector_id === entry.family_id)!;
    if (entry.change_kinds?.guarantee === 'unknown') family.change = 'unknown';
    const changes = memberChanges(entry);
    for (const member of [...entry.before.members, ...entry.after.members]) {
      const row = catalogue.outline.invariants.find(
        (one) => one.selector_id === member.invariant_id,
      );
      const primary = changes.get(member.member_id)?.primary;
      if (row && primary === 'implementation') row.change = 'unchanged';
      if (row && primary === 'unknown') row.change = 'unknown';
    }
  }
  return { state: 'entries', total_subjects: catalogue.entries.length, ...catalogue };
}

// Synthetic family question for reaching its existing card: use the exact family selection already
// carried by the captured invariant answer. The marker's target answer remains the untouched capture.
export function syntheticMarkerFamilyReview(result: ReviewResult): ReviewResult {
  const family = structuredClone(result);
  family.payload!.knowledge.revision_selection =
    family.payload!.family_context!.entries[0].selection;
  return family;
}
