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
