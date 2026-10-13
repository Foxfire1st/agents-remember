// L127-C-F04: register content is conserved per form. Canonical registers
// stand in fixed slots whatever the author order; only the sprint consumes
// them into tables; a register's own view keeps every section byte; an
// intro-only decisions section keeps its text; prose-only "register" sections
// stay Further notes.
import { fireEvent, render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { DetailPanel } from './DetailPanel';
import { registerTables } from './model';
import { seedTaskDocuments, taskDoc } from './test-utils';

function tableSection(
  heading: string,
  rows: string[][],
): {
  kind: 'freeform';
  heading: string;
  body: string;
} {
  const head = `| ${rows[0].map((_, i) => `c${i + 1}`).join(' | ')} |`;
  const rule = `| ${rows[0].map(() => '---').join(' | ')} |`;
  const body = rows.map((row) => `| ${row.join(' | ')} |`).join('\n');
  return { kind: 'freeform', heading, body: `${head}\n${rule}\n${body}` };
}

describe('register conservation (L127-C-F04)', () => {
  it('stands canonical registers in fixed slots independent of author order', () => {
    const tables = registerTables([
      tableSection('Priority Register', [['p1', 'x']]),
      tableSection('Judgment Register', [['j1', 'y']]),
    ]);

    expect(tables.map((table) => table.heading)).toEqual([
      'Judgment Register',
      'Priority Register',
    ]);
  });

  it('shows the judgment register first on a sprint written priority-first', () => {
    const docPath = '/tasks/repo-a/sprint/task.json';
    seedTaskDocuments([
      taskDoc({
        kind: 'master',
        form: 'sprint',
        orchestrates: ['master-a'],
        docPath,
        sections: [
          {
            kind: 'freeform',
            heading: 'Priority Register',
            body: 'Priority prose.\n\n| item | state |\n| --- | --- |\n| p1 | open |',
          },
          {
            kind: 'freeform',
            heading: 'Judgment Register',
            body: 'Judgment prose.\n\n| item | state |\n| --- | --- |\n| j1 | shut |',
          },
        ],
      }),
    ]);
    const { getByTestId } = render(<DetailPanel selectedId={`taskdoc:${docPath}`} />);

    expect(getByTestId('register-head-0').textContent).toContain('Judgment Register');
    expect(getByTestId('register-head-1').textContent).toContain('Priority Register');
  });

  it("exposes a one-row table's surrounding prose one link away", () => {
    // L127-C-F04 residual: short tables keep their prose behind the link too.
    const docPath = '/tasks/repo-a/sprint/task.json';
    seedTaskDocuments([
      taskDoc({
        kind: 'master',
        form: 'sprint',
        orchestrates: ['master-a'],
        docPath,
        sections: [
          {
            kind: 'freeform',
            heading: 'Judgment Register',
            body: 'BEFORE-TABLE prose.\n\n| item | state |\n| --- | --- |\n| j1 | shut |\n\nAFTER-TABLE prose.',
          },
        ],
      }),
    ]);
    const { getByTestId, getByText } = render(<DetailPanel selectedId={`taskdoc:${docPath}`} />);

    fireEvent.click(getByTestId('register-all-link-0'));
    expect(getByTestId('doc-view-title').textContent).toBe('Judgment Register');
    expect(getByText(/BEFORE-TABLE/)).toBeTruthy();
    expect(getByText(/AFTER-TABLE/)).toBeTruthy();
  });

  it('keeps every section byte one link away in a sprint register view', () => {
    const docPath = '/tasks/repo-a/sprint/task.json';
    seedTaskDocuments([
      taskDoc({
        kind: 'master',
        form: 'sprint',
        orchestrates: ['master-a'],
        docPath,
        sections: [
          {
            kind: 'freeform',
            heading: 'Judgment Register',
            body:
              'Why this shut.\n\n| item | state |\n| --- | --- |\n' +
              ['j1', 'j2', 'j3', 'j4', 'j5', 'j6'].map((j) => `| ${j} | shut |`).join('\n'),
          },
        ],
      }),
    ]);
    const { getByTestId, getByText } = render(<DetailPanel selectedId={`taskdoc:${docPath}`} />);

    fireEvent.click(getByTestId('register-all-link-0'));
    expect(getByTestId('doc-view-title').textContent).toBe('Judgment Register');
    // The surrounding prose and the table both survive the link.
    expect(getByText(/Why this shut/)).toBeTruthy();
    expect(getByText(/j1/)).toBeTruthy();
  });

  it("leaves a master's register section whole in Further notes", () => {
    const docPath = '/tasks/repo-a/m/task.json';
    seedTaskDocuments([
      taskDoc({
        kind: 'master',
        docPath,
        sections: [
          {
            kind: 'freeform',
            heading: 'Judgment Register',
            body: 'Master prose.\n\n| item | state |\n| --- | --- |\n| j1 | shut |',
          },
        ],
      }),
    ]);
    const { container, getByTestId, getByText, queryByTestId } = render(
      <DetailPanel selectedId={`taskdoc:${docPath}`} />,
    );

    // The master draws no register tables.
    expect(
      Array.from(container.querySelectorAll('[data-block]')).map((el) =>
        el.getAttribute('data-block'),
      ),
    ).not.toContain('registers');
    expect(queryByTestId('register-head-0')).toBeNull();
    // The section stands whole one link away instead.
    expect(getByTestId('further-open-0').textContent).toContain('Judgment Register');
    fireEvent.click(getByTestId('further-open-0'));
    expect(getByText(/Master prose/)).toBeTruthy();
    expect(getByText(/j1/)).toBeTruthy();
  });

  it('keeps introducing text of an intro-only decisions section', () => {
    const docPath = '/tasks/repo-a/m/01_intro.json';
    seedTaskDocuments([
      taskDoc({
        kind: 'subTask',
        docPath,
        sections: [{ kind: 'sharedDecisions', heading: 'Decisions', body: 'No calls yet.' }],
      }),
    ]);
    const { getByTestId, queryByText } = render(<DetailPanel selectedId={`taskdoc:${docPath}`} />);

    expect(getByTestId('decisions-intro').textContent).toContain('No calls yet.');
    expect(queryByText('No decision recorded.')).toBeTruthy();
  });

  it('leaves a prose-only register section in Further notes', () => {
    const docPath = '/tasks/repo-a/sprint/task.json';
    seedTaskDocuments([
      taskDoc({
        kind: 'master',
        form: 'sprint',
        orchestrates: ['master-a'],
        docPath,
        sections: [
          { kind: 'freeform', heading: 'Priority Register', body: 'No table here, only prose.' },
        ],
      }),
    ]);
    const { container, getByTestId, queryByTestId } = render(
      <DetailPanel selectedId={`taskdoc:${docPath}`} />,
    );

    // Unparseable: no table block, and the section stays a Further note.
    expect(queryByTestId('register-head-0')).toBeNull();
    expect(
      Array.from(container.querySelectorAll('[data-block]')).map((el) =>
        el.getAttribute('data-block'),
      ),
    ).not.toContain('registers');
    expect(getByTestId('further-open-0').textContent).toContain('Priority Register');
  });
});
