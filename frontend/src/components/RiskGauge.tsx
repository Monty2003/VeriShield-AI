/**
 * The risk dial.
 *
 * Shows two numbers that are constantly confused with each other and must
 * never be merged:
 *
 *   score      -- how much evidence of a problem was found (0 clean .. 100)
 *   confidence -- how COMPLETE the evidence was, whatever it said
 *
 * A 4/100 score at 40% confidence is not a clean document; it is a document
 * nobody managed to check. The outer arc is the score, the inner arc is the
 * confidence, and they are labelled separately on purpose.
 */

import { useEffect, useState } from 'react';
import { ShieldAlert } from 'lucide-react';
import type { RiskAssessment } from '../types/api';
import {
  BAND_HEX,
  BAND_LABEL,
  DECISION_BLURB,
  DECISION_CLASS,
  DECISION_LABEL,
  cx,
  percent,
} from '../lib/format';

const SIZE = 208;
const RADIUS = 88;
const SWEEP = 0.75; // 270 degrees -- the gap sits at the bottom
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;
const ARC = CIRCUMFERENCE * SWEEP;

const INNER_RADIUS = 68;
const INNER_ARC = 2 * Math.PI * INNER_RADIUS * SWEEP;

export default function RiskGauge({ risk }: { risk: RiskAssessment }) {
  // Animate from zero so the needle visibly travels -- a number that is
  // simply there reads as a label, one that moves reads as a measurement.
  const [shown, setShown] = useState(0);
  const [shownConfidence, setShownConfidence] = useState(0);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      setShown(risk.score);
      setShownConfidence(risk.confidence);
    });
    return () => window.cancelAnimationFrame(frame);
  }, [risk.score, risk.confidence]);

  const colour = BAND_HEX[risk.band];
  const blocked = risk.blocking_reasons.length > 0;

  return (
    <div className="flex flex-col items-center">
      <div className="relative" style={{ width: SIZE, height: SIZE }}>
        <svg
          width={SIZE}
          height={SIZE}
          viewBox={`0 0 ${SIZE} ${SIZE}`}
          className="-rotate-[225deg]"
          role="img"
          aria-label={`Risk score ${risk.score} of 100, ${BAND_LABEL[risk.band]}`}
        >
          <circle
            cx={SIZE / 2}
            cy={SIZE / 2}
            r={RADIUS}
            fill="none"
            stroke="#1a2233"
            strokeWidth={14}
            strokeLinecap="round"
            strokeDasharray={`${ARC} ${CIRCUMFERENCE}`}
          />
          <circle
            cx={SIZE / 2}
            cy={SIZE / 2}
            r={RADIUS}
            fill="none"
            stroke={colour}
            strokeWidth={14}
            strokeLinecap="round"
            strokeDasharray={`${ARC} ${CIRCUMFERENCE}`}
            strokeDashoffset={ARC * (1 - Math.min(shown, 100) / 100)}
            style={{
              transition: 'stroke-dashoffset 900ms cubic-bezier(0.22, 1, 0.36, 1)',
              filter: `drop-shadow(0 0 6px ${colour}55)`,
            }}
          />

          <circle
            cx={SIZE / 2}
            cy={SIZE / 2}
            r={INNER_RADIUS}
            fill="none"
            stroke="#151d2c"
            strokeWidth={5}
            strokeLinecap="round"
            strokeDasharray={`${INNER_ARC} ${2 * Math.PI * INNER_RADIUS}`}
          />
          <circle
            cx={SIZE / 2}
            cy={SIZE / 2}
            r={INNER_RADIUS}
            fill="none"
            stroke="#64748b"
            strokeWidth={5}
            strokeLinecap="round"
            strokeDasharray={`${INNER_ARC} ${2 * Math.PI * INNER_RADIUS}`}
            strokeDashoffset={INNER_ARC * (1 - shownConfidence)}
            style={{ transition: 'stroke-dashoffset 900ms ease-out 150ms' }}
          />
        </svg>

        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span
            className="font-mono text-5xl font-semibold tabular-nums"
            style={{ color: colour }}
          >
            {risk.score.toFixed(1)}
          </span>
          <span className="mt-0.5 text-[11px] uppercase tracking-[0.2em] text-slate-500">
            risk / 100
          </span>
          <span className="mt-2 text-xs text-slate-400">
            evidence {percent(risk.confidence)}
          </span>
        </div>
      </div>

      <div
        className={cx(
          'mt-4 rounded-lg border px-4 py-2 text-center',
          DECISION_CLASS[risk.decision],
        )}
      >
        <p className="text-sm font-semibold">{DECISION_LABEL[risk.decision]}</p>
        <p className="mt-0.5 text-[11px] opacity-80">{BAND_LABEL[risk.band]}</p>
      </div>

      <p className="mt-2 max-w-[15rem] text-center text-xs text-slate-500">
        {DECISION_BLURB[risk.decision]}
      </p>

      {blocked && (
        <div className="mt-4 w-full rounded-lg border border-verdict-review/40 bg-verdict-review/10 px-3 py-2.5">
          <p className="flex items-center gap-1.5 text-xs font-semibold text-verdict-review">
            <ShieldAlert className="h-3.5 w-3.5" aria-hidden />
            Acceptance withheld
          </p>
          {/* Separate from the score on purpose: an expired document is
              almost certainly genuine -- low fraud risk -- and still must
              not be accepted. Inflating the score to express that would be
              a lie about the evidence. */}
          <p className="mt-1 text-[11px] leading-relaxed text-slate-400">
            Not a fraud finding. These are hard validity failures that prevent
            acceptance whatever the score says.
          </p>
          <ul className="mt-2 space-y-1">
            {risk.blocking_reasons.map((reason, index) => (
              <li key={index} className="text-[11px] leading-relaxed text-slate-300">
                &bull; {reason}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
