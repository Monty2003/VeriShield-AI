/**
 * Display preferences.
 *
 * These were previously three useState hooks that styled their own toggles
 * and nothing else -- clicking them changed the switch and left the interface
 * exactly as it was. A control that appears to do something and does not is
 * worse than no control, so each one now writes a data attribute on <html>
 * that the stylesheet actually reads, and survives a reload.
 *
 * Nothing here is sent to the server: this is how one browser renders, not
 * account state.
 */

export type Accent = 'cyan' | 'magenta' | 'green';
export type Density = 'comfortable' | 'compact';

export interface Preferences {
  accent: Accent;
  motion: boolean;
  density: Density;
}

export const DEFAULT_PREFERENCES: Preferences = {
  accent: 'cyan',
  motion: true,
  density: 'comfortable',
};

const STORAGE_KEY = 'vs_preferences';

const ACCENTS: Accent[] = ['cyan', 'magenta', 'green'];
const DENSITIES: Density[] = ['comfortable', 'compact'];

export function loadPreferences(): Preferences {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_PREFERENCES;
    const parsed = JSON.parse(raw) as Partial<Preferences>;
    // Validated rather than trusted: a stale or hand-edited value must not
    // put an unknown string into a data attribute the CSS cannot match.
    return {
      accent: ACCENTS.includes(parsed.accent as Accent)
        ? (parsed.accent as Accent)
        : DEFAULT_PREFERENCES.accent,
      motion: typeof parsed.motion === 'boolean' ? parsed.motion : true,
      density: DENSITIES.includes(parsed.density as Density)
        ? (parsed.density as Density)
        : DEFAULT_PREFERENCES.density,
    };
  } catch {
    return DEFAULT_PREFERENCES;
  }
}

export function applyPreferences(preferences: Preferences): void {
  const root = document.documentElement;
  root.dataset.accent = preferences.accent;
  root.dataset.motion = preferences.motion ? 'on' : 'off';
  root.dataset.density = preferences.density;
}

export function savePreferences(preferences: Preferences): void {
  applyPreferences(preferences);
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(preferences));
  } catch {
    // Private browsing, or storage disabled. The attributes are already set,
    // so the choice holds for this session and simply does not persist.
  }
}
