// R06 (one-sided knowledge statements) at the real renderer: the Intent Reviewer surface over the
// Synthetic component cases for the tree wire's three statement states. Backend producer cases
// live in test_knowledge_review_one_sided_statements.py; no unsupported binary state is retained.
import { cleanup, render } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import type { ReviewSideContent } from '../../data/review';
import { KnowledgeStatements } from './KnowledgeStatements';

const present = (text: string): ReviewSideContent => ({ state: 'present', text, language: 'text', detail: 'Recorded statement' });
const absent = (detail: string): ReviewSideContent => ({ state: 'absent', language: 'text', detail });
const unresolved = (detail: string): ReviewSideContent => ({ state: 'unresolved', language: 'text', detail });
afterEach(cleanup);

it('draws the full added statement beside its named absence', async () => {
  const view = render(<KnowledgeStatements before={absent('Not recorded before')} after={present('Full added statement')} />);
  expect(view.getByTestId('review-before-state').textContent).toContain('Not recorded before');
  expect((await view.findByTestId('diff-pane')).textContent).toContain('Full added statement');
});
it('draws the full removed statement beside its named absence', async () => {
  const view = render(<KnowledgeStatements before={present('Full removed statement')} after={absent('Not recorded after')} />);
  expect(view.getByTestId('review-after-state').textContent).toContain('Not recorded after');
  expect((await view.findByTestId('diff-pane')).textContent).toContain('Full removed statement');
});
it('keeps both present statements', async () => {
  const view = render(<KnowledgeStatements before={present('Before wording')} after={present('After wording')} />);
  const pane = await view.findByTestId('diff-pane');
  expect(pane.textContent).toContain('Before wording');
  expect(pane.textContent).toContain('After wording');
  expect(view.queryByTestId('review-before-state')).toBeNull();
});
it('keeps available prose with the unreadable sides reason, without claiming a diff', async () => {
  const view = render(<KnowledgeStatements before={present('Still readable')} after={unresolved('After file cannot be read')} />);
  expect(view.getByTestId('review-after-state').textContent).toContain('After file cannot be read');
  expect((await view.findByTestId('file-pane')).textContent).toContain('Still readable');
  expect(view.getByTestId('review-no-diff-claimed')).toBeTruthy();
  expect(view.queryByTestId('diff-pane')).toBeNull();
});
it.each([absent('Known absent'), unresolved('Unreadable file')])('names $state distinctly when neither side is present', (side) => {
  const view = render(<KnowledgeStatements before={side} after={absent('Absent after')} />);
  expect(view.getByTestId('review-before-state').dataset.sideState).toBe(side.state);
  expect(view.getByTestId('review-after-state').dataset.sideState).toBe('absent');
  expect(view.queryByTestId('diff-pane')).toBeNull();
  expect(view.queryByTestId('file-pane')).toBeNull();
});
