/**
 * Extracted fields, with their provenance.
 *
 * The raw OCR string is shown next to the normalized value whenever the two
 * differ, because that gap is where a "failed checksum" usually turns out to
 * be a misread digit rather than a forgery.
 */

import { useState } from 'react';
import { EyeOff, FileText } from 'lucide-react';
import type { ExtractedFields, FieldConfidence } from '../types/api';
import { cx, fieldLabel, formatValue, percent } from '../lib/format';

const SOURCE_STYLE: Record<string, string> = {
  ocr: 'bg-slate-500/10 text-slate-400',
  mrz: 'bg-sky-500/10 text-sky-400',
  qr: 'bg-emerald-500/10 text-emerald-400',
  barcode: 'bg-emerald-500/10 text-emerald-400',
  layout: 'bg-indigo-500/10 text-indigo-400',
  manual: 'bg-amber-500/10 text-amber-400',
};

/** Long free text is shown in its own block, not squeezed into a grid cell. */
const LONG_FIELDS = new Set(['raw_text', 'address', 'mrz_line1', 'mrz_line2']);

function confidenceColour(value: number): string {
  if (value >= 0.8) return '#10b981';
  if (value >= 0.5) return '#f59e0b';
  return '#ef4444';
}

function isPresent(field: FieldConfidence): boolean {
  return field.value !== null && field.value !== undefined && field.value !== '';
}

function looksMasked(value: unknown): boolean {
  return typeof value === 'string' && /^X{2,}/.test(value);
}

export default function FieldGrid({ fields }: { fields: ExtractedFields }) {
  const [showRaw, setShowRaw] = useState(false);

  const entries = Object.entries(fields) as Array<[string, FieldConfidence]>;
  const populated = entries.filter(([, field]) => isPresent(field));
  const missing = entries.filter(([, field]) => !isPresent(field)).map(([key]) => key);

  const short = populated.filter(([key]) => !LONG_FIELDS.has(key));
  const long = populated.filter(([key]) => LONG_FIELDS.has(key));

  if (populated.length === 0) {
    return (
      <div className="card p-6 text-center">
        <FileText className="mx-auto h-5 w-5 text-slate-600" aria-hidden />
        <p className="mt-2 text-sm text-slate-400">No fields were extracted.</p>
        <p className="mt-1 text-xs text-slate-500">
          On the reverse of a document this is the correct outcome, not a failure
          -- the back of a PAN card carries no identity data.
        </p>
      </div>
    );
  }

  return (
    <div className="card p-4">
      <div className="mb-4 flex items-center justify-between gap-3">
        <p className="section-title">Extracted fields</p>
        <span className="font-mono text-xs text-slate-500">
          {populated.length} of {entries.length}
        </span>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        {short.map(([key, field]) => {
          const masked = looksMasked(field.value);
          const differs =
            field.raw && String(field.raw).trim() !== String(field.value).trim();

          return (
            <div key={key} className="rounded-lg border border-ink-700 bg-ink-900/50 p-3">
              <div className="flex items-start justify-between gap-2">
                <p className="text-[11px] uppercase tracking-wider text-slate-500">
                  {fieldLabel(key)}
                </p>
                <span
                  className={cx(
                    'chip shrink-0',
                    SOURCE_STYLE[field.source] ?? SOURCE_STYLE.ocr,
                  )}
                >
                  {field.source}
                </span>
              </div>

              <p className="mt-1.5 break-words font-mono text-sm text-slate-100">
                {formatValue(field.value)}
              </p>

              {masked && (
                <p className="mt-1 flex items-center gap-1 text-[10px] text-slate-500">
                  <EyeOff className="h-3 w-3" aria-hidden />
                  masked by the server
                </p>
              )}

              {differs && showRaw && (
                <p className="mt-1 break-words font-mono text-[11px] text-slate-500">
                  read as: {field.raw}
                </p>
              )}

              <div className="mt-2 flex items-center gap-2">
                <div className="h-1 flex-1 overflow-hidden rounded-full bg-ink-700">
                  <div
                    className="h-full rounded-full transition-all duration-700"
                    style={{
                      width: `${Math.round(field.confidence * 100)}%`,
                      backgroundColor: confidenceColour(field.confidence),
                    }}
                  />
                </div>
                <span className="w-9 shrink-0 text-right font-mono text-[10px] text-slate-500">
                  {percent(field.confidence)}
                </span>
              </div>
            </div>
          );
        })}
      </div>

      {long.length > 0 && (
        <div className="mt-4 space-y-3">
          {long.map(([key, field]) => (
            <details key={key} className="rounded-lg border border-ink-700 bg-ink-900/50">
              <summary className="cursor-pointer list-none px-3 py-2.5 text-[11px] uppercase tracking-wider text-slate-500 hover:text-slate-300">
                {fieldLabel(key)}
                <span className="ml-2 font-mono text-[10px] text-slate-600">
                  {String(field.value).length} chars
                </span>
              </summary>
              <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words border-t border-ink-700 px-3 py-2.5 font-mono text-[11px] leading-relaxed text-slate-300">
                {String(field.value)}
              </pre>
            </details>
          ))}
        </div>
      )}

      <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-ink-700 pt-3">
        <button
          onClick={() => setShowRaw((value) => !value)}
          className="font-mono text-[10px] uppercase tracking-wider text-slate-500 hover:text-slate-300"
        >
          {showRaw ? 'hide' : 'show'} raw OCR text
        </button>
        {missing.length > 0 && (
          <p className="font-mono text-[10px] text-slate-600">
            not found: {missing.map(fieldLabel).join(', ')}
          </p>
        )}
      </div>
    </div>
  );
}
