// Selection helpers for the Change-Set Viewer: the media selector for the open side and the
// changed-file opener. Kept beside the screen so the screen stays a layout and its state.
import type { FileDiff, LeafMode, MasterNetPins } from '../../data/changeset';
import {
  comparisonFileDiff,
  fileDiff,
  leafFileDiff,
  masterFileDiff,
} from '../../data/changeset';
import type { ChangeSetMediaSelector } from './ChangeSetPane';
import type { ViewerProps } from './changeSetTarget';

export function mediaSelectorFor(
  { repo, scope, master, leaf, mode, comparison }: ViewerProps,
  generation: ViewerProps['generation'],
): ChangeSetMediaSelector {
  return { repo, scope, master, leaf, mode, comparison, generation };
}

export function diffRequestFor(
  repo: string,
  scope: string | undefined,
  master: string | undefined,
  leaf: string | undefined,
  mode: LeafMode | undefined,
  generation: MasterNetPins | undefined,
  kind: 'code' | 'memory',
  path: string,
  comparison?: number,
): Promise<FileDiff | null> {
  if (comparison !== undefined)
    return comparisonFileDiff(repo, master ?? '', leaf ?? '', comparison, kind, path);
  return leaf
    ? leafFileDiff(repo, master ?? '', leaf, kind, path, mode ?? 'committed')
    : master
      ? masterFileDiff(repo, master, kind, path, generation ?? {})
      : fileDiff(repo, scope ?? '', kind, path);
}
