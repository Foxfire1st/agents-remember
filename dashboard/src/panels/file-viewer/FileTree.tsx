// File API adapter for the dashboard's shared async explorer.
import { listDir, type DirEntry, type Scope } from '../../data/files';
import { ExplorerTree } from '../../grammar/ExplorerTree';

export function FileTree({
  repo,
  scope,
  side,
  onOpen,
}: {
  repo: string;
  scope: Scope;
  side: 'code' | 'onboarding';
  onOpen: (entry: DirEntry) => void;
}) {
  return (
    <ExplorerTree
      key={`${repo}\0${scope}\0${side}`}
      label={`${side} files`}
      testid={`tree-${side}`}
      root={{ name: repo, path: '', kind: 'dir' }}
      onOpen={onOpen}
      loadChildren={async (path) => {
        if (!repo) return [];
        const listing = await listDir(repo, scope, path);
        return side === 'code' ? listing.code : listing.onboarding;
      }}
      renderSuffix={(entry) =>
        side === 'code' && entry.hasSidecar ? (
          <span aria-hidden title="has onboarding">
            ◖
          </span>
        ) : null
      }
    />
  );
}
