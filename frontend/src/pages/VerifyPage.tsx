/**
 * Document assessment, shaped by the document.
 *
 * Aadhaar, PAN, certificates and passports keep their evidence in different
 * places, so the form asks for what the chosen document actually has: an
 * Aadhaar front and, when its QR is printed there, the back; a PAN front
 * alone; one marksheet page; one passport photo page. "Not sure" detects the
 * type and offers both slots.
 *
 * The choice guides the form; it does not decide the rulebook. The type is
 * still detected from the image, and a disagreement is shown rather than
 * silently resolved either way -- declaring a type skips detection, which is
 * what the Options panel is for.
 *
 * The options panel exposes exactly what the API exposes, including the two
 * flags that come with caveats -- copy-move detection (measured at chance on
 * this data) and identifier reveal (a permission, not a preference). Hiding
 * them would be tidier and would misrepresent what the system does.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import type { LucideIcon } from 'lucide-react';
import {
  Camera,
  CreditCard,
  FileSearch,
  GraduationCap,
  IdCard,
  Play,
  Plane,
  QrCode,
  RotateCcw,
  ScanSearch,
  Settings2,
} from 'lucide-react';
import Dropzone from '../components/Dropzone';
import DocumentReport from '../components/DocumentReport';
import CaseResult from '../components/CaseResult';
import { Banner, Spinner } from '../components/ui';
import { useAuth } from '../auth/AuthContext';
import { describeError } from '../api/client';
import { verifyCase, verifyDocument } from '../api/endpoints';
import type { DocumentAnalysis, DocumentType, VerificationResult } from '../types/api';
import {
  ALL_DOCUMENT_TYPES,
  DOCUMENT_TYPE_LABEL,
  SUPPORTED_TYPES,
  cx,
} from '../lib/format';
import { DOCUMENT_CHOICES, PROFILES } from '../lib/documentProfiles';
import type { DocumentChoice } from '../lib/documentProfiles';

/** Raised by the backend on an Aadhaar whose signed QR was never compared. */
const QR_UNCHECKED = 'aadhaar.qr.unchecked';

const ICONS: Record<DocumentChoice, LucideIcon> = {
  aadhaar: IdCard,
  pan: CreditCard,
  certificate: GraduationCap,
  passport: Plane,
  auto: ScanSearch,
};

const BACK_ONLY: Partial<Record<DocumentType, { title: string; text: string; action: string }>> = {
  aadhaar: {
    title: 'This is the back of an Aadhaar',
    text: 'The photo, name and number are on the front. Add the front, and the QR on this side will be checked against it.',
    action: 'Add the front',
  },
  pan: {
    title: 'This is the back of a PAN card',
    text: 'It carries no identity data. Upload the front of the card instead.',
    action: 'Upload the front',
  },
  certificate: {
    title: 'This is the reverse of the certificate',
    text: 'It holds the grading notes, not the marks. Upload the page with the marks table.',
    action: 'Upload the marks page',
  },
};

export default function VerifyPage() {
  const { can } = useAuth();
  const [choice, setChoice] = useState<DocumentChoice>('auto');
  const [front, setFront] = useState<File[]>([]);
  const [back, setBack] = useState<File[]>([]);
  const [declaredType, setDeclaredType] = useState<DocumentType | ''>('');
  const [copyMove, setCopyMove] = useState(false);
  const [reveal, setReveal] = useState(false);
  const [ocrText, setOcrText] = useState('');
  const [showOptions, setShowOptions] = useState(false);

  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState('');
  const [analysis, setAnalysis] = useState<DocumentAnalysis | null>(null);
  const [caseResult, setCaseResult] = useState<VerificationResult | null>(null);
  const [previews, setPreviewUrls] = useState<string[]>([]);
  const previewsRef = useRef<string[]>([]);

  const profile = PROFILES[choice];
  const paired = profile.back !== null && back.length > 0;

  // The report keeps showing the images after the pickers are cleared, so
  // these URLs outlive the Dropzones' own previews and are revoked here.
  const setPreviews = useCallback((files: File[]) => {
    previewsRef.current.forEach((url) => URL.revokeObjectURL(url));
    previewsRef.current = files.map((file) => URL.createObjectURL(file));
    setPreviewUrls(previewsRef.current);
  }, []);

  useEffect(
    () => () => previewsRef.current.forEach((url) => URL.revokeObjectURL(url)),
    [],
  );

  function choose(next: DocumentChoice) {
    setChoice(next);
    // A one-image document has nowhere to put a back; keeping it would send
    // a pair the user can no longer see.
    if (PROFILES[next].back === null) setBack([]);
  }

  async function run(asType?: DocumentType) {
    const frontFile = front[0];
    if (!frontFile) return;

    setBusy(true);
    setError('');
    setProgress(0);
    setAnalysis(null);
    setCaseResult(null);

    try {
      if (paired && !asType) {
        const pair = [frontFile, back[0]];
        const result = await verifyCase(pair, {
          enableCopyMove: copyMove,
          revealIdentifiers: reveal,
          onProgress: setProgress,
        });
        setPreviews(pair);
        setCaseResult(result);
      } else {
        const type = asType ?? (declaredType || undefined);
        if (asType) setDeclaredType(asType);
        const result = await verifyDocument(frontFile, {
          declaredType: type,
          ocrText: ocrText.trim() || undefined,
          enableCopyMove: copyMove,
          revealIdentifiers: reveal,
          onProgress: setProgress,
        });
        setPreviews([frontFile]);
        setAnalysis(result);
      }
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
      setProgress(0);
    }
  }

  function clearResult() {
    setAnalysis(null);
    setCaseResult(null);
    setError('');
  }

  function reset() {
    setFront([]);
    setBack([]);
    setOcrText('');
    setDeclaredType('');
    setPreviews([]);
    clearResult();
  }

  /** Back to the form with the front in place, waiting for its back. */
  function addBack() {
    if (PROFILES[choice].back === null) setChoice('aadhaar');
    setBack([]);
    clearResult();
  }

  /** Back to the form for a new photograph of the front. */
  function retakeFront() {
    setFront([]);
    clearResult();
  }

  /** The image was a back: keep it as the back where it is useful, and ask for the front. */
  function addFrontTo(type: DocumentType) {
    if (type === 'aadhaar') {
      if (PROFILES[choice].back === null) setChoice('aadhaar');
      setBack(front);
    }
    setFront([]);
    clearResult();
  }

  const hasResult = analysis !== null || caseResult !== null;

  // --- what the result asks of the user ---
  const frontUnchecked =
    analysis !== null &&
    analysis.side !== 'back' &&
    (analysis.risk?.blocking_codes.includes(QR_UNCHECKED) ?? false);
  const pairUnchecked =
    caseResult?.overall_risk?.blocking_codes.includes(QR_UNCHECKED) ?? false;
  const backOnly = analysis !== null && analysis.side === 'back' ? analysis.document_type : null;

  const detected: DocumentType[] = analysis
    ? [analysis.document_type]
    : (caseResult?.documents ?? []).map((document) => document.document_type);
  const expected = profile.expects;
  const mismatch =
    expected !== null && !declaredType
      ? detected.find((type) => type !== expected) ?? null
      : null;

  return (
    <div className="mx-auto max-w-7xl space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-2.5 text-xl font-semibold text-white">
            <FileSearch className="h-5 w-5 text-slate-500" aria-hidden />
            Verify a document
          </h1>
          <p className="mt-1 text-sm text-slate-500">
            Choose the document, add what it needs, and it goes through the full pipeline.
          </p>
        </div>
        {hasResult && (
          <button onClick={reset} className="btn-ghost">
            <RotateCcw className="h-3.5 w-3.5" aria-hidden />
            New document
          </button>
        )}
      </header>

      {!hasResult && (
        <>
          <div
            role="group"
            aria-label="Document type"
            className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5"
          >
            {DOCUMENT_CHOICES.map((key) => {
              const Icon = ICONS[key];
              const selected = key === choice;
              return (
                <button
                  key={key}
                  type="button"
                  aria-pressed={selected}
                  onClick={() => choose(key)}
                  disabled={busy}
                  className={cx(
                    'card flex flex-col items-start gap-1.5 p-3 text-left transition-colors',
                    selected ? 'border-cyber-cyan/70 bg-cyber-cyan/10' : 'hover:border-cyber-cyan/40',
                  )}
                >
                  <Icon
                    className={cx('h-5 w-5', selected ? 'text-cyber-cyan' : 'text-slate-500')}
                    aria-hidden
                  />
                  <span
                    className={cx('text-sm font-medium', selected ? 'text-white' : 'text-slate-300')}
                  >
                    {PROFILES[key].title}
                  </span>
                  <span className="text-[11px] leading-snug text-slate-500">
                    {PROFILES[key].blurb}
                  </span>
                </button>
              );
            })}
          </div>

          <div className="grid gap-4 lg:grid-cols-[1fr_20rem]">
            <div>
              <div className={cx('grid gap-4', profile.back && 'sm:grid-cols-2')}>
                <div>
                  <p className="label">{profile.front.label}</p>
                  <Dropzone
                    files={front}
                    onChange={setFront}
                    disabled={busy}
                    label={profile.front.dropLabel}
                    hint={profile.front.hint}
                  />
                </div>
                {profile.back && (
                  <div>
                    <p className="label">
                      {profile.back.label}{' '}
                      <span className="normal-case text-slate-500">(optional)</span>
                    </p>
                    <Dropzone
                      files={back}
                      onChange={setBack}
                      disabled={busy}
                      label={profile.back.dropLabel}
                      hint={profile.back.hint}
                    />
                  </div>
                )}
              </div>

              {error && (
                <div className="mt-4">
                  <Banner>{error}</Banner>
                </div>
              )}

              <button
                onClick={() => run()}
                disabled={busy || front.length === 0}
                className="btn-primary mt-4 w-full py-2.5"
              >
                {busy ? <Spinner /> : <Play className="h-4 w-4" aria-hidden />}
                {busy
                  ? progress > 0 && progress < 100
                    ? `Uploading ${progress}%`
                    : 'Running the pipeline'
                  : paired
                    ? 'Assess front and back'
                    : 'Assess this document'}
              </button>

              {busy && (
                <div className="mt-3">
                  <div className="h-1 overflow-hidden rounded-full bg-ink-700">
                    <div
                      className={cx(
                        'h-full rounded-full bg-slate-400 transition-all duration-300',
                        progress >= 100 && 'animate-pulse-ring',
                      )}
                      style={{ width: `${progress || 4}%` }}
                    />
                  </div>
                  <p className="mt-2 text-center text-[11px] text-slate-500">
                    OCR and the face models run per image. The first one after a
                    restart is slower -- the models are loading.
                  </p>
                </div>
              )}
            </div>

            <div className="space-y-4">
              <div className="card p-4">
                <p className="section-title flex items-center gap-2">
                  <Camera className="h-4 w-4 text-slate-500" aria-hidden />
                  {choice === 'auto' ? 'Photographing a document' : `Photographing: ${profile.title}`}
                </p>
                <ul className="mt-3 space-y-2">
                  {profile.tips.map((tip) => (
                    <li key={tip} className="flex gap-2 text-[11px] leading-relaxed text-slate-400">
                      <span
                        className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-cyber-cyan/70"
                        aria-hidden
                      />
                      {tip}
                    </li>
                  ))}
                </ul>
              </div>

              <div className="card h-fit p-4">
                <button
                  onClick={() => setShowOptions((value) => !value)}
                  className="flex w-full items-center gap-2 text-left"
                >
                  <Settings2 className="h-4 w-4 text-slate-500" aria-hidden />
                  <span className="section-title flex-1">Options</span>
                  <span className="font-mono text-[10px] text-slate-600">
                    {showOptions ? 'hide' : 'show'}
                  </span>
                </button>

                {showOptions && (
                  <div className="mt-4 space-y-5 animate-fade-up">
                    {paired && (
                      <p className="text-[11px] leading-relaxed text-slate-500">
                        With a back added, each side is classified and read from its
                        own image, so the type and text overrides below apply only to a
                        single image.
                      </p>
                    )}

                    <div className={cx(paired && 'opacity-50')}>
                      <label className="label" htmlFor="declared-type">
                        Declare the type
                      </label>
                      <select
                        id="declared-type"
                        className="input"
                        value={declaredType}
                        disabled={paired}
                        onChange={(event) =>
                          setDeclaredType(event.target.value as DocumentType | '')
                        }
                      >
                        <option value="">Let the classifier decide</option>
                        {ALL_DOCUMENT_TYPES.map((type) => (
                          <option key={type} value={type}>
                            {DOCUMENT_TYPE_LABEL[type]}
                            {SUPPORTED_TYPES.includes(type) ? '' : ' (partial support)'}
                          </option>
                        ))}
                      </select>
                      <p className="mt-1.5 text-[11px] leading-relaxed text-slate-500">
                        Skips classification and applies that rulebook directly. The
                        choice above only shapes this form; this decides the rules.
                      </p>
                    </div>

                    <label className="flex cursor-pointer gap-3">
                      <input
                        type="checkbox"
                        checked={copyMove}
                        onChange={(event) => setCopyMove(event.target.checked)}
                        className="mt-0.5 h-4 w-4 shrink-0 accent-slate-300"
                      />
                      <span>
                        <span className="block text-xs font-medium text-slate-200">
                          Copy-move detection
                        </span>
                        <span className="mt-0.5 block text-[11px] leading-relaxed text-slate-500">
                          Off by default because calibration measured it at chance on
                          this data. It will produce output; treat that output as
                          unproven.
                        </span>
                      </span>
                    </label>

                    <label
                      className={cx(
                        'flex gap-3',
                        can('verify:reveal_identifiers')
                          ? 'cursor-pointer'
                          : 'cursor-not-allowed opacity-50',
                      )}
                    >
                      <input
                        type="checkbox"
                        checked={reveal}
                        disabled={!can('verify:reveal_identifiers')}
                        onChange={(event) => setReveal(event.target.checked)}
                        className="mt-0.5 h-4 w-4 shrink-0 accent-slate-300"
                      />
                      <span>
                        <span className="block text-xs font-medium text-slate-200">
                          Reveal the full identifier
                        </span>
                        <span className="mt-0.5 block text-[11px] leading-relaxed text-slate-500">
                          {can('verify:reveal_identifiers')
                            ? 'Responses reach logs, browser history and screenshots. Masked unless you ask.'
                            : 'Needs the verify:reveal_identifiers permission, which your role does not hold.'}
                        </span>
                      </span>
                    </label>

                    <div className={cx(paired && 'opacity-50')}>
                      <label className="label" htmlFor="ocr-text">
                        Supply text instead of OCR
                      </label>
                      <textarea
                        id="ocr-text"
                        rows={4}
                        className="input font-mono text-[11px]"
                        placeholder="Paste corrected text to re-run the rule and risk layers"
                        value={ocrText}
                        disabled={paired}
                        onChange={(event) => setOcrText(event.target.value)}
                      />
                      <p className="mt-1.5 text-[11px] leading-relaxed text-slate-500">
                        A misread digit produces a failed checksum that looks exactly
                        like tampering. Correct the text here to tell the two apart.
                      </p>
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        </>
      )}

      {mismatch !== null && expected !== null && (
        <div className="space-y-3">
          <Banner tone="info" title={`You chose ${DOCUMENT_TYPE_LABEL[expected]}`}>
            {mismatch === 'unknown'
              ? `This could not be identified as a ${DOCUMENT_TYPE_LABEL[expected]}, so no rulebook was applied. Retake it with the whole document in frame, or check it with the ${DOCUMENT_TYPE_LABEL[expected]} rules anyway.`
              : `This was identified as ${DOCUMENT_TYPE_LABEL[mismatch]} and checked with those rules. If that is wrong, check it with the ${DOCUMENT_TYPE_LABEL[expected]} rules instead.`}
          </Banner>
          {analysis && (
            <button onClick={() => run(expected)} disabled={busy} className="btn-ghost">
              {busy ? <Spinner /> : <RotateCcw className="h-3.5 w-3.5" aria-hidden />}
              Check it as {DOCUMENT_TYPE_LABEL[expected]}
            </button>
          )}
        </div>
      )}

      {backOnly !== null && (
        <div className="space-y-3">
          <Banner tone="warn" title={BACK_ONLY[backOnly]?.title ?? 'This is the reverse side'}>
            {BACK_ONLY[backOnly]?.text ??
              'The identity details are on the other side. Upload the front of the document.'}
          </Banner>
          <button onClick={() => addFrontTo(backOnly)} className="btn-primary">
            <IdCard className="h-4 w-4" aria-hidden />
            {BACK_ONLY[backOnly]?.action ?? 'Upload the front'}
          </button>
        </div>
      )}

      {frontUnchecked && (
        <div className="space-y-3">
          <Banner tone="warn" title="The QR was not verified">
            Only the number could be checked: nothing else printed on an Aadhaar
            carries a checksum, so an edited name, date of birth or photograph would
            look exactly like this. UIDAI's signed QR settles it. If this card's QR is
            on the back, add the back. If it is on the front, the photo was not sharp
            or large enough to read it -- retake it closer.
          </Banner>
          <div className="flex flex-wrap gap-3">
            <button onClick={addBack} className="btn-primary">
              <QrCode className="h-4 w-4" aria-hidden />
              Add the back side
            </button>
            <button onClick={retakeFront} className="btn-ghost">
              <Camera className="h-3.5 w-3.5" aria-hidden />
              Retake the front
            </button>
          </div>
        </div>
      )}

      {pairUnchecked && (
        <Banner tone="warn" title="The QR on the back could not be read">
          Both sides were submitted, but the QR did not decode, so the front is still
          unchecked. Retake it: hold the phone steady so the QR is sharp, let the QR
          fill most of the frame (a close-up of just the QR is fine), and tilt the card
          so no light reflects across it. A zoomed-in screenshot of a blurry photo will
          not help -- zooming cannot restore detail the camera did not capture.
        </Banner>
      )}

      {analysis && <DocumentReport analysis={analysis} previewUrl={previews[0] ?? null} />}
      {caseResult && <CaseResult result={caseResult} previews={previews} />}
    </div>
  );
}
