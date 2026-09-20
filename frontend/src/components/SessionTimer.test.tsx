/**
 * The timer in the top bar: its warning is a real dialog with a real way out,
 * and zero ends the sign-in rather than merely saying so.
 */

import { act, fireEvent, render, screen } from '@testing-library/react';
import SessionTimer from './SessionTimer';
import { RENEWED_KEY } from '../api/client';

const mockEndSession = jest.fn();
const mockSignOut = jest.fn().mockResolvedValue(undefined);

jest.mock('../auth/AuthContext', () => ({
  useAuth: () => ({ endSession: mockEndSession, signOut: mockSignOut }),
}));
jest.mock('../api/endpoints', () => ({ keepAlive: jest.fn().mockResolvedValue(undefined) }));

beforeEach(() => {
  jest.useFakeTimers();
  jest.setSystemTime(new Date('2026-09-20T10:00:00Z'));
  mockEndSession.mockReset();
  mockSignOut.mockClear();
  sessionStorage.clear();
  sessionStorage.setItem('vs_access_token', 'token');
  sessionStorage.setItem(RENEWED_KEY, String(Date.now()));
});

afterEach(() => jest.useRealTimers());

function pass(ms: number) {
  act(() => {
    jest.advanceTimersByTime(ms);
  });
}

test('shows the time left, and a warning dialog in the last minute', () => {
  render(<SessionTimer timeoutSeconds={300} />);
  expect(screen.getByRole('timer')).toHaveTextContent('5:00');
  expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();

  pass(245_000);
  expect(screen.getByRole('alertdialog')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /stay signed in/i })).toHaveFocus();
});

test('signing out from the warning signs out', () => {
  render(<SessionTimer timeoutSeconds={300} />);
  pass(245_000);
  fireEvent.click(screen.getByRole('button', { name: /sign out now/i }));
  expect(mockSignOut).toHaveBeenCalled();
});

test('at zero the sign-in is ended, for inactivity', () => {
  render(<SessionTimer timeoutSeconds={300} />);
  pass(301_000);
  expect(mockEndSession).toHaveBeenCalledWith('idle');
});

test('a server configured with no limit shows no timer', () => {
  render(<SessionTimer timeoutSeconds={0} />);
  expect(screen.queryByRole('timer')).not.toBeInTheDocument();
});

test('clicking the timer extends the session', () => {
  const { keepAlive } = jest.requireMock('../api/endpoints');
  render(<SessionTimer timeoutSeconds={300} />);
  pass(120_000);
  expect(screen.getByRole('timer')).toHaveTextContent('3:00');

  fireEvent.click(screen.getByRole('button', { name: /extend it now/i }));
  expect(keepAlive).toHaveBeenCalled();
  expect(screen.getByRole('timer')).toHaveTextContent('5:00');
});
