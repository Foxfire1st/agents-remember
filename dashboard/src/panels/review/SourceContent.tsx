import { useEffect, useState } from 'react';

import type {
  ReviewChangedFile,
  ReviewFailure,
  ReviewRefusal,
  ReviewSourceContentResult,
  ReviewSourceExpansion,
  ReviewSourceSide,
} from '../../data/review';
import { reviewProblemFromCause, reviewSourceContent } from '../../data/review';
import { ReviewProblemBlock } from './ReviewOutcome';
import { DiffPane, type DiffMode } from '../changeset/DiffPane';
import { FilePane } from '../file-viewer/FilePane';

const textual = (side: ReviewSourceSide) => side.text !== undefined;

const mutedStyle = { color: 'var(--muted)', margin: '0.2rem 0' } as const;

function sideLine(side: ReviewSourceSide, name: 'before' | 'after') {
  const facts = [
    side.object_id ? `object ${side.object_id}` : null,
    side.byte_length === undefined ? null : `${side.byte_length} byte(s)`,
  ].filter((fact): fact is string => fact !== null);
  const needsDetail = side.state !== 'present' || side.truncated;
  return (
    <div
      style={mutedStyle}
      data-testid={`review-source-${name}-state`}
      data-side-state={side.state}
      data-side-truncated={side.truncated ? 'true' : 'false'}
    >
      {name} ({side.state})
      <details>
        <summary>Object identity</summary>
        {facts.join(' · ')}
      </details>
      {needsDetail ? ` — ${side.detail}` : ''}
    </div>
  );
}

function contentBlock(side: ReviewSourceSide, name: 'before' | 'after', language: string) {
  return (
    <div
      data-testid={`review-source-${name}-content`}
      style={{ height: '26rem', maxHeight: '60vh', minHeight: '12rem' }}
    >
      <FilePane content={side.text ?? ''} language={language} />
    </div>
  );
}

function Sides({
  expansion,
  mode,
  collapse,
}: {
  expansion: ReviewSourceExpansion;
  mode: DiffMode;
  collapse: boolean;
}) {
  const { before, after } = expansion;
  const lines = (
    <>
      {sideLine(before, 'before')}
      {sideLine(after, 'after')}
    </>
  );
  if (before.state === 'present' && after.state === 'present') {
    return (
      <>
        {lines}
        <div style={{ height: '26rem', maxHeight: '60vh', minHeight: '12rem' }}>
          <DiffPane
            before={before.text ?? ''}
            after={after.text ?? ''}
            language={expansion.language}
            mode={mode}
            collapse={collapse}
          />
        </div>
      </>
    );
  }
  if (!textual(before) && !textual(after)) return lines;
  return (
    <>
      {lines}
      <p style={mutedStyle} data-testid="review-source-no-diff-claimed">
        no diff is drawn: at least one side is not a regular file's text, so an addition or a change
        cannot be claimed from the two sides beside it.
      </p>
      {textual(before) ? contentBlock(before, 'before', expansion.language) : null}
      {textual(after) ? contentBlock(after, 'after', expansion.language) : null}
    </>
  );
}

function boundedNote(expansion: ReviewSourceExpansion) {
  const truncated = (['before', 'after'] as const).filter((name) => expansion[name].truncated);
  if (!truncated.length) return null;
  return (
    <p style={mutedStyle} data-testid="review-source-truncated">
      bounded expansion: the {truncated.join(' and ')} text above is a stated prefix of the object,
      not the whole of it; the object's exact identity and size are named on its own line and the
      reproduction below reaches the rest.
    </p>
  );
}

function refusalBlock(refusal: ReviewRefusal) {
  return (
    <div data-testid="review-source-refusal">
      <p style={{ margin: '0.2rem 0' }}>
        this entry's content could not be opened ({refusal.code}): {refusal.detail}
      </p>
      <p style={mutedStyle}>next: {refusal.next_action}</p>
      {refusal.offending_input ? (
        <p style={mutedStyle}>offending input: {refusal.offending_input}</p>
      ) : null}
    </div>
  );
}

function Expansion({
  expansion,
  mode,
  collapse,
}: {
  expansion: ReviewSourceExpansion;
  mode: DiffMode;
  collapse: boolean;
}) {
  return (
    <div data-testid="review-source-expansion" data-currentness={expansion.currentness}>
      <details>
        <summary>Source generation and provenance</summary>
        <p style={mutedStyle} data-testid="review-source-currentness">
          {expansion.currentness}: {expansion.currentness_detail}
        </p>
        <p style={mutedStyle} data-testid="review-source-generation">
          generation {expansion.before_code_tree_id} → {expansion.after_code_tree_id} ·{' '}
          {expansion.status}
          {expansion.mode_change ? ' · mode changed' : ''}
        </p>
        {expansion.path_bound === 'leaf_change_set' ? (
          <p style={mutedStyle} data-testid="review-source-path-bound">
            path admitted by: {expansion.path_bound_detail}
          </p>
        ) : null}
        <p style={{ ...mutedStyle, whiteSpace: 'pre-wrap' }} data-testid="review-source-command">
          reproduce: {expansion.command}
        </p>
      </details>
      {expansion.currentness !== 'current' ? (
        <p style={mutedStyle}>
          {expansion.currentness}: {expansion.currentness_detail}
        </p>
      ) : null}
      <Sides expansion={expansion} mode={mode} collapse={collapse} />
      {boundedNote(expansion)}
    </div>
  );
}

export function SourceContent({
  repo,
  master,
  leaf,
  entry,
  beforeCodeTreeId,
  afterCodeTreeId,
  mode = 'split',
  collapse = false,
}: {
  repo: string;
  master: string;
  leaf: string;
  entry: ReviewChangedFile;
  beforeCodeTreeId: string;
  afterCodeTreeId: string;
  mode?: DiffMode;
  collapse?: boolean;
}) {
  const [result, setResult] = useState<ReviewSourceContentResult | null>(null);
  const [problem, setProblem] = useState<ReviewFailure | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let live = true;
    setResult(null);
    setProblem(null);
    reviewSourceContent(repo, master, leaf, entry.path, beforeCodeTreeId, afterCodeTreeId)
      .then((opened) => {
        if (live) setResult(opened);
      })
      .catch((cause: unknown) => {
        if (live) setProblem(reviewProblemFromCause(cause));
      });
    return () => {
      live = false;
    };
  }, [repo, master, leaf, entry.path, beforeCodeTreeId, afterCodeTreeId, attempt]);

  if (problem !== null) {
    return (
      <ReviewProblemBlock
        origin="failure"
        subject="this entry's content"
        problem={problem}
        onRetry={() => setAttempt((previous) => previous + 1)}
      />
    );
  }
  if (result === null) {
    return (
      <p style={mutedStyle} data-testid="review-source-loading">
        opening {entry.path} at the listed generation…
      </p>
    );
  }
  if (result.state === 'refused' && result.refusal) return refusalBlock(result.refusal);
  if (result.expansion)
    return <Expansion expansion={result.expansion} mode={mode} collapse={collapse} />;
  return null;
}
