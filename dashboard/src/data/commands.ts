export interface Command {
  id: string;
  label: string;
}

// Command identities and their user-facing names. Bindings belong to the keymap.
export const COMMANDS: readonly Command[] = [
  { id: 'review.nextChange', label: 'Next change' },
  { id: 'review.previousChange', label: 'Previous change' },
];
