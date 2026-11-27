import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { taskEntry, taskSummary } from '../test/fixtures/wire';
import { forgetRememberedRoleDefaults } from './launcherDefaultsMemory';
import { RoleChatsPane } from './RoleChats';
import { tasklessRequestStorageKey } from './roleLaunchModel';

const optionsRoute = '/api/role-launch/options';
const dispatchRoute = '/api/role-launch/dispatch';
const savedCatalogRoute = '/api/role-launch/saved-catalog';
const resultRoute = '/api/role-launch/result';
const master = { repository: 'fixture', path: 'example/task.json' };
const tasks = [taskEntry({ reference: master, summary: taskSummary({ form: 'master' }) })];
const agents = [
  { id: 'codex', label: 'Codex', models: [
    { id: 'a', label: 'Model A', isDefault: true },
    { id: 'b', label: 'Model B' },
    { id: 'document', label: 'Document model' },
  ] },
  { id: 'claude', label: 'Claude', models: [{ id: 'a', label: 'Claude A', isDefault: true }] },
];

type RequestBody = Record<string, unknown>;
type OptionsReply = (body: RequestBody) => Response | Promise<Response>;

function metadata(body: RequestBody, offered = agents) {
  const model = body.masterDocumentRef ? 'document' : 'a';
  const agent = body.role === 'investigator' && !body.masterDocumentRef ? 'claude' : 'codex';
  return Response.json({ agents: offered, roleDefaults: { agent, model, available: true }, executions: [] });
}

function selected(body: RequestBody, label = 'Speed', source = 'fresh') {
  return Response.json({ modelOptions: {
    agentId: body.agentId,
    modelId: body.modelId,
    features: [{ id: 'service_tier', label, type: 'select', value: 'normal', permissionBearing: false,
      options: [{ id: 'normal', label: 'Normal' }, { id: 'priority', label: 'Fast' }] }],
    serviceTiers: [{ id: 'normal', label: 'Normal' }, { id: 'priority', label: 'Fast' }],
    discovery: { features: { source }, serviceTiers: { source } },
  } });
}

function parked<T>() {
  let answer!: (value: T) => void;
  const reply = new Promise<T>((resolve) => { answer = resolve; });
  return { reply, answer };
}

function fixture(replies: {
  metadata?: OptionsReply;
  selected?: OptionsReply;
  result?: OptionsReply;
  savedCatalog?: OptionsReply;
} = {}) {
  const requests: RequestBody[] = [];
  const starts: RequestBody[] = [];
  const metadataReads: string[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, request?: RequestInit) => {
    const body = JSON.parse(String(request?.body ?? '{}')) as RequestBody;
    if ((url === optionsRoute && !body.modelId) || url === savedCatalogRoute || url === resultRoute) {
      metadataReads.push(url);
    }
    if (url === optionsRoute) {
      requests.push(body);
      return body.modelId
        ? (replies.selected ?? selected)(body)
        : (replies.metadata ?? metadata)(body);
    }
    if (url === savedCatalogRoute && replies.savedCatalog) return replies.savedCatalog(body);
    if (url === dispatchRoute) starts.push(body);
    if (url === resultRoute && replies.result) return replies.result(body);
    if (url === dispatchRoute || url === resultRoute) {
      return Response.json({ status: 'completed', requestId: body.requestId, canStart: true });
    }
    return Response.json({}, { status: 503 });
  }));
  return { requests, starts, metadataReads, modelRequests: () => requests.filter((body) => body.modelId) };
}

function start(view: ReturnType<typeof render>): HTMLButtonElement {
  return view.getByRole('button', { name: 'Start role' }) as HTMLButtonElement;
}

const openA = { role: 'architect', agentId: 'codex', modelId: 'a' };

beforeEach(() => {
  sessionStorage.clear();
  forgetRememberedRoleDefaults();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

it('opens with all choices available and asks only for the shown model', async () => {
  const run = fixture();
  const view = render(<RoleChatsPane active tasks={tasks} />);
  await waitFor(() => expect(start(view).disabled).toBe(false));
  await waitFor(() => expect(run.requests).toEqual([{ role: 'architect' }, openA]));
  expect((view.getByLabelText('Role agent override') as HTMLSelectElement).options).toHaveLength(3);
  expect((view.getByLabelText('Role model override') as HTMLSelectElement).options).toHaveLength(4);
});

it.each([
  ['agent', 'claude', { ...openA, agentId: 'claude' }],
  ['model', 'b', { ...openA, modelId: 'b' }],
  ['effort', 'high', undefined],
  ['mode', 'ask', undefined],
] as const)('keeps metadata and the result when setting or clearing the first %s override', async (field, value, newPair) => {
  sessionStorage.setItem(tasklessRequestStorageKey('architect'), JSON.stringify({ requestId: 'saved-request' }));
  const offered = [{
    ...agents[0],
    modes: [{ id: 'ask', label: 'Ask' }],
    models: agents[0].models.map((model) => ({ ...model, efforts: [{ id: 'high', label: 'High' }] })),
  }, agents[1]];
  const run = fixture({ metadata: (body) => metadata(body, offered) });
  const view = render(<RoleChatsPane active tasks={tasks} />);
  await waitFor(() => expect(start(view).disabled).toBe(false));
  await view.findByLabelText('Speed override');
  const before = [savedCatalogRoute, optionsRoute, resultRoute];
  expect(run.metadataReads).toEqual(before);

  const control = view.getByLabelText('Role ' + field + ' override') as HTMLSelectElement;
  fireEvent.change(control, { target: { value } });
  await act(async () => {});
  expect(control.value).toBe(value);
  expect(run.metadataReads).toEqual(before);
  const features = newPair ? [openA, newPair] : [openA];
  expect(run.modelRequests()).toEqual(features);

  fireEvent.change(control, { target: { value: '' } });
  await act(async () => {});
  expect(control.value).toBe('');
  expect(run.metadataReads).toEqual(before);
  expect(run.modelRequests()).toEqual(features);
});

it('queries each newly shown provider and model, reusing its answer across roles and documents', async () => {
  const run = fixture();
  const view = render(<RoleChatsPane active tasks={tasks} />);
  await waitFor(() => expect(start(view).disabled).toBe(false));
  fireEvent.change(view.getByLabelText('Role agent override'), { target: { value: 'claude' } });
  await waitFor(() => expect(start(view).disabled).toBe(false));
  const claudeA = { ...openA, agentId: 'claude' };
  expect(run.modelRequests()).toEqual([openA, claudeA]);

  fireEvent.change(view.getByLabelText('Role'), { target: { value: 'investigator' } });
  await waitFor(() => expect(start(view).disabled).toBe(false));
  expect(run.modelRequests()).toEqual([openA, claudeA]);
  fireEvent.change(view.getByLabelText('Role'), { target: { value: 'manager' } });
  fireEvent.change(view.getByLabelText('AR master'), { target: { value: '0' } });
  await waitFor(() => expect(start(view).disabled).toBe(false));
  expect(run.modelRequests()).toEqual([openA, claudeA, {
    role: 'manager', masterDocumentRef: master, agentId: 'codex', modelId: 'document',
  }]);
  fireEvent.change(view.getByLabelText('Role model override'), { target: { value: 'b' } });
  await waitFor(() => expect(run.modelRequests().at(-1)).toMatchObject({ agentId: 'codex', modelId: 'b' }));
});

it('keeps A in flight through A to B to A and applies replies only to the shown model', async () => {
  const first = parked<Response>();
  const second = parked<Response>();
  const run = fixture({ selected: (body) => body.modelId === 'a' ? first.reply : second.reply });
  const view = render(<RoleChatsPane active tasks={tasks} />);
  await waitFor(() => expect(start(view).disabled).toBe(false));
  fireEvent.change(view.getByLabelText('Role model override'), { target: { value: 'b' } });
  const b = { ...openA, modelId: 'b' };
  await waitFor(() => expect(run.modelRequests()).toEqual([openA, b]));
  fireEvent.change(view.getByLabelText('Role model override'), { target: { value: 'a' } });
  await act(async () => second.answer(selected(b, 'Speed B')));
  expect(view.queryByLabelText('Speed B override')).toBeNull();
  expect(start(view).disabled).toBe(false);
  const model = view.getByLabelText('Role model override') as HTMLSelectElement;
  expect(model.disabled).toBe(false);
  expect(model.getAttribute('aria-busy')).toBe('true');
  expect(model.selectedOptions[0].textContent).toContain('options pending');
  await act(async () => first.answer(selected(openA, 'Speed A')));
  expect(view.getByLabelText('Speed A override')).toBeTruthy();
  expect(run.modelRequests()).toEqual([openA, b]);
});

it('Refresh retries a held lock, renews metadata first and drops previous-generation replies', async () => {
  const old = parked<Response>();
  const renewed = parked<Response>();
  const fresh = parked<Response>();
  let refreshAttempts = 0;
  let modelAttempts = 0;
  const run = fixture({
    metadata: (body) => {
      if (!body.refreshCatalog) return metadata(body);
      refreshAttempts += 1;
      return refreshAttempts === 1
        ? Response.json({ launchInProgress: true }, { status: 409 })
        : renewed.reply;
    },
    selected: () => (++modelAttempts === 1 ? old.reply : fresh.reply),
  });
  const view = render(<RoleChatsPane active tasks={tasks} />);
  await waitFor(() => expect(start(view).disabled).toBe(false));

  vi.useFakeTimers();
  fireEvent.click(view.getByRole('button', { name: 'Refresh role agents' }));
  await act(async () => {});
  const refresh = { role: 'architect', refreshCatalog: true };
  expect(run.requests).toEqual([{ role: 'architect' }, openA, refresh]);
  await act(async () => vi.advanceTimersByTimeAsync(1500));
  expect(run.requests).toEqual([{ role: 'architect' }, openA, refresh, refresh]);

  await act(async () => renewed.answer(metadata(refresh)));
  expect(run.requests.at(-1)).toEqual(openA);
  await act(async () => old.answer(selected(openA, 'Old Speed')));
  expect(view.queryByLabelText('Old Speed override')).toBeNull();
  await act(async () => fresh.answer(selected(openA, 'New Speed')));
  expect(view.getByLabelText('New Speed override')).toBeTruthy();
  expect(run.modelRequests()).toEqual([openA, openA]);
});

it('starts once during pending discovery with host defaults and rereads the same model path', async () => {
  let calls = 0;
  const codex = {
    ...agents[0],
    models: agents[0].models.map((model) => ({
      ...model,
      serviceTiers: [{ id: 'priority', label: 'Fast' }],
    })),
  };
  const run = fixture({
    metadata: (body) => metadata(body, [codex, agents[1]]),
    selected: (body) => ++calls === 1
      ? Response.json({ launchInProgress: true }, { status: 409 })
      : selected(body),
  });
  const view = render(<RoleChatsPane active tasks={tasks} boundSelection={{
    role: 'architect',
    agentOverride: { agentId: 'codex', modelId: 'a', featureValues: { service_tier: 'priority' } },
  }} />);
  await waitFor(() => expect(start(view).disabled).toBe(false));
  fireEvent.click(start(view));
  await waitFor(() => expect(run.requests.filter((body) => !body.modelId)).toHaveLength(2));
  await act(async () => {});
  expect(run.requests).toEqual([
    { role: 'architect' }, openA,
    { role: 'architect' }, openA,
  ]);
  expect(run.starts).toHaveLength(1);
  expect(run.starts[0].agentOverride).toEqual({ agentId: 'codex', modelId: 'a' });

  await waitFor(() => expect(start(view).disabled).toBe(false));
  fireEvent.click(start(view));
  await waitFor(() => expect(run.starts).toHaveLength(2));
  await waitFor(() => expect(run.requests).toHaveLength(5));
  expect(run.modelRequests()).toEqual([openA, openA]);
});

it('retains the model and starts with defaults when the host cannot read its options', async () => {
  const run = fixture({ selected: (body) => selected(body, 'Saved Speed', 'saved') });
  const view = render(<RoleChatsPane active tasks={tasks} boundSelection={{
    role: 'architect',
    agentOverride: { agentId: 'codex', modelId: 'a', featureValues: { service_tier: 'unknown' } },
  }} />);
  await waitFor(() => expect(start(view).disabled).toBe(false));
  fireEvent.click(start(view));
  await waitFor(() => expect(run.starts).toHaveLength(1));
  expect(run.starts[0].agentOverride).toEqual({ agentId: 'codex', modelId: 'a' });
});


it('replays the exact saved feature selection while discovery is pending', async () => {
  const saved = {
    role: 'architect',
    agentOverride: { agentId: 'codex', modelId: 'a', featureValues: { service_tier: 'priority' } },
  };
  const requestId = '67dda0d2-586f-4fb8-88e3-f88f541919dc';
  sessionStorage.setItem(tasklessRequestStorageKey('architect'), JSON.stringify({
    requestId, pending: true, retryPayload: saved,
  }));
  const options = parked<Response>();
  const run = fixture({
    selected: () => options.reply,
    result: () => Response.json({}, { status: 404 }),
  });
  const view = render(<RoleChatsPane active tasks={tasks} />);
  const retry = await view.findByRole('button', { name: 'Retry role launch' }) as HTMLButtonElement;
  await waitFor(() => expect(retry.disabled).toBe(false));
  expect(run.modelRequests()).toEqual([openA]);
  fireEvent.click(retry);
  await waitFor(() => expect(run.starts).toEqual([{ ...saved, action: 'start', requestId }]));
});

it('shows a selectable saved model with options pending throughout Refresh', async () => {
  const renewed = parked<Response>();
  const fresh = parked<Response>();
  const stale = await selected(openA, 'Saved Speed').json() as { modelOptions: { features: unknown[] } };
  const savedAgents = [
    { ...agents[0], models: [{
      ...agents[0].models[0],
      label: 'Saved Model A',
      features: stale.modelOptions.features,
    }] },
    agents[1],
  ];
  let modelReads = 0;
  const run = fixture({
    metadata: (body) => body.refreshCatalog ? renewed.reply : metadata(body),
    savedCatalog: () => Response.json({ agents: savedAgents }),
    selected: (body) => ++modelReads === 1 ? selected(body, 'Initial Speed') : fresh.reply,
  });
  const view = render(<RoleChatsPane active tasks={tasks} />);
  await view.findByLabelText('Initial Speed override');

  fireEvent.click(view.getByRole('button', { name: 'Refresh role agents' }));
  const model = view.getByLabelText('Role model override') as HTMLSelectElement;
  await waitFor(() => expect(model.selectedOptions[0].textContent).toContain('Saved Model A'));
  expect(model.disabled).toBe(false);
  expect(start(view).disabled).toBe(false);
  expect({
    pending: model.getAttribute('aria-busy'),
    showsSavedOptions: Boolean(view.queryByLabelText('Saved Speed override')),
  }).toEqual({ pending: 'true', showsSavedOptions: false });
  expect(view.queryByLabelText('Initial Speed override')).toBeNull();
  expect(run.modelRequests()).toEqual([openA]);

  await act(async () => renewed.answer(metadata({ role: 'architect' })));
  expect(model.getAttribute('aria-busy')).toBe('true');
  expect(run.modelRequests()).toEqual([openA, openA]);

  await act(async () => fresh.answer(selected(openA, 'Renewed Speed')));
  expect(model.getAttribute('aria-busy')).toBeNull();
  expect(view.getByLabelText('Renewed Speed override')).toBeTruthy();
  expect(view.queryByLabelText('Saved Speed override')).toBeNull();
});

it('shows the foreign model effort default and sends only an explicit effort override', async () => {
  const efforts = [{ id: 'low', label: 'Low' }, { id: 'high', label: 'High' }];
  const nativeModel = 'foreign-native';
  const offered = [
    { ...agents[0], models: [{ ...agents[0].models[0], efforts, defaultEffort: 'high' }] },
    { ...agents[1], models: [{
      id: nativeModel, label: 'Foreign native', isDefault: true, efforts, defaultEffort: 'low',
    }] },
  ];
  const run = fixture({
    metadata: () => Response.json({
      agents: offered,
      roleDefaults: {
        agent: 'codex', model: 'a', effort: 'high', available: true,
        choices: { effort: { value: 'high', source: 'host role' } },
      },
      executions: [],
    }),
  });
  const view = render(<RoleChatsPane active tasks={tasks} />);
  const effortControl = () => view.getByLabelText('Role effort override') as HTMLSelectElement;

  await waitFor(() => expect(effortControl().options[0].text).toBe('High · default'));
  fireEvent.change(view.getByLabelText('Role agent override'), { target: { value: 'claude' } });
  await waitFor(() => expect(start(view).disabled).toBe(false));
  expect(effortControl().options[0].text).toBe('Low · default');
  expect(effortControl().title).toBe('Low · default — effort: Low · host default');
  fireEvent.click(start(view));
  await waitFor(() => expect(run.starts[0]?.agentOverride).toEqual({ agentId: 'claude' }));

  await waitFor(() => expect(start(view).disabled).toBe(false));
  fireEvent.change(view.getByLabelText('Role model override'), { target: { value: nativeModel } });
  fireEvent.change(effortControl(), { target: { value: 'high' } });
  expect(effortControl().value).toBe('high');
  expect(effortControl().title).toBe('High — effort: Low · host default');
  fireEvent.click(start(view));
  await waitFor(() => expect(run.starts[1]?.agentOverride).toEqual({
    agentId: 'claude', modelId: nativeModel, effortId: 'high',
  }));
});
