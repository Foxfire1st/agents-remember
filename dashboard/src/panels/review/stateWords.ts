// The words a reader sees for the review answer's state tokens (finding 2: nothing raw shows). Each
// token stays in the page's data attributes; the text says what it means.
import type { ReviewFamilyContextState } from '../../data/reviewFamily';

// The family context's state, as the slot's family lines name it.
const FAMILY_STATE_WORDS: Record<ReviewFamilyContextState, string> = {
  recorded: 'Family context recorded',
  partial: 'Family context partly read',
  no_family_recorded: 'No recorded family',
  no_subject_selected: 'No subject selected',
  unavailable: 'Family context unavailable',
};

export function familyStateWords(state: ReviewFamilyContextState): string {
  return FAMILY_STATE_WORDS[state] ?? tokenWords(state);
}

// The source pane's remaining counts (`ReviewRemainingCount.name`), by what each one counts.
const REMAINING_WORDS: Record<string, string> = {
  locations_remaining: 'comparison items not on this page',
  changed_paths_outside_selection: 'changed files no link of this subject reaches',
  records_present_outside_selection: 'changed records outside this subject',
  references_unresolved: 'unresolved references',
};

export function remainingWords(name: string): string {
  return REMAINING_WORDS[name] ?? tokenWords(name);
}

// Any other token (a field name, a member or roster state): its words.
export function tokenWords(token: string): string {
  return token.replaceAll('_', ' ');
}
