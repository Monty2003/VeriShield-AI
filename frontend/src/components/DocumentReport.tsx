/**
 * Everything the pipeline learned about one document, laid out for a person
 * who has to make a decision and then defend it.
 *
 * Order is deliberate: the recommendation first, then what drove it, then the
 * evidence it was drawn from. A reviewer should never have to reconstruct the
 * reasoning from a score.
 */

import { useState } from 'react';
import { Clock, FileCheck2, Layers } from 'lucide-react';
import type { DocumentAnalysis } from '../types/api';
import {
  DOCUMENT_TYPE_LABEL,
  cx,
  ms,
  percent,
  shortDateTime,
} from '../lib/format';
import RiskGauge from './RiskGauge';
import PipelineTrace from './PipelineTrace';
import ContributionChart from './ContributionChart';
import SignalList from './SignalList';
import EvidenceImage from './EvidenceImage';
import FieldGrid from './FieldGrid';

interface Props {
  analysis: DocumentAnalysis;
  previewUrl?: string | null;
}

export default function DocumentReport({ analysis, previewUrl }: Props) {
  const [activeCode, setActiveCode] = useState<string | null>(null);
  const risk = analysis.risk;
  const totalMs = Object.values(analysis.processing_ms).reduce((a, b) => a + b, 0);

  return (
    <div className="animate-fade-up space-y-4">
      <div className="card flex flex-wrap items-center gap-x-6 gap-y-3 px-4 py-3">
        <div className="flex items-center gap-2.5">
          <FileCheck2 className="h-4 w-4 text-slate-500" aria-hidden />
          <div>
            <p className="text-sm font-semibold text-white">
              {DOCUMENT_TYPE_LABEL[analysis.document_type]}
            </p>
            <p className="font-mono text-[10px] uppercase tracking-wider text-slate-500">
              classified at {percent(analysis.type_confidence)} confidence
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2.5">
          <Layers className="h-4 w-4 text-slate-500" aria-hidden />
          <div>
            <p className="text-sm text-slate-200">{analysis.side}</p>
            <p className="font-mono text-[10px] uppercase tracking-wider text-slate-500">
              side
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2.5">
          <Clock className="h-4 w-4 text-slate-500" aria-hidden />
          <div>
            <p className="font-mono text-sm text-slate-200">{ms(totalMs)}</p>
            <p className="font-mono text-[10px] uppercase tracking-wider text-slate-500">
              {shortDateTime(analysis.created_at)}
            </p>
          </div>
        </div>

        <p className="ml-auto truncate font-mono text-[10px] text-slate-600">
          {analysis.document_id}
        </p>
      </div>

      <div className="grid gap-4 lg:grid-cols-[19rem_1fr]">
        <div className="space-y-4">
          {risk ? (
            <div className="card p-5">
              <RiskGauge risk={risk} />
            </div>
          ) : (
            <div className="card p-5 text-sm text-slate-400">
              No risk assessment was produced for this document.
            </div>
          )}

          {risk && risk.top_reasons.length > 0 && (
            <div className="card p-4">
              <p className="section-title">Why</p>
              <ol className="mt-2.5 space-y-2">
                {risk.top_reasons.map((reason, index) => (
                  <li key={index} className="flex gap-2.5 text-xs leading-relaxed">
                    <span className="mt-px font-mono text-[10px] text-slate-600">
                      {String(index + 1).padStart(2, '0')}
                    </span>
                    <span className="text-slate-300">{reason}</span>
                  </li>
                ))}
              </ol>
            </div>
          )}

          <PipelineTrace
            risk={risk}
            timings={analysis.processing_ms}
            signals={analysis.signals}
          />
        </div>

        <div className="space-y-4">
          {previewUrl && (
            <EvidenceImage
              analysis={analysis}
              src={previewUrl}
              activeCode={activeCode}
              onHover={setActiveCode}
            />
          )}

          <FieldGrid fields={analysis.fields} />

          {risk && <ContributionChart contributions={risk.contributions} />}

          <SignalList
            signals={analysis.signals}
            activeCode={activeCode}
            onHover={setActiveCode}
            emptyText="No signals match this filter."
          />
        </div>
      </div>
    </div>
  );
}

/** Compact row for listing several documents in a case. */
export function DocumentTab({
  analysis,
  active,
  onClick,
}: {
  analysis: DocumentAnalysis;
  active: boolean;
  onClick: () => void;
}) {
  const risk = analysis.risk;
  const colour =
    risk?.decision === 'reject'
      ? '#ef4444'
      : risk?.decision === 'manual_review'
        ? '#f59e0b'
        : '#10b981';

  return (
    <button
      onClick={onClick}
      className={cx(
        'flex min-w-[11rem] items-center gap-2.5 rounded-lg border px-3 py-2.5 text-left transition',
        active
          ? 'border-slate-400 bg-ink-700'
          : 'border-ink-600 hover:border-ink-500 hover:bg-ink-800',
      )}
    >
      <span
        className="h-8 w-1 shrink-0 rounded-full"
        style={{ backgroundColor: colour }}
        aria-hidden
      />
      <span className="min-w-0">
        <span className="block truncate text-xs font-medium text-slate-100">
          {DOCUMENT_TYPE_LABEL[analysis.document_type]}
        </span>
        <span className="block truncate font-mono text-[10px] text-slate-500">
          {analysis.filename}
        </span>
      </span>
      <span className="ml-auto shrink-0 font-mono text-sm tabular-nums" style={{ color: colour }}>
        {risk ? risk.score.toFixed(0) : '--'}
      </span>
    </button>
  );
}
