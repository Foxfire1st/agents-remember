import { describe, expect, it } from 'vitest';

import type { LifecycleProjection } from '../../types/projection';
import { masterIdsByFolder, progressFigure, rowHover, rowState, rowWord, shortTaskId } from './rowState';
import { lifecycle, taskDoc } from './test-utils';

const reported = (over: Partial<LifecycleProjection> = {}) => lifecycle({ id: 'LC', ...over });
// What the server assumes for every task with a worktree: a pause that no event ever reported.
const assumed = () => lifecycle({ id: 'LC', inferred: true, lastEventTs: '' });

describe('what a row of the tasks tree says', () => {
  it('lets the document speak where the lifecycle never reported', () => {
    // The task page's words and dot, so the column and the task page say one thing.
    const waiting = rowState({ status: 'planning', lifecycle: assumed() });
    expect(waiting).toMatchObject({ mark: { kind: 'status', tone: 'wait' }, word: 'waiting', band: 2 });
    expect(rowState({ status: 'inProgress', lifecycle: assumed() }).word).toBe('in work');
    expect(rowState({ status: 'Completed' })).toMatchObject({ word: 'completed', band: 3 });
    expect(rowState({ status: 'abandoned' }).mark).toEqual({ kind: 'status', tone: 'gone' });
  });

  it('lets a lifecycle that reported speak, with its own mark and its age', () => {
    const facts = { status: 'planning', ageSeconds: 1020 };
    expect(rowWord({ ...facts, lifecycle: reported({ state: 'running' }) })).toMatchObject({
      text: 'running 17m',
      tone: 'run',
      band: 1,
    });
    // A pause the server assumed after events went quiet is not a pause somebody set.
    expect(rowWord({ ...facts, lifecycle: reported({ inferred: true }) }).text).toBe('quiet 17m');
    expect(rowWord({ ...facts, lifecycle: reported() }).text).toBe('paused 17m');
    expect(rowState({ status: 'planning', lifecycle: reported({ state: 'paused' }) }).mark).toEqual({
      kind: 'state',
      variant: 'paused',
    });
    // A row without a document whose lifecycle never reported says "waiting", not "quiet":
    // "quiet" is for a lifecycle that reported once. Once it reports, it speaks.
    expect(rowState({ status: '', lifecycle: assumed() })).toMatchObject({
      mark: { kind: 'status', tone: 'wait' },
      word: 'waiting',
      band: 2,
    });
    expect(rowWord({ status: '', ageSeconds: 60, lifecycle: reported({ inferred: true }) }).text).toBe('quiet 1m');
  });

  it('says the task\'s own state as the one word, and what asks stands first', () => {
    const base = { status: 'inProgress' };
    const words = [
      rowWord({ ...base, lifecycle: reported({ state: 'blocked' }) }),
      rowWord({ ...base, lifecycle: reported({ state: 'awaiting-developer' }) }),
      rowWord({ ...base, lifecycle: reported({ state: 'running' }) }),
      rowWord(base),
    ];
    expect(words.map((word) => word.text)).toEqual(['blocked', 'your turn', 'running', 'in work']);
    expect(words.map((word) => word.tone)).toEqual(['fault', 'ask', 'run', 'rest']);
    // Everything that asks for the developer stands first under its parent.
    expect(words.map((word) => word.band)).toEqual([0, 0, 1, 1]);
  });

  it('keeps in the hover what the line has no room for', () => {
    const hover = rowHover({
      heading: 'Text helpers',
      kind: 'master',
      repo: 'sandbox-app',
      status: 'inProgress',
      progress: { done: 79, total: 138, abandoned: 1, discarded: 5 },
      progressNoun: 'Tasks completed',
      worktree: false,
      lifecycle: assumed(),
    }).split('\n');
    expect(progressFigure({ done: 79, total: 138, abandoned: 1 })).toBe('79/138');
    expect(hover).toEqual([
      'Text helpers',
      'State: in work',
      'Kind: master',
      'Document status: inProgress',
      'Tasks completed: 79 of 138, 1 abandoned, 5 discarded',
      'Lifecycle: LC · paused · phase build · inferred from the contract: no event was seen',
      'Repo: sandbox-app',
    ]);
  });

  it('shortens the id of a task inside its master', () => {
    const master = taskDoc({ id: '260928-MIK', kind: 'master', title: 'M', docPath: '/t/mik/task.json' });
    const leaf = taskDoc({ id: '260928-MIK-L144', title: 'L', docPath: '/t/mik/144_sidebar.json' });
    const foreign = taskDoc({ id: 'OTHER-7', title: 'F', docPath: '/t/mik/07_other.json' });
    const alone = taskDoc({ id: 'SOLO-1', title: 'S', docPath: '/t/solo/task.json' });
    const ids = masterIdsByFolder([master, leaf, foreign, alone]);
    expect(shortTaskId(leaf, ids)).toBe('L144');
    // An id that does not start with its master's id, and a task that is its own folder's
    // document, keep their whole id; a master shows none.
    expect(shortTaskId(foreign, ids)).toBe('OTHER-7');
    expect(shortTaskId(alone, ids)).toBe('SOLO-1');
    expect(shortTaskId(master, ids)).toBe('');
  });
});
