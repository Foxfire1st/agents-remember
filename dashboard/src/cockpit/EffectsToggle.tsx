import { useState } from 'react';

import { effectsToggle } from './layout';

// A visible motion toggle. Flips html[data-effects] (which useShouldAnimate reads live, so the
// engine-room backdrop + all gated motion respond at once) and persists the choice to the
// `calm-cockpit` localStorage flag main.tsx reads on the next load. Default = effects on.
export function EffectsToggle() {
  const [on, setOn] = useState(
    () => typeof document === 'undefined' || document.documentElement.dataset.effects !== 'off',
  );
  const toggle = () => {
    const next = !on;
    setOn(next);
    if (next) {
      delete document.documentElement.dataset.effects;
      window.localStorage.removeItem('calm-cockpit');
    } else {
      document.documentElement.dataset.effects = 'off';
      window.localStorage.setItem('calm-cockpit', '1');
    }
  };
  return (
    <button
      type="button"
      className={effectsToggle({ on })}
      onClick={toggle}
      aria-pressed={on}
      data-testid="effects-toggle"
      title={
        on
          ? 'Effects on — click to calm (freeze motion + backdrop)'
          : 'Calm — click to enable motion + backdrop'
      }
    >
      {on ? '✦ Effects' : '❄ Calm'}
    </button>
  );
}
