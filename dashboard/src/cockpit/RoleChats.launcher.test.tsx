// The launcher bar against the host catalog (PNT-R02): an unoffered role default disables Start
// until an offered value is picked, a failed model listing leaves its agent launchable, and an
// options error is shown with a Refresh control that repeats the call.
import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { RoleChatsPane } from './RoleChats';
import { forgetRememberedRoleDefaults } from './launcherDefaultsMemory';
import {
  launchChoiceProblem,
  roleAgentOverrideFor,
  type RoleAgentChoice,
  type RoleDefaults,
} from './roleLaunchModel';

const AGENTS: RoleAgentChoice[] = [
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
        serviceTiers: [
          { id: 'default', label: 'Normal' },
          { id: 'priority', label: 'Fast' },
        ],
      },
      { id: 'gpt-b', label: 'GPT B', efforts: [] },
    ],
  },
  { id: 'eve', label: 'Eve', models: [] },
  { id: 'pi', label: 'Pi', models: [], listingError: 'auth missing' },
];

type Reply = { status: number; body: unknown };
let optionsReplies: Reply[] = [];
let optionsRequests: Record<string, unknown>[] = [];
let optionsBarrier: Promise<void> | null = null;
let savedCatalogReply: Reply | null = null;

function optionsReply(roleDefaults: Record<string, unknown>): Reply {
  return {
    status: 200,
    body: { roleDefaults, agents: AGENTS, catalogOrigin: 'paseo:1', executions: [] },
  };
}

function renderLauncher() {
  return render(<RoleChatsPane active tasks={[]} />);
}

beforeEach(() => {
  optionsReplies = [];
  optionsRequests = [];
  optionsBarrier = null;
  savedCatalogReply = null;
  sessionStorage.clear();
  forgetRememberedRoleDefaults();
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url === '/api/role-launch/saved-catalog') return new Response(JSON.stringify(savedCatalogReply?.body ?? {}), { status: savedCatalogReply?.status ?? 503 });
      if (url !== '/api/role-launch/options')
        return new Response(JSON.stringify({ available: false, frameUrl: null }), { status: 503 });
      optionsRequests.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
      if (optionsBarrier) await optionsBarrier;
      const reply = optionsReplies.length > 1 ? optionsReplies.shift() : optionsReplies[0];
      return new Response(JSON.stringify(reply?.body), { status: reply?.status ?? 500 });
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('launcher bar and the host catalog', () => {
  it('keeps a speed override when choosing model or effort and names the resolved agent', () => {
    const values = { service_tier: 'priority' };
    const defaults = { agent: 'codex', model: 'gpt-a' };
    expect(roleAgentOverrideFor('codex', 'gpt-a', 'high', defaults, values)?.featureValues).toEqual(
      values,
    );
    expect(
      roleAgentOverrideFor('codex', 'gpt-a', undefined, defaults, values)?.featureValues,
    ).toEqual(values);
    expect(launchChoiceProblem({ agent: 'missing' }, AGENTS, { featureValues: values })).toContain(
      'Agent missing',
    );
  });
  it('displays the configured tier, revalidates a model override and discloses other-agent defaults', async () => {
    optionsReplies = [
      optionsReply({
        agent: 'codex',
        model: 'gpt-a',
        effort: 'high',
        serviceTier: 'fast',
        available: true,
      }),
    ];
    const { getByRole, getByLabelText, getByTestId, queryByTestId } = renderLauncher();
    const start = getByRole('button', { name: 'Start role' }) as HTMLButtonElement;
    await waitFor(() => expect(start.disabled).toBe(false));
    expect(getByTestId('role-service-tier').textContent).toBe('Speed: Fast');
    expect(getByTestId('role-service-tier').getAttribute('title')).toContain('default agent');
    fireEvent.change(getByLabelText('Role model override'), { target: { value: 'gpt-b' } });
    await waitFor(() => expect(start.disabled).toBe(true));
    expect(getByTestId('role-choice-problem').textContent).toContain(
      'Service tier fast is not offered',
    );
    fireEvent.change(getByLabelText('Role agent override'), { target: { value: 'eve' } });
    await waitFor(() => expect(start.disabled).toBe(false));
    expect(getByTestId('role-service-tier').textContent).toBe('Speed: provider default');
    expect(queryByTestId('role-choice-problem')).toBeNull();
  });

  it('disables Start for an unoffered role default until an offered value is picked', async () => {
    optionsReplies = [
      optionsReply({ agent: 'codex', model: 'gpt-z', effort: null, available: false }),
    ];
    const { getByRole, getByLabelText, findByTestId, queryByTestId, queryByRole } =
      renderLauncher();

    const problem = await findByTestId('role-choice-problem');
    expect(problem.textContent).toContain(
      'Role default codex · gpt-z is not offered by the Paseo runtime',
    );
    const start = getByRole('button', { name: 'Start role' }) as HTMLButtonElement;
    expect(start.disabled).toBe(true);
    expect(optionsRequests).toEqual([{ role: 'architect' }]);
    const unavailable = await findByTestId('paseo-frame-unavailable');
    expect(queryByRole('button', { name: /chat navigation/ })).toBeNull();
    expect(
      getByLabelText('Role')
        .closest('[data-testid="role-launcher"]')
        ?.querySelector('button,select,input'),
    ).toBe(getByLabelText('Role'));
    expect(await findByTestId('paseo-frame-unavailable')).toBe(unavailable);
    expect(optionsRequests).toEqual([{ role: 'architect' }]);
    expect(start.disabled).toBe(true);

    // Naming the role's own agent keeps the role's unoffered model, so Start stays disabled.
    fireEvent.change(getByLabelText('Role agent override'), { target: { value: 'codex' } });
    await waitFor(() => expect(optionsRequests.length).toBe(2));
    await waitFor(() =>
      expect(queryByTestId('role-choice-problem')?.textContent).toContain(
        'Model gpt-z is not offered for Codex',
      ),
    );
    expect(start.disabled).toBe(true);

    fireEvent.change(getByLabelText('Role model override'), { target: { value: 'gpt-a' } });
    await waitFor(() => expect(start.disabled).toBe(false));
    expect(queryByTestId('role-choice-problem')).toBeNull();
  });

  it('keeps an agent whose models could not be listed selectable without a model', async () => {
    optionsReplies = [optionsReply({ agent: 'pi', model: null, effort: null, available: true })];
    const { getByRole, getByLabelText, findByTestId } = renderLauncher();

    const problem = await findByTestId('role-choice-problem');
    expect(problem.textContent).toBe('Models of Pi could not be listed: auth missing');
    expect((getByLabelText('Role model override') as HTMLSelectElement).disabled).toBe(true);
    expect((getByRole('button', { name: 'Start role' }) as HTMLButtonElement).disabled).toBe(false);
  });

  it('shows an options error and repeats the call from Refresh', async () => {
    optionsReplies = [
      {
        status: 503,
        body: {
          detail: 'no Paseo runtime configured: /settings/mcp.json has no paseoRuntime block',
        },
      },
      optionsReply({ agent: 'codex', model: null, effort: null, available: true }),
    ];
    const { getByRole, findByRole, queryByRole } = renderLauncher();

    const alert = await findByRole('alert');
    expect(alert.textContent).toContain(
      '(HTTP 503): no Paseo runtime configured: /settings/mcp.json has no paseoRuntime block',
    );
    expect((getByRole('button', { name: 'Start role' }) as HTMLButtonElement).disabled).toBe(true);
    const refresh = getByRole('button', { name: 'Refresh role agents' }) as HTMLButtonElement;
    expect(refresh.disabled).toBe(false);

    let release!: () => void;
    optionsBarrier = new Promise<void>((resolve) => {
      release = resolve;
    });
    fireEvent.click(refresh);
    await waitFor(() => expect(refresh.disabled).toBe(true));
    expect(queryByRole('alert')?.textContent).toContain('no Paseo runtime configured');
    release();
    await waitFor(() =>
      expect(optionsRequests).toEqual([
        { role: 'architect' },
        { role: 'architect', refreshCatalog: true },
      ]),
    );
    await waitFor(() => expect(queryByRole('alert')).toBeNull());
    expect((getByRole('button', { name: 'Start role' }) as HTMLButtonElement).disabled).toBe(false);
  });

  it('rereads a launch in progress without an ordinary status line', async () => {
    vi.useFakeTimers();
    // The backend's launch lock is held by a launch: the options route marks its refusal.
    optionsReplies = [
      {
        status: 409,
        body: {
          detail: 'A role launch or result check is already in progress.',
          launchInProgress: true,
        },
      },
      optionsReply({ agent: 'codex', model: null, effort: null, available: true }),
    ];
    const { getByRole, getByTestId, queryByRole } = renderLauncher();

    await act(async () => {});
    expect(optionsRequests.length).toBe(1);
    expect(getByTestId('role-launcher').querySelector('[role="status"]')).toBeNull();
    expect(queryByRole('alert')).toBeNull();
    expect(optionsRequests).toEqual([{ role: 'architect' }]);
    expect((getByRole('button', { name: 'Start role' }) as HTMLButtonElement).disabled).toBe(true);

    // No click: the launcher reads again by itself and restores Start when the read succeeds.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500);
    });
    expect(optionsRequests.length).toBe(2);
    expect(getByTestId('role-launcher').querySelector('[role="status"],[role="alert"]')).toBeNull();
    expect(queryByRole('alert')).toBeNull();
    expect((getByRole('button', { name: 'Start role' }) as HTMLButtonElement).disabled).toBe(false);
  });

  it('keeps showing another refusal of the options route as an error', async () => {
    optionsReplies = [
      {
        status: 409,
        body: { detail: 'The selected Projects repository is not admitted by MCP settings.' },
      },
    ];
    const { findByRole, getByTestId } = renderLauncher();

    expect((await findByRole('alert')).textContent).toContain(
      '(HTTP 409): The selected Projects repository is not admitted',
    );
    expect(getByTestId('role-launcher').querySelector('[role="status"]')).toBeNull();
    expect(optionsRequests.length).toBe(1);
  });
});

describe('launchChoiceProblem', () => {
  const role: RoleDefaults = { agent: 'codex', model: 'gpt-a', effort: 'high', available: true };

  it("mirrors the backend's launch validation", () => {
    const unset = null as unknown as undefined; // the backend sends an unset default as null
    const offered: [RoleDefaults, Parameters<typeof launchChoiceProblem>[2]][] = [
      [role, undefined],
      [{ agent: 'codex' }, undefined],
      [role, { agentId: 'codex', modelId: 'gpt-b' }],
      [role, { agentId: 'codex', effortId: 'low' }],
      [role, { agentId: 'eve' }],
      [{ agent: 'claude', model: 'opus', available: false }, { agentId: 'eve' }],
      [{ agent: 'codex', model: unset, effort: unset, available: true }, { agentId: 'codex' }],
    ];
    for (const [defaults, override] of offered)
      expect(launchChoiceProblem(defaults, AGENTS, override)).toBeNull();

    const refused: [RoleDefaults, Parameters<typeof launchChoiceProblem>[2], string][] = [
      [
        { ...role, model: 'gpt-z', available: false },
        undefined,
        'Role default codex · gpt-z · high is not offered',
      ],
      [{ available: false }, undefined, 'No agent is configured for this role'],
      [role, { agentId: 'claude' }, 'Agent claude is not offered'],
      [role, { agentId: 'codex', modelId: 'gpt-z' }, 'Model gpt-z is not offered for Codex'],
      [
        role,
        { agentId: 'codex', modelId: 'gpt-a', effortId: 'max' },
        'Effort max is not offered for GPT A',
      ],
      [
        { agent: 'codex', model: 'gpt-b', effort: 'low' },
        { agentId: 'codex' },
        'Effort low is not offered for GPT B',
      ],
      [role, { agentId: 'eve', effortId: 'low' }, 'Effort low needs a model'],
      [role, { agentId: 'eve', modelId: 'eve-1' }, 'Model eve-1 is not offered for Eve'],
    ];
    for (const [defaults, override, text] of refused)
      expect(launchChoiceProblem(defaults, AGENTS, override)).toContain(text);
  });
});

describe('R71 speed feature choices', () => {
  it('submits only an explicit speed feature and shows permission choices as settings owned', async () => {
    const offered = structuredClone(AGENTS);
    offered[0].modes = [{ id: 'auto', label: 'Default Permissions' }];
    offered[0].models[0].features = [
      {
        id: 'service_tier',
        label: 'Speed feature',
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
    ];
    optionsReplies = [
      {
        status: 200,
        body: {
          roleDefaults: {
            agent: 'codex',
            model: 'gpt-a',
            available: true,
            mode: 'auto',
            choices: {
              mode: { source: 'least permissive mode' },
              featureValues: { service_tier: { value: 'default', source: 'host role' } },
              notes: { present: true, baselineReplaced: true, source: 'host role' },
            },
          },
          agents: offered,
          catalogOrigin: 'r71',
          executions: [],
        },
      },
    ];
    const requests: Record<string, unknown>[] = [];
    const optionsFetch = globalThis.fetch;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        if (url === '/api/role-launch/dispatch') {
          requests.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
          return new Response(
            JSON.stringify({
              requestId: requests.at(-1)?.requestId,
              status: 'rejected',
              detail: 'fixture',
              canStart: true,
              canRevive: false,
            }),
            { status: 200 },
          );
        }
        return optionsFetch(url, init);
      }),
    );
    const view = renderLauncher();
    await waitFor(() => expect(view.getByText('Default Permissions')).toBeTruthy());
    const modeChip = view.getByText('Default Permissions');
    expect(modeChip.getAttribute('title')).toContain('least permissive mode');
    expect(view.queryByTestId('role-default-sources')).toBeNull();
    expect(view.queryByText(/Features:/)).toBeNull();
    expect(view.queryByText(/Role defaults:/)).toBeNull();
    const instructions = view.getByText('Instructions');
    expect(instructions.getAttribute('title')).toContain('host role');
    expect(instructions.getAttribute('title')).toContain('baseline replaced');
    expect(view.queryByLabelText('Plan mode override')).toBeNull();
    expect(view.queryByLabelText('Mode override')).toBeNull();
    fireEvent.change(view.getByLabelText('Speed feature override'), {
      target: { value: 'priority' },
    });
    fireEvent.click(view.getByRole('button', { name: 'Start role' }));
    await waitFor(() => expect(requests).toHaveLength(1));
    expect(requests[0].agentOverride).toEqual({ featureValues: { service_tier: 'priority' } });
    expect(requests[0]).not.toHaveProperty('mode');
    await waitFor(() =>
      expect((view.getByRole('button', { name: 'Start role' }) as HTMLButtonElement).disabled).toBe(
        false,
      ),
    );
    fireEvent.change(view.getByLabelText('Role model override'), { target: { value: 'gpt-a' } });
    await waitFor(() =>
      expect((view.getByRole('button', { name: 'Start role' }) as HTMLButtonElement).disabled).toBe(
        false,
      ),
    );
    fireEvent.change(view.getByLabelText('Role effort override'), { target: { value: 'high' } });
    fireEvent.click(view.getByRole('button', { name: 'Start role' }));
    await waitFor(() => expect(requests).toHaveLength(2));
    expect((requests[1].agentOverride as { featureValues: unknown }).featureValues).toEqual({
      service_tier: 'priority',
    });
  });
});


describe('R214 fresh launcher views', () => {
  it('asks again on the next view and replaces a same-origin catalog answer', async () => {
    optionsReplies = [optionsReply({ agent: 'codex', model: 'gpt-a', available: true })];
    const first = renderLauncher();
    await waitFor(() => expect((first.getByRole('button', { name: 'Start role' }) as HTMLButtonElement).disabled).toBe(false));
    const firstCount = optionsRequests.length;
    first.unmount();
    optionsReplies = [{ status: 200, body: { roleDefaults: { agent: 'fresh-provider', available: true }, agents: [{ id: 'fresh-provider', label: 'Fresh Provider', models: [] }], catalogOrigin: 'paseo:1', catalogDiscovery: { source: 'fresh', readAt: '2026-10-26T15:30:00Z' }, executions: [] } }];
    const second = renderLauncher();
    await waitFor(() => expect(optionsRequests.length).toBeGreaterThan(firstCount));
    await waitFor(() => expect((second.getByLabelText('Role agent override') as HTMLSelectElement).textContent).toContain('Fresh Provider'));
    expect((second.getByLabelText('Role agent override') as HTMLSelectElement).textContent).not.toContain('Codex');
  });
});


describe('R214 cold saved preview', () => {
  it('offers the shared disk answer silently before fresh options settle, then replaces it (R215)', async () => {
    let releaseFresh: (() => void) | undefined;
    optionsBarrier = new Promise<void>((resolve) => { releaseFresh = resolve; });
    savedCatalogReply = { status: 200, body: {
      agents: [{ id: 'saved-agent', label: 'Saved Agent', models: [{ id: 'saved-model', label: 'Saved Model' }] }],
      catalogDiscovery: { source: 'saved', readAt: '2026-10-26T14:20:00Z' }, catalogOrigin: 'paseo:saved-origin',
    } };
    optionsReplies = [{ status: 200, body: {
      agents: [{ id: 'fresh-agent', label: 'Fresh Agent', models: [] }],
      roleDefaults: { agent: 'fresh-agent', available: true },
      catalogDiscovery: { source: 'fresh', readAt: '2026-10-26T16:00:00Z' }, catalogOrigin: 'paseo:fresh-origin', executions: [],
    } }];
    const view = renderLauncher();
    await waitFor(() => expect((view.getByLabelText('Role agent override') as HTMLSelectElement).textContent).toContain('Saved Agent'));
    expect(view.queryByRole('status', { name: 'Paseo catalog status' })).toBeNull();
    expect(optionsRequests.length).toBeGreaterThan(0);
    expect((view.getByLabelText('Role agent override') as HTMLSelectElement).disabled).toBe(false);
    expect((view.getByRole('button', { name: 'Start role' }) as HTMLButtonElement).disabled).toBe(false);
    await act(async () => releaseFresh?.());
    await waitFor(() => expect((view.getByLabelText('Role agent override') as HTMLSelectElement).textContent).toContain('Fresh Agent'));
    expect((view.getByRole('button', { name: 'Start role' }) as HTMLButtonElement).disabled).toBe(false);
    expect(view.queryByRole('status', { name: 'Paseo catalog status' })).toBeNull();
    expect((view.getByLabelText('Role agent override') as HTMLSelectElement).textContent).not.toContain('Saved Agent');
  });
});


describe('R5 pending launcher provenance', () => {
  it.each(['agent change', 'same-scope refresh'])('replaces the previous fresh display with the disk snapshot on %s, silently (R215)', async (change) => {
    optionsReplies = [optionsReply({ agent: 'codex', model: 'gpt-a', available: true })];
    const view = renderLauncher();
    const start = view.getByRole('button', { name: 'Start role' }) as HTMLButtonElement;
    await waitFor(() => expect(start.disabled).toBe(false));
    let releaseFresh: (() => void) | undefined;
    optionsBarrier = new Promise<void>((resolve) => { releaseFresh = resolve; });
    savedCatalogReply = { status: 200, body: {
      agents: [{ id: 'disk', label: 'Disk Agent', models: [] }, { id: 'eve', label: 'Disk Eve', models: [] }],
      catalogDiscovery: { source: 'saved', readAt: '2026-10-26T16:20:00Z' }, catalogOrigin: 'paseo:disk',
    } };
    if (change === 'agent change') {
      fireEvent.change(view.getByLabelText('Role agent override'), { target: { value: 'eve' } });
    } else {
      fireEvent.click(view.getByRole('button', { name: 'Refresh role agents' }));
    }
    const agent = view.getByLabelText('Role agent override') as HTMLSelectElement;
    await waitFor(() => expect(agent.textContent).toContain('Disk Agent'));
    expect(agent.textContent).not.toContain('Codex');
    expect(view.queryByRole('status', { name: 'Paseo catalog status' })).toBeNull();
    expect(start.disabled).toBe(false);
    await act(async () => releaseFresh?.());
    await waitFor(() => expect(agent.textContent).toContain('Codex'));
    expect(start.disabled).toBe(false);
    expect(view.queryByRole('status', { name: 'Paseo catalog status' })).toBeNull();
  });
});


// MIK-R215: Paseo's last answer is used silently, nothing on the page moves for it, and the fallback
// offers every choice that answer had; only a real error speaks.
const SAVED_ANSWER = {
  agents: AGENTS,
  catalogDiscovery: { source: 'saved', readAt: '2026-10-27T06:00:00Z' },
  catalogOrigin: 'paseo:saved',
};

function savedCatalogReads(): number {
  return vi.mocked(fetch).mock.calls.filter(([url]) => url === '/api/role-launch/saved-catalog').length;
}

// What the launcher row lays out, in order: its controls and any status line.
function launcherLayout(view: ReturnType<typeof renderLauncher>): (string | null)[] {
  const row = view.getByTestId('role-launcher');
  return Array.from(row.querySelectorAll('select,button,[role="status"]')).map((node) =>
    node.getAttribute('aria-label'),
  );
}

function optionLabels(select: HTMLElement): string[] {
  return Array.from((select as HTMLSelectElement).options).map((option) => option.text);
}

describe('R215 the launcher speaks only of real errors', () => {
  it('offers the saved choices with the role defaults, none blocked and no notice, while a fresh answer is on its way', async () => {
    optionsReplies = [optionsReply({ agent: 'codex', model: 'gpt-a', available: true })];
    const view = renderLauncher();
    const start = view.getByRole('button', { name: 'Start role' }) as HTMLButtonElement;
    await waitFor(() => expect(start.disabled).toBe(false));
    const settled = launcherLayout(view);
    const settledModels = optionLabels(view.getByLabelText('Role model override'));
    let releaseFresh: (() => void) | undefined;
    optionsBarrier = new Promise<void>((resolve) => { releaseFresh = resolve; });
    savedCatalogReply = { status: 200, body: SAVED_ANSWER };
    fireEvent.click(view.getByRole('button', { name: 'Refresh role agents' }));
    await waitFor(() => expect(savedCatalogReads()).toBeGreaterThan(1));
    await act(async () => {});

    expect(view.queryByRole('status', { name: 'Paseo catalog status' })).toBeNull();
    for (const label of ['Role', 'Role agent override', 'Role model override', 'Role effort override']) {
      expect((view.getByLabelText(label) as HTMLSelectElement).disabled, label).toBe(false);
    }
    expect(optionLabels(view.getByLabelText('Role model override'))).toEqual(settledModels);
    expect(optionLabels(view.getByLabelText('Role effort override'))).toEqual(['Low · default', 'Low', 'High']);
    expect(start.disabled).toBe(false);
    expect(launcherLayout(view)).toEqual(settled);

    await act(async () => releaseFresh?.());
    await waitFor(() => expect(optionsRequests.length).toBe(2));
    expect(launcherLayout(view)).toEqual(settled);
    expect(view.queryByRole('status', { name: 'Paseo catalog status' })).toBeNull();
  });

  it('opened cold while Paseo does not answer, shows the saved choices selectable and silent, and a start passes unchecked', async () => {
    optionsBarrier = new Promise<void>(() => {});
    // Model labels only the disk answer has: what the model select lists can come from nowhere else.
    const diskAgents = AGENTS.map((row) => ({
      ...row,
      models: row.models.map((model) => ({ ...model, label: `Saved ${model.label}` })),
    }));
    savedCatalogReply = { status: 200, body: { ...SAVED_ANSWER, agents: diskAgents } };
    const dispatched: Record<string, unknown>[] = [];
    const base = globalThis.fetch;
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      if (url !== '/api/role-launch/dispatch') return base(url, init);
      dispatched.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
      return new Response(JSON.stringify({ requestId: dispatched.at(-1)?.requestId, status: 'rejected', detail: 'fixture', canStart: true, canRevive: false }), { status: 200 });
    }));
    const view = renderLauncher();
    await waitFor(() => expect(savedCatalogReads()).toBe(1));
    await act(async () => {});

    expect(view.queryByRole('status', { name: 'Paseo catalog status' })).toBeNull();
    const agent = view.getByLabelText('Role agent override') as HTMLSelectElement;
    expect(agent.disabled).toBe(false);
    expect(optionLabels(agent)).toEqual(['Role default', 'Codex', 'Eve', 'Pi']);
    expect((view.getByLabelText('Role') as HTMLSelectElement).disabled).toBe(false);
    // With no agent known the model select asks for one; it is a step, not a refusal (rule 2).
    const model = view.getByLabelText('Role model override') as HTMLSelectElement;
    expect(optionLabels(model)).toEqual(['Pick an agent first']);
    expect(view.queryByRole('alert')).toBeNull();
    const start = view.getByRole('button', { name: 'Start role' }) as HTMLButtonElement;
    expect(start.disabled).toBe(false);

    fireEvent.click(start);
    await waitFor(() => expect(dispatched).toHaveLength(1));
    expect(dispatched[0].role).toBe('architect');
    expect(dispatched[0]).not.toHaveProperty('agentOverride');

    // Picking an agent lists its models from the saved answer alone: the fresh question the new
    // scope asks never answers, and the saved one is read again from disk for it.
    const readsBeforePick = savedCatalogReads();
    fireEvent.change(agent, { target: { value: 'codex' } });
    await waitFor(() => expect(savedCatalogReads()).toBeGreaterThan(readsBeforePick));
    await waitFor(() => expect(optionLabels(view.getByLabelText('Role model override'))).toEqual(['Agent default', 'Saved GPT A', 'Saved GPT B']));
    expect((view.getByLabelText('Role model override') as HTMLSelectElement).disabled).toBe(false);
    expect(optionsRequests.at(-1)?.agentId).toBe('codex');
    expect(view.queryByRole('status', { name: 'Paseo catalog status' })).toBeNull();
  });

  it('answers from the saved parts without a word, and a refusal still speaks', async () => {
    const saved = { source: 'saved', readAt: '2026-10-27T06:00:00Z', error: 'paseo_daemon_unreachable' };
    const savedAgents = AGENTS.map((row) => ({
      ...row,
      discovery: { models: saved, modes: saved, acceptsToolServers: saved },
      models: row.models.map((model) => ({ ...model, discovery: { efforts: saved, features: saved, serviceTiers: saved } })),
    }));
    optionsReplies = [
      { status: 200, body: { roleDefaults: { agent: 'codex', model: 'gpt-a', available: true }, agents: savedAgents, catalogDiscovery: saved, catalogOrigin: 'paseo:1', executions: [] } },
      { status: 409, body: { detail: 'The Paseo runtime does not offer agent codex.' } },
    ];
    const view = renderLauncher();
    const start = view.getByRole('button', { name: 'Start role' }) as HTMLButtonElement;
    await waitFor(() => expect(start.disabled).toBe(false));
    expect(view.queryByRole('status', { name: 'Paseo catalog status' })).toBeNull();
    expect(optionLabels(view.getByLabelText('Role model override'))).toEqual(['GPT A · default', 'GPT A', 'GPT B']);

    fireEvent.click(view.getByRole('button', { name: 'Refresh role agents' }));
    expect((await view.findByRole('alert')).textContent).toContain('(HTTP 409): The Paseo runtime does not offer agent codex.');
  });
});
