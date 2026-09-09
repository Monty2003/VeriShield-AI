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
