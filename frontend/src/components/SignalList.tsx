/**
 * The evidence list.
 *
 * Every stage emits Signals rather than booleans, so this is the whole
 * reasoning of the system in one place. Sorted by how much a reviewer needs
 * to see it: blocking failures first, then fails, errors, warnings, and only
 * then the checks that passed.
 */

import { useMemo, useState } from 'react';
import { ChevronRight, Lock, Search } from 'lucide-react';
import type { Signal, SignalStatus } from '../types/api';
import {
  SEVERITY_LABEL,
  SEVERITY_RANK,
  STAGE_LABEL,
  STATUS_META,
  STATUS_RANK,
  cx,
  percent,
} from '../lib/format';

interface Props {
  signals: Signal[];
  /** Code of the signal whose regions are currently lit on the image. */
  activeCode?: string | null;
  onHover?: (code: string | null) => void;
  title?: string;
  emptyText?: string;
}

const FILTERS: Array<{ key: 'all' | SignalStatus; label: string }> = [
  { key: 'all', label: 'All' },
  { key: 'fail', label: 'Fail' },
  { key: 'error', label: 'Error' },
  { key: 'warn', label: 'Warn' },
  { key: 'pass', label: 'Pass' },
  { key: 'skip', label: 'Skip' },
];

export default function SignalList({
  signals,
  activeCode,
  onHover,
  title = 'Evidence',
  emptyText = 'No signals were emitted.',
}: Props) {
  const [filter, setFilter] = useState<'all' | SignalStatus>('all');
  const [query, setQuery] = useState('');
  const [expanded, setExpanded] = useState<string | null>(null);

  const counts = useMemo(() => {
    const result: Record<string, number> = { all: signals.length };
    signals.forEach((s) => {
      result[s.status] = (result[s.status] ?? 0) + 1;
    });
    return result;
  }, [signals]);

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return signals
      .filter((s) => filter === 'all' || s.status === filter)
      .filter(
        (s) =>
          !needle ||
          s.title.toLowerCase().includes(needle) ||
          s.code.toLowerCase().includes(needle) ||
          s.reason.toLowerCase().includes(needle),
      )
      .slice()
      .sort((a, b) => {
        const aBlocks = a.blocking && (a.status === 'fail' || a.status === 'error');
        const bBlocks = b.blocking && (b.status === 'fail' || b.status === 'error');
        if (aBlocks !== bBlocks) return aBlocks ? -1 : 1;
        if (STATUS_RANK[a.status] !== STATUS_RANK[b.status]) {
          return STATUS_RANK[a.status] - STATUS_RANK[b.status];
        }
        return SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity];
      });
  }, [signals, filter, query]);

  return (
    <div className="card overflow-hidden">
      <div className="border-b border-ink-600/70 p-4">
        <div className="flex items-center justify-between gap-3">
          <p className="section-title">{title}</p>
          <span className="font-mono text-xs text-slate-500">
            {visible.length}/{signals.length}
          </span>
        </div>

        <div className="mt-3 flex flex-wrap gap-1.5">
          {FILTERS.map((option) => {
            const count = counts[option.key] ?? 0;
            if (option.key !== 'all' && count === 0) return null;
            const active = filter === option.key;
            return (
              <button
                key={option.key}
                onClick={() => setFilter(option.key)}
                className={cx(
                  'chip border transition',
                  active
                    ? 'border-slate-400 bg-slate-100 text-ink-900'
                    : 'border-ink-600 text-slate-400 hover:border-ink-500 hover:text-slate-200',
                )}
              >
                {option.label}
                <span className="opacity-60">{count}</span>
              </button>
            );
          })}
        </div>

        <div className="relative mt-3">
          <Search
            className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-500"
            aria-hidden
          />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Filter by code, title or reason"
            className="input pl-9 text-xs"
          />
        </div>
      </div>

      <div className="max-h-[32rem] divide-y divide-ink-700/60 overflow-y-auto">
        {visible.length === 0 && (
          <p className="px-4 py-8 text-center text-sm text-slate-500">{emptyText}</p>
        )}

        {visible.map((s, position) => {
          // Two stages can legitimately emit the same code, so the row key is
          // composite. Hover still keys on the code alone: lighting up every
          // region a code refers to is the behaviour we want.
          const rowId = `${s.code}#${position}`;
          const meta = STATUS_META[s.status];
          const isOpen = expanded === rowId;
          const isActive = activeCode === s.code;
          const blocks = s.blocking && (s.status === 'fail' || s.status === 'error');
          const hasEvidence = Object.keys(s.evidence).length > 0;

          return (
            <div
              key={rowId}
              onMouseEnter={() => onHover?.(s.regions.length ? s.code : null)}
              onMouseLeave={() => onHover?.(null)}
              className={cx(
                'px-4 py-3 transition-colors',
                isActive ? 'bg-ink-700/60' : 'hover:bg-ink-700/30',
              )}
            >
              <button
                onClick={() => setExpanded(isOpen ? null : rowId)}
                className="flex w-full items-start gap-3 text-left"
              >
                <span
                  className={cx('mt-1.5 h-2 w-2 shrink-0 rounded-full', meta.dot)}
                  aria-hidden
                />

                <span className="min-w-0 flex-1">
                  <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <span className="text-sm font-medium text-slate-100">{s.title}</span>
                    {blocks && (
                      <span className="chip bg-verdict-review/15 text-verdict-review">
                        <Lock className="h-3 w-3" aria-hidden />
                        blocking
                      </span>
                    )}
                    {s.regions.length > 0 && (
                      <span className="chip bg-sky-500/10 text-sky-400">
                        {s.regions.length} region
                      </span>
                    )}
                  </span>

                  <span className="mt-1 block text-xs leading-relaxed text-slate-400">
                    {s.reason}
                  </span>

                  <span className="mt-2 flex flex-wrap items-center gap-2 font-mono text-[10px] text-slate-500">
                    <span className={cx('chip', meta.chip)}>{meta.label}</span>
                    <span>{STAGE_LABEL[s.stage]}</span>
                    <span>&middot;</span>
                    <span>{SEVERITY_LABEL[s.severity]}</span>
                    <span>&middot;</span>
                    <span title="How sure we are of this verdict, not how good the document is">
                      conf {percent(s.confidence)}
                    </span>
                    <span className="truncate opacity-60">{s.code}</span>
                  </span>
                </span>

                <ChevronRight
                  className={cx(
                    'mt-1 h-4 w-4 shrink-0 text-slate-600 transition-transform',
                    isOpen && 'rotate-90',
                  )}
                  aria-hidden
                />
              </button>

              {isOpen && (
                <div className="mt-3 animate-fade-up space-y-3 rounded-lg bg-ink-900/70 p-3">
                  <p className="text-[11px] text-slate-500">{meta.blurb}</p>

                  {hasEvidence ? (
                    <div>
                      <p className="section-title mb-1.5">Backing data</p>
                      <pre className="max-h-56 overflow-auto font-mono text-[11px] leading-relaxed text-slate-300">
                        {JSON.stringify(s.evidence, null, 2)}
                      </pre>
                    </div>
                  ) : (
                    <p className="text-[11px] text-slate-600">
                      This signal carries no structured evidence.
                    </p>
                  )}

                  {s.regions.length > 0 && (
                    <div>
                      <p className="section-title mb-1.5">Regions</p>
                      <ul className="space-y-1 font-mono text-[11px] text-slate-400">
                        {s.regions.map((r, index) => (
                          <li key={index}>
                            {r.label ?? 'region'} &mdash; {r.width}&times;{r.height} at (
                            {r.x}, {r.y})
                            {r.suspicion !== null && r.suspicion !== undefined
                              ? ` -- suspicion ${percent(r.suspicion)}`
                              : ''}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
