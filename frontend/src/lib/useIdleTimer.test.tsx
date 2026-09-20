/**
 * The idle countdown answers the moment the page is used, follows the server's
 * clock, warns a minute ahead, ends the sign-in at zero, and cannot be
 * extended by a stray movement once the warning is showing.
 */

import { act, render, screen } from '@testing-library/react';
import { RENEWED_KEY } from '../api/client';
import { KEEPALIVE_EVERY_MS, RENEW_EVERY_MS, clockFormat, useIdleTimer } from './useIdleTimer';

let clock = 0;
const now = () => clock;

function Probe(props: { keepAlive: () => Promise<void>; onExpire: () => void }) {
  const { remaining, warning, stay } = useIdleTimer({ timeoutSeconds: 300, now, ...props });
  return (
    <div>
      <span data-testid="left">{remaining}</span>
      <span data-testid="warning">{String(warning)}</span>
      <button onClick={() => void stay()}>stay</button>
    </div>
  );
}

function advance(ms: number) {
  clock += ms;
  act(() => {
    jest.advanceTimersByTime(ms);
  });
}

/** Let a keep-alive promise settle: one is never sent while another is in flight. */
async function settle() {
  await act(async () => {});
}

function fire(name: string) {
  act(() => {
    window.dispatchEvent(new Event(name));
  });
}

const left = () => Number(screen.getByTestId('left').textContent);

beforeEach(() => {
  jest.useFakeTimers();
  clock = 1_000_000;
  sessionStorage.clear();
  localStorage.clear();
  sessionStorage.setItem('vs_access_token', 'token');
  sessionStorage.setItem(RENEWED_KEY, String(clock));
});

afterEach(() => jest.useRealTimers());

test('counts down from the server renewal and ends the sign-in at zero', () => {
  const onExpire = jest.fn();
  render(<Probe keepAlive={jest.fn().mockResolvedValue(undefined)} onExpire={onExpire} />);
  expect(left()).toBe(300);

  advance(239_000); // 61 s left: not yet
  expect(screen.getByTestId('warning')).toHaveTextContent('false');

  advance(1_000); // the last minute
  expect(screen.getByTestId('warning')).toHaveTextContent('true');
  expect(onExpire).not.toHaveBeenCalled();

  advance(60_000);
  expect(onExpire).toHaveBeenCalledTimes(1);
});

test('a click resets the countdown at once, and tells the server', () => {
  const keepAlive = jest.fn().mockResolvedValue(undefined);
  render(<Probe keepAlive={keepAlive} onExpire={jest.fn()} />);
  advance(100_000);
  expect(left()).toBe(200);

  fire('pointerdown');
  expect(left()).toBe(300); // no waiting for a throttle window, or a round trip
  expect(keepAlive).toHaveBeenCalledTimes(1);
});

test('rapid clicks tell the server at most every few seconds', async () => {
  const keepAlive = jest.fn().mockResolvedValue(undefined);
  render(<Probe keepAlive={keepAlive} onExpire={jest.fn()} />);

  fire('pointerdown');
  await settle();
  advance(RENEW_EVERY_MS - 500);
  fire('keydown');
  fire('pointerdown');
  expect(keepAlive).toHaveBeenCalledTimes(1);
  expect(left()).toBe(300); // still reset locally, every time

  advance(1_000);
  fire('pointerdown');
  expect(keepAlive).toHaveBeenCalledTimes(2);
});

test('being present without acting tells the server at most every 30 seconds', () => {
  const keepAlive = jest.fn().mockResolvedValue(undefined);
  render(<Probe keepAlive={keepAlive} onExpire={jest.fn()} />);

  advance(10_000);
  fire('mousemove');
  expect(keepAlive).not.toHaveBeenCalled(); // renewed 10 s ago: nothing to say

  advance(KEEPALIVE_EVERY_MS);
  fire('mousemove');
  fire('scroll');
  expect(keepAlive).toHaveBeenCalledTimes(1);
});

test('a renewal recorded by the API client resets the countdown', () => {
  render(<Probe keepAlive={jest.fn().mockResolvedValue(undefined)} onExpire={jest.fn()} />);
  advance(200_000);
  sessionStorage.setItem(RENEWED_KEY, String(clock)); // what any request records
  advance(1_000);
  expect(left()).toBeGreaterThanOrEqual(298);
});

test('once the warning is up, only the button extends the session', () => {
  const keepAlive = jest.fn().mockResolvedValue(undefined);
  render(<Probe keepAlive={keepAlive} onExpire={jest.fn()} />);
  advance(250_000);
  expect(screen.getByTestId('warning')).toHaveTextContent('true');

  fire('mousemove');
  fire('pointerdown'); // even a click: the person may have left the desk
  expect(keepAlive).not.toHaveBeenCalled();
  expect(left()).toBeLessThanOrEqual(50);

  act(() => {
    screen.getByRole('button', { name: 'stay' }).click();
  });
  expect(keepAlive).toHaveBeenCalledTimes(1);
  expect(left()).toBe(300);
});

test('a sign-out in another tab ends this one too', () => {
  const onExpire = jest.fn();
  render(<Probe keepAlive={jest.fn().mockResolvedValue(undefined)} onExpire={onExpire} />);
  sessionStorage.removeItem('vs_access_token');
  advance(1_000);
  expect(onExpire).toHaveBeenCalledTimes(1);
});

test('the clock reads as minutes and seconds', () => {
  expect(clockFormat(300)).toBe('5:00');
  expect(clockFormat(59)).toBe('0:59');
  expect(clockFormat(-3)).toBe('0:00');
});
