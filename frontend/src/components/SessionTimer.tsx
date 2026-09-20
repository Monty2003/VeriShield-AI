/**
 * The idle countdown, and the warning before it ends the sign-in.
 *
 * Not a display: at zero the sign-in is ended here and on the server, and the
 * server ends it on its own clock even if this page is closed, asleep or
 * tampered with. What this adds is warning -- a minute's notice, one click to
 * stay -- and an answer the moment the page is used, so the clock visibly
 * belongs to the person using it.
 */

import { useEffect, useRef } from 'react';
import { LogOut, ShieldCheck } from 'lucide-react';
import { useAuth } from '../auth/AuthContext';
import { keepAlive } from '../api/endpoints';
import { clockFormat, useIdleTimer } from '../lib/useIdleTimer';
import { cx } from '../lib/format';

const RADIUS = 9;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

export default function SessionTimer({ timeoutSeconds }: { timeoutSeconds: number }) {
  const { endSession, signOut } = useAuth();
  const expire = useRef(() => endSession('idle'));
  expire.current = () => endSession('idle');

  const { enabled, remaining, warning, stay } = useIdleTimer({
    timeoutSeconds,
    keepAlive,
    onExpire: () => expire.current(),
  });

  if (!enabled) return null;

  const minutes = Math.round(timeoutSeconds / 60);
  const left = Math.max(0, Math.min(1, remaining / timeoutSeconds));

  return (
    <>
      <button
        type="button"
        onClick={() => void stay()}
        aria-label={`Session ends in ${clockFormat(remaining)} without activity. Extend it now`}
        title={`Signs out after ${minutes} minute${minutes === 1 ? '' : 's'} without activity. Using the page keeps it going; click to extend now.`}
        className={cx(
          'group flex items-center gap-2 border px-2.5 py-1.5 font-mono text-xs transition-colors',
          warning
            ? 'border-verdict-review/70 bg-verdict-review/10 text-verdict-review'
            : 'border-cyber-cyan/30 text-cyber-cyan hover:border-cyber-cyan/70 hover:bg-cyber-cyan/5',
        )}
      >
        <svg viewBox="0 0 24 24" className={cx('h-4 w-4 -rotate-90', warning && 'animate-pulse')} aria-hidden>
          <circle cx="12" cy="12" r={RADIUS} className="fill-none stroke-current opacity-20" strokeWidth="3" />
          <circle
            cx="12"
            cy="12"
            r={RADIUS}
            className="fill-none stroke-current transition-[stroke-dashoffset] duration-700 ease-linear"
            strokeWidth="3"
            strokeLinecap="round"
            strokeDasharray={CIRCUMFERENCE}
            strokeDashoffset={CIRCUMFERENCE * (1 - left)}
          />
        </svg>
        <span role="timer" className="tabular-nums">
          {clockFormat(remaining)}
        </span>
        <span className="hidden font-sans text-[10px] uppercase tracking-wider opacity-0 transition-opacity group-hover:opacity-70 sm:inline">
          extend
        </span>
      </button>

      {warning && (
        <IdleWarning
          remaining={remaining}
          total={timeoutSeconds}
          onStay={() => void stay()}
          onSignOut={() => void signOut()}
        />
      )}
    </>
  );
}

function IdleWarning({
  remaining,
  total,
  onStay,
  onSignOut,
}: {
  remaining: number;
  total: number;
  onStay: () => void;
  onSignOut: () => void;
}) {
  const stayRef = useRef<HTMLButtonElement>(null);
  useEffect(() => stayRef.current?.focus(), []);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink-900/85 p-4 backdrop-blur-sm animate-fade-up">
      <div
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="idle-title"
        aria-describedby="idle-body"
        className="card w-full max-w-sm border-verdict-review/60 p-6 text-center shadow-[0_0_40px_rgba(255,184,0,0.15)]"
      >
        <p className="font-mono text-6xl font-bold tabular-nums text-verdict-review">
          {clockFormat(remaining)}
        </p>
        <div className="mx-auto mt-3 h-1 w-40 overflow-hidden rounded-full bg-ink-700">
          <div
            className="h-full rounded-full bg-verdict-review transition-[width] duration-1000 ease-linear"
            style={{ width: `${Math.max(0, Math.min(100, (remaining / Math.max(1, Math.round(total * 0.2))) * 100))}%` }}
          />
        </div>

        <h2 id="idle-title" className="mt-5 font-display text-lg font-bold uppercase tracking-widest text-white">
          Still there?
        </h2>
        <p id="idle-body" className="mt-2 text-xs leading-relaxed text-slate-400">
          This page has not been used for a while. For security you will be
          signed out when the clock reaches zero, here and on the server, and
          anything unsaved will be lost.
        </p>

        <div className="mt-5 flex flex-col gap-2 sm:flex-row">
          <button ref={stayRef} onClick={onStay} className="btn-primary flex-1 py-2">
            <ShieldCheck className="h-4 w-4" aria-hidden />
            Stay signed in
          </button>
          <button onClick={onSignOut} className="btn-ghost flex-1 py-2">
            <LogOut className="h-3.5 w-3.5" aria-hidden />
            Sign out now
          </button>
        </div>
      </div>
    </div>
  );
}
