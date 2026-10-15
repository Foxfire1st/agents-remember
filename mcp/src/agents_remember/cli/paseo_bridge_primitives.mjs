// Shared bridge value predicates: one owner.
export function nonEmpty(value) {
  return typeof value === 'string' && value.trim() ? value : null
}

export function serverIdOf(daemon) {
  return daemon.getLastServerInfoMessage().serverId
}
