import { nonEmpty, serverIdOf } from './paseo_bridge_primitives.mjs'

const USAGE_REFRESH_AGE_MS = 60000

export async function readUsageReports({ daemon }, input, errorText) {
  const agentIds = Array.isArray(input.agentIds) ? input.agentIds.filter(nonEmpty) : []
  const leafAgentIds = new Set(Array.isArray(input.leafAgentIds) ? input.leafAgentIds : [])
  const readAgent = cachedAgentReader(daemon, errorText)
  const [stamped, due] = await Promise.all([
    Promise.all(agentIds.map(readAgent)),
    Promise.all(Object.entries(input.periodicCandidates ?? {}).map(
      ([reportId, candidates]) => choosePeriodicAgent(reportId, candidates, readAgent)
    ))
  ])
  const rows = new Map(stamped.map((row) => [row.agentId, row]))
  const periodicAgents = new Set()
  for (const target of due) {
    if (!target.entry) continue
    rows.set(target.row.agentId, target.row)
    periodicAgents.add(target.row.agentId)
  }
  const refreshAgents = new Set([...leafAgentIds, ...periodicAgents])
  const refreshes = await fetchRefreshes(daemon, rows, refreshAgents, errorText)
  const overrides = periodicEntries(due, rows, periodicAgents, refreshes)
  return {
    serverId: serverIdOf(daemon),
    reports: [...rows.values()].map(
      (row) => usageRow(row, refreshes, overrides, refreshAgents.has(row.agentId))
    ),
    periodic: due.map((target) => periodicResult(target, refreshes))
  }
}

function cachedAgentReader(daemon, errorText) {
  const reads = new Map()
  return function readAgent(agentId) {
    if (!reads.has(agentId)) reads.set(agentId, readCachedUsage(daemon, agentId, errorText))
    return reads.get(agentId)
  }
}

async function readCachedUsage(daemon, agentId, errorText) {
  try {
    const answer = await daemon.listUsageReports({ agentId })
    if (!Array.isArray(answer?.reports)) return { agentId, error: 'the host returned no usage list' }
    const entries = answer.reports
    const byId = new Map(
      entries.filter((entry) => nonEmpty(entry?.id)).map((entry) => [entry.id, entry])
    )
    if (entries.length && !byId.size) return { agentId, error: 'the host returned no usage report IDs' }
    return { agentId, entries, byId }
  } catch (error) {
    const detail = errorText(error?.message ?? error) || 'usage read failed'
    return { agentId, error: detail, unknown: unknownAgent(detail, agentId) }
  }
}

function unknownAgent(detail, agentId) {
  const reason = detail.replace(/^Request failed:\s*/, '')
  return /^Unknown agent(?:$|:| requestType=)/.test(reason) || reason === `Unknown agent ${agentId}`
}

async function choosePeriodicAgent(reportId, candidates, readAgent) {
  for (const agentId of candidates) {
    const row = await readAgent(agentId)
    if (row.unknown) continue
    if (row.error) return { reportId, row, error: row.error }
    const entry = row.byId.get(reportId)
    if (entry) return { reportId, row, entry }
    return { reportId, row, error: 'the host did not return the mapped report' }
  }
  return { reportId, error: 'no mapped agent was found in the host' }
}

function staleEntry(entry, now) {
  // A forced fetch after a 429 can turn the shared entry into an error until Retry-After.
  if (entry?.report?.status === 'error' || !nonEmpty(entry?.id)) return false
  const fetchedAt = Date.parse(entry.fetchedAt ?? '')
  return Number.isFinite(fetchedAt) && now - fetchedAt > USAGE_REFRESH_AGE_MS
}

async function fetchRefreshes(daemon, rows, refreshAgents, errorText) {
  const reportIds = new Set()
  const now = Date.now()
  for (const agentId of refreshAgents) {
    for (const entry of rows.get(agentId)?.entries ?? []) {
      if (staleEntry(entry, now)) reportIds.add(entry.id)
    }
  }
  // Hold each report's one refresh result for all seats. Independent accounts share the process
  // without adding their host fetch deadlines together.
  return new Map(await Promise.all([...reportIds].map(
    async (id) => [id, await refreshUsageReport(daemon, id, errorText)]
  )))
}

async function refreshUsageReport(daemon, reportId, errorText) {
  try {
    // The client refuses a selector that includes both agentId and reportIds.
    const answer = await daemon.listUsageReports({ forceRefresh: true, reportIds: [reportId] })
    const entry = Array.isArray(answer?.reports) && answer.reports.find((row) => row?.id === reportId)
    return entry ? { entry } : { error: 'the host returned no refreshed report' }
  } catch (error) {
    return { error: errorText(error?.message ?? error) || 'usage refresh failed' }
  }
}

function periodicEntries(due, rows, periodicAgents, refreshes) {
  const entries = new Map()
  for (const agentId of periodicAgents) {
    for (const cached of rows.get(agentId).entries) {
      const fresh = refreshes.get(cached?.id)?.entry
      if (hasReportVerdict(fresh)) entries.set(cached.id, fresh)
    }
  }
  // A stamped-only seat may have listed stale data before the periodic request. Apply the
  // periodic verdict to every occurrence so the order of submitted seat rows cannot undo it.
  for (const { reportId, entry } of due) {
    if (!entry) continue
    const refresh = refreshes.get(reportId)
    const verdict = refresh?.entry ?? entry
    if (!refresh?.error && hasReportVerdict(verdict)) entries.set(reportId, verdict)
  }
  return entries
}

function hasReportVerdict(entry) {
  const status = entry?.report?.status
  return status === 'available' ||
    (status === 'error' && entry?.lastSuccessful?.report?.status === 'available')
}

function usageRow(row, refreshes, overrides, refreshAgent) {
  const { agentId, entries, error } = row
  if (error) return { agentId, error }
  const failed = new Set()
  const projected = entries.map((entry) => {
    const refresh = refreshes.get(entry?.id)
    if (refresh?.error) failed.add(entry.id)
    const agentRefresh = refreshAgent && entry?.report?.status !== 'error' ? refresh?.entry : null
    return projectUsageEntry(overrides.get(entry?.id) ?? agentRefresh ?? entry)
  })
  return {
    agentId,
    entries: projected,
    ...(failed.size ? { failedRefreshReportIds: [...failed] } : {})
  }
}

function periodicResult({ reportId, row, entry, error }, refreshes) {
  const refresh = refreshes.get(reportId)
  const status = (refresh?.entry ?? entry)?.report?.status
  const failure = error ?? refresh?.error ?? (
    status === 'available' ? null : 'usage report is unavailable or in error'
  )
  return {
    reportId,
    ...(row ? { agentId: row.agentId } : {}),
    ...(failure ? { error: failure } : {})
  }
}

// Ids and labels only: an account's own fields never leave the host.
function projectUsageEntry(entry) {
  if (entry === null || typeof entry !== 'object') return {}
  const { account, ...rest } = entry
  const label = account?.label
  return { ...rest, ...(typeof label === 'string' ? { account: { label } } : {}) }
}
