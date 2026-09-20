/**
 * The connection indicator says which of four different things is true:
 * connected, connected but degraded, server unreachable, or no network here.
 */

import { fireEvent, render, screen } from '@testing-library/react';
import ConnectionStatus, { levelFrom } from './ConnectionStatus';
import { health } from '../api/endpoints';

jest.mock('../api/endpoints', () => ({ health: jest.fn() }));
const mockedHealth = health as jest.MockedFunction<typeof health>;

const healthy = {
  status: 'ok',
  capabilities: {
    audit_trail: true,
    shared_state: 'in-process',
    ocr: true,
    face_recognition: true,
    aadhaar_qr_reading: ['zxing-cpp', 'zbar'],
  },
  degraded: [],
};

beforeEach(() => mockedHealth.mockReset());

test.each([
  [healthy, true, true, 'online'],
  [{ ...healthy, degraded: ['Audit store unreachable'] }, true, true, 'degraded'],
  [null, false, true, 'offline'],
  [healthy, true, false, 'no-network'],
])('levels', (body, reachable, online, level) => {
  expect(levelFrom(body as Record<string, unknown> | null, reachable, online)).toBe(level);
});

test('a healthy server reads as connected, with its parts listed', async () => {
  mockedHealth.mockResolvedValue(healthy);
  render(<ConnectionStatus />);
  const pill = await screen.findByRole('button', { name: /server connected/i });
  fireEvent.click(pill);
  expect(screen.getByText('Database')).toBeInTheDocument();
  expect(screen.getByText('zxing-cpp, zbar')).toBeInTheDocument();
});

test('a degraded server says what is down', async () => {
  mockedHealth.mockResolvedValue({ ...healthy, degraded: ['Audit store unreachable: decisions are not recorded.'] });
  render(<ConnectionStatus />);
  fireEvent.click(await screen.findByRole('button', { name: /server degraded/i }));
  expect(screen.getByText(/audit store unreachable/i)).toBeInTheDocument();
});

test('an unreachable server says so', async () => {
  mockedHealth.mockRejectedValue(new Error('network'));
  render(<ConnectionStatus />);
  expect(await screen.findByRole('button', { name: /server unreachable/i })).toBeInTheDocument();
});

test('an optional service that was never started is not degradation', async () => {
  mockedHealth.mockResolvedValue({
    ...healthy,
    degraded: [],
    optional_off: ['Document retention (object store): submitted images are not kept.'],
  });
  render(<ConnectionStatus />);
  const pill = await screen.findByRole('button', { name: /server connected/i });
  fireEvent.click(pill);
  expect(screen.getByText(/optional, switched off/i)).toBeInTheDocument();
  expect(screen.getByText(/document retention/i)).toBeInTheDocument();
  expect(screen.queryByText(/needs attention/i)).not.toBeInTheDocument();
});

test('the dot beats only while the server is answering', async () => {
  mockedHealth.mockResolvedValue(healthy);
  const alive = render(<ConnectionStatus />);
  await screen.findByRole('button', { name: /server connected/i });
  expect(screen.getByTestId('status-dot').className).toContain('animate-heartbeat');
  alive.unmount();

  mockedHealth.mockRejectedValue(new Error('network'));
  render(<ConnectionStatus />);
  await screen.findByRole('button', { name: /server unreachable/i });
  // A dead server must not look like a beating one.
  expect(screen.getByTestId('status-dot').className).not.toContain('animate-heartbeat');
});
