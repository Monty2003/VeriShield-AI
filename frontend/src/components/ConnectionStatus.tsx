/**
 * Is this page connected to the verification server, and is the server whole?
 *
 * Four states, each meaning something different to the person using it:
 *   online     -- the server answers and nothing it needs is broken;
 *   degraded   -- it answers, but a part it needs is down, so some results
 *                 will be missing or refused;
 *   offline    -- the server does not answer;
 *   no network -- this computer is offline, so the server cannot be judged.
 *
 * Optional capabilities that were never configured -- document retention, a
 * GPU -- are listed as off, not as damage. Reporting them as degradation
 * coloured the console amber on a server doing everything asked of it, which
 * is how a warning stops being read.
 *
 * Polled from the public health endpoint without the session's token, so
 * polling is never mistaken for activity by the idle timer.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import type { LucideIcon } from 'lucide-react';
import {
  Camera,
  ChevronDown,
  Cpu,
  Database,
  KeyRound,
  QrCode,
  RefreshCw,
  ScanFace,
  Server,
  Wifi,
  WifiOff,
} from 'lucide-react';
import { health } from '../api/endpoints';
import { cx } from '../lib/format';

type Level = 'checking' | 'online' | 'degraded' | 'offline' | 'no-network';

const POLL_MS = 15_000;

interface Snapshot {
  level: Level;
  latencyMs: number | null;
  checkedAt: Date | null;
  body: Record<string, unknown> | null;
}

const LABEL: Record<Level, string> = {
  checking: 'Checking server',
  online: 'Server connected',
  degraded: 'Server degraded',
  offline: 'Server unreachable',
  'no-network': 'No internet',
};

const TONE: Record<Level, string> = {
  checking: 'border-slate-700 text-slate-400',
  online: 'border-verdict-accept/40 text-verdict-accept hover:border-verdict-accept/70',
  degraded: 'border-verdict-review/60 text-verdict-review',
  offline: 'border-verdict-reject/60 text-verdict-reject',
  'no-network': 'border-verdict-reject/60 text-verdict-reject',
};

const DOT: Record<Level, string> = {
  checking: 'bg-slate-500',
  // Beating, not blinking: the server is answering, right now, at this rate.
  online: 'bg-verdict-accept animate-heartbeat',
  degraded: 'bg-verdict-review shadow-[0_0_6px_rgba(255,184,0,0.8)] animate-pulse',
  offline: 'bg-verdict-reject shadow-[0_0_6px_rgba(255,0,60,0.8)]',
  'no-network': 'bg-verdict-reject',
};

function list(body: Record<string, unknown> | null, key: string): string[] {
  const value = body?.[key];
  return Array.isArray(value) ? (value as string[]) : [];
}

export function levelFrom(
  body: Record<string, unknown> | null,
  reachable: boolean,
  online: boolean,
): Level {
  if (!online) return 'no-network';
  if (!reachable || !body) return 'offline';
  return list(body, 'degraded').length > 0 ? 'degraded' : 'online';
}

export default function ConnectionStatus() {
  const [snap, setSnap] = useState<Snapshot>({
    level: 'checking',
    latencyMs: null,
    checkedAt: null,
    body: null,
  });
  const [open, setOpen] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);

  const check = useCallback(async () => {
    if (typeof navigator !== 'undefined' && navigator.onLine === false) {
      setSnap((prev) => ({ ...prev, level: 'no-network', checkedAt: new Date() }));
      return;
    }
    const started = performance.now();
    try {
      const body = await health();
      setSnap({
        level: levelFrom(body, true, true),
        latencyMs: Math.round(performance.now() - started),
        checkedAt: new Date(),
        body,
      });
    } catch {
      setSnap({ level: 'offline', latencyMs: null, checkedAt: new Date(), body: null });
    }
  }, []);

  useEffect(() => {
    void check();
    const timer = window.setInterval(check, POLL_MS);
    const recheck = () => void check();
    window.addEventListener('online', recheck);
    window.addEventListener('offline', recheck);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener('online', recheck);
      window.removeEventListener('offline', recheck);
    };
  }, [check]);

  useEffect(() => {
    if (!open) return undefined;
    const close = (event: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [open]);

  const capabilities = (snap.body?.capabilities ?? {}) as Record<string, unknown>;
  const degraded = list(snap.body, 'degraded');
  const optionalOff = list(snap.body, 'optional_off');
  const reachable = snap.level === 'online' || snap.level === 'degraded';
  const qr = capabilities.aadhaar_qr_reading;

  const core: { icon: LucideIcon; label: string; ok: boolean | null; value: string }[] = [
    {
      icon: Server,
      label: 'API server',
      ok: reachable ? true : snap.level === 'checking' ? null : false,
      value: reachable ? `${snap.latencyMs ?? '?'} ms` : LABEL[snap.level],
    },
    {
      icon: Database,
      label: 'Database',
      ok: reachable ? Boolean(capabilities.audit_trail) : null,
      value: reachable ? (capabilities.audit_trail ? 'connected' : 'unreachable') : '--',
    },
    {
      icon: KeyRound,
      label: 'Session store',
      ok: reachable ? !degraded.some((item) => /session|redis/i.test(item)) : null,
      value: reachable ? String(capabilities.shared_state ?? '--').split('(')[0].trim() : '--',
    },
    {
      icon: Cpu,
      label: 'Text recognition',
      ok: reachable ? Boolean(capabilities.ocr) : null,
      value: reachable ? (capabilities.ocr ? 'ready' : 'not installed') : '--',
    },
    {
      icon: ScanFace,
      label: 'Face recognition',
      ok: reachable ? Boolean(capabilities.face_recognition) : null,
      value: reachable ? (capabilities.face_recognition ? 'ready' : 'not installed') : '--',
    },
    {
      icon: QrCode,
      label: 'QR decoders',
      ok: reachable ? Array.isArray(qr) && qr.length > 0 : null,
      value: reachable ? (Array.isArray(qr) && qr.length ? qr.join(', ') : 'none') : '--',
    },
  ];

  const PillIcon = snap.level === 'no-network' ? WifiOff : Wifi;

  return (
    <div ref={panelRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-label={`${LABEL[snap.level]}${reachable && snap.latencyMs !== null ? `, ${snap.latencyMs} milliseconds` : ''}. Show details`}
        className={cx(
          'flex items-center gap-2 border px-2.5 py-1.5 font-mono text-xs transition-colors',
          TONE[snap.level],
        )}
      >
        <span className="relative flex h-2 w-2" aria-hidden>
          {snap.level === 'online' && (
            <span className="absolute inline-flex h-2 w-2 rounded-full bg-verdict-accept animate-heartbeat-ring" />
          )}
          <span
            data-testid="status-dot"
            data-level={snap.level}
            className={cx('relative inline-flex h-2 w-2 rounded-full', DOT[snap.level])}
          />
        </span>
        <PillIcon className="h-3.5 w-3.5" aria-hidden />
        <span className="hidden sm:inline">{LABEL[snap.level]}</span>
        {reachable && snap.latencyMs !== null && (
          <span className="tabular-nums text-slate-500">{snap.latencyMs} ms</span>
        )}
        <ChevronDown
          className={cx('h-3 w-3 opacity-60 transition-transform', open && 'rotate-180')}
          aria-hidden
        />
      </button>

      {open && (
        <div className="card absolute right-0 top-full z-40 mt-2 w-80 p-4 text-left animate-fade-up">
          <div className="flex items-center justify-between">
            <p className="section-title">System status</p>
            <button
              type="button"
              onClick={() => void check()}
              className="text-slate-500 transition-colors hover:text-cyber-cyan"
              aria-label="Check again"
            >
              <RefreshCw className="h-3.5 w-3.5" aria-hidden />
            </button>
          </div>

          <ul className="mt-3 space-y-2">
            {core.map((row) => (
              <li key={row.label} className="flex items-center gap-2 text-xs">
                <span
                  className={cx(
                    'h-1.5 w-1.5 shrink-0 rounded-full',
                    row.ok === null
                      ? 'bg-slate-600'
                      : row.ok
                        ? 'bg-verdict-accept'
                        : 'bg-verdict-reject',
                  )}
                  aria-hidden
                />
                <row.icon className="h-3.5 w-3.5 shrink-0 text-slate-500" aria-hidden />
                <span className="flex-1 text-slate-300">{row.label}</span>
                <span
                  className="max-w-[45%] truncate font-mono text-[11px] text-slate-400"
                  title={row.value}
                >
                  {row.value}
                </span>
              </li>
            ))}
          </ul>

          {degraded.length > 0 && (
            <div className="mt-3 border-t border-ink-700 pt-3">
              <p className="font-mono text-[10px] uppercase tracking-wider text-verdict-review">
                Needs attention
              </p>
              <ul className="mt-1.5 space-y-1.5">
                {degraded.map((item) => (
                  <li key={item} className="text-[11px] leading-relaxed text-verdict-review/90">
                    {item}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {optionalOff.length > 0 && (
            <div className="mt-3 border-t border-ink-700 pt-3">
              <p className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider text-slate-500">
                <Camera className="h-3 w-3" aria-hidden />
                Optional, switched off
              </p>
              <ul className="mt-1.5 space-y-1.5">
                {optionalOff.map((item) => (
                  <li key={item} className="text-[11px] leading-relaxed text-slate-500">
                    {item}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <p className="mt-3 font-mono text-[10px] text-slate-600">
            {snap.checkedAt ? `checked ${snap.checkedAt.toLocaleTimeString()}` : 'checking'} · every{' '}
            {POLL_MS / 1000}s
          </p>
        </div>
      )}
    </div>
  );
}
