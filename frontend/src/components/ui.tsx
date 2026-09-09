/** Small shared primitives. Anything used by three or more screens lands here. */

import type { ReactNode } from 'react';
import { AlertTriangle, Info, Loader2 } from 'lucide-react';
import { cx } from '../lib/format';

export function Spinner({ className = 'h-4 w-4' }: { className?: string }) {
  return <Loader2 className={cx(className, 'animate-spin text-cyber-cyan')} aria-hidden />;
}

export function Banner({
  tone = 'error',
  title,
  children,
}: {
  tone?: 'error' | 'warn' | 'info';
  title?: string;
  children: ReactNode;
}) {
  const tones = {
    error: 'border-verdict-reject/60 bg-verdict-reject/10 text-verdict-reject shadow-[inset_0_0_10px_rgba(255,0,60,0.2)]',
    warn: 'border-verdict-review/60 bg-verdict-review/10 text-verdict-review shadow-[inset_0_0_10px_rgba(255,184,0,0.2)]',
    info: 'border-cyber-cyan/60 bg-cyber-cyan/10 text-cyber-cyan shadow-[inset_0_0_10px_rgb(var(--accent)/0.2)]',
  } as const;

  const Icon = tone === 'info' ? Info : AlertTriangle;

  return (
    <div
      role={tone === 'error' ? 'alert' : undefined}
      className={cx('flex gap-3 px-4 py-3 text-sm relative overflow-hidden', tones[tone])}
      style={{ clipPath: 'polygon(10px 0, 100% 0, 100% calc(100% - 10px), calc(100% - 10px) 100%, 0 100%, 0 10px)' }}
    >
      <div className={cx('absolute top-0 left-0 w-1 h-full', tone === 'error' ? 'bg-verdict-reject' : tone === 'warn' ? 'bg-verdict-review' : 'bg-cyber-cyan')}></div>
      <Icon className="mt-0.5 h-4 w-4 shrink-0 animate-pulse" aria-hidden />
      <div className="min-w-0 z-10">
        {title && <p className="font-bold uppercase tracking-wider font-display">{title}</p>}
        <div className={cx('break-words font-mono text-xs mt-1', title && 'opacity-90')}>{children}</div>
      </div>
    </div>
  );
}

export function Stat({
  label,
  value,
  hint,
  accent,
}: {
  label: string;
  value: ReactNode;
  hint?: string;
  accent?: string;
}) {
  return (
    <div className="card p-4 relative group">
      <div className="absolute top-0 right-0 w-8 h-8 border-t-2 border-r-2 border-cyber-cyan/30 transition-colors group-hover:border-cyber-cyan/80"></div>
      <p className="section-title">{label}</p>
      <p
        className="mt-2 text-3xl font-display font-bold tabular-nums text-white drop-shadow-[0_0_8px_rgba(255,255,255,0.5)]"
        style={accent ? { color: accent, textShadow: `0 0 10px ${accent}` } : undefined}
      >
        {value}
      </p>
      {hint && <p className="mt-1 font-mono text-[10px] text-cyber-cyan/70 uppercase tracking-wider">{hint}</p>}
    </div>
  );
}

export function Empty({
  icon,
  title,
  children,
}: {
  icon?: ReactNode;
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="card flex flex-col items-center gap-3 px-6 py-14 text-center relative overflow-hidden">
      <div className="absolute inset-0 bg-cyber-cyan/5 animate-pulse-glow pointer-events-none"></div>
      <div className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 w-32 h-32 border border-cyber-cyan/10 rounded-full animate-pulse-ring pointer-events-none"></div>
      {icon && <div className="text-cyber-cyan/50 drop-shadow-[0_0_8px_rgb(var(--accent)/0.3)] z-10">{icon}</div>}
      <p className="font-display font-bold uppercase tracking-widest text-cyber-cyan z-10">{title}</p>
      {children && <p className="max-w-md font-mono text-xs text-slate-400 z-10">{children}</p>}
    </div>
  );
}

export function Skeleton({ className = 'h-4 w-full' }: { className?: string }) {
  return <div className={cx('relative overflow-hidden bg-ink-800/80 border border-cyber-cyan/20', className)}>
    <div className="absolute inset-0 -translate-x-full animate-[shimmer_2s_infinite] bg-gradient-to-r from-transparent via-cyber-cyan/10 to-transparent"></div>
  </div>;
}

export function SectionHeading({
  title,
  hint,
  right,
}: {
  title: string;
  hint?: string;
  right?: ReactNode;
}) {
  return (
    <div className="mb-4 flex items-end justify-between gap-4 border-b border-cyber-cyan/20 pb-2">
      <div>
        <h2 className="section-title">{title}</h2>
        {hint && <p className="mt-1 font-mono text-[10px] text-cyber-cyan/60 uppercase tracking-wider">{hint}</p>}
      </div>
      {right}
    </div>
  );
}
