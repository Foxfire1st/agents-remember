// An outline of the rendered document. Navigation only; cited code replaces this pane.
import { useEffect, useRef, useState, type RefObject } from 'react';
import { css } from '../../../styled-system/css';
interface Chapter {
  id: string;
  title: string;
  level: number;
}
const headingSelector = 'h2[id], h3[id], h4[id], h5[id], h6[id], [data-page-section]';

function chaptersOf(document: HTMLElement) {
  const found = [...document.querySelectorAll<HTMLElement>(headingSelector)];
  const rows = found.map((heading) => ({
    id: heading.id || heading.parentElement?.id || '',
    title: heading.textContent ?? '',
    level: Number(heading.tagName.slice(1)),
  }));
  return { found, rows };
}
function signatureOf(rows: Chapter[]) {
  return rows.map((row) => `${row.level}\u0000${row.id}\u0000${row.title}`).join('\n');
}
function currentChapter(document: HTMLElement, found: HTMLElement[], rows: Chapter[]) {
  const top = document.getBoundingClientRect().top + 48;
  let id = rows[0]?.id ?? '';
  for (let index = 0; index < found.length; index++) {
    if (found[index].getBoundingClientRect().top > top) break;
    id = rows[index].id;
  }
  return id;
}
export function ReaderOutline({
  pane,
  ready,
}: {
  pane: RefObject<HTMLDivElement | null>;
  ready: string;
}) {
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const [current, setCurrent] = useState('');
  const viewport = useRef<HTMLElement>(null);
  useEffect(() => {
    const document = pane.current;
    if (!document || !ready) return;
    let found: HTMLElement[] = [];
    let rows: Chapter[] = [];
    // Nothing enumerated yet: the first pass of a new pane must commit its real list, even when
    // the pane renders no chapter at all. '' is a real empty signature, so it cannot mean "unset".
    let seen: string | null = null;
    // The chapters are the ones rendered, whatever rendered them: whole prose replaces a
    // summary in place (PathViews), so this list follows the pane instead of the address.
    const enumerate = () => {
      ({ found, rows } = chaptersOf(document));
      const signature = signatureOf(rows);
      if (signature === seen) return false;
      seen = signature;
      setChapters(rows.filter((row) => row.id));
      return true;
    };
    const mark = () => setCurrent(currentChapter(document, found, rows));
    enumerate();
    mark();
    document.addEventListener('scroll', mark, { passive: true });
    const changes = new MutationObserver(() => {
      if (enumerate()) mark();
    });
    changes.observe(document, { childList: true, characterData: true, subtree: true });
    return () => {
      changes.disconnect();
      document.removeEventListener('scroll', mark);
    };
  }, [pane, ready]);
  useEffect(() => revealCurrentChapter(viewport.current), [current, chapters]);
  return (
    <nav
      ref={viewport}
      aria-label="On this page"
      data-testid="reader-outline"
      className={css({ height: '100%', overflow: 'auto', fontSize: '0.75rem', padding: '0.5rem' })}
    >
      <strong>On this page</strong>
      {chapters.map((chapter) => (
        <button
          key={chapter.id}
          type="button"
          aria-current={chapter.id === current ? 'location' : undefined}
          className={css({
            display: 'block',
            width: '100%',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            textAlign: 'left',
            font: 'inherit',
            background: 'transparent',
            border: '0',
            color: 'muted',
            padding: '0.2rem 0',
            cursor: 'pointer',
            '&[aria-current]': { color: 'amber' },
          })}
          style={{ paddingLeft: chapter.level > 2 ? '0.6rem' : '0' }}
          title={chapter.title}
          onClick={() =>
            window.document.getElementById(chapter.id)?.scrollIntoView({ block: 'start' })
          }
        >
          {chapter.title}
        </button>
      ))}
    </nav>
  );
}

function revealCurrentChapter(outline: HTMLElement | null) {
  const selected = outline?.querySelector<HTMLElement>('[aria-current]');
  if (!outline?.clientHeight || !selected) return;
  const bounds = outline.getBoundingClientRect();
  const row = selected.getBoundingClientRect();
  if (row.top < bounds.top) outline.scrollTop += row.top - bounds.top;
  else if (row.bottom > bounds.bottom) outline.scrollTop += row.bottom - bounds.bottom;
}
