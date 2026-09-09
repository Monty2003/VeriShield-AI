/**
 * Case workspace: several documents assessed together, plus the person.
 *
 * This is the screen that answers what a single document cannot -- do these
 * documents describe the same person, and is that person the one presenting
 * them. Case risk is not the maximum of the document risks: inconsistency
 * between documents is risk that exists only at this level.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { Layers3, Play, RotateCcw, UserRound } from 'lucide-react';
import Dropzone from '../components/Dropzone';
import DocumentReport, { DocumentTab } from '../components/DocumentReport';
import RiskGauge from '../components/RiskGauge';
import SignalList from '../components/SignalList';
import ContributionChart from '../components/ContributionChart';
import { Banner, Empty, SectionHeading, Spinner } from '../components/ui';
import { useAuth } from '../auth/AuthContext';
import { describeError } from '../api/client';
import { verifyCase } from '../api/endpoints';
import type { VerificationResult } from '../types/api';
import { cx } from '../lib/format';

const MAX_DOCUMENTS = 6;

export default function CasePage() {
  const { can } = useAuth();
  const [files, setFiles] = useState<File[]>([]);
  const [selfie, setSelfie] = useState<File[]>([]);
  const [reveal, setReveal] = useState(false);

  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState('');
  const [result, setResult] = useState<VerificationResult | null>(null);
  const [active, setActive] = useState(0);

  const previewsRef = useRef<string[]>([]);
  const [previews, setPreviews] = useState<string[]>([]);

  const releasePreviews = useCallback(() => {
    previewsRef.current.forEach((url) => URL.revokeObjectURL(url));
    previewsRef.current = [];
  }, []);

  useEffect(() => releasePreviews, [releasePreviews]);

  async function run() {
    if (files.length === 0) return;
    setBusy(true);
    setError('');
    setProgress(0);
    setResult(null);

    try {
      const response = await verifyCase(files, {
        selfie: selfie[0] ?? null,
        revealIdentifiers: reveal,
        onProgress: setProgress,
      });

      // Kept in submission order, which is the order the backend returns
      // documents in -- so previews line up with analyses by index.
      releasePreviews();
      previewsRef.current = files.map((file) => URL.createObjectURL(file));
      setPreviews(previewsRef.current);

      setResult(response);
      setActive(0);
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
      setProgress(0);
    }
  }

  function reset() {
    setFiles([]);
    setSelfie([]);
    setResult(null);
    setError('');
    releasePreviews();
    setPreviews([]);
  }

  const crossSignals = result?.cross_document_signals ?? [];

  return (
    <div className="mx-auto max-w-7xl space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-2.5 text-xl font-semibold text-white">
            <Layers3 className="h-5 w-5 text-slate-500" aria-hidden />
            Case workspace
          </h1>
          <p className="mt-1 text-sm text-slate-500">
            Up to {MAX_DOCUMENTS} documents, assessed together with an optional selfie.
          </p>
        </div>
        {result && (
          <button onClick={reset} className="btn-ghost">
            <RotateCcw className="h-3.5 w-3.5" aria-hidden />
            New case
          </button>
        )}
      </header>

      {!result && (
        <div className="grid gap-4 lg:grid-cols-2">
          <div>
            <SectionHeading
              title="Documents"
              hint="Front and back count as two -- submit both when the back carries a QR."
            />
            <Dropzone
              files={files}
              onChange={setFiles}
              multiple
              max={MAX_DOCUMENTS}
              disabled={busy}
              label="Drop the documents"
              hint={`${files.length} of ${MAX_DOCUMENTS} added`}
            />
          </div>

          <div>
            <SectionHeading
              title="The person"
              hint="Compared against the portrait printed on the documents."
            />
            <Dropzone
              files={selfie}
              onChange={setSelfie}
              disabled={busy}
              label="Drop a photo of the presenter"
              hint="Optional. Without it, face comparison is skipped, not failed."
            />

            {can('verify:reveal_identifiers') && (
              <label className="mt-4 flex cursor-pointer items-start gap-3">
                <input
                  type="checkbox"
                  checked={reveal}
                  onChange={(event) => setReveal(event.target.checked)}
                  className="mt-0.5 h-4 w-4 shrink-0 accent-slate-300"
                />
                <span className="text-xs leading-relaxed text-slate-400">
                  Reveal full identifiers in the response
                </span>
              </label>
            )}
          </div>

          <div className="lg:col-span-2">
            {error && (
              <div className="mb-4">
                <Banner>{error}</Banner>
              </div>
            )}

            <button
              onClick={run}
              disabled={busy || files.length === 0}
              className="btn-primary w-full py-2.5"
            >
              {busy ? <Spinner /> : <Play className="h-4 w-4" aria-hidden />}
              {busy
                ? progress > 0 && progress < 100
                  ? `Uploading ${progress}%`
                  : `Assessing ${files.length} document(s)`
                : 'Assess this case'}
            </button>

            {busy && (
              <p className="mt-3 text-center text-[11px] text-slate-500">
                Roughly 5-6 seconds per document, plus face comparison.
              </p>
            )}
          </div>
        </div>
      )}

      {result && (
        <div className="animate-fade-up space-y-6">
          <div className="grid gap-4 lg:grid-cols-[19rem_1fr]">
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
                  {selfie.length ? ' + presenter photo' : ''}
                </p>
              </div>
            </div>

            <div className="space-y-4">
              <div>
                <SectionHeading
                  title="Cross-document consistency"
                  hint="Risk that exists only when documents are read together."
                />
                {crossSignals.length > 0 ? (
                  <SignalList
                    signals={crossSignals}
                    title="Cross-document signals"
                    emptyText="No cross-document signal matches this filter."
                  />
                ) : (
                  <Empty
                    icon={<UserRound className="h-5 w-5" aria-hidden />}
                    title="Nothing to compare"
                  >
                    Cross-document checks need at least two documents carrying the
                    same field, or a selfie to compare against a portrait.
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

          <div className={cx('rounded-xl border border-ink-700 p-4 sm:p-5')}>
            {result.documents[active] && (
              <DocumentReport
                analysis={result.documents[active]}
                previewUrl={previews[active] ?? null}
              />
            )}
          </div>
        </div>
      )}
    </div>
  );
}
