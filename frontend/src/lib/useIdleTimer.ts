/**
 * The idle countdown, kept in step with the server's.
 *
 * The deadline counts from the server's last renewal of this sign-in, not from
 * the last mouse movement. The server ends an idle sign-in on its own clock; a
 * countdown that ran ahead of it would show time the session does not have.
 * Using the page renews the server's clock -- a keep-alive at most every 30
 * seconds -- and it is the renewal, recorded by the API client, that resets
 * this countdown.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { RENEWED_KEY, tokens } from '../api/client';

/**
 * Deliberate acts -- a click, a key, a tap. Each one renews the countdown at
 * once, so the timer answers the moment it is used rather than at the end of
 * some throttle window.
 */
const DELIBERATE_EVENTS = ['pointerdown', 'keydown', 'touchstart'];
/** Being present, rather than acting: worth keeping the session, not worth a
 * request for every pixel of movement. */
const PRESENCE_EVENTS = ['mousemove', 'wheel', 'scroll'];

/** A deliberate act tells the server at most this often. */
export const RENEW_EVERY_MS = 3_000;
/** Presence alone tells the server at most this often. */
export const KEEPALIVE_EVERY_MS = 30_000;

interface Options {
  /** 0 turns the timer off, as it is off on a server configured with 0. */
  timeoutSeconds: number;
  warnSeconds?: number;
  keepAlive: () => Promise<void>;
  onExpire: () => void;
  now?: () => number;
}

export function useIdleTimer({
  timeoutSeconds,
  warnSeconds = 60,
  keepAlive,
  onExpire,
  now = Date.now,
}: Options) {
  const [remaining, setRemaining] = useState(timeoutSeconds);
  const expired = useRef(false);
  const pinging = useRef(false);
  const warning = useRef(false);
  const lastPing = useRef(0);

  const renewedAt = useCallback(() => {
    const stored = Number(tokens.getMeta(RENEWED_KEY));
    if (stored > 0) return stored;
    // No record (storage refused it): start from now rather than from 1970,
    // which would read as a sign-in idle for fifty years.
    const started = now();
    tokens.setMeta(RENEWED_KEY, String(started));
    return started;
  }, [now]);

  const ping = useCallback(async () => {
    if (pinging.current) return;
    pinging.current = true;
    try {
      await keepAlive();
    } catch {
      // A failed keep-alive leaves the deadline where it was; if the server
      // has already ended the sign-in, the API client handles the 401.
    } finally {
      pinging.current = false;
    }
  }, [keepAlive]);

  const tick = useCallback(() => {
    if (!timeoutSeconds || expired.current) return;
    // Signed out elsewhere -- another tab on a remembered device.
    const left = tokens.access()
      ? Math.ceil(timeoutSeconds - (now() - renewedAt()) / 1000)
      : 0;
    setRemaining(Math.max(0, left));
    warning.current = left <= warnSeconds;
    if (left <= 0) {
      expired.current = true;
      onExpire();
    }
  }, [timeoutSeconds, warnSeconds, now, renewedAt, onExpire]);

  useEffect(() => {
    if (!timeoutSeconds) return undefined;
    tick();
    const timer = window.setInterval(tick, 1000);
    // Background tabs run timers late; catch up the moment one is looked at.
    const onVisible = () => {
      if (document.visibilityState === 'visible') tick();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [tick, timeoutSeconds]);

  useEffect(() => {
    if (!timeoutSeconds) return undefined;

    // Once the warning is up, neither kind counts: only the button does. A
    // stray movement must not silently extend a session whose user may have
    // walked away from the desk.
    const held = () => warning.current || expired.current;

    const onDeliberate = () => {
      if (held()) return;
      const at = now();
      // Reset the countdown here and now: the server is told within moments,
      // and a person who clicks should see the clock answer, not wait out a
      // throttle window wondering whether the page noticed.
      tokens.setMeta(RENEWED_KEY, String(at));
      setRemaining(timeoutSeconds);
      if (at - lastPing.current >= RENEW_EVERY_MS) {
        lastPing.current = at;
        void ping();
      }
    };

    const onPresence = () => {
      if (held()) return;
      if (now() - renewedAt() >= KEEPALIVE_EVERY_MS) {
        lastPing.current = now();
        void ping();
      }
    };

    DELIBERATE_EVENTS.forEach((name) => window.addEventListener(name, onDeliberate, { passive: true }));
    PRESENCE_EVENTS.forEach((name) => window.addEventListener(name, onPresence, { passive: true }));
    return () => {
      DELIBERATE_EVENTS.forEach((name) => window.removeEventListener(name, onDeliberate));
      PRESENCE_EVENTS.forEach((name) => window.removeEventListener(name, onPresence));
    };
  }, [ping, timeoutSeconds, now, renewedAt]);

  /** The "Stay signed in" button, and the timer itself: renew now, whatever
   * the throttle says. */
  const stay = useCallback(async () => {
    const at = now();
    tokens.setMeta(RENEWED_KEY, String(at));
    lastPing.current = at;
    warning.current = false;
    setRemaining(timeoutSeconds);
    await ping();
    tick();
  }, [ping, tick, now, timeoutSeconds]);

  return {
    enabled: timeoutSeconds > 0,
    remaining,
    warning: timeoutSeconds > 0 && remaining > 0 && remaining <= warnSeconds,
    stay,
  };
}

export function clockFormat(seconds: number): string {
  const safe = Math.max(0, Math.floor(seconds));
  return `${Math.floor(safe / 60)}:${String(safe % 60).padStart(2, '0')}`;
}
