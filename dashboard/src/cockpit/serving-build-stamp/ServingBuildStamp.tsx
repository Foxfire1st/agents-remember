import { clientMatchesServingBuild, servingCommitLabel } from '../../data/buildIdentity';
import { humanizeDuration } from '../../data/conversation/format';
import { useDashboard } from '../../data/store';
import type { BuildCurrentness, ServingBuild } from '../../types/projection';
import { caution, dim } from '../layout';

function currentnessDetails(currentness: BuildCurrentness | null): string {
  if (!currentness) return '';
  return [
    `${currentness.mode} build ${currentness.state}`,
    currentness.loadedDigest && `loaded Python digest ${currentness.loadedDigest}`,
    currentness.installedDigest && `installed Python digest ${currentness.installedDigest}`,
    currentness.firstSeenAt && `difference first seen ${currentness.firstSeenAt}`,
    currentness.reason,
    currentness.remedy,
  ]
    .filter(Boolean)
    .join(' · ');
}

function servingTitle(
  build: ServingBuild,
  currentness: BuildCurrentness | null,
  processId: number | null,
): string {
  const commitLabel = servingCommitLabel(build);
  const clientMismatchNote =
    clientMatchesServingBuild(build) === false
      ? ' · client bundle differs from the serving build — reload to update'
      : '';
  const details = currentnessDetails(currentness);
  return `Serving build v${build.version}${commitLabel ? ` @ ${commitLabel}` : ''}${build.dirty ? ' (dirty — serving uncommitted code)' : ''} · process up since ${build.bootedAt}${processId === null ? '' : ` · process ${processId}`}${details ? ` · ${details}` : ''}${clientMismatchNote}`;
}

/** The immutable boot identity, current Python files and client-bundle identity stay distinct. */
export function ServingBuildStamp() {
  const build = useDashboard((state) => state.servingBuild);
  const currentness = useDashboard((state) => state.buildCurrentness);
  const processId = useDashboard((state) => state.servingProcessId);
  if (!build) return null;
  const clientCurrent = clientMatchesServingBuild(build);
  const commitLabel = servingCommitLabel(build);
  const booted = new Date(build.bootedAt);
  const bootLabel = Number.isNaN(booted.getTime())
    ? build.bootedAt
    : humanizeDuration(Date.now() - booted.getTime());
  const title = servingTitle(build, currentness, processId);
  return (
    <span
      className={dim}
      data-testid="serving-build"
      data-client-build-current={clientCurrent === null ? 'unknown' : String(clientCurrent)}
      data-build-currentness={currentness?.state}
      data-build-mode={currentness?.mode}
      title={title}
      aria-label={title}
    >
      {commitLabel ?? `v${build.version}`} · up {bootLabel}
      {currentness && (
        <span className={currentness.state === 'stale' ? caution({ sev: 'warn' }) : undefined}>
          {' · '}
          {currentness.mode === 'checkout' ? 'checkout · ' : ''}
          {currentness.state}
        </span>
      )}
    </span>
  );
}
