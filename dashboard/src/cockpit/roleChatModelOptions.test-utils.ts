import type { RoleAgentChoice } from './roleLaunchModel';

export function launcherModelReply(request: Record<string, unknown>, catalog: unknown): Response {
  const { agentId, modelId } = request;
  const agents = (catalog as { agents?: RoleAgentChoice[] } | undefined)?.agents;
  const model = agents?.find((row) => row.id === agentId)?.models.find((row) => row.id === modelId);
  const fresh = { source: 'fresh', readAt: null };
  return Response.json({
    modelOptions: {
      agentId,
      modelId,
      features: model?.features ?? [],
      serviceTiers: model?.serviceTiers ?? [],
      discovery: { features: fresh, serviceTiers: fresh },
    },
  });
}
