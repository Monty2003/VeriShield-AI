/**
 * Wiring smoke test.
 *
 * Rendering <App /> exercises every import in the route table, so a bad
 * default export or a missing module fails here rather than as a blank page
 * in the browser. It also pins the security-relevant default: with no token,
 * the app must land on the login screen and nowhere else.
 */

import { render, screen } from '@testing-library/react';
import App from './App';

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  window.history.pushState({}, '', '/');
});

test('a session the old version left on this device does not open the dashboard', async () => {
  // Before sessions ended with the tab, tokens were kept in localStorage
  // unasked, and opening the app went straight past the sign-in screen.
  localStorage.setItem('vs_access_token', 'left-behind');
  localStorage.setItem('vs_refresh_token', 'left-behind-refresh');
  render(<App />);
  expect(await screen.findByRole('heading', { name: /sign in/i })).toBeInTheDocument();
  expect(localStorage.getItem('vs_access_token')).toBeNull();
});

test('lands on the sign-in screen when there is no session', async () => {
  render(<App />);
  expect(
    await screen.findByRole('heading', { name: /sign in/i }),
  ).toBeInTheDocument();
});

test('does not render any protected screen without a session', async () => {
  window.history.pushState({}, '', '/cases');
  render(<App />);
  expect(await screen.findByRole('heading', { name: /sign in/i })).toBeInTheDocument();
  expect(screen.queryByText(/audit trail/i)).not.toBeInTheDocument();
});
