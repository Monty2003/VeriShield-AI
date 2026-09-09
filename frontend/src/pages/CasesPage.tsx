/**
 * Audit trail.
 *
 * Two collections, not one. /verify/document writes a document record;
 * /verify/case writes a case record. This screen used to list only cases,
 * which meant the commonest action in the whole product -- verifying a single
 * document -- left an audit record nothing could display, and the page looked
 * broken to anyone who had not run a multi-document case.
 *
 * Identifiers are masked by the audit store at write time, so nothing here can
 * leak a number the stored record does not already contain.
 *
 * The store being unreachable is reported as its own state rather than as an
 * empty list. "Nothing recorded yet" and "we are not recording anything" look
 * identical in a table and mean opposite things.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { ArcElement, Chart as ChartJS, Legend, Tooltip } from 'chart.js';
import { Doughnut } from 'react-chartjs-2';
import { Activity, Database, FileSearch, Layers3, RefreshCw, Search } from 'lucide-react';
import { Banner, Empty, SectionHeading, Skeleton, Stat } from '../components/ui';
import { describeError } from '../api/client';
import { documentHistory, recentCases, recentDocuments } from '../api/endpoints';
import type {
  Decision,
  DocumentHistoryResponse,
  RecordedDocument,
} from '../types/api';
import {
  DECISION_HEX,
  DECISION_LABEL,
  DOCUMENT_TYPE_LABEL,
  cx,
  percent,
  shortDateTime,
  timeAgo,
} from '../lib/format';

ChartJS.register(ArcElement, Tooltip, Legend);

interface CaseRecord {
  case_id?: string;
  recorded_at?: string;
  started_at?: string;
  documents?: Array<Record<string, unknown>>;
  overall_risk?: {
    score?: number;
    decision?: Decision;
    top_reasons?: string[];
    blocking_reasons?: string[];
  } | null;
}

type Tab = 'documents' | 'cases';

const EMPTY_COUNTS: Record<Decision, number> = {
  accept: 0,
  manual_review: 0,
  reject: 0,
};

export default function CasesPage() {
  const [tab, setTab] = useState<Tab>('documents');

  const [documents, setDocuments] = useState<RecordedDocument[]>([]);
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
      // Both in flight together -- one round trip's latency, not two.
      const [docs, cased] = await Promise.all([recentDocuments(50), recentCases(50)]);
      setAvailable(docs.available && cased.available);
      setReason(docs.reason ?? cased.reason ?? '');
      setDocuments(docs.documents ?? []);
      setCases((cased.cases ?? []) as CaseRecord[]);
    } catch (err) {
      setError(describeError(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const rows = tab === 'documents' ? documents : cases;

  const distribution = useMemo(() => {
    const counts = { ...EMPTY_COUNTS };
    if (tab === 'documents') {
      documents.forEach((d) => {
        const decision = d.risk?.decision;
        if (decision && decision in counts) counts[decision] += 1;
      });
    } else {
      cases.forEach((c) => {
        const decision = c.overall_risk?.decision;
        if (decision && decision in counts) counts[decision] += 1;
      });
    }
    return counts;
  }, [tab, documents, cases]);

  const meanScore = useMemo(() => {
    const scores =
      tab === 'documents'
        ? documents.map((d) => d.risk?.score)
        : cases.map((c) => c.overall_risk?.score);
    const usable = scores.filter((v): v is number => typeof v === 'number');
    if (usable.length === 0) return null;
    return usable.reduce((a, b) => a + b, 0) / usable.length;
  }, [tab, documents, cases]);

  const lookup = useCallback(async (value: string) => {
    setFingerprint(value);
    setHistoryError('');
    setHistory(null);
    try {
      setHistory(await documentHistory(value.trim()));
    } catch (err) {
      setHistoryError(describeError(err));
    }
  }, []);

  const TABS: Array<{ id: Tab; label: string; count: number; icon: typeof FileSearch }> = [
    { id: 'documents', label: 'Documents', count: documents.length, icon: FileSearch },
    { id: 'cases', label: 'Cases', count: cases.length, icon: Layers3 },
  ];

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-2.5 text-xl font-display font-bold uppercase tracking-widest text-white">
            <Activity className="h-5 w-5 text-cyber-cyan" aria-hidden />
            Audit trail
          </h1>
          <p className="mt-1 font-mono text-xs text-slate-500">
            What was decided, when, and on what evidence
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
            'The audit store is unreachable. Verifications still run, but no record is kept.'}
        </Banner>
      )}

      <div className="flex gap-2">
        {TABS.map((option) => (
          <button
            key={option.id}
            onClick={() => setTab(option.id)}
            className={cx(
              'flex items-center gap-2 border px-4 py-2 font-mono text-[11px] uppercase tracking-wider transition-colors',
              tab === option.id
                ? 'border-cyber-cyan bg-cyber-cyan/20 text-cyber-cyan'
                : 'border-cyber-cyan/30 text-slate-500 hover:border-cyber-cyan/60',
            )}
            aria-pressed={tab === option.id}
          >
            <option.icon className="h-3.5 w-3.5" aria-hidden />
            {option.label}
            <span className="opacity-60">{option.count}</span>
          </button>
        ))}
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label={`${tab} recorded`} value={rows.length} />
        <Stat
          label="Mean risk"
          value={meanScore === null ? '--' : meanScore.toFixed(1)}
          hint="across what is listed"
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
          <SectionHeading
            title={tab === 'documents' ? 'Recent documents' : 'Recent cases'}
            hint={
              tab === 'documents'
                ? 'Written by Verify document. Newest first.'
                : 'Written by the Case workspace. Newest first.'
            }
          />

          {loading && (
            <div className="card space-y-3 p-4">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          )}

          {!loading && rows.length === 0 && (
            <Empty
              icon={<Database className="h-5 w-5" aria-hidden />}
              title={`No ${tab} recorded yet`}
            >
              {!available
                ? 'The store is unreachable, so nothing can be listed.'
                : tab === 'documents'
                  ? 'Run a document through Verify document and it will appear here.'
                  : 'A case is only written by the Case workspace -- verifying a single document records a document instead. Try the Documents tab.'}
            </Empty>
          )}

          {!loading && tab === 'documents' && documents.length > 0 && (
            <div className="card divide-y divide-cyber-cyan/10">
              {documents.map((record, index) => (
                <DocumentRow
                  key={record.document_id ?? index}
                  record={record}
                  onLookup={lookup}
                />
              ))}
            </div>
          )}

          {!loading && tab === 'cases' && cases.length > 0 && (
            <div className="card divide-y divide-cyber-cyan/10">
              {cases.map((record, index) => (
                <CaseRow key={record.case_id ?? index} record={record} />
              ))}
            </div>
          )}
        </div>

        <div className="space-y-4">
          {rows.length > 0 && (
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
                      className="h-2 w-2"
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
            <p className="mt-1 font-mono text-[10px] leading-relaxed text-slate-500">
              Every previous assessment of a byte-identical document.
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
                onClick={() => lookup(fingerprint)}
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
              <div className="mt-3 border border-cyber-cyan/20 bg-ink-900/50 p-3">
                {!history.available ? (
                  <p className="text-[11px] text-slate-400">
                    The audit store is unreachable, so history cannot be read.
                  </p>
                ) : history.submissions.length === 0 ? (
                  <p className="text-[11px] text-slate-400">
                    No previous submission of this document.
                  </p>
                ) : (
                  <p
                    className={cx(
                      'text-[11px] font-medium',
                      history.resubmission ? 'text-verdict-review' : 'text-slate-300',
                    )}
                  >
                    {history.submissions.length} submission
                    {history.submissions.length === 1 ? '' : 's'}
                    {history.resubmission
                      ? ' -- this exact file has been seen before'
                      : ' -- first time this file has been seen'}
                  </p>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function Verdict({ decision, score }: { decision?: Decision; score?: number }) {
  const colour = decision ? DECISION_HEX[decision] : '#64748b';
  return (
    <>
      <span
        className="shrink-0 font-mono text-sm tabular-nums"
        style={{ color: colour }}
      >
        {typeof score === 'number' ? score.toFixed(1) : '--'}
      </span>
      <span className="hidden w-24 shrink-0 text-right text-[11px] text-slate-400 sm:block">
        {decision ? DECISION_LABEL[decision] : 'no decision'}
      </span>
    </>
  );
}

function DocumentRow({
  record,
  onLookup,
}: {
  record: RecordedDocument;
  onLookup: (fingerprint: string) => void;
}) {
  const risk = record.risk;
  const colour = risk?.decision ? DECISION_HEX[risk.decision] : '#64748b';
  const blocked = (risk?.blocking_reasons ?? []).length > 0;

  return (
    <details className="group">
      <summary className="flex cursor-pointer list-none items-center gap-3 px-4 py-3 hover:bg-cyber-cyan/5">
        <span
          className="h-8 w-1 shrink-0"
          style={{ backgroundColor: colour }}
          aria-hidden
        />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-xs text-slate-200">
            {DOCUMENT_TYPE_LABEL[record.document_type ?? 'unknown']}
            {record.side && record.side !== 'unknown' && (
              <span className="ml-1.5 font-mono text-[10px] text-slate-500">
                {record.side}
              </span>
            )}
          </span>
          <span className="mt-0.5 block truncate font-mono text-[10px] text-slate-500">
            {record.filename ?? 'unnamed'} &middot;{' '}
            {timeAgo(record.recorded_at ?? record.created_at)}
          </span>
        </span>
        {blocked && (
          <span className="chip shrink-0 border-verdict-review text-verdict-review">
            blocked
          </span>
        )}
        <Verdict decision={risk?.decision} score={risk?.score} />
      </summary>

      <div className="space-y-3 border-t border-cyber-cyan/10 bg-ink-900/50 px-4 py-3">
        <div className="flex flex-wrap gap-x-5 gap-y-1 font-mono text-[10px] text-slate-500">
          <span>recorded {shortDateTime(record.recorded_at)}</span>
          {typeof record.type_confidence === 'number' && (
            <span>classified {percent(record.type_confidence)}</span>
          )}
        </div>

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
          <div className="border border-verdict-review/30 bg-verdict-review/5 p-2.5">
            <p className="font-mono text-[10px] uppercase tracking-wider text-verdict-review">
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

        {record.fingerprint && (
          <button
            onClick={() => onLookup(record.fingerprint as string)}
            className="font-mono text-[10px] text-cyber-cyan hover:underline"
          >
            check for resubmissions of this exact file &rarr;
          </button>
        )}
      </div>
    </details>
  );
}

function CaseRow({ record }: { record: CaseRecord }) {
  const risk = record.overall_risk;
  const colour = risk?.decision ? DECISION_HEX[risk.decision] : '#64748b';
  const blocked = (risk?.blocking_reasons ?? []).length > 0;
  const count = record.documents?.length ?? 0;

  return (
    <details className="group">
      <summary className="flex cursor-pointer list-none items-center gap-3 px-4 py-3 hover:bg-cyber-cyan/5">
        <span
          className="h-8 w-1 shrink-0"
          style={{ backgroundColor: colour }}
          aria-hidden
        />
        <span className="min-w-0 flex-1">
          <span className="block truncate font-mono text-[11px] text-slate-400">
            {record.case_id ?? 'unknown case'}
          </span>
          <span className="mt-0.5 block font-mono text-[10px] text-slate-500">
            {count} document{count === 1 ? '' : 's'} &middot;{' '}
            {timeAgo(record.recorded_at ?? record.started_at)}
          </span>
        </span>
        {blocked && (
          <span className="chip shrink-0 border-verdict-review text-verdict-review">
            blocked
          </span>
        )}
        <Verdict decision={risk?.decision} score={risk?.score} />
      </summary>

      <div className="space-y-3 border-t border-cyber-cyan/10 bg-ink-900/50 px-4 py-3">
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
          <div className="border border-verdict-review/30 bg-verdict-review/5 p-2.5">
            <p className="font-mono text-[10px] uppercase tracking-wider text-verdict-review">
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
}
