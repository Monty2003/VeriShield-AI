/**
 * Audit trail.
 *
 * Identifiers are masked at write time by the backend, so what is shown here
 * is what was stored -- this screen cannot leak a number the audit record
 * does not already contain.
 *
 * The store being unreachable is reported as a distinct state, not as an
 * empty list. "No cases yet" and "we are not recording anything" look
 * identical in a table and mean opposite things.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ArcElement,
  Chart as ChartJS,
  Legend,
  Tooltip,
} from 'chart.js';
import { Doughnut } from 'react-chartjs-2';
import { Activity, Database, RefreshCw, Search } from 'lucide-react';
import { Banner, Empty, SectionHeading, Skeleton, Stat } from '../components/ui';
import { describeError } from '../api/client';
import { documentHistory, recentCases } from '../api/endpoints';
import type { Decision, DocumentHistoryResponse } from '../types/api';
import {
  DECISION_HEX,
  DECISION_LABEL,
  cx,
  shortDateTime,
  timeAgo,
} from '../lib/format';

ChartJS.register(ArcElement, Tooltip, Legend);

interface CaseRecord {
  case_id?: string;
  recorded_at?: string;
  started_at?: string;
  completed_at?: string | null;
  documents?: Array<Record<string, unknown>>;
  overall_risk?: {
    score?: number;
    band?: string;
    decision?: Decision;
    top_reasons?: string[];
    blocking_reasons?: string[];
  } | null;
}

export default function CasesPage() {
  const [cases, setCases] = useState<CaseRecord[]>([]);
  const [available, setAvailable] = useState(true);
  const [reason, setReason] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const [fingerprint, setFingerprint] = useState('');
  const [history, setHistory] = useState<DocumentHistoryResponse | null>(null);
  const [historyError, setHistoryError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const response = await recentCases(50);
      setAvailable(response.available);
      setReason(response.reason ?? '');
      setCases((response.cases ?? []) as CaseRecord[]);
    } catch (err) {
      setError(describeError(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const distribution = useMemo(() => {
    const counts: Record<Decision, number> = {
      accept: 0,
      manual_review: 0,
      reject: 0,
    };
    cases.forEach((record) => {
      const decision = record.overall_risk?.decision;
      if (decision && decision in counts) counts[decision] += 1;
    });
    return counts;
  }, [cases]);

  const total = cases.length;
  const averageScore = useMemo(() => {
    const scores = cases
      .map((record) => record.overall_risk?.score)
      .filter((value): value is number => typeof value === 'number');
    if (scores.length === 0) return null;
    return scores.reduce((a, b) => a + b, 0) / scores.length;
  }, [cases]);

  async function lookup() {
    setHistoryError('');
    setHistory(null);
    try {
      setHistory(await documentHistory(fingerprint.trim()));
    } catch (err) {
      setHistoryError(describeError(err));
    }
  }

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-2.5 text-xl font-semibold text-white">
            <Activity className="h-5 w-5 text-slate-500" aria-hidden />
            Audit trail
          </h1>
          <p className="mt-1 text-sm text-slate-500">
            What was decided, when, and on what evidence.
          </p>
        </div>
        <button onClick={load} disabled={loading} className="btn-ghost">
          <RefreshCw className={cx('h-3.5 w-3.5', loading && 'animate-spin')} aria-hidden />
          Refresh
        </button>
      </header>

      {error && <Banner>{error}</Banner>}

      {!available && (
        <Banner tone="warn" title="Nothing is being recorded">
          {reason ||
            'The audit store is unreachable. Verifications still run; they are simply not being written down.'}
        </Banner>
      )}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Cases recorded" value={total} />
        <Stat
          label="Mean risk"
          value={averageScore === null ? '--' : averageScore.toFixed(1)}
          hint="across the cases listed"
        />
        <Stat
          label="Sent to review"
          value={distribution.manual_review}
          accent={DECISION_HEX.manual_review}
        />
        <Stat label="Rejected" value={distribution.reject} accent={DECISION_HEX.reject} />
      </div>

      <div className="grid gap-4 lg:grid-cols-[1fr_18rem]">
        <div>
          <SectionHeading title="Recent cases" hint="Newest first." />

          {loading && (
            <div className="card space-y-3 p-4">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          )}

          {!loading && cases.length === 0 && (
            <Empty icon={<Database className="h-5 w-5" aria-hidden />} title="No cases yet">
              {available
                ? 'Run a case from the workspace and it will appear here.'
                : 'The store is unreachable, so nothing can be listed.'}
            </Empty>
          )}

          {!loading && cases.length > 0 && (
            <div className="card divide-y divide-ink-700/60">
              {cases.map((record, index) => {
                const risk = record.overall_risk;
                const decision = risk?.decision;
                const colour = decision ? DECISION_HEX[decision] : '#64748b';
                const blocked = (risk?.blocking_reasons ?? []).length > 0;

                return (
                  <details key={record.case_id ?? index} className="group">
                    <summary className="flex cursor-pointer list-none items-center gap-3 px-4 py-3 hover:bg-ink-700/30">
                      <span
                        className="h-8 w-1 shrink-0 rounded-full"
                        style={{ backgroundColor: colour }}
                        aria-hidden
                      />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-mono text-[11px] text-slate-400">
                          {record.case_id ?? 'unknown case'}
                        </span>
                        <span className="mt-0.5 block text-[11px] text-slate-500">
                          {record.documents?.length ?? 0} document
                          {(record.documents?.length ?? 0) === 1 ? '' : 's'}
                          {' · '}
                          {timeAgo(record.recorded_at ?? record.started_at)}
                        </span>
                      </span>
                      {blocked && (
                        <span className="chip shrink-0 bg-verdict-review/15 text-verdict-review">
                          blocked
                        </span>
                      )}
                      <span
                        className="shrink-0 font-mono text-sm tabular-nums"
                        style={{ color: colour }}
                      >
                        {typeof risk?.score === 'number' ? risk.score.toFixed(1) : '--'}
                      </span>
                      <span className="hidden w-24 shrink-0 text-right text-[11px] text-slate-400 sm:block">
                        {decision ? DECISION_LABEL[decision] : 'no decision'}
                      </span>
                    </summary>

                    <div className="space-y-3 border-t border-ink-700/60 bg-ink-900/50 px-4 py-3">
                      <p className="font-mono text-[10px] text-slate-500">
                        recorded {shortDateTime(record.recorded_at)}
                      </p>
                      {(risk?.top_reasons ?? []).length > 0 && (
                        <ol className="space-y-1.5">
                          {risk?.top_reasons?.map((line, i) => (
                            <li key={i} className="text-[11px] leading-relaxed text-slate-300">
                              {line}
                            </li>
                          ))}
                        </ol>
                      )}
                      {blocked && (
                        <div className="rounded-lg border border-verdict-review/30 bg-verdict-review/5 p-2.5">
                          <p className="text-[10px] uppercase tracking-wider text-verdict-review">
                            acceptance withheld
                          </p>
                          <ul className="mt-1 space-y-1">
                            {risk?.blocking_reasons?.map((line, i) => (
                              <li key={i} className="text-[11px] text-slate-300">
                                {line}
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}
                    </div>
                  </details>
                );
              })}
            </div>
          )}
        </div>

        <div className="space-y-4">
          {total > 0 && (
            <div className="card p-4">
              <p className="section-title">Decision mix</p>
              <div className="mx-auto mt-4 h-40 w-40">
                <Doughnut
                  data={{
                    labels: (Object.keys(distribution) as Decision[]).map(
                      (key) => DECISION_LABEL[key],
                    ),
                    datasets: [
                      {
                        data: (Object.keys(distribution) as Decision[]).map(
                          (key) => distribution[key],
                        ),
                        backgroundColor: (Object.keys(distribution) as Decision[]).map(
                          (key) => DECISION_HEX[key],
                        ),
                        borderWidth: 0,
                      },
                    ],
                  }}
                  options={{
                    cutout: '68%',
                    maintainAspectRatio: false,
                    plugins: { legend: { display: false } },
                  }}
                />
              </div>
              <ul className="mt-4 space-y-1.5">
                {(Object.keys(distribution) as Decision[]).map((key) => (
                  <li key={key} className="flex items-center gap-2 text-[11px]">
                    <span
                      className="h-2 w-2 rounded-full"
                      style={{ backgroundColor: DECISION_HEX[key] }}
                      aria-hidden
                    />
                    <span className="flex-1 text-slate-400">{DECISION_LABEL[key]}</span>
                    <span className="font-mono text-slate-300">{distribution[key]}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="card p-4">
            <p className="section-title">Resubmission check</p>
            <p className="mt-1 text-[11px] leading-relaxed text-slate-500">
              Look up every previous assessment of a byte-identical document.
              Resubmitting a refused document is itself a signal, and it is only
              visible from history.
            </p>
            <div className="mt-3 flex gap-2">
              <input
                className="input font-mono text-[11px]"
                placeholder="document fingerprint"
                value={fingerprint}
                onChange={(event) => setFingerprint(event.target.value)}
              />
              <button
                onClick={lookup}
                disabled={!fingerprint.trim()}
                className="btn-ghost shrink-0 px-3"
                aria-label="Look up"
              >
                <Search className="h-3.5 w-3.5" aria-hidden />
              </button>
            </div>

            {historyError && (
              <p className="mt-2 text-[11px] text-verdict-reject">{historyError}</p>
            )}

            {history && (
              <div className="mt-3 rounded-lg border border-ink-700 bg-ink-900/50 p-3">
                {!history.available ? (
                  <p className="text-[11px] text-slate-400">
                    The audit store is unreachable, so history cannot be read.
                  </p>
                ) : history.submissions.length === 0 ? (
                  <p className="text-[11px] text-slate-400">
                    No previous submission of this document.
                  </p>
                ) : (
                  <>
                    <p
                      className={cx(
                        'text-[11px] font-medium',
                        history.resubmission ? 'text-verdict-review' : 'text-slate-300',
                      )}
                    >
                      {history.submissions.length} submission
                      {history.submissions.length === 1 ? '' : 's'}
                      {history.resubmission ? ' -- this document has been seen before' : ''}
                    </p>
                    <pre className="mt-2 max-h-48 overflow-auto font-mono text-[10px] leading-relaxed text-slate-400">
                      {JSON.stringify(history.submissions, null, 2)}
                    </pre>
                  </>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
