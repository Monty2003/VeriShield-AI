/**
 * The sign-in form asks before remembering a device, and does not by default.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import LoginPage from './LoginPage';

const mockSignIn = jest.fn();

jest.mock('../auth/AuthContext', () => ({
  useAuth: () => ({
    signIn: mockSignIn,
    signingIn: false,
    error: '',
    user: null,
    clearError: jest.fn(),
  }),
}));

beforeEach(() => mockSignIn.mockReset().mockResolvedValue(false));

function fillAndSubmit() {
  fireEvent.change(screen.getByLabelText(/username/i), { target: { value: 'admin' } });
  fireEvent.change(screen.getByLabelText(/^password$/i), { target: { value: 'secret-pass' } });
  fireEvent.click(screen.getByRole('button', { name: /^sign in$/i }));
}

test('the device is not remembered unless asked', async () => {
  render(<MemoryRouter><LoginPage /></MemoryRouter>);
  expect(screen.getByRole('checkbox', { name: /keep me signed in/i })).not.toBeChecked();
  fillAndSubmit();
  await waitFor(() => expect(mockSignIn).toHaveBeenCalledWith('admin', 'secret-pass', false));
});

test('asking to be kept signed in is passed on', async () => {
  render(<MemoryRouter><LoginPage /></MemoryRouter>);
  fireEvent.click(screen.getByRole('checkbox', { name: /keep me signed in/i }));
  fillAndSubmit();
  await waitFor(() => expect(mockSignIn).toHaveBeenCalledWith('admin', 'secret-pass', true));
});

test('after an inactivity sign-out, the sign-in screen says why -- once', () => {
  sessionStorage.setItem('vs_signout_reason', 'idle');
  const { unmount } = render(<MemoryRouter><LoginPage /></MemoryRouter>);
  expect(screen.getByText(/signed out for inactivity/i)).toBeInTheDocument();
  unmount();
  render(<MemoryRouter><LoginPage /></MemoryRouter>);
  expect(screen.queryByText(/signed out for inactivity/i)).not.toBeInTheDocument();
});
