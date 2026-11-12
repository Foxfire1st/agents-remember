import { expect, it } from 'vitest';

import { COMMANDS } from './commands';
import * as chordTables from './keymap/chords';

it('registers every command named by a keymap chord', () => {
  const registered = new Set(COMMANDS.map((command) => command.id));
  const chords = Object.values(chordTables).flat();
  expect(chords.filter((binding) => !registered.has(binding.commandId))).toEqual([]);
});
