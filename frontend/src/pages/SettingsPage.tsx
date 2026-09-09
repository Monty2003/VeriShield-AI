/**
 * Display preferences and real deployment diagnostics.
 *
 * Two things were wrong with the previous version of this screen and both
 * mattered:
 *
 *   1. The three controls were local useState. They styled their own toggles
 *      and changed nothing else, so the interface looked configurable and
 *      was not. They now write through lib/preferences.ts and persist.
 *
 *   2. The diagnostics panel printed "Backend API -- Connected" and
 *      "Database -- Syncing" as fixed text, regardless of whether anything
 *      was reachable. In a tool whose entire job is telling someone what was
 *      and was not verified, a status badge that cannot report failure is the
 *      worst possible widget. Every row below now comes from /health.
 */

import { useCallback, useEffect, useState } from 'react';
import { Cpu, Database, HardDrive, Monitor, RefreshCw, Settings } from 'lucide-react';
import { SectionHeading } from '../components/ui';
import { cx } from '../lib/format';
import { health } from '../api/endpoints';
import { describeError } from '../api/client';
import {
  Accent,
  Density,
  loadPreferences,
  savePreferences,
} from '../lib/preferences';

interface HealthResponse {
  capabilities?: Record<string, unknown>;
  degraded?: string[];
}

const ACCENTS: Array<{ id: Accent; label: string; swatch: string }> = [
  { id: 'cyan', label: 'Neon Cyan', swatch: '#00f0ff' },
  { id: 'magenta', label: 'Cyber Magenta', swatch: '#ff00ff' },
  { id: 'green', label: 'Matrix Green', swatch: '#00ff9f' },
];

const CLIP = {
  clipPath:
    'polygon(10px 0, 100% 0, 100% calc(100% - 10px), calc(100% - 10px) 100%, 0 100%, 0 10px)',
};

export default function SettingsPage() {
  const [preferences, setPreferences] = useState(loadPreferences);
  const [data, setData] = useState<HealthResponse | null>(null);
  const [reachable, setReachable] = useState<boolean | null>(null);
  const [error, setError] = useState('');
  const [checking, setChecking] = useState(false);

  function update(patch: Partial<typeof preferences>) {
    const next = { ...preferences, ...patch };
    setPreferences(next);
    savePreferences(next);
  }

  const check = useCallback(async () => {
    setChecking(true);
    setError('');
    try {
      setData((await health()) as HealthResponse);
      setReachable(true);
    } catch (err) {
      setReachable(false);
      setError(describeError(err));
      setData(null);
    } finally {
      setChecking(false);
    }
  }, []);

  useEffect(() => {
    check();
  }, [check]);

  const capabilities = data?.capabilities ?? {};
  const auditUp = capabilities.audit_trail === true;
  const objectsUp = capabilities.object_storage === true;

  return (
    <div className="mx-auto max-w-5xl space-y-6 animate-fade-up">
      <header className="flex flex-wrap items-center gap-3">
        <Settings className="h-6 w-6 text-cyber-cyan animate-spin-slow" aria-hidden />
        <div className="flex-1">
          <h1 className="text-xl font-display font-bold uppercase tracking-widest text-white">
            System Settings
          </h1>
          <p className="mt-1 font-mono text-xs text-cyber-cyan/70">
            Interface preferences, and what this deployment can actually do
          </p>
        </div>
        <button onClick={check} disabled={checking} className="btn-ghost">
          <RefreshCw className={cx('h-3.5 w-3.5', checking && 'animate-spin')} aria-hidden />
          Re-check
        </button>
      </header>

      <div className="grid gap-6 lg:grid-cols-2">
        <div className="space-y-6">
          <div className="card p-6">
            <SectionHeading title="Interface Accent" hint="Applies immediately and is remembered" />
            <div className="mt-4 grid grid-cols-3 gap-3">
              {ACCENTS.map((option) => (
                <button
                  key={option.id}
                  onClick={() => update({ accent: option.id })}
                  className={cx(
                    'relative flex flex-col items-center gap-2 overflow-hidden border-2 p-3 transition-all duration-300',
                    preferences.accent === option.id
                      ? 'border-cyber-cyan bg-cyber-cyan/10 shadow-[0_0_15px_rgb(var(--accent)/0.3)]'
                      : 'border-cyber-cyan/30 hover:border-cyber-cyan/60',
                  )}
                  style={CLIP}
                  aria-pressed={preferences.accent === option.id}
                >
                  <span
                    className="h-6 w-6 rounded-full"
                    style={{
                      backgroundColor: option.swatch,
                      boxShadow: `0 0 10px ${option.swatch}`,
                    }}
                  />
                  <span className="font-mono text-[10px] uppercase">{option.label}</span>
                </button>
              ))}
            </div>
            <p className="mt-3 font-mono text-[10px] leading-relaxed text-slate-500">
              Verdict colours -- accept, review, reject -- are deliberately not
              themeable. They carry meaning, and meaning must not change with a
              preference.
            </p>
          </div>

          <div className="card p-6">
            <SectionHeading title="Display Parameters" hint="Stored in this browser only" />
            <div className="mt-4 space-y-4">
              <label className="group flex cursor-pointer items-center justify-between">
                <span>
                  <span className="block font-mono text-sm text-slate-200 transition-colors group-hover:text-cyber-cyan">
                    Holographic animations
                  </span>
                  <span className="block font-mono text-[10px] text-slate-500">
                    Grid scan, glow pulses and transitions
                  </span>
                </span>
                <span className="relative">
                  <input
                    type="checkbox"
                    className="sr-only"
                    checked={preferences.motion}
                    onChange={(event) => update({ motion: event.target.checked })}
                  />
                  <span
                    className={cx(
                      'block h-6 w-10 border-2 transition-colors duration-300',
                      preferences.motion
                        ? 'border-cyber-cyan bg-cyber-cyan/20'
                        : 'border-slate-600 bg-ink-800',
                    )}
                    style={{
                      clipPath:
                        'polygon(4px 0, 100% 0, 100% calc(100% - 4px), calc(100% - 4px) 100%, 0 100%, 0 4px)',
                    }}
                  />
                  <span
                    className={cx(
                      'absolute left-1 top-1 h-4 w-4 transition-transform duration-300',
                      preferences.motion
                        ? 'translate-x-4 bg-cyber-cyan shadow-[0_0_5px_currentColor]'
                        : 'bg-slate-500',
                    )}
                    style={{
                      clipPath:
                        'polygon(2px 0, 100% 0, 100% calc(100% - 2px), calc(100% - 2px) 100%, 0 100%, 0 2px)',
                    }}
                  />
                </span>
              </label>

              <div className="border-t border-cyber-cyan/20 pt-4">
                <p className="mb-2 font-mono text-sm text-slate-200">Data density</p>
                <div className="flex gap-2">
                  {(['comfortable', 'compact'] as Density[]).map((option) => (
                    <button
                      key={option}
                      onClick={() => update({ density: option })}
                      className={cx(
                        'flex-1 border py-2 font-mono text-[11px] uppercase transition-colors',
                        preferences.density === option
                          ? 'border-cyber-cyan bg-cyber-cyan/20 text-cyber-cyan'
                          : 'border-cyber-cyan/30 text-slate-500 hover:border-cyber-cyan/60',
                      )}
                      aria-pressed={preferences.density === option}
                    >
                      {option}
                    </button>
                  ))}
                </div>
                <p className="mt-2 font-mono text-[10px] text-slate-500">
                  Compact scales the root type size, so every panel tightens at once.
                </p>
              </div>
            </div>
          </div>
        </div>

        <div className="space-y-6">
          <div className="card p-6">
            <SectionHeading title="System Diagnostics" hint="Read from /health, not assumed" />
            <div className="mt-4 space-y-3">
              <DiagnosticRow
                icon={<Monitor className="h-5 w-5" aria-hidden />}
                title="Frontend core"
                detail="React 19 / Tailwind / this browser"
                state="up"
                label="Running"
              />

              <DiagnosticRow
                icon={<Cpu className="h-5 w-5" aria-hidden />}
                title="Backend API"
                detail={
                  reachable === false
                    ? error || 'No response from the API'
                    : 'FastAPI pipeline'
                }
                state={reachable === null ? 'unknown' : reachable ? 'up' : 'down'}
                label={
                  reachable === null ? 'Checking' : reachable ? 'Reachable' : 'Unreachable'
                }
              />

              <DiagnosticRow
                icon={<Database className="h-5 w-5" aria-hidden />}
                title="Audit trail"
                detail={
                  auditUp
                    ? 'Decisions are being recorded'
                    : 'Verifications run, but nothing is written down'
                }
                state={reachable === null ? 'unknown' : auditUp ? 'up' : 'warn'}
                label={reachable === null ? 'Checking' : auditUp ? 'Recording' : 'Not recording'}
              />

              <DiagnosticRow
                icon={<HardDrive className="h-5 w-5" aria-hidden />}
                title="Object storage"
                detail={
                  objectsUp
                    ? 'Submitted images are retained'
                    : 'Images are not retained, so a decision cannot be re-examined'
                }
                state={reachable === null ? 'unknown' : objectsUp ? 'up' : 'warn'}
                label={reachable === null ? 'Checking' : objectsUp ? 'Retaining' : 'Off'}
              />
            </div>

            {(data?.degraded ?? []).length > 0 && (
              <ul className="mt-4 space-y-2 border-t border-cyber-cyan/20 pt-4">
                {data?.degraded?.map((line, index) => (
                  <li key={index} className="font-mono text-[10px] leading-relaxed text-verdict-review">
                    {line}
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="card p-6">
            <SectionHeading title="What this build guarantees" hint="And what it does not" />
            {/* The previous copy here claimed the connection was encrypted and
                monitored and that models ran in "secure isolation mode". None
                of that is true of this deployment, and a false assurance in a
                verification tool is worse than none. */}
            <ul className="mt-4 space-y-2.5 font-mono text-[11px] leading-relaxed text-slate-400">
              <li>
                <span className="text-cyber-cyan">Does:</span> every request is
                authenticated and authorised on the server, by named permission.
              </li>
              <li>
                <span className="text-cyber-cyan">Does:</span> identity numbers are
                masked in responses unless a role explicitly holds the reveal
                permission.
              </li>
              <li>
                <span className="text-verdict-review">Does not:</span> encrypt anything
                by itself. Over plain HTTP on localhost the traffic is readable;
                TLS is the deployment's job.
              </li>
              <li>
                <span className="text-verdict-review">Does not:</span> detect image
                tampering. Both detectors measured at chance during calibration and
                are switched off rather than reporting noise as evidence.
              </li>
            </ul>
          </div>
        </div>
      </div>
    </div>
  );
}

function DiagnosticRow({
  icon,
  title,
  detail,
  state,
  label,
}: {
  icon: React.ReactNode;
  title: string;
  detail: string;
  state: 'up' | 'down' | 'warn' | 'unknown';
  label: string;
}) {
  const tone = {
    up: 'text-cyber-cyan border-cyber-cyan/20',
    warn: 'text-verdict-review border-verdict-review/40',
    down: 'text-verdict-reject border-verdict-reject/40',
    unknown: 'text-slate-500 border-slate-600/40',
  }[state];

  const chip = {
    up: 'text-cyber-cyan border-cyber-cyan',
    warn: 'text-verdict-review border-verdict-review',
    down: 'text-verdict-reject border-verdict-reject',
    unknown: 'text-slate-500 border-slate-600',
  }[state];

  return (
    <div className={cx('flex items-center gap-3 border bg-ink-900/50 p-3', tone)}>
      <span className="shrink-0">{icon}</span>
      <span className="min-w-0 flex-1">
        <span className="block font-mono text-xs text-slate-200">{title}</span>
        <span className="block truncate font-mono text-[10px] text-slate-500">{detail}</span>
      </span>
      <span className={cx('chip shrink-0', chip)}>{label}</span>
    </div>
  );
}
