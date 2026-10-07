// Knowledge's code read stays bound to its memory selection; rendering is the File Viewer's pane.
import { css } from '../../../styled-system/css';
import type { CodeAnswer } from '../../data/knowledgeReader';
import { FilePane } from '../file-viewer/FilePane';
import { PathLink, muted } from './readerParts';

export function CodeView({ answer }: { answer: CodeAnswer }) {
  if (answer.state !== 'present')
    return (
      <p className={muted} data-testid="reader-code">
        {answer.path}: {answer.state} {answer.detail ? `— ${answer.detail}` : ''}
      </p>
    );
  const lines = answer.locator?.state === 'resolved' ? answer.locator.lines : undefined;
  return (
    <div
      data-testid="reader-code"
      data-locator={answer.locator?.state ?? 'none'}
      className={css({ height: '100%', display: 'flex', flexDirection: 'column', minHeight: '0' })}
    >
      <CodeHeader answer={answer} lines={lines} />
      <div className={css({ flex: '1', minHeight: '0' })} data-testid="reader-code-lines">
        <FilePane
          content={answer.text ?? ''}
          language={answer.language ?? 'text'}
          highlightedLines={lines}
        />
      </div>
    </div>
  );
}

function CodeHeader({ answer, lines }: { answer: CodeAnswer; lines?: [number, number] }) {
  return (
    <>
      <p>
        <PathLink path={answer.path} />{' '}
        <span className={muted}>
          blob {answer.blob?.slice(0, 12)}
          {lines ? ` · lines ${lines[0]}–${lines[1]}` : ''}
        </span>
      </p>
      {answer.locator?.state === 'unresolved' ? (
        <p className={muted} data-testid="locator-unresolved">
          The locator does not resolve here: {answer.locator.detail}
        </p>
      ) : null}
    </>
  );
}
