// The identity of a pick's object (MIK-R188 rule 1: a second ⊕ on the same object adds nothing).
import { blockKey } from '../panels/review/blockIdentity';
import type { TrayPickInput, TrayReference } from './types';

function referenceKey(reference: TrayReference): string {
  switch (reference.kind) {
    case 'invariant':
    case 'family':
      return [reference.kind, reference.repository, reference.id, reference.revisionId ?? '', reference.memoryTree].join('\u0000');
    case 'review-block':
      return ['review-block', reference.comparison.repository, reference.comparison.number, blockKey(reference.block)].join('\u0000');
    case 'code-without-invariant': {
      // A hunk names its trees; a whole file names its comparison's leaf and four trees, so the same
      // path in another leaf or another snapshot at the same number is another object (rules 1 and 2).
      const { comparison } = reference;
      const { trees } = comparison;
      const part =
        reference.hunk.kind === 'file'
          ? ['file', reference.hunk.path, comparison.task, comparison.leaf, trees.codeBase, trees.codeCandidate, trees.memoryBase, trees.memoryCandidate].join('\u0000')
          : blockKey(reference.hunk);
      return ['code-without-invariant', comparison.repository, comparison.number, part].join('\u0000');
    }
    case 'task':
      return ['task', reference.reference.repository, reference.reference.path].join('\u0000');
    case 'file-line':
      return ['file-line', reference.repository, reference.scope, reference.path, reference.start, reference.end].join('\u0000');
    case 'change-set':
      return ['change-set', reference.comparison.repository, reference.comparison.task, reference.comparison.leaf, reference.comparison.number].join('\u0000');
  }
}

export function pickKey(input: TrayPickInput): string {
  if (input.kind === 'reference') return `ref\u0000${referenceKey(input.reference)}`;
  if (input.kind === 'image') return `image\u0000${input.sha256}`;
  const source = input.provenance.source === 'chat'
    ? `chat\u0000${input.provenance.agentId}\u0000${input.provenance.messageId ?? ''}`
    : `panel\u0000${input.provenance.panel}`;
  return `text\u0000${source}\u0000${input.text}`;
}
