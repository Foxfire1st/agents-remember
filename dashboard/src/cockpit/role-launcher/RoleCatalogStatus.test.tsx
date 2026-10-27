import { cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { RoleLauncher } from './RoleLauncher';
import { RoleCatalogStatus } from './RoleCatalogStatus';
import { launchChoiceProblem, type RoleAgentChoice } from '../roleLaunchModel';
import { resolveLauncherCatalog, resolveOptionsDisplay } from '../roleChatExecutionView';

afterEach(cleanup);

const unknown = { source: 'unknown', readAt: null } as const;
const fresh = { source: 'fresh', readAt: '2026-10-26T15:00:00Z' } as const;
const saved = { source: 'saved', readAt: '2026-10-26T14:00:00Z', error: 'Paseo timed out' } as const;
const agent: RoleAgentChoice = {
  id: 'codex', label: 'Codex', modelsKnown: true, discovery: { models: fresh },
  models: [{ id: 'gpt-x', label: 'GPT X', efforts: [{ id: 'high', label: 'High' }],
    serviceTiers: [{ id: 'priority', label: 'Fast' }], discovery: { efforts: fresh, serviceTiers: fresh } }],
};

describe('Paseo catalog provenance', () => {
  it('bounds the saved preview to three model labels per provider with an exact remaining count', () => {
    const manyModels = { ...agent, models: Array.from({ length: 279 }, (_, index) => ({ id: `m-${index}`, label: `Model ${index + 1}` })) };
    const view = render(<RoleCatalogStatus catalog={saved} pendingAgents={[manyModels]} origin="paseo:saved" loading className="status" />);
    expect(view.getByText(/Saved Paseo choices/).textContent).toBe('Saved Paseo choices (paseo:saved): Codex · Model 1, Model 2, Model 3 (+276 more)');
    expect(view.getByText(/Agents: saved answer from 2026-10-26T14:00:00Z/)).toBeTruthy();
    expect(view.queryByText(/Model 4/)).toBeNull();
  });
  it('matches ids or Paseo labels without hard-coded speed names', () => {
    expect(launchChoiceProblem({ agent: 'codex', model: 'gpt-x', effort: 'HIGH', serviceTier: 'fast' }, [agent], { agentId: 'CODEX' })).toBeNull();
    const renamed = { ...agent, models: [{ ...agent.models[0], serviceTiers: [{ id: 'express', label: 'Quick' }] }] };
    expect(launchChoiceProblem({ agent: 'codex', model: 'gpt-x', serviceTier: 'fast' }, [renamed], { agentId: 'codex' })).toContain('not offered');
    expect(launchChoiceProblem({ agent: 'codex', model: 'gpt-x', serviceTier: 'quick' }, [renamed], { agentId: 'codex' })).toBeNull();
  });

  it('passes unknown parts through while successful empty answers still refuse', () => {
    const selected = { agentId: 'codex', modelId: 'unlisted', effortId: 'unlisted' };
    expect(launchChoiceProblem({}, [], selected, unknown)).toBeNull();
    expect(launchChoiceProblem({}, [{ ...agent, models: [], modelsKnown: false, discovery: { models: unknown } }], selected, fresh)).toBeNull();
    expect(launchChoiceProblem({}, [{ ...agent, models: [], discovery: { models: fresh } }], selected, fresh)).toContain('not offered');
    const partial = { ...agent, models: [{ ...agent.models[0], efforts: [], serviceTiers: [], discovery: { efforts: unknown, serviceTiers: unknown } }] };
    expect(launchChoiceProblem({}, [partial], { agentId: 'codex', modelId: 'gpt-x', effortId: 'max', featureValues: { service_tier: 'ultrafast' } }, fresh)).toBeNull();
  });

  it('shows each saved part with its own timestamp and unknown values as unchecked', () => {
    const view = render(<RoleCatalogStatus catalog={fresh} agent={{ ...agent, discovery: { models: saved, modes: unknown } }}
      model={{ ...agent.models[0], discovery: { serviceTiers: { ...saved, readAt: '2026-10-26T13:00:00Z' } } }} loading={false} className="status" />);
    expect(view.getByText(/Models: saved answer from 2026-10-26T14:00:00Z/)).toBeTruthy();
    expect(view.getByText(/Speed tiers: saved answer from 2026-10-26T13:00:00Z/)).toBeTruthy();
    expect(view.getByText(/Permission modes: Paseo has not answered; configured values will be sent unchecked/)).toBeTruthy();
  });

  it('keeps the configured selection unchanged when no catalog answer exists', () => {
    const onLaunch = vi.fn().mockResolvedValue(undefined);
    const onSelectionChange = vi.fn();
    const selection = { role: 'architect' as const, agentOverride: { agentId: 'custom', modelId: 'private', effortId: 'extreme', featureValues: { service_tier: 'private-tier' } } };
    const view = render(<RoleLauncher tasks={[]} roleDefaults={{}} agents={[]} catalogDiscovery={unknown} selection={selection}
      optionsReady optionsLoading={false} busy={false} optionsError={null} executionError={null} onSelectionChange={onSelectionChange}
      onLaunch={onLaunch} onRevive={vi.fn()} onRetry={vi.fn()} onRefreshOptions={vi.fn()} onRefreshCatalog={vi.fn()} onRefreshResult={vi.fn()} />);
    fireEvent.click(view.getByRole('button', { name: 'Start role' }));
    expect(onLaunch).toHaveBeenCalledWith(selection);
    expect(onSelectionChange).not.toHaveBeenCalled();
  });

  it('uses only the current server answer, including a known empty replacement', () => {
    const oldAnswer = resolveLauncherCatalog({ roleDefaults: { agent: 'codex' }, agents: [agent] }, false);
    expect(oldAnswer.agents).toHaveLength(1);
    const nextAnswer = resolveLauncherCatalog({ roleDefaults: {}, agents: [], catalogDiscovery: fresh }, false);
    expect(nextAnswer.agents).toEqual([]);
    expect(nextAnswer.roleDefaults).toEqual({});
  });
});


describe('sealed launcher regressions', () => {
  it('R4 sends a configured tier unchecked when the native default model is unresolved', () => {
    const defaults = { agent: 'codex', serviceTier: 'priority' };
    const selection = { role: 'architect' as const, agentOverride: { agentId: 'codex' } };
    const onLaunch = vi.fn().mockResolvedValue(undefined);
    expect(launchChoiceProblem(defaults, [agent], selection.agentOverride, fresh)).toBeNull();
    const view = render(<RoleLauncher tasks={[]} roleDefaults={defaults} agents={[agent]} catalogDiscovery={fresh} selection={selection}
      optionsReady optionsLoading={false} busy={false} optionsError={null} executionError={null} onSelectionChange={vi.fn()}
      onLaunch={onLaunch} onRevive={vi.fn()} onRetry={vi.fn()} onRefreshOptions={vi.fn()} onRefreshCatalog={vi.fn()} onRefreshResult={vi.fn()} />);
    fireEvent.click(view.getByRole('button', { name: 'Start role' }));
    expect(onLaunch).toHaveBeenCalledWith(selection);
  });

  it.each(['agent change', 'same-scope refresh'])('R5 shows only the timestamped disk snapshot during %s', (change) => {
    const scope = { role: 'architect' as const, agentId: 'codex' };
    const nextScope = change === 'agent change' ? { ...scope, agentId: 'claude' } : scope;
    const oldAnswer = { roleDefaults: { agent: 'codex' }, agents: [agent], catalogDiscovery: fresh, catalogOrigin: 'paseo:old' };
    const disk = { agents: [{ id: 'claude', label: 'Disk Claude', models: [] }], catalogDiscovery: saved, catalogOrigin: 'paseo:disk' };
    const display = resolveOptionsDisplay({ scope, value: oldAnswer }, nextScope, nextScope);
    const catalog = resolveLauncherCatalog(display.options, display.optionsLoading, disk);
    expect(display.options).toBeNull();
    expect(catalog.roleDefaults).toEqual({});
    expect(catalog.optionsReady).toBe(false);
    expect(catalog.agents).toEqual(disk.agents);
    const view = render(<RoleCatalogStatus catalog={catalog.catalogDiscovery} pendingAgents={catalog.agents}
      origin={catalog.catalogOrigin} loading={display.optionsLoading} className="status" />);
    expect(view.getByText(/Saved Paseo choices/).textContent).toBe('Saved Paseo choices (paseo:disk): Disk Claude');
    expect(view.getByText(/Agents: saved answer from 2026-10-26T14:00:00Z/)).toBeTruthy();
    expect(view.queryByText(/paseo:old/)).toBeNull();
    expect(resolveLauncherCatalog(oldAnswer, true, disk).agents).toEqual(disk.agents);
    const withoutDisk = resolveLauncherCatalog(display.options, display.optionsLoading);
    expect(withoutDisk.agents).toEqual([]);
    expect(withoutDisk.optionsReady).toBe(false);
  });
});
