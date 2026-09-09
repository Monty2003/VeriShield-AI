/**
 * Face match: is the person presenting this document its holder?
 *
 * The scale is the point of this screen. A single number tells a reviewer
 * nothing on its own -- 0.31 sounds low until you know the decision boundary
 * sits at 0.28 and the confident boundary at 0.45. Drawing the bands makes the
 * middle zone visible as a real third outcome rather than a rounding error,
 * which is exactly what it is: age, lighting and pose push the same person
 * down into it, and push some genuinely different people up into it.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { Camera, Play, RotateCcw, ScanFace, Upload, UserRound } from 'lucide-react';
import CameraCapture from '../components/CameraCapture';
import Dropzone from '../components/Dropzone';
import FaceBoxImage from '../components/FaceBoxImage';
import RiskGauge from '../components/RiskGauge';
import SignalList from '../components/SignalList';
import { Banner, SectionHeading, Spinner } from '../components/ui';
import { describeError } from '../api/client';
import { verifyFaceMatch } from '../api/endpoints';
import type { FaceMatchOutcome, FaceMatchResponse } from '../types/api';
import { cx, ms } from '../lib/format';

interface OutcomeMeta {
  label: string;
  blurb: string;
  hex: string;
  cls: string;
}

const OUTCOME: Record<FaceMatchOutcome, OutcomeMeta> = {
  match: {
    label: 'Same person',
    blurb:
      'The presented face matches the portrait on the document, above the confident threshold.',
    hex: '#00ff9f',
    cls: 'border-verdict-accept/40 bg-verdict-accept/10 text-verdict-accept',
  },
  uncertain: {
    label: 'Undecided',
    blurb:
      'Similarity landed between the thresholds. This is not a weak yes and not a soft no -- the comparison does not decide, and a person should look.',
    hex: '#ffb800',
    cls: 'border-verdict-review/40 bg-verdict-review/10 text-verdict-review',
  },
  mismatch: {
    label: 'Different person',
    blurb:
      'The faces do not match. The document may well be genuine -- but the person presenting it does not appear to be its holder.',
    hex: '#ff003c',
    cls: 'border-verdict-reject/40 bg-verdict-reject/10 text-verdict-reject',
  },
  not_compared: {
    label: 'Not compared',
    blurb:
      'No comparison took place. This is not a low score -- nothing was measured, so nothing about the holder was established.',
    hex: '#64748b',
    cls: 'border-slate-600/50 bg-slate-500/10 text-slate-400',
  },
};

/** Where the presented face comes from. Camera first -- upload is the fallback. */
type Source = 'camera' | 'upload';

export default function FaceMatchPage() {
  const [documentFiles, setDocumentFiles] = useState<File[]>([]);
  const [source, setSource] = useState<Source>('camera');
  const [selfieFiles, setSelfieFiles] = useState<File[]>([]);
  const [captured, setCaptured] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState('');
  const [result, setResult] = useState<FaceMatchResponse | null>(null);

  // The report keeps showing both images after the pickers are cleared, so
  // these URLs outlive the Dropzone previews and are revoked here.
  const urlsRef = useRef<string[]>([]);
  const [previews, setPreviews] = useState<{ doc: string; selfie: string } | null>(null);

  const release = useCallback(() => {
    urlsRef.current.forEach((url) => URL.revokeObjectURL(url));
    urlsRef.current = [];
  }, []);

  useEffect(() => release, [release]);

  // Whichever source is active is the one that counts, so switching tabs
  // cannot leave a stale file from the other one in play.
  const selfieFile = source === 'camera' ? captured : selfieFiles[0] ?? null;

  async function run() {
    const document = documentFiles[0];
    const selfie = selfieFile;
    if (!document || !selfie) return;

    setBusy(true);
    setError('');
    setProgress(0);
    setResult(null);

    try {
      const response = await verifyFaceMatch(document, selfie, setProgress);
      release();
      const docUrl = URL.createObjectURL(document);
      const selfieUrl = URL.createObjectURL(selfie);
      urlsRef.current = [docUrl, selfieUrl];
      setPreviews({ doc: docUrl, selfie: selfieUrl });
      setResult(response);
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
      setProgress(0);
    }
  }

  function reset() {
    setDocumentFiles([]);
    setSelfieFiles([]);
    setCaptured(null);
    setResult(null);
    setError('');
    release();
    setPreviews(null);
  }

  const meta = result ? OUTCOME[result.outcome] : null;

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-2.5 text-xl font-display font-bold uppercase tracking-widest text-white">
            <ScanFace className="h-5 w-5 text-cyber-cyan" aria-hidden />
            Face match
          </h1>
          <p className="mt-1 font-mono text-xs text-slate-500">
            Compare a presented face against the portrait printed on a document
          </p>
        </div>
        {result && (
          <button onClick={reset} className="btn-ghost">
            <RotateCcw className="h-3.5 w-3.5" aria-hidden />
            New comparison
          </button>
        )}
      </header>

      {!result && (
        <>
          <div className="grid gap-4 md:grid-cols-2">
            <div>
              <SectionHeading
                title="The document"
                hint="Aadhaar, passport, driving licence -- anything with a portrait"
              />
              <Dropzone
                files={documentFiles}
                onChange={setDocumentFiles}
                disabled={busy}
                label="Drop the document"
                hint="The printed photograph is what gets compared"
              />
            </div>

            <div>
              <SectionHeading
                title="The person"
                hint="Photograph whoever is presenting it, right now"
                right={
                  <div className="flex gap-1">
                    {(
                      [
                        { id: 'camera' as Source, label: 'Camera', icon: Camera },
                        { id: 'upload' as Source, label: 'Upload', icon: Upload },
                      ]
                    ).map((option) => (
                      <button
                        key={option.id}
                        onClick={() => setSource(option.id)}
                        disabled={busy}
                        className={cx(
                          'flex items-center gap-1.5 border px-2.5 py-1 font-mono text-[10px] uppercase tracking-wider transition-colors',
                          source === option.id
                            ? 'border-cyber-cyan bg-cyber-cyan/20 text-cyber-cyan'
                            : 'border-cyber-cyan/30 text-slate-500 hover:border-cyber-cyan/60',
                        )}
                        aria-pressed={source === option.id}
                      >
                        <option.icon className="h-3 w-3" aria-hidden />
                        {option.label}
                      </button>
                    ))}
                  </div>
                }
              />

              {source === 'camera' ? (
                <CameraCapture onCapture={setCaptured} disabled={busy} />
              ) : (
                <Dropzone
                  files={selfieFiles}
                  onChange={setSelfieFiles}
                  disabled={busy}
                  label="Drop the presented face"
                  hint="A clear, front-facing photo works best"
                />
              )}

              <p className="mt-2 font-mono text-[10px] leading-relaxed text-slate-500">
                {source === 'camera'
                  ? 'A photograph taken now is harder to substitute than a file. It still does not prove the person is live -- run a liveness challenge for that.'
                  : 'Useful for reviewing a case after the fact, or when no camera is available.'}
              </p>
            </div>
          </div>

          {error && <Banner>{error}</Banner>}

          <div>
            <button
              onClick={run}
              disabled={busy || !documentFiles[0] || !selfieFile}
              className="btn-primary w-full py-2.5"
            >
              {busy ? <Spinner /> : <Play className="h-4 w-4" aria-hidden />}
              {busy
                ? progress > 0 && progress < 100
                  ? `Uploading ${progress}%`
                  : 'Comparing'
                : 'Compare the faces'}
            </button>

            {!busy && documentFiles[0] && !selfieFile && (
              <p className="mt-2 text-center font-mono text-[10px] text-verdict-review">
                {source === 'camera'
                  ? 'Capture a shot first -- the live view is not the photograph.'
                  : 'Add a photo of the person.'}
              </p>
            )}
            <p className="mt-3 text-center font-mono text-[10px] text-slate-500">
              Face stage only -- no OCR, no rulebooks. Sub-second once the model is warm.
            </p>
          </div>
        </>
      )}

      {result && meta && (
        <div className="animate-fade-up space-y-5">
          <div className={cx('card border p-5', meta.cls)}>
            <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
              <p className="text-2xl font-display font-bold uppercase tracking-wider">
                {meta.label}
              </p>
              <p className="font-mono text-sm">
                {result.similarity === null
                  ? 'no similarity measured'
                  : `similarity ${result.similarity.toFixed(3)}`}
              </p>
              <p className="ml-auto font-mono text-[10px] opacity-70">
                {result.engine} &middot; {ms(result.processing_ms)}
              </p>
            </div>
            <p className="mt-2 max-w-3xl text-xs leading-relaxed text-slate-300">
              {meta.blurb}
            </p>
          </div>

          <SimilarityScale result={result} />

          <div className="grid gap-4 md:grid-cols-2">
            {previews && (
              <>
                <FaceBoxImage
                  src={previews.doc}
                  region={result.document.region}
                  refWidth={result.document.image_width}
                  refHeight={result.document.image_height}
                  detectionConfidence={result.document.detection_confidence}
                  label="Document portrait"
                  colour={meta.hex}
                  emptyNote={
                    result.document.error ?? 'No face was found on the document'
                  }
                />
                <FaceBoxImage
                  src={previews.selfie}
                  region={result.selfie.region}
                  refWidth={result.selfie.image_width}
                  refHeight={result.selfie.image_height}
                  detectionConfidence={result.selfie.detection_confidence}
                  label="Presented face"
                  colour={meta.hex}
                  emptyNote={
                    result.selfie.error ?? 'No face was found in the presented photo'
                  }
                />
              </>
            )}
          </div>

          <div className="grid gap-4 lg:grid-cols-[19rem_1fr]">
            <div className="card p-5">
              <RiskGauge risk={result.risk} />
            </div>
            <div className="space-y-4">
              <SignalList signals={result.signals} title="Face evidence" />
              <div className="card p-4">
                <p className="section-title">What this does not tell you</p>
                <p className="mt-2.5 text-[11px] leading-relaxed text-slate-400">
                  {result.limitations}
                </p>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * The decision boundaries, drawn.
 *
 * Cosine similarity is clamped to 0..1 for display. Negative values are
 * possible in principle and mean "even less alike than unrelated"; they sit at
 * the far left and there is nothing useful to distinguish below zero.
 */
function SimilarityScale({ result }: { result: FaceMatchResponse }) {
  const { possible, strong } = result.thresholds;
  const value = result.similarity;
  const position = value === null ? null : Math.min(1, Math.max(0, value)) * 100;

  const bands = [
    { from: 0, to: possible * 100, colour: '#ff003c', label: 'Different' },
    { from: possible * 100, to: strong * 100, colour: '#ffb800', label: 'Undecided' },
    { from: strong * 100, to: 100, colour: '#00ff9f', label: 'Same' },
  ];

  return (
    <div className="card p-5">
      <SectionHeading
        title="Where it landed"
        hint="ArcFace cosine similarity against the two policy thresholds"
      />

      <div className="relative mt-8 h-3">
        {bands.map((band) => (
          <div
            key={band.label}
            className="absolute top-0 h-full"
            style={{
              left: `${band.from}%`,
              width: `${band.to - band.from}%`,
              backgroundColor: `${band.colour}44`,
              borderLeft: band.from > 0 ? `2px solid ${band.colour}` : undefined,
            }}
          />
        ))}

        {position !== null && (
          <div
            className="absolute -top-1.5 z-10 h-6 w-1 -translate-x-1/2 bg-white"
            style={{
              left: `${position}%`,
              boxShadow: '0 0 10px rgba(255,255,255,0.9)',
            }}
          >
            <span className="absolute -top-6 left-1/2 -translate-x-1/2 whitespace-nowrap bg-white px-1.5 py-0.5 font-mono text-[10px] font-bold text-ink-900">
              {value?.toFixed(3)}
            </span>
          </div>
        )}
      </div>

      <div className="relative mt-1 h-8">
        {[
          { at: possible * 100, text: possible.toFixed(2) },
          { at: strong * 100, text: strong.toFixed(2) },
        ].map((tick) => (
          <span
            key={tick.text}
            className="absolute -translate-x-1/2 font-mono text-[10px] text-slate-500"
            style={{ left: `${tick.at}%` }}
          >
            {tick.text}
          </span>
        ))}
        <span className="absolute left-0 font-mono text-[10px] text-slate-600">0.00</span>
        <span className="absolute right-0 font-mono text-[10px] text-slate-600">1.00</span>
      </div>

      <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1.5">
        {bands.map((band) => (
          <span key={band.label} className="flex items-center gap-1.5">
            <span
              className="h-2 w-2"
              style={{ backgroundColor: band.colour }}
              aria-hidden
            />
            <span className="font-mono text-[10px] uppercase tracking-wider text-slate-400">
              {band.label}
            </span>
          </span>
        ))}
      </div>

      {value === null && (
        <p className="mt-4 flex items-start gap-2 border-l-2 border-slate-600 pl-3 text-[11px] leading-relaxed text-slate-400">
          <UserRound className="mt-px h-3.5 w-3.5 shrink-0" aria-hidden />
          No marker is drawn because no similarity was produced. Showing this as
          0.00 would place it in the &ldquo;different person&rdquo; band and
          claim a finding the system never made.
        </p>
      )}
    </div>
  );
}
