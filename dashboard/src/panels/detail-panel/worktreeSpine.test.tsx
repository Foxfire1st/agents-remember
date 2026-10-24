// The Tasks view's worktree spine after the search providers left the projection (MIK-R199 rule 1).
//
// The spine used to carry two engine lanes — `code → CGC` and `memory → GrepAI` — fed by a
// provider lookup keyed on the enclosure's worktree group, plus a "no isolated engine recorded"
// placeholder for the (common) enclosure that had none. All of it is gone: the lanes are the two
// checkouts, and the facts beside them are the enclosure's own. These assertions are the
// failing-before half of that removal — they name the lane labels that must never come back and
// the checkout/task facts that must survive it.
import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { DetailPanel } from './DetailPanel';
import { WorktreeSpine } from './worktreeSpine';
import { enclosure, seedTaskEntries, taskEntry, taskSummary } from './test-utils';

const OWNER = enclosure({
  enclosure: '/tasks/legacy/enclosures/legacy/series-contract.md',
  enclosureId: '/tasks/legacy/enclosures/legacy/series-contract.md',
  repoName: 'agents-remember',
  taskName: 'legacy',
  worktreeGroup: '/worktrees/agents-remember/legacy-ar',
  cleanup: 'pending',
  codeWorktreeExists: true,
  memoryWorktreeExists: false,
});

// The engine vocabulary the lanes used to print. None of it may reach the surface again.
const RETIRED_LANE_WORDS = ['CGC', 'GrepAI', 'codegraphcontext', 'grepai', 'no isolated engine'];

// Wording that would assert a creation history the projection cannot support. `exists === false`
// only says the path is not there now; a checkout that was cleaned up answers it identically.
const UNSUPPORTED_HISTORY_WORDS = ['not created', 'never created', 'never existed', 'was created'];

const cleanupMeta = (view: ReturnType<typeof render>, kind: 'code' | 'memory'): string =>
  view.getByTestId(`spine-lane-${kind}`).textContent ?? '';

describe('the worktree spine carries checkouts, not engines', () => {
  it('names both checkout paths with their existence and cleanup facts', () => {
    const view = render(
      <WorktreeSpine enclosure={OWNER} groupName={OWNER.worktreeGroup} />,
    );
    const spine = view.getByTestId('worktree-spine');
    expect(spine.textContent).toContain(`worktree · ${OWNER.worktreeGroup}`);
    const code = view.getByTestId('spine-lane-code');
    const memory = view.getByTestId('spine-lane-memory');
    expect(code.textContent).toContain('code checkout');
    expect(code.textContent).toContain('agents-remember');
    expect(code.textContent).toContain('worktree present · pending cleanup');
    expect(memory.textContent).toContain('memory checkout');
    expect(memory.textContent).toContain('ar-agents-remember');
    expect(memory.textContent).toContain('worktree absent · pending cleanup');
    for (const word of RETIRED_LANE_WORDS) {
      expect(spine.textContent, word).not.toContain(word);
    }
  });

  // The projection cannot distinguish these two histories: both answer false for the existence
  // boolean, and `EnclosureNode` carries no creation/cleanup-history field to separate them. So
  // both must render the same current-absence label — the label may not narrate how the path got
  // empty, and the facts around it (path, repo, cleanup state) still render.
  const ABSENCE_CASES = [
    { name: 'a checkout absent before anything was created', cleanup: 'pending' },
    { name: 'a checkout absent after a completed cleanup', cleanup: 'complete' },
  ] as const;

  it.each(ABSENCE_CASES)('reports only current absence for $name', ({ cleanup }) => {
    const absent = enclosure({
      enclosure: '/tasks/legacy/enclosures/legacy/series-contract.md',
      enclosureId: '/tasks/legacy/enclosures/legacy/series-contract.md',
      repoName: 'agents-remember',
      taskName: 'legacy',
      worktreeGroup: '/worktrees/agents-remember/legacy-ar',
      cleanup,
      codeWorktreeExists: false,
      memoryWorktreeExists: false,
    });
    const view = render(<WorktreeSpine enclosure={absent} groupName={absent.worktreeGroup} />);
    for (const kind of ['code', 'memory'] as const) {
      const meta = cleanupMeta(view, kind);
      expect(meta, kind).toContain(`worktree absent · ${cleanup} cleanup`);
      expect(meta, kind).not.toContain('worktree present');
      for (const word of UNSUPPORTED_HISTORY_WORDS) {
        expect(meta, `${kind}: ${word}`).not.toContain(word);
      }
    }
    // Path and repo facts survive the absence change on both lanes.
    expect(cleanupMeta(view, 'code')).toContain('code checkout');
    expect(cleanupMeta(view, 'code')).toContain('agents-remember');
    expect(cleanupMeta(view, 'memory')).toContain('memory checkout');
    expect(cleanupMeta(view, 'memory')).toContain('ar-agents-remember');
    expect(view.getByTestId('worktree-spine').textContent).toContain(
      `worktree · ${absent.worktreeGroup}`,
    );
  });

  it('keeps a present checkout labelled present with its facts intact', () => {
    const present = enclosure({
      enclosure: '/tasks/legacy/enclosures/legacy/series-contract.md',
      enclosureId: '/tasks/legacy/enclosures/legacy/series-contract.md',
      repoName: 'agents-remember',
      taskName: 'legacy',
      worktreeGroup: '/worktrees/agents-remember/legacy-ar',
      cleanup: 'complete',
      codeWorktreeExists: true,
      memoryWorktreeExists: true,
    });
    const view = render(<WorktreeSpine enclosure={present} groupName={present.worktreeGroup} />);
    expect(cleanupMeta(view, 'code')).toContain('worktree present · complete cleanup');
    expect(cleanupMeta(view, 'memory')).toContain('worktree present · complete cleanup');
    expect(cleanupMeta(view, 'code')).toContain('agents-remember');
    expect(cleanupMeta(view, 'memory')).toContain('ar-agents-remember');
    for (const word of UNSUPPORTED_HISTORY_WORDS) {
      expect(view.getByTestId('worktree-spine').textContent, word).not.toContain(word);
    }
  });

  it('keeps the task and enclosure facts on the Tasks view', () => {
    const leaf = taskEntry({
      reference: { repository: 'agents-remember', path: 'tasks/legacy/01_leaf.json' },
      id: 'LEGACY-1',
      title: 'Legacy leaf',
      stage: 'started',
      activity: 'paused',
      worktrees: [OWNER],
      summary: taskSummary({ type: '', objective: 'The leaf objective' }),
    });
    seedTaskEntries([leaf]);
    const view = render(<DetailPanel selectedId={`taskdoc:${leaf.key}`} />);
    const spine = view.getByTestId('worktree-spine');
    expect(spine.textContent).toContain(OWNER.worktreeGroup);
    expect(spine.textContent).toContain(OWNER.repoName);
    expect(view.container.textContent).toContain('Legacy leaf');
    for (const word of RETIRED_LANE_WORDS) {
      expect(view.container.textContent, word).not.toContain(word);
    }
  });
});
