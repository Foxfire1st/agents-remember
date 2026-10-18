// The expression cards' presentation controls do not select or classify source.
import { css } from '../../../styled-system/css';
import type { DiffMode } from '../changeset/DiffPane';

const bar = css({
  display: 'flex',
  gap: '0.5rem',
  alignItems: 'center',
  flexWrap: 'wrap',
  justifyContent: 'space-between',
  marginBottom: '0.75rem',
});
const muted = css({ color: 'muted', fontSize: '0.75rem' });

export function ExpressionControls({ layout, onLayout, fullFile, onFullFile }: {
  layout: DiffMode;
  onLayout: (next: DiffMode) => void;
  fullFile: boolean;
  onFullFile: (next: boolean) => void;
}) {
  return (
    <div className={bar}>
      <h3 className={css({ color: 'cyan', fontSize: '0.75rem', letterSpacing: '0.1em', textTransform: 'uppercase' })}>
        Code & test expressions
      </h3>
      <div className={bar} data-testid="review-center-display-controls">
        <label className={muted} htmlFor="review-center-diff-layout">Layout</label>
        <select id="review-center-diff-layout" data-testid="review-center-diff-layout" value={layout}
          onChange={(event) => onLayout(event.target.value === 'inline' ? 'inline' : 'split')}>
          <option value="split">Side by side</option>
          <option value="inline">Inline</option>
        </select>
        <button type="button" data-testid="review-center-full-file" aria-pressed={fullFile}
          onClick={() => onFullFile(!fullFile)}>
          {fullFile ? 'Show changed regions' : 'Expand full file'}
        </button>
      </div>
    </div>
  );
}
