// A held leaf seat is called held in the launcher's row, and Start stays disabled for it
// (MIK-R100 rule 5: the word only; no other dashboard element changes).
import { cleanup, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { RoleLauncher } from './RoleLauncher';
import type { RoleAgentChoice } from '../roleLaunchModel';

const AGENTS: RoleAgentChoice[] = [{ id: 'codex', label: 'Codex', models: [] }];

const REFS = {
  sprintDocumentRef: { repository: 'agents-remember', path: 'sprint/task.json' },
  masterDocumentRef: { repository: 'agents-remember', path: 'master/task.json' },
  taskDocumentRef: { repository: 'agents-remember', path: 'master/leaf.json' },
};

function renderWorker(status: string, canStart: boolean) {
  const noop = () => {};
  return render(
    <RoleLauncher
      tasks={[]}

      boundRoles={['worker', 'reviewer', 'curator']}
      roleDefaults={{ agent: 'codex', available: true }}
      agents={AGENTS}
      selection={{ role: 'worker', ...REFS }}
      currentScopedExecution={{ status, detail: 'fixture', canStart, canRevive: false }}
      optionsReady
      optionsLoading={false}
      busy={false}
      optionsError={null}
      executionError={null}
      onSelectionChange={noop}
      onLaunch={async () => {}}
      onRevive={async () => {}}
      onRetry={async () => {}}
      onRefreshOptions={noop}
      onRefreshCatalog={noop}
      onRefreshResult={noop}
    />,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('held seat word', () => {
  it('names a held seat held and keeps Start disabled', () => {
    const { getByLabelText, getByRole } = renderWorker('held', false);
    const role = getByLabelText('Role') as HTMLSelectElement;
    expect(role.selectedOptions[0]?.text).toBe('Worker (held)');
    expect(role.selectedOptions[0]?.text).not.toContain('running');
    expect((getByRole('button', { name: 'Start role' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('still names a running seat running', () => {
    const { getByLabelText } = renderWorker('running', false);
    expect((getByLabelText('Role') as HTMLSelectElement).selectedOptions[0]?.text).toBe(
      'Worker (running)',
    );
  });
});
