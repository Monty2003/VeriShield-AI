/**
 * The pipeline, as it actually ran on this document.
 *
 * Reads two independent sources and shows them together:
 *   risk.coverage      -- did the stage produce usable evidence?
 *   processing_ms      -- how long did it take?
 *
 * Worth having because "skipped" and "errored" are invisible in a score.
 * A document assessed at 4/100 with three errored stages was barely checked,
 * and this is the panel where that becomes obvious.
 */

import type { RiskAssessment, Signal, Stage } from '../types/api';
import { STAGE_BLURB, STAGE_LABEL, STAGE_ORDER, cx, ms } from '../lib/format';

interface Props {
  risk: RiskAssessment | null;
  timings: Record<string, number>;
  signals: Signal[];
}

const STATE_STYLE: Record<string, { dot: string; text: string; label: string }> = {
  ran: { dot: 'bg-verdict-accept', text: 'text-verdict-accept', label: 'ran' },
  skipped: { dot: 'bg-slate-600', text: 'text-slate-500', label: 'not applicable' },
  errored: { dot: 'bg-purple-500', text: 'text-purple-400', label: 'could not run' },
  absent: { dot: 'bg-ink-600', text: 'text-slate-600', label: 'not expected' },
};

export default function PipelineTrace({ risk, timings, signals }: Props) {
  const coverage = risk?.coverage ?? {};
  const slowest = Math.max(1, ...Object.values(timings));

  const counts: Record<string, number> = {};
  signals.forEach((s) => {
    counts[s.stage] = (counts[s.stage] ?? 0) + 1;
  });

  // Show a stage if the risk engine expected it, if it emitted a signal, or
  // if it was timed. Hiding the rest keeps the trace honest about what this
  // document type actually involves.
  const visible = STAGE_ORDER.filter(
    (stage) => stage in coverage || counts[stage] || timings[stage] !== undefined,
  );

  return (
    <div className="card p-4">
      <div className="mb-4 flex items-baseline justify-between">
        <p className="section-title">Pipeline trace</p>
        <p className="font-mono text-xs text-slate-500">
          {ms(Object.values(timings).reduce((a, b) => a + b, 0))} total
        </p>
      </div>

      <ol className="space-y-1">
        {visible.map((stage: Stage, index) => {
          const state = coverage[stage] ?? 'absent';
          const style = STATE_STYLE[state] ?? STATE_STYLE.absent;
          const took = timings[stage];
          const width = took ? Math.max(2, (took / slowest) * 100) : 0;

          return (
            <li key={stage} className="group relative flex items-center gap-3 py-1.5">
              {index < visible.length - 1 && (
                <span
                  className="absolute left-[5px] top-6 h-full w-px bg-ink-600"
                  aria-hidden
                />
              )}

              <span
                className={cx('relative z-10 h-2.5 w-2.5 shrink-0 rounded-full', style.dot)}
                aria-hidden
              />

              <div className="min-w-0 flex-1">
                <div className="flex items-baseline justify-between gap-3">
                  <span className="truncate text-sm text-slate-200">
                    {STAGE_LABEL[stage]}
                  </span>
                  <span className={cx('shrink-0 font-mono text-[11px]', style.text)}>
                    {style.label}
                  </span>
                </div>

                <div className="mt-1 flex items-center gap-2">
                  <div className="h-1 flex-1 overflow-hidden rounded-full bg-ink-700">
                    <div
                      className="h-full rounded-full bg-slate-600 transition-all duration-700"
                      style={{ width: `${width}%` }}
                    />
                  </div>
                  <span className="w-14 shrink-0 text-right font-mono text-[10px] text-slate-500">
                    {took !== undefined ? ms(took) : ''}
                  </span>
                </div>

                <p className="mt-1 hidden text-[11px] text-slate-500 group-hover:block">
                  {STAGE_BLURB[stage]}
                  {counts[stage] ? ` -- ${counts[stage]} signal(s)` : ''}
                </p>
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
