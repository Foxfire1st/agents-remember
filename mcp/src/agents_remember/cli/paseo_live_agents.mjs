// The existing bounded, nonmutating live-agent listing.
import { readFileSync } from 'node:fs'
import { nonEmpty, serverIdOf } from './paseo_bridge_primitives.mjs'

const LIVE_PAGE_LIMIT = 200
const LIVE_PAGES = 10

export async function listLiveAgents({ daemon }) {
  const agentIds = []
  let cursor = null
  let complete = false
  for (let page = 0; page < LIVE_PAGES; page += 1) {
    const listed = await daemon.fetchAgents({
      // The pinned host (0.16.2) leaves background agents out of a list unless it is asked
      // for them, and this list is what the product treats as live: a seat must not be missed.
      filter: { includeBackground: true },
      page: { limit: LIVE_PAGE_LIMIT, ...(cursor ? { cursor } : {}) }
    })
    for (const entry of listed.entries ?? []) {
      const agent = entry?.agent
      if (nonEmpty(agent?.id) && !nonEmpty(agent.archivedAt)) agentIds.push(agent.id)
    }
    // The listing has ended when the runtime says that no agents follow, and only then. A page
    // without a next page ends the read, not the listing.
    complete = listed.pageInfo?.hasMore === false
    cursor = nonEmpty(listed.pageInfo?.nextCursor) ? listed.pageInfo.nextCursor : null
    if (complete || cursor === null) break
  }
  return { serverId: serverIdOf(daemon), agentIds, complete, ...await hostStart(daemon) }
}


async function hostStart(daemon) {
  try {
    const status = await daemon.getDaemonStatus()
    if (status.serverId !== serverIdOf(daemon) || !Number.isSafeInteger(status.pid) || status.pid <= 0) {
      throw new Error('the answering host has no matching process identity')
    }
    // Field 22 is the kernel birth tick. comm may contain spaces and parentheses.
    const processStat = () => {
      const stat = readFileSync(`/proc/${status.pid}/stat`, 'utf8')
      if (!stat.startsWith(`${status.pid} (`)) throw new Error('the OS named a different process')
      return stat.slice(stat.lastIndexOf(')') + 2).trim().split(/\s+/)[19]
    }
    const bootId = readFileSync('/proc/sys/kernel/random/boot_id', 'utf8').trim()
    const processStart = processStat()
    const confirmed = await daemon.getDaemonStatus()
    if (!bootId || !/^[1-9][0-9]*$/.test(processStart ?? '') || processStart !== processStat()
        || confirmed.pid !== status.pid || confirmed.serverId !== status.serverId) {
      throw new Error('the host process changed while its start was being read')
    }
    return { startIdentity: { pid: status.pid, processStart, bootId }, startDetail: null }
  } catch (error) {
    return { startIdentity: null, startDetail: `host process-start identity unavailable: ${error.message ?? error}` }
  }
}

// History enumerates unarchived agents, including those with unavailable providers.
// Exact UUID reads supply archive and absence independently of placement.
export async function readRoleRegistry({ daemon, deadline }, { agentIds }) {
  const result = agentIds === undefined
    ? await readRoleHistory(daemon, deadline)
    : await readExactRoleAgents(daemon, deadline, agentIds)
  return { ...result, serverId: serverIdOf(daemon) }
}

async function readRoleHistory(daemon, deadline) {
  const agents = []
  const cursors = new Set()
  let cursor = null
  while (Date.now() < deadline) {
    const reply = await daemon.fetchAgentHistory({
      filter: { includeArchived: false, includeBackground: true },
      page: { limit: LIVE_PAGE_LIMIT, ...(cursor ? { cursor } : {}) }
    })
    if (reply.error || !Array.isArray(reply.entries)) break
    const rows = reply.entries.map(({ agent }) => registryAgent(agent))
    if (rows.some((row) => row === null)) break
    agents.push(...rows)
    if (reply.pageInfo?.hasMore === false) return { agents, complete: true }
    const next = nonEmpty(reply.pageInfo?.nextCursor)
    if (reply.pageInfo?.hasMore !== true || next === null || cursors.has(next)) break
    cursors.add(next)
    cursor = next
  }
  return { agents, complete: false }
}

const FULL_AGENT_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/

async function readExactRoleAgents(daemon, deadline, agentIds) {
  const agents = []
  const absent = []
  let exactReads = 0
  if (!Array.isArray(agentIds) || !agentIds.every((id) => typeof id === 'string' && FULL_AGENT_ID.test(id))) {
    return { agents, absent, exactReads, complete: false }
  }
  for (const id of new Set(agentIds)) {
    const timeout = deadline - Date.now()
    if (timeout <= 0) return { agents, absent, exactReads, complete: false }
    exactReads += 1
    try {
      const reply = await daemon.fetchAgent({ agentId: id, timeout })
      if (reply === null) {
        absent.push(id)
        continue
      }
      const row = registryAgent(reply?.agent)
      if (row?.id !== id) return { agents, absent, exactReads, complete: false }
      agents.push(row)
    } catch {
      return { agents, absent, exactReads, complete: false }
    }
  }
  return { agents, absent, exactReads, complete: true }
}

function registryAgent(agent) {
  const id = nonEmpty(agent?.id)
  const archivedAt = agent?.archivedAt ?? null
  if (id === null || (archivedAt !== null && !nonEmpty(archivedAt))) return null
  return { id, archivedAt, role: nonEmpty(agent.labels?.['ar.role']) }
}
