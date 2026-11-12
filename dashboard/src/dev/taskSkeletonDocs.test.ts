import { expect,it } from 'vitest';

import { richApiAnswer,richTaskEntries } from './taskSkeletonDocs';

it('uses one entry per declared child and answers bodies by the qualified reference', () => {
  const entries = richTaskEntries();
  const byKey = new Map(entries.map((entry) => [entry.key, entry]));
  expect(byKey.size).toBe(entries.length);
  for (const entry of entries) {
    expect(entry.counts.completed + entry.counts.aborted + entry.counts.started + entry.counts.planned)
      .toBe(entry.children.length);
    for (const child of entry.children) {
      expect(byKey.get(child)?.parent).toEqual(entry.key);
    }
    if (entry.summary) {
      const url = `/api/task-document?path=${encodeURIComponent(entry.key)}`;
      expect(richApiAnswer(url, undefined)).toEqual(entry.summary);
    }
  }
  expect(richTaskEntries().map((entry) => entry.counts)).toEqual(entries.map((entry) => entry.counts));
  const leaf = entries.find((entry) => entry.id === 'SBX-M1-L1');
  expect(leaf?.stepsTotal).toBe(6);
  expect(leaf?.stepsDone).toBe(3);
  expect(entries.find((entry) => entry.id === 'SBX-M1-L7')?.summary?.completedAt)
    .toBe('2026-10-07T12:00:00Z');
});
