// Cyan charge rising bottom-up INSIDE an outline (note 08 progress grammar). Reused by the
// detail panel (task-step progress) now and the engine room (provider seed progress) in 5c.
export function ProgressFill({
  completed,
  total,
  label,
}: {
  completed: number;
  total: number;
  label?: string;
}) {
  const pct = total > 0 ? Math.min(100, Math.round((completed / total) * 100)) : 0;
  return (
    <div className="fill" role="img" aria-label={label ?? `${pct}% complete`}>
      <div className="fill__level" style={{ height: `${pct}%` }} />
      <span className="fill__pct">
        {completed}/{total}
      </span>
    </div>
  );
}
