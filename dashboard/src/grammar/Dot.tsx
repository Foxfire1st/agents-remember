// State/severity carried by colour, never chrome (note 08). `variant` maps to a `dot--*`
// class: lifecycle state (running/blocked/paused/…) or attention severity (alarm/warn/info).
export function Dot({ variant }: { variant: string }) {
  return <span className={`dot dot--${variant}`} aria-hidden="true" />;
}
