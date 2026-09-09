/**
 * MRZ lab: check digits with no image and no model.
 *
 * Useful for its own sake, and useful as a demonstration -- these results are
 * arithmetic defined by ICAO 9303, not a model's guess, and the table shows
 * the computed digit next to the stated one so anyone can verify it by hand.
 *
 * The "evaluated" column is the honest part: when OCR drops the filler
 * characters, the service re-inflates line 2 to recover the layout, and a
 * check that verifies against filler WE inserted proves nothing. Those are
 * reported as not evaluated rather than quietly counted as passes.
 */

import { useState } from 'react';
import { ScanLine, Play } from 'lucide-react';
import { Banner, Spinner } from '../components/ui';
import RiskGauge from '../components/RiskGauge';
import SignalList from '../components/SignalList';
import { describeError } from '../api/client';
import { verifyMrz } from '../api/endpoints';
import type { MrzVerifyResponse } from '../api/endpoints';
import { cx, fieldLabel, formatValue } from '../lib/format';

const SAMPLE_1 = 'P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<';
const SAMPLE_2 = 'L898902C36UTO7408122F1204159ZE184226B<<<<<10';

export default function MrzPage() {
  const [line1, setLine1] = useState('');
  const [line2, setLine2] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState<MrzVerifyResponse | null>(null);

  async function run() {
    setBusy(true);
    setError('');
    setResult(null);
    try {
      setResult(await verifyMrz(line1.trim(), line2.trim()));
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <header>
        <h1 className="flex items-center gap-2.5 text-xl font-semibold text-white">
          <ScanLine className="h-5 w-5 text-slate-500" aria-hidden />
          MRZ lab
        </h1>
        <p className="mt-1 text-sm text-slate-500">
          Paste the two machine-readable lines from a passport. No image, no model.
        </p>
      </header>

      <div className="card space-y-4 p-4">
        <div>
          <label className="label" htmlFor="line1">
            Line 1
          </label>
          <input
            id="line1"
            className="input font-mono text-xs tracking-wider"
            value={line1}
            spellCheck={false}
            onChange={(event) => setLine1(event.target.value.toUpperCase())}
            placeholder={SAMPLE_1}
          />
        </div>
        <div>
          <label className="label" htmlFor="line2">
            Line 2
          </label>
          <input
            id="line2"
            className="input font-mono text-xs tracking-wider"
            value={line2}
            spellCheck={false}
            onChange={(event) => setLine2(event.target.value.toUpperCase())}
            placeholder={SAMPLE_2}
          />
        </div>

        <div className="flex flex-wrap gap-3">
          <button onClick={run} disabled={busy || !line1 || !line2} className="btn-primary">
            {busy ? <Spinner /> : <Play className="h-4 w-4" aria-hidden />}
            Check the digits
          </button>
          <button
            onClick={() => {
              setLine1(SAMPLE_1);
              setLine2(SAMPLE_2);
            }}
            className="btn-ghost"
          >
            Load the ICAO specimen
          </button>
        </div>

        <p className="text-[11px] leading-relaxed text-slate-500">
          Passports are outside this project's supported set (Aadhaar, PAN and
          certificates), but the MRZ rulebook is complete and this is the fastest
          way to see the validation layer working on its own.
        </p>
      </div>

      {error && <Banner>{error}</Banner>}

      {result && (
        <div className="animate-fade-up grid gap-4 lg:grid-cols-[19rem_1fr]">
          <div className="card p-5">
            <RiskGauge risk={result.risk} />
          </div>

          <div className="space-y-4">
            {result.line2_reconstructed && (
              <Banner tone="warn" title="Line 2 was reconstructed">
                {result.reconstruction_note ??
                  'Filler characters were re-inserted to recover the field layout. Checks that depend on inserted filler are reported as not evaluated.'}
              </Banner>
            )}

            <div className="card p-4">
              <p className="section-title">Check digits</p>
              <div className="mt-3 overflow-x-auto">
                <table className="w-full text-left text-xs">
                  <thead>
                    <tr className="border-b border-ink-700 font-mono text-[10px] uppercase tracking-wider text-slate-500">
                      <th className="pb-2 pr-3 font-normal">Field</th>
                      <th className="pb-2 pr-3 font-normal">Value</th>
                      <th className="pb-2 pr-3 font-normal">Stated</th>
                      <th className="pb-2 pr-3 font-normal">Computed</th>
                      <th className="pb-2 font-normal">Result</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-ink-700/60">
                    {result.check_digits.map((check) => (
                      <tr key={check.field}>
                        <td className="py-2 pr-3 text-slate-300">{check.field}</td>
                        <td className="py-2 pr-3 font-mono text-[11px] text-slate-400">
                          {check.value}
                        </td>
                        <td className="py-2 pr-3 font-mono text-slate-300">
                          {check.stated}
                        </td>
                        <td className="py-2 pr-3 font-mono text-slate-300">
                          {check.computed}
                        </td>
                        <td className="py-2">
                          {!check.evaluated ? (
                            <span className="chip bg-slate-500/10 text-slate-400">
                              not evaluated
                            </span>
                          ) : check.valid ? (
                            <span className="chip bg-verdict-accept/10 text-verdict-accept">
                              valid
                            </span>
                          ) : (
                            <span className="chip bg-verdict-reject/10 text-verdict-reject">
                              mismatch
                            </span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <p
                className={cx(
                  'mt-3 text-xs',
                  result.all_trusted_checks_valid
                    ? 'text-verdict-accept'
                    : 'text-verdict-reject',
                )}
              >
                {result.all_trusted_checks_valid
                  ? 'Every check that could be trusted is valid.'
                  : 'At least one trustworthy check failed.'}
              </p>
            </div>

            <div className="card p-4">
              <p className="section-title">Parsed</p>
              <dl className="mt-3 grid gap-2 sm:grid-cols-2">
                {Object.entries(result.parsed).map(([key, value]) => (
                  <div
                    key={key}
                    className="rounded-lg border border-ink-700 bg-ink-900/50 px-3 py-2"
                  >
                    <dt className="text-[10px] uppercase tracking-wider text-slate-500">
                      {fieldLabel(key)}
                    </dt>
                    <dd className="mt-0.5 font-mono text-xs text-slate-200">
                      {formatValue(value)}
                    </dd>
                  </div>
                ))}
              </dl>
            </div>

            <SignalList signals={result.signals} title="Validation evidence" />
          </div>
        </div>
      )}
    </div>
  );
}
