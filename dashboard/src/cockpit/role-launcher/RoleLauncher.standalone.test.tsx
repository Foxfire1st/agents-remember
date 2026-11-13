// A manager or a task crew starts on a master that no sprint commands. The free launcher's
// Sprint field keeps its empty choice as a real one, called "No sprint", and the Master field
// then lists the masters the served tree places outside every sprint.
import { cleanup, fireEvent, render } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { taskReferenceKey } from '../../data/taskReference';
import { taskEntry, taskSummary } from '../../test/fixtures/wire';
import { documentChatBinding } from '../document-chat/model';
import {
  type LauncherRole,
  type RoleAgentChoice,
  type RoleLaunchSelection,
} from '../roleLaunchModel';
import { RoleLauncher } from './RoleLauncher';

const sprintRef = { repository: 'repo', path: 'sprint/task.json' };
const masterRef = { repository: 'repo', path: 'master/task.json' };
const leafRef = { repository: 'repo', path: 'master/leaf.json' };
const soloRef = { repository: 'repo', path: 'solo/task.json' };
const soloLeafRef = { repository: 'repo', path: 'solo/leaf.json' };

const sprint = taskEntry({
  reference: sprintRef,
  id: 'SPRINT',
  title: 'Sprint',
  summary: taskSummary({ kind: 'master', form: 'sprint', orchestrates: ['master'] }),
  parent: undefined,
  children: [taskReferenceKey(masterRef)],
  worktrees: [],
});
const master = taskEntry({
  reference: masterRef,
  id: 'MASTER',
  title: 'Master',
  summary: taskSummary({ kind: 'master', form: 'master', orchestrates: [] }),
  parent: taskReferenceKey(sprintRef),
  children: [taskReferenceKey(leafRef)],
  worktrees: [],
});
const leaf = taskEntry({
  reference: leafRef,
  id: 'LEAF',
  title: 'Leaf',
  summary: taskSummary({ kind: 'subTask', form: 'task', orchestrates: [] }),
  parent: taskReferenceKey(masterRef),
  children: [],
  worktrees: [],
});
const solo = taskEntry({
  reference: soloRef,
  id: 'SOLO',
  title: 'Solo master',
  summary: taskSummary({ kind: 'master', form: 'master', orchestrates: [] }),
  parent: undefined,
  children: [taskReferenceKey(soloLeafRef)],
  worktrees: [],
});
const soloLeaf = taskEntry({
  reference: soloLeafRef,
  id: 'SOLO-LEAF',
  title: 'Solo leaf',
  summary: taskSummary({ kind: 'subTask', form: 'task', orchestrates: [] }),
  parent: taskReferenceKey(soloRef),
  children: [],
  worktrees: [],
});
const TASKS = [sprint, master, leaf, solo, soloLeaf];
const AGENTS: RoleAgentChoice[] = [{ id: 'codex', label: 'Codex', models: [] }];

function Rig({
  initial,
  onChange,
}: {
  initial: RoleLaunchSelection;
  onChange: (selection: RoleLaunchSelection) => void;
}) {
  const [selection, setSelection] = useState(initial);
  const noop = () => {};
  return (
    <RoleLauncher
      tasks={TASKS}
      roleDefaults={{ agent: 'codex', available: true }}
      agents={AGENTS}
      selection={selection}
      optionsReady
      optionsLoading={false}
      busy={false}
      optionsError={null}
      executionError={null}
      onSelectionChange={(next) => {
        onChange(next);
        setSelection(next);
      }}
      onLaunch={async () => {}}
      onRevive={async () => {}}
      onRetry={async () => {}}
      onRefreshOptions={noop}
      onRefreshCatalog={noop}
      onRefreshResult={noop}
    />
  );
}

function optionTexts(select: HTMLSelectElement): string[] {
  return [...select.options].map((option) => option.textContent ?? '');
}

function renderRig(role: LauncherRole, refs: Partial<RoleLaunchSelection> = {}) {
  const onChange = vi.fn();
  const view = render(<Rig initial={{ role, ...refs }} onChange={onChange} />);
  const lastSelection = () => onChange.mock.calls.at(-1)?.[0] as RoleLaunchSelection;
  return { view, onChange, lastSelection };
}

afterEach(cleanup);

describe('the launcher on a master that no sprint commands', () => {
  it('keeps "No sprint" as a real choice and lists those masters without a sprint', () => {
    const { view, lastSelection } = renderRig('manager');
    const sprintSelect = view.getByLabelText('AR sprint') as HTMLSelectElement;
    expect(optionTexts(sprintSelect)[0]).toBe('No sprint');
    const masterSelect = view.getByLabelText('AR master') as HTMLSelectElement;
    expect(optionTexts(masterSelect)).toEqual(['Select master…', 'Solo master']);
    expect((view.getByRole('button', { name: 'Start role' }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    fireEvent.change(masterSelect, { target: { value: '0' } });
    expect(lastSelection()).toMatchObject({ role: 'manager', masterDocumentRef: soloRef });
    expect(lastSelection().sprintDocumentRef).toBeUndefined();
    expect((view.getByRole('button', { name: 'Start role' }) as HTMLButtonElement).disabled).toBe(
      false,
    );
  });

  it('lists a sprint-commanded master only under its own sprint, and its task under it', () => {
    const { view, lastSelection } = renderRig('worker');
    const sprintSelect = view.getByLabelText('AR sprint') as HTMLSelectElement;
    expect(optionTexts(view.getByLabelText('AR master') as HTMLSelectElement)).toEqual([
      'Select master…',
      'Solo master',
    ]);
    expect((view.getByRole('button', { name: 'Start role' }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    fireEvent.change(sprintSelect, { target: { value: '0' } });
    expect(lastSelection()).toMatchObject({ role: 'worker', sprintDocumentRef: sprintRef });
    const masterSelect = view.getByLabelText('AR master') as HTMLSelectElement;
    expect(optionTexts(masterSelect)).toEqual(['Select master…', 'Master']);
    fireEvent.change(masterSelect, { target: { value: '0' } });
    expect(lastSelection()).toMatchObject({
      role: 'worker',
      sprintDocumentRef: sprintRef,
      masterDocumentRef: masterRef,
    });
    const taskSelect = view.getByLabelText('AR task') as HTMLSelectElement;
    expect(optionTexts(taskSelect)).toEqual(['Select task…', 'Leaf']);
    expect((view.getByRole('button', { name: 'Start role' }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    fireEvent.change(taskSelect, { target: { value: '0' } });
    expect(lastSelection()).toMatchObject({
      role: 'worker',
      sprintDocumentRef: sprintRef,
      masterDocumentRef: masterRef,
      taskDocumentRef: leafRef,
    });
    expect((view.getByRole('button', { name: 'Start role' }) as HTMLButtonElement).disabled).toBe(
      false,
    );
  });

  it('gives a leaf role its task on a standalone master with no sprint in the selection', () => {
    const { view, lastSelection } = renderRig('curator');
    fireEvent.change(view.getByLabelText('AR master'), { target: { value: '0' } });
    const taskSelect = view.getByLabelText('AR task') as HTMLSelectElement;
    expect(optionTexts(taskSelect)).toEqual(['Select task…', 'Solo leaf']);
    fireEvent.change(taskSelect, { target: { value: '0' } });
    expect(lastSelection()).toMatchObject({
      role: 'curator',
      masterDocumentRef: soloRef,
      taskDocumentRef: soloLeafRef,
    });
    expect(lastSelection().sprintDocumentRef).toBeUndefined();
    expect((view.getByRole('button', { name: 'Start role' }) as HTMLButtonElement).disabled).toBe(
      false,
    );
  });

  it('still requires its sprint from the Orchestrator', () => {
    const { view } = renderRig('orchestrator');
    expect((view.getByRole('button', { name: 'Start role' }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    fireEvent.change(view.getByLabelText('AR sprint'), { target: { value: '0' } });
    expect((view.getByRole('button', { name: 'Start role' }) as HTMLButtonElement).disabled).toBe(
      false,
    );
  });

  it('binds a standalone master and its task to the same roles and refuses only a document with no start', () => {
    expect(documentChatBinding(soloRef, TASKS)).toEqual({
      selection: { role: 'manager', masterDocumentRef: soloRef },
      roles: ['manager'],
    });
    expect(documentChatBinding(soloLeafRef, TASKS)).toEqual({
      selection: {
        role: 'worker',
        masterDocumentRef: soloRef,
        taskDocumentRef: soloLeafRef,
      },
      roles: ['worker', 'reviewer', 'curator'],
    });
    expect(documentChatBinding(masterRef, TASKS)).toEqual({
      selection: { role: 'manager', sprintDocumentRef: sprintRef, masterDocumentRef: masterRef },
      roles: ['manager'],
    });
    const ghost = documentChatBinding({ repository: 'repo', path: 'ghost/task.json' }, TASKS);
    expect(ghost.roles).toEqual([]);
    expect(ghost.problem).toContain('No role can start here');
    expect(ghost.problem).not.toContain('sprint');
  });
});
