// The subject heading's short title (MIK-R89 v2 rule 1, d9a1ec8f; mockup shots/selected-1920.png):
// a record carries no short title of its own, so the heading shows its statement's lead clause, up to
// the first dash, semicolon or colon. The full title stays the heading's tooltip and the statement
// below holds the whole text. A lead shorter than MIN_LEAD names nothing, so the title stays whole.
const BREAK = /\s[—–]\s|\s--\s|;\s|:\s/;
const MIN_LEAD = 12;

export function leadClause(title: string): string {
  const cut = BREAK.exec(title);
  if (!cut || cut.index < MIN_LEAD) return title;
  return title.slice(0, cut.index).trimEnd();
}
