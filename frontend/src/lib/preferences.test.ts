/**
 * These assertions exist because the settings screen previously shipped with
 * three controls that changed nothing. A test that a preference reaches the
 * document is the cheapest way to stop that regressing.
 */

import {
  DEFAULT_PREFERENCES,
  applyPreferences,
  loadPreferences,
  savePreferences,
} from './preferences';

beforeEach(() => {
  localStorage.clear();
  const root = document.documentElement;
  delete root.dataset.accent;
  delete root.dataset.motion;
  delete root.dataset.density;
});

test('a saved preference reaches the document and survives a reload', () => {
  savePreferences({ accent: 'magenta', motion: false, density: 'compact' });

  // The stylesheet keys off these attributes; if they are absent the toggle
  // is decorative.
  expect(document.documentElement.dataset.accent).toBe('magenta');
  expect(document.documentElement.dataset.motion).toBe('off');
  expect(document.documentElement.dataset.density).toBe('compact');

  expect(loadPreferences()).toEqual({
    accent: 'magenta',
    motion: false,
    density: 'compact',
  });
});

test('a stale or hand-edited value falls back instead of reaching the DOM', () => {
  localStorage.setItem(
    'vs_preferences',
    JSON.stringify({ accent: 'chartreuse', motion: 'yes', density: 'roomy' }),
  );

  const loaded = loadPreferences();
  expect(loaded).toEqual(DEFAULT_PREFERENCES);

  applyPreferences(loaded);
  expect(document.documentElement.dataset.accent).toBe('cyan');
});

test('unreadable storage does not throw', () => {
  localStorage.setItem('vs_preferences', 'not json');
  expect(loadPreferences()).toEqual(DEFAULT_PREFERENCES);
});
