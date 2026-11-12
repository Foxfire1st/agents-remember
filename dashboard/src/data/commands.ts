export interface Command {
  id: string;
  label: string;
}

// Command identities and their user-facing names. Bindings belong to the keymap.
export const COMMANDS: readonly Command[] = [
  // `j`/`k` move by block (MIK-R89 v2 rule 11); the ids stay, so a stored rebinding still applies.
  { id: 'review.nextChange', label: 'Next block' },
  { id: 'review.previousChange', label: 'Previous block' },
];
