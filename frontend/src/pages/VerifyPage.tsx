/**
 * Single-document assessment.
 *
 * The options panel exposes exactly what the API exposes, including the two
 * flags that come with caveats -- copy-move detection (measured at chance on
 * this data) and identifier reveal (a permission, not a preference). Hiding
 * them would be tidier and would misrepresent what the system does.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { FileSearch, Play, RotateCcw, Settings2 } from 'lucide-react';
import Dropzone from '../components/Dropzone';
import DocumentReport from '../components/DocumentReport';
import { Banner, Spinner } from '../components/ui';
import { useAuth } from '../auth/AuthContext';
import { describeError } from '../api/client';
import { verifyDocument } from '../api/endpoints';
import type { DocumentAnalysis, DocumentType } from '../types/api';
import {
  ALL_DOCUMENT_TYPES,
  DOCUMENT_TYPE_LABEL,
  SUPPORTED_TYPES,
  cx,
} from '../lib/format';

export default function VerifyPage() {
  const { can } = useAuth();
  const [files, setFiles] = useState<File[]>([]);
  const [declaredType, setDeclaredType] = useState<DocumentType | ''>('');
  const [copyMove, setCopyMove] = useState(false);
  const [reveal, setReveal] = useState(false);
  const [ocrText, setOcrText] = useState('');
  const [showOptions, setShowOptions] = useState(false);

  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState('');
  const [analysis, setAnalysis] = useState<DocumentAnalysis | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const previewRef = useRef<string | null>(null);

  // The report keeps showing the image after the picker is cleared, so this
  // URL outlives the Dropzone's own previews and is revoked here instead.
  const setPreview = useCallback((file: File | null) => {
    if (previewRef.current) URL.revokeObjectURL(previewRef.current);
    previewRef.current = file ? URL.createObjectURL(file) : null;
    setPreviewUrl(previewRef.current);
  }, []);

  useEffect(
    () => () => {
      if (previewRef.current) URL.revokeObjectURL(previewRef.current);
    },
    [],
  );

  async function run() {
    const file = files[0];
    if (!file) return;

    setBusy(true);
    setError('');
    setProgress(0);
    setAnalysis(null);

    try {
      const result = await verifyDocument(file, {
        declaredType: declaredType || undefined,
        ocrText: ocrText.trim() || undefined,
        enableCopyMove: copyMove,
        revealIdentifiers: reveal,
        onProgress: setProgress,
      });
      setPreview(file);
      setAnalysis(result);
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
      setProgress(0);
    }
  }

  function reset() {
    setFiles([]);
    setAnalysis(null);
    setError('');
    setOcrText('');
    setPreview(null);
  }

  return (
    <div className="mx-auto max-w-7xl space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-2.5 text-xl font-semibold text-white">
            <FileSearch className="h-5 w-5 text-slate-500" aria-hidden />
            Verify a document
          </h1>
          <p className="mt-1 text-sm text-slate-500">
            One image through the full pipeline, with every signal it produced.
          </p>
        </div>
        {analysis && (
          <button onClick={reset} className="btn-ghost">
            <RotateCcw className="h-3.5 w-3.5" aria-hidden />
            New document
          </button>
        )}
      </header>

      {!analysis && (
        <div className="grid gap-4 lg:grid-cols-[1fr_20rem]">
          <div>
            <Dropzone
              files={files}
              onChange={setFiles}
              disabled={busy}
              label="Drop an Aadhaar, PAN or certificate"
              hint="JPEG, PNG or HEIC, up to 20 MB"
            />

            {error && (
              <div className="mt-4">
                <Banner>{error}</Banner>
              </div>
            )}

            <button
              onClick={run}
              disabled={busy || files.length === 0}
              className="btn-primary mt-4 w-full py-2.5"
            >
              {busy ? <Spinner /> : <Play className="h-4 w-4" aria-hidden />}
              {busy
                ? progress > 0 && progress < 100
                  ? `Uploading ${progress}%`
                  : 'Running the pipeline'
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
                  OCR and the face models run per document. The first one after a
                  restart is slower -- the models are loading.
                </p>
              </div>
            )}
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
                <div>
                  <label className="label" htmlFor="declared-type">
                    Declare the type
                  </label>
                  <select
                    id="declared-type"
                    className="input"
                    value={declaredType}
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
                    Skips classification and applies that rulebook directly. Useful
                    when the workflow already knows the type.
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

                <div>
                  <label className="label" htmlFor="ocr-text">
                    Supply text instead of OCR
                  </label>
                  <textarea
                    id="ocr-text"
                    rows={4}
                    className="input font-mono text-[11px]"
                    placeholder="Paste corrected text to re-run the rule and risk layers"
                    value={ocrText}
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
      )}

      {analysis && <DocumentReport analysis={analysis} previewUrl={previewUrl} />}
    </div>
  );
}
