/**
 * Where a sign-in is kept.
 *
 * By default it lasts as long as the tab: opening the app later, in a new
 * browser session, must land on the sign-in screen. Only an explicit "keep me
 * signed in on this device" puts tokens where they outlive the browser.
 */

import { tokens } from './client';

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});

test('a sign-in is kept for this tab only, by default', () => {
  tokens.save('access-1', 'refresh-1');
  expect(sessionStorage.getItem('vs_access_token')).toBe('access-1');
  expect(localStorage.getItem('vs_access_token')).toBeNull();
  expect(tokens.access()).toBe('access-1');
});

test('remembering the device keeps it past the browser session', () => {
  tokens.rememberDevice(true);
  tokens.save('access-2', 'refresh-2');
  expect(localStorage.getItem('vs_access_token')).toBe('access-2');
  expect(tokens.refresh()).toBe('refresh-2');
});

test('signing out leaves nothing in either store', () => {
  sessionStorage.setItem('vs_access_token', 'a');
  localStorage.setItem('vs_access_token', 'b');
  localStorage.setItem('vs_refresh_token', 'c');
  tokens.clear();
  expect(sessionStorage.getItem('vs_access_token')).toBeNull();
  expect(localStorage.getItem('vs_access_token')).toBeNull();
  expect(localStorage.getItem('vs_refresh_token')).toBeNull();
});

test('a token the old behaviour left on the device is not used, and is handed over once to be revoked', () => {
  localStorage.setItem('vs_access_token', 'left-behind');
  localStorage.setItem('vs_refresh_token', 'left-behind-refresh');
  expect(tokens.access()).toBeNull(); // not a session any more
  expect(tokens.takeLegacy()).toBe('left-behind');
  expect(localStorage.getItem('vs_refresh_token')).toBeNull();
  expect(tokens.takeLegacy()).toBeNull();
});

test('a device that chose to be remembered keeps its session', () => {
  tokens.rememberDevice(true);
  tokens.save('kept');
  expect(tokens.takeLegacy()).toBeNull();
  expect(tokens.access()).toBe('kept');
});
