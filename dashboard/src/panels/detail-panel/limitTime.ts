// The exact served instant, written with its date, its seconds, its fractional seconds and the
// reader's time zone. Nothing here reads a provider limit for itself: the value is the server's
// `until` / `resetsAt`, shown as served. Seconds are kept because the served moments are exact: two
// resets a minute apart are two different instants, and a detail that showed the same time for both
// would be a false one. Milliseconds are kept for the same reason at the scale the producer sends:
// `.001000Z` and `.999000Z` are two instants inside one second. A served value finer than a
// millisecond is not thrown away either — it is written verbatim beside the localized time, so the
// producer's own precision is never rounded off by this formatter.
const LIMIT_TIME = new Intl.DateTimeFormat(undefined, {
  day: 'numeric',
  fractionalSecondDigits: 3,
  hour: '2-digit',
  hour12: false,
  minute: '2-digit',
  month: 'short',
  second: '2-digit',
  timeZoneName: 'short',
  year: 'numeric',
});

// A fractional part longer than three digits: the part past the millisecond is the producer's own
// remainder. Trailing zeros there are not a difference worth showing; anything else is.
const SUB_MILLISECOND = /\.(\d{4,})(?=Z|[+-]\d{2}:?\d{2}$|$)/;

export function formatLimitTime(iso: string): string {
  const at = Date.parse(iso);
  if (Number.isNaN(at)) return iso;
  const shown = LIMIT_TIME.format(at);
  const finer = SUB_MILLISECOND.exec(iso);
  return finer !== null && /[1-9]/.test(finer[1].slice(3)) ? `${shown} (served ${iso})` : shown;
}
