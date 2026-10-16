// MIK-R133 evidence 1 + 2: the role launcher is one tight row of controls in
// the look of the view tabs. Each test fails without the change: the row held
// a sentence, titled selectors, a browser-look feature control, and its own
// copy of the tabs' sizes.
import { fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ModeBar } from '../grammar/ModeBar';
import {
  launcherButton,
  launcherChip,
  launcherSelect,
  viewTabButton,
} from '../grammar/viewTabLook';
import { RoleChatsPane } from './RoleChats';
import { taskEntry, taskSummary } from '../test/fixtures/wire';
import modeBarSrc from '../grammar/ModeBar.tsx?raw';
import roleLauncherSrc from './role-launcher/RoleLauncher.tsx?raw';
import roleLaunchFeaturesSrc from './RoleLaunchFeatures.tsx?raw';

const AGENTS = [
  {
    id: 'codex',
    label: 'Codex',
    models: [
      {
        id: 'gpt-a',
        label: 'GPT A',
        efforts: [
          { id: 'low', label: 'Low' },
          { id: 'high', label: 'High' },
        ],
        defaultEffort: 'low',
        isDefault: true,
        features: [
          {
            id: 'service_tier',
            label: 'Speed',
            type: 'select',
            value: 'default',
            permissionBearing: false,
            options: [{ id: 'priority', label: 'Fast' }],
          },
          {
            id: 'plan_mode',
            label: 'Plan mode',
            type: 'toggle',
            value: false,
            permissionBearing: true,
          },
        ],
      },
      { id: 'gpt-b', label: 'GPT Bravo model with a very long host label', efforts: [] },
    ],
    modes: [
      {
        id: 'auto',
        label: 'Default Permissions',
        description: "The host's least permissive mode.",
      },
    ],
  },
  { id: 'eve', label: 'Eve', models: [] },
];

function roleDefaultsFixture(extra: Record<string, unknown> = {}) {
  return {
    agent: 'codex',
    model: 'gpt-a',
    effort: 'high',
    available: true,
    mode: 'auto',
    choices: {
      mode: { source: 'least permissive mode' },
      agent: { value: 'codex', source: 'our settings' },
      model: { value: 'gpt-a', source: 'our settings' },
      effort: { value: 'high', source: 'our settings' },
      featureValues: { service_tier: { value: 'default', source: 'host role' } },
      notes: { present: true, baselineReplaced: false, source: 'host role' },
    },
    ...extra,
  };
}

const SPRINT = taskEntry({
  id: 'SPRINT-1',
  title: 'Sprint one',
  reference: { repository: 'demo', path: 'sprint-one/task.json' },
  summary: taskSummary({ kind: 'master', form: 'sprint', orchestrates: ['some-master'] }),
});
type Reply = { status: number; body: unknown };

let optionsReplies: Reply[] = [];

function stubOptions(roleDefaults: Record<string, unknown>) {
  optionsReplies = [
    { status: 200, body: { roleDefaults, agents: AGENTS, catalogOrigin: 'r133', executions: [] } },
  ];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      if (String(url) !== '/api/role-launch/options') {
        return new Response(JSON.stringify({ available: false, frameUrl: null }), { status: 503 });
      }
      const reply = optionsReplies.length > 1 ? optionsReplies.shift() : optionsReplies[0];
      void init;
      return new Response(JSON.stringify(reply?.body), { status: reply?.status ?? 500 });
    }),
  );
}

function renderArchitectRow(tasks: ReturnType<typeof taskEntry>[] = []) {
  return render(<RoleChatsPane active tasks={tasks} />);
}

beforeEach(() => {
  sessionStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('the row holds controls only', () => {
  it('has no sentence of the old form and no visible title above a control', async () => {
    stubOptions(roleDefaultsFixture());
    const view = renderArchitectRow();
    await waitFor(() =>
      expect((view.getByRole('button', { name: 'Start role' }) as HTMLButtonElement).disabled).toBe(
        false,
      ),
    );
    const launcher = view.getByTestId('role-launcher');
    expect(view.queryByTestId('role-default-sources')).toBeNull();
    for (const sentence of [
      'Mode:',
      'Role defaults:',
      'Features:',
      'User instructions (',
      'Role default (',
    ]) {
      expect(launcher.textContent).not.toContain(sentence);
    }
    for (const title of ['Agent override', 'Model override', 'Effort override']) {
      expect(launcher.textContent).not.toContain(title);
    }
    // Each label wraps exactly its selector: nothing but the control.
    for (const label of Array.from(launcher.querySelectorAll('label'))) {
      expect(Array.from(label.childNodes).map((node) => node.nodeName)).toEqual(['SELECT']);
    }
    // Every selector keeps its accessible name and carries a hover text.
    for (const name of [
      'Role',
      'Role agent override',
      'Role model override',
      'Role effort override',
      'Speed override',
    ]) {
      const select = view.getByLabelText(name) as HTMLSelectElement;
      expect(launcher.contains(select)).toBe(true);
      expect(select.getAttribute('title')).toBeTruthy();
    }
  });

  it('keeps an empty document selector on what it selects', async () => {
    stubOptions(roleDefaultsFixture());
    const view = renderArchitectRow([SPRINT]);
    await waitFor(() =>
      expect((view.getByRole('button', { name: 'Start role' }) as HTMLButtonElement).disabled).toBe(
        false,
      ),
    );
    fireEvent.change(view.getByLabelText('Role'), { target: { value: 'orchestrator' } });
    const sprint = view.getByLabelText('AR sprint') as HTMLSelectElement;
    expect(sprint.getAttribute('title')).toBe('Sprint');
    expect(sprint.options[0]?.textContent).toBe('Select sprint…');
  });
});

describe('the mode is one chip', () => {
  it.each(['our settings', 'host profile', 'least permissive mode'])(
    'says a %s mode with the host label and description in hover',
    async (source) => {
      const defaults = roleDefaultsFixture();
      (defaults.choices as unknown as Record<string, Record<string, string>>).mode = { source };
      stubOptions(defaults);
      const view = renderArchitectRow();
      await waitFor(() => expect(view.getByText('Default Permissions')).toBeTruthy());
      const chip = view.getByText('Default Permissions');
      expect(chip.getAttribute('title')).toContain(source);
      expect(chip.getAttribute('title')).toContain("The host's least permissive mode.");
    },
  );

  it("says a foreign agent's mode in two words with the sentence in hover", async () => {
    stubOptions(roleDefaultsFixture());
    const view = renderArchitectRow();
    await waitFor(() => expect(view.getByText('Default Permissions')).toBeTruthy());
    fireEvent.change(view.getByLabelText('Role agent override'), { target: { value: 'eve' } });
    const chip = await view.findByText('At start');
    expect(chip.textContent!.split(' ').filter(Boolean)).toHaveLength(2);
    expect(chip.getAttribute('title')).toContain('resolved for the selected agent at start');
  });
});

describe('where a default comes from is hover text', () => {
  it('names the source on agent, model, effort and feature controls', async () => {
    stubOptions(roleDefaultsFixture());
    const view = renderArchitectRow();
    await waitFor(() => expect(view.getByText('Default Permissions')).toBeTruthy());
    expect(
      (view.getByLabelText('Role agent override') as HTMLElement).getAttribute('title'),
    ).toContain('agent: codex · our settings');
    expect(
      (view.getByLabelText('Role model override') as HTMLElement).getAttribute('title'),
    ).toContain('model: gpt-a · our settings');
    expect(
      (view.getByLabelText('Role effort override') as HTMLElement).getAttribute('title'),
    ).toContain('effort: high · our settings');
    expect((view.getByLabelText('Speed override') as HTMLElement).getAttribute('title')).toContain(
      'service_tier: default · host role',
    );
  });

  it('keeps the complete displayed label in every hover', async () => {
    stubOptions(roleDefaultsFixture());
    const view = renderArchitectRow();
    await waitFor(() =>
      expect((view.getByRole('button', { name: 'Start role' }) as HTMLButtonElement).disabled).toBe(
        false,
      ),
    );
    // Defaults: the displayed first-option label is in the hover.
    expect(
      (view.getByLabelText('Role agent override') as HTMLElement).getAttribute('title'),
    ).toContain('Codex · default');
    expect(
      (view.getByLabelText('Role model override') as HTMLElement).getAttribute('title'),
    ).toContain('GPT A · default');
    expect(
      (view.getByLabelText('Role effort override') as HTMLElement).getAttribute('title'),
    ).toContain('High · default');
    expect((view.getByLabelText('Speed override') as HTMLElement).getAttribute('title')).toContain(
      'Speed · default',
    );
    expect(
      (view.getByLabelText('Role', { exact: true }) as HTMLElement).getAttribute('title'),
    ).toContain('Architect');
  });

  it('keeps chosen option labels in hovers for explicit overrides', async () => {
    stubOptions({
      agent: 'codex',
      model: 'gpt-a',
      effort: 'high',
      available: true,
      mode: 'auto',
      choices: { mode: { source: 'our settings' } },
    });
    const view = renderArchitectRow();
    await waitFor(() =>
      expect((view.getByRole('button', { name: 'Start role' }) as HTMLButtonElement).disabled).toBe(
        false,
      ),
    );
    fireEvent.change(view.getByLabelText('Speed override'), { target: { value: 'priority' } });
    expect((view.getByLabelText('Speed override') as HTMLElement).getAttribute('title')).toContain(
      'Fast',
    );
    fireEvent.change(view.getByLabelText('Role effort override'), { target: { value: 'low' } });
    expect(
      (view.getByLabelText('Role effort override') as HTMLElement).getAttribute('title'),
    ).toContain('Low');
    fireEvent.change(view.getByLabelText('Role model override'), { target: { value: 'gpt-b' } });
    expect(
      (view.getByLabelText('Role model override') as HTMLElement).getAttribute('title'),
    ).toContain('GPT Bravo model with a very long host label');
    fireEvent.change(view.getByLabelText('Role agent override'), { target: { value: 'eve' } });
    expect(
      (view.getByLabelText('Role agent override') as HTMLElement).getAttribute('title'),
    ).toContain('Eve');
  });

  it('carries the rendered document title in document hovers', async () => {
    stubOptions(roleDefaultsFixture());
    const view = renderArchitectRow([SPRINT]);
    await waitFor(() =>
      expect((view.getByRole('button', { name: 'Start role' }) as HTMLButtonElement).disabled).toBe(
        false,
      ),
    );
    fireEvent.change(view.getByLabelText('Role'), { target: { value: 'orchestrator' } });
    const sprint = view.getByLabelText('AR sprint') as HTMLSelectElement;
    fireEvent.change(sprint, { target: { value: '0' } });
    const title = sprint.getAttribute('title') ?? '';
    expect(title).toContain('Sprint one');
    expect(title).toContain('demo/');
  });
});

describe('every selector is the same selector', () => {
  it('gives a plain feature the row selector and a permission feature a chip', async () => {
    stubOptions(roleDefaultsFixture());
    const view = renderArchitectRow();
    await waitFor(() => expect(view.getByText('Default Permissions')).toBeTruthy());
    const agent = view.getByLabelText('Role agent override') as HTMLSelectElement;
    const speed = view.getByLabelText('Speed override') as HTMLSelectElement;
    expect(speed.className).toBe(agent.className);
    expect(speed.options[0]?.textContent).toBe('Speed · default');
    expect(view.queryByLabelText('Plan mode override')).toBeNull();
    const chip = view.getByText('Plan mode Off');
    expect(chip.nodeName).toBe('SPAN');
    expect(chip.getAttribute('title')).toContain("set in the user's settings");
  });
});

describe('user instructions are one chip', () => {
  it('is there with a text and absent without one', async () => {
    stubOptions(roleDefaultsFixture());
    const first = renderArchitectRow();
    await waitFor(() => expect(first.getByText('Default Permissions')).toBeTruthy());
    const chip = first.getByText('Instructions');
    expect(chip.getAttribute('title')).toContain('host role');
    expect(chip.getAttribute('title')).toContain('added to baseline');
    first.unmount();
    const without = roleDefaultsFixture({ choices: { mode: { source: 'our settings' } } });
    stubOptions(without);
    const second = renderArchitectRow();
    await waitFor(() =>
      expect(
        (second.getByRole('button', { name: 'Start role' }) as HTMLButtonElement).disabled,
      ).toBe(false),
    );
    expect(second.queryByText('Instructions')).toBeNull();
  });
});

describe('service-tier disclosure is a shared chip', () => {
  it('keeps its test id, text and provenance on the chip', async () => {
    stubOptions(roleDefaultsFixture({ serviceTier: 'fast' }));
    const view = renderArchitectRow();
    await waitFor(() => expect(view.getByTestId('role-service-tier')).toBeTruthy());
    const tier = view.getByTestId('role-service-tier');
    expect(tier.className).toBe(launcherChip);
    expect(tier.textContent).toBe('Speed: Fast');
    expect(tier.getAttribute('title')).toContain('default agent');
  });
});

describe('an overfull row wraps whole', () => {
  it('renders every extra feature control with no overflow menu and no nowrap', async () => {
    const many = Array.from({ length: 24 }, (_, index) =>
      index % 2 === 0
        ? {
            id: `speed_${index}`,
            label: `Speed ${index}`,
            type: 'select',
            value: 'default',
            permissionBearing: false,
            options: [{ id: 'priority', label: 'Fast' }],
          }
        : {
            id: `plan_${index}`,
            label: `Plan ${index}`,
            type: 'toggle',
            value: false,
            permissionBearing: true,
          },
    );
    const agents = [
      {
        id: 'codex',
        label: 'Codex',
        models: [{ id: 'gpt-a', label: 'GPT A', efforts: [], isDefault: true, features: many }],
        modes: [{ id: 'auto', label: 'Default Permissions' }],
      },
    ];
    optionsReplies = [
      {
        status: 200,
        body: {
          roleDefaults: roleDefaultsFixture(),
          agents,
          catalogOrigin: 'r133',
          executions: [],
        },
      },
    ];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        if (String(url) !== '/api/role-launch/options') {
          return new Response(JSON.stringify({ available: false, frameUrl: null }), {
            status: 503,
          });
        }
        return new Response(JSON.stringify(optionsReplies[0]?.body), { status: 200 });
      }),
    );
    const view = renderArchitectRow();
    await waitFor(() => expect(view.getByTestId('role-launcher')).toBeTruthy());
    const launcher = view.getByTestId('role-launcher');
    for (const feature of many) {
      if (feature.permissionBearing) {
        expect(view.getByText(`${feature.label} Off`)).toBeTruthy();
      } else {
        expect(view.getByLabelText(`${feature.label} override`)).toBeTruthy();
      }
    }
    // No control hides behind a menu and the row never forbids wrapping.
    expect(launcher.querySelector("[role='menu'], [aria-haspopup='menu']")).toBeNull();
    expect(roleLauncherSrc).not.toContain('nowrap');
  });
});

describe('tabs and launcher share one definition', () => {
  it('styles both from the same module and keeps no copy beside it', async () => {
    stubOptions(roleDefaultsFixture());
    const view = renderArchitectRow();
    await waitFor(() => expect(view.getByText('Default Permissions')).toBeTruthy());
    const tabs = render(
      <ModeBar
        items={[{ id: 'chats', label: 'Chats' }]}
        value="chats"
        onChange={() => {}}
        label="Views"
      />,
    );
    expect(tabs.getByRole('radio', { name: 'Chats' }).className).toBe(viewTabButton);
    const launcher = view.getByTestId('role-launcher');
    for (const select of Array.from(launcher.querySelectorAll('select'))) {
      expect(select.className).toBe(launcherSelect);
    }
    expect(view.getByRole('button', { name: 'Start role' }).className).toBe(
      launcherButton({ selected: true }),
    );
    expect(view.getByRole('button', { name: 'Refresh role agents' }).className).toBe(
      launcherButton({ selected: false }),
    );
    expect(view.getByText('Default Permissions').className).toBe(launcherChip);
    expect(modeBarSrc).toContain('viewTabLook');
    expect(modeBarSrc).not.toContain('0.72rem');
    expect(roleLauncherSrc).toContain('viewTabLook');
    expect(roleLauncherSrc).not.toContain('2.55rem');
    expect(roleLaunchFeaturesSrc).toContain('viewTabLook');
    expect(roleLaunchFeaturesSrc).not.toContain('2.55rem');
  });
});
