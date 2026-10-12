import { describe, expect, it } from 'vitest';

import type { AgentPickupNode, LifecycleProjection } from '../../types/projection';
import { masterIdsByFolder, progressFigure, rowHover, rowState, rowWord, shortTaskId } from './rowState';
import { lifecycle, taskDoc } from './test-utils';

const reported = (over: Partial<LifecycleProjection> = {}) => lifecycle({ id: 'LC', ...over });
// What the server assumes for every task with a worktree: a pause that no event ever reported.
const assumed = () => lifecycle({ id: 'LC', inferred: true, lastEventTs: '' });
const pickup = (state: string): AgentPickupNode => ({
  id: 'pickup:1',
  entryId: 'E1',
  lifecycleId: 'LC',
  messageKind: 'dispatch-brief',
  deliveryState: 'delivered',
  attemptCount: 0,
  state,
  ttlSeconds: 300,
});

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
    const facts = { status: 'planning', gate: '', ageSeconds: 1020 };
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
    expect(rowWord({ status: '', gate: '', ageSeconds: 60, lifecycle: reported({ inferred: true }) }).text).toBe(
      'quiet 1m',
    );
  });

  it('says the most pressing fact as the one word', () => {
    const running = reported({ state: 'running' });
    const chat = (state: 'needs-input' | 'working') => ({ state, label: state, detail: '' });
    const base = { status: 'inProgress', lifecycle: running, gate: '' };
    const words = [
      rowWord({ ...base, gate: 'plan-approval', pickup: pickup('check-chat') }),
      rowWord({ ...base, pickup: pickup('check-chat'), chatActivity: chat('needs-input') }),
      rowWord({ ...base, pickup: pickup('waiting-for-agent'), chatActivity: chat('needs-input') }),
      rowWord({ ...base, chatActivity: chat('needs-input') }),
      rowWord({ ...base, lifecycle: reported({ state: 'blocked' }), chatActivity: chat('working') }),
      rowWord({ ...base, lifecycle: reported({ state: 'awaiting-developer' }) }),
      rowWord({ ...base, chatActivity: chat('working') }),
      rowWord(base),
    ];
    expect(words.map((word) => word.text)).toEqual([
      'plan-approval',
      'check chat',
      'unacknowledged',
      'needs input',
      'blocked',
      'your turn',
      'working',
      'running',
    ]);
    // Everything that asks for the developer stands first under its parent. Only an overdue
    // hand-over offers its dismissal, and it does so also where an open gate is the word.
    expect(words.map((word) => word.band)).toEqual([0, 0, 0, 0, 0, 0, 1, 1]);
    expect(words.map((word) => word.dismissPickup)).toEqual([
      true,
      true,
      false,
      false,
      false,
      false,
      false,
      false,
    ]);
    // A gate alone, or with a hand-over that is not overdue yet, offers none.
    const gated = { ...base, gate: 'plan-approval' };
    expect(rowWord(gated).dismissPickup).toBe(false);
    expect(rowWord({ ...gated, pickup: pickup('waiting-for-agent') }).dismissPickup).toBe(false);
  });

  it('keeps in the hover what the line has no room for', () => {
    const hover = rowHover({
      heading: 'Text helpers',
      kind: 'master',
      repo: 'sandbox-app',
      status: 'inProgress',
      gate: '',
      progress: { done: 79, total: 138, abandoned: 1, discarded: 5 },
      progressNoun: 'Tasks completed',
      worktree: false,
      lifecycle: assumed(),
      chatActivity: { state: 'working', label: 'working', detail: 'curator: working; worker: idle' },
      pickup: pickup('check-chat'),
    }).split('\n');
    expect(progressFigure({ done: 79, total: 138, abandoned: 1 })).toBe('79/138');
    expect(hover).toEqual([
      'Text helpers',
      'State: check chat',
      'Kind: master',
      'Document status: inProgress',
      'Tasks completed: 79 of 138, 1 abandoned, 5 discarded',
      'Lifecycle: LC · paused · phase build · inferred from the contract: no event was seen',
      'Chat: working. curator: working; worker: idle',
      'Inbox: acknowledgment overdue; check chat',
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
