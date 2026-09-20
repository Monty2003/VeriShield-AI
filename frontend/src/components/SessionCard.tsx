/**
 * This session at a glance: where, when, on what, and since when.
 *
 * The point is recognition. A person who sees a place, a device or a sign-in
 * time they do not recognise has found a problem worth reporting; one who sees
 * their own has confirmed nothing is amiss.
 */

import { useEffect, useState } from 'react';
import { Clock, Globe, LocateFixed, LogIn, MapPin, Monitor } from 'lucide-react';
import { SIGNED_IN_KEY, tokens } from '../api/client';
import type { SessionInfo } from '../api/endpoints';
import { describeDevice, locate } from '../lib/place';
import type { PlaceState } from '../lib/place';

function sinceText(from: number, now: number): string {
  const minutes = Math.floor((now - from) / 60_000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  return `${hours} h ${minutes % 60} min ago`;
}

export default function SessionCard({ info }: { info: SessionInfo | null }) {
  const [now, setNow] = useState(() => new Date());
  const [place, setPlace] = useState<PlaceState>({ status: 'locating' });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => locate(setPlace), [attempt]);

  const signedIn = Number(tokens.getMeta(SIGNED_IN_KEY)) || null;
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const device = describeDevice(typeof navigator !== 'undefined' ? navigator.userAgent : '');

  return (
    <section aria-label="This session" className="border border-cyber-cyan/20 bg-ink-800/60 p-3">
      <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-cyber-cyan">This session</p>
      <ul className="mt-2 space-y-1.5 text-[11px] text-slate-300">
        <li className="flex items-start gap-2">
          <MapPin className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-500" aria-hidden />
          {place.status === 'found' && (
            <span className="min-w-0" title={`Accurate to about ${Math.round(place.accuracy)} m`}>
              {place.label}
            </span>
          )}
          {place.status === 'locating' && <span className="text-slate-500">Finding location...</span>}
          {(place.status === 'denied' || place.status === 'unavailable') && (
            <span className="min-w-0 text-slate-500">
              {place.status === 'denied' ? 'Location not shared' : 'Location unavailable'}{' '}
              <button
                type="button"
                onClick={() => setAttempt((n) => n + 1)}
                className="inline-flex items-center gap-1 text-cyber-cyan hover:underline"
                title={
                  place.status === 'denied'
                    ? 'Allow location for this site in the browser, then try again'
                    : place.reason
                }
              >
                <LocateFixed className="h-3 w-3" aria-hidden />
                retry
              </button>
            </span>
          )}
        </li>
        <li className="flex items-start gap-2">
          <Clock className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-500" aria-hidden />
          <span className="min-w-0">
            <span className="font-mono tabular-nums">{now.toLocaleTimeString()}</span>
            <span className="text-slate-500"> · {now.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })}</span>
            <span className="block truncate text-[10px] text-slate-500">{zone}</span>
          </span>
        </li>
        <li className="flex items-center gap-2">
          <Monitor className="h-3.5 w-3.5 shrink-0 text-slate-500" aria-hidden />
          <span className="truncate">{device}</span>
        </li>
        <li className="flex items-center gap-2" title="The address this server received your requests from">
          <Globe className="h-3.5 w-3.5 shrink-0 text-slate-500" aria-hidden />
          <span className="truncate font-mono">{info?.client_ip ?? '--'}</span>
          <span className="text-[10px] text-slate-500">as seen by server</span>
        </li>
        {signedIn && (
          <li className="flex items-center gap-2">
            <LogIn className="h-3.5 w-3.5 shrink-0 text-slate-500" aria-hidden />
            <span className="truncate">
              Signed in {new Date(signedIn).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
              <span className="text-slate-500"> · {sinceText(signedIn, now.getTime())}</span>
            </span>
          </li>
        )}
      </ul>
    </section>
  );
}
