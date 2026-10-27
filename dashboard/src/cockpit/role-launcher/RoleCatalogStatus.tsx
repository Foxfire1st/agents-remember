import type { CatalogDiscoveryState, RoleAgentChoice, RoleModelChoice } from '../roleLaunchModel';

function discoveryText(label: string, state: CatalogDiscoveryState | undefined): string | null {
  if (!state || state.source === 'fresh') return null;
  if (state.source === 'saved') {
    return `${label}: saved answer from ${state.readAt ?? 'an unknown time'}${state.error ? ` (${state.error})` : ''}.`;
  }
  return `${label}: Paseo has not answered; configured values will be sent unchecked${state.error ? ` (${state.error})` : ''}.`;
}

function agentNotices(agent: RoleAgentChoice | undefined): Array<string | null> {
  const state = agent?.discovery;
  return [discoveryText('Models', state?.models), discoveryText('Permission modes', state?.modes),
    discoveryText('AR tool server support', state?.acceptsToolServers)];
}

function modelNotices(model: RoleModelChoice | undefined): Array<string | null> {
  const state = model?.discovery;
  return [discoveryText('Effort', state?.efforts), discoveryText('Features', state?.features),
    discoveryText('Speed tiers', state?.serviceTiers)];
}

function discoveryNotices(catalog: CatalogDiscoveryState | undefined, agent: RoleAgentChoice | undefined, model: RoleModelChoice | undefined): string[] {
  return [discoveryText('Agents', catalog), ...agentNotices(agent), ...modelNotices(model)]
    .filter((notice): notice is string => notice !== null);
}

function savedChoiceLabel(choice: RoleAgentChoice): string {
  const shown = choice.models.slice(0, 3);
  const remaining = choice.models.length - shown.length;
  if (!shown.length) return choice.label;
  return `${choice.label} · ${shown.map((model) => model.label).join(', ')}${remaining ? ` (+${remaining} more)` : ''}`;
}

export function RoleCatalogStatus({ catalog, agent, model, loading, origin, pendingAgents, className }: {
  catalog?: CatalogDiscoveryState;
  agent?: RoleAgentChoice;
  model?: RoleModelChoice;
  loading: boolean;
  origin?: string;
  pendingAgents?: RoleAgentChoice[];
  className: string;
}) {
  const notices = discoveryNotices(catalog, agent, model);
  if (!notices.length && !loading) return null;
  return <div className={className} role="status" aria-label="Paseo catalog status">
    {loading ? <div>Asking Paseo for current choices. Any displayed choices are saved from the last answer.</div> : null}
    {loading && pendingAgents?.length ? <div>Saved Paseo choices ({origin ?? "saved catalog"}): {pendingAgents.map(savedChoiceLabel).join("; ")}</div> : null}
    {notices.map((notice) => <div key={notice}>{notice}</div>)}
  </div>;
}
