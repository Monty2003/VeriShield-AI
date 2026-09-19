/**
 * The result of a case: the case-level verdict, what only the documents read
 * together could show, and each document's own report.
 *
 * Shared by the case workspace and by the front-and-back flow on the Verify
 * page -- an Aadhaar front and back are a case of two, and the thing that makes
 * them worth submitting together (the QR on one compared with the print on the
 * other) only exists at this level.
 */

import { useState } from 'react';
import { UserRound } from 'lucide-react';
import DocumentReport, { DocumentTab } from './DocumentReport';
import RiskGauge from './RiskGauge';
import SignalList from './SignalList';
import ContributionChart from './ContributionChart';
import QrSignatureBadge from './QrSignatureBadge';
import ReviewPanel from './ReviewPanel';
import { Empty, SectionHeading } from './ui';
import type { VerificationResult } from '../types/api';

interface Props {
  result: VerificationResult;
  /** Object URLs in submission order, which is the order documents come back in. */
  previews: string[];
  hasSelfie?: boolean;
}

export default function CaseResult({ result, previews, hasSelfie = false }: Props) {
  const [active, setActive] = useState(0);
  const crossSignals = result.cross_document_signals ?? [];

  return (
    <div className="animate-fade-up space-y-6">
      <div className="grid gap-4 lg:grid-cols-[19rem_1fr]">
        <div className="space-y-4">
          <div className="card p-5">
            {result.overall_risk ? (
              <RiskGauge risk={result.overall_risk} />
            ) : (
              <p className="text-sm text-slate-400">No case-level risk was produced.</p>
            )}
            <div className="mt-5 border-t border-ink-700 pt-4">
              <p className="section-title">Case</p>
              <p className="mt-1 break-all font-mono text-[11px] text-slate-500">
                {result.case_id}
              </p>
              <p className="mt-2 text-xs text-slate-400">
                {result.documents.length} document
                {result.documents.length === 1 ? '' : 's'}
                {hasSelfie ? ' + presenter photo' : ''}
              </p>
            </div>
          </div>

          {/* One decision for the case: it is the pair of documents, and the
              person presenting them, that is being signed off. */}
          <ReviewPanel
            subject="case"
            subjectId={result.case_id}
            systemDecision={result.overall_risk?.decision}
            blocked={(result.overall_risk?.blocking_reasons ?? []).length > 0}
          />
        </div>

        <div className="space-y-4">
          <div>
            <SectionHeading
              title="Cross-document consistency"
              hint="Risk that exists only when documents are read together."
            />
            <div className="mb-3">
              <QrSignatureBadge signals={crossSignals} />
            </div>
            {crossSignals.length > 0 ? (
              <SignalList
                signals={crossSignals}
                title="Cross-document signals"
                emptyText="No cross-document signal matches this filter."
              />
            ) : (
              <Empty icon={<UserRound className="h-5 w-5" aria-hidden />} title="Nothing to compare">
                Cross-document checks need at least two documents carrying the same
                field, or a selfie to compare against a portrait.
              </Empty>
            )}
          </div>

          {result.overall_risk && (
            <ContributionChart contributions={result.overall_risk.contributions} />
          )}
        </div>
      </div>

      <div>
        <SectionHeading title="Documents in this case" />
        <div className="flex gap-2 overflow-x-auto pb-2 scrollbar-none">
          {result.documents.map((document, index) => (
            <DocumentTab
              key={document.document_id}
              analysis={document}
              active={index === active}
              onClick={() => setActive(index)}
            />
          ))}
        </div>
      </div>

      <div className="rounded-xl border border-ink-700 p-4 sm:p-5">
        {result.documents[active] && (
          <DocumentReport
            reviewable={false}
            analysis={result.documents[active]}
            previewUrl={previews[active] ?? null}
          />
        )}
      </div>
    </div>
  );
}
