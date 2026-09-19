/**
 * Where a person makes the decision the system handed over.
 *
 * The system recommends and a person decides -- that is the whole premise of
 * this product, and until this panel existed the decision had nowhere to go.
 *
 * The note rules shown here mirror the server's, only to save a round trip.
 * The server enforces them and fills in everything that matters for trust --
 * who decided, and what the system had said -- so nothing typed here can
 * misstate either.
 */

import { useCallback, useEffect, useState } from 'react';
import type { LucideIcon } from 'lucide-react';
import { CheckCircle2, Gavel, HelpCircle, History, XCircle } from 'lucide-react';
import { useAuth } from '../auth/AuthContext';
import { describeError } from '../api/client';
import { decisionHistory, recordDecision } from '../api/endpoints';
import type { ReviewSubject } from '../api/endpoints';
import type { Decision, DecisionHistory, ReviewDecision, ReviewOutcome } from '../types/api';
import { DECISION_LABEL, cx, shortDateTime } from '../lib/format';
import { Banner, Skeleton, Spinner } from './ui';

export const MIN_NOTE_CHARS = 10;

interface OutcomeLook {
  label: string;
  verb: string;
  icon: LucideIcon;
  className: string;
}

export const OUTCOMES: Record<ReviewOutcome, OutcomeLook> = {
  approve: {
    label: 'Approved',
    verb: 'Approve',
    icon: CheckCircle2,
    className: 'border-verdict-accept/60 bg-verdict-accept/10 text-verdict-accept',
  },
  reject: {
    label: 'Rejected',
    verb: 'Reject',
    icon: XCircle,
    className: 'border-verdict-reject/60 bg-verdict-reject/10 text-verdict-reject',
  },
  needs_info: {
    label: 'Needs more info',
    verb: 'Needs more info',
    icon: HelpCircle,
    className: 'border-verdict-review/60 bg-verdict-review/10 text-verdict-review',
  },
};

/** Why a note is needed for this choice, or '' when it is optional. */
export function noteRule(
  outcome: ReviewOutcome | null,
  systemDecision: Decision | null | undefined,
  blocked: boolean,
): string {
  if (outcome === 'reject') return 'A rejection needs a reason.';
  if (outcome === 'needs_info') return 'Say what information is needed.';
  if (outcome === 'approve' && blocked) return 'Approving past a blocking finding needs a reason.';
  if (outcome === 'approve' && systemDecision === 'reject') {
    return 'Approving against the system needs a justification.';
  }
  return '';
}

interface Props {
  subject: ReviewSubject;
  subjectId: string;
  systemDecision?: Decision | null;
  blocked?: boolean;
  onDecided?: (decision: ReviewDecision) => void;
}

export default function ReviewPanel({
  subject,
  subjectId,
  systemDecision,
  blocked = false,
  onDecided,
}: Props) {
  const { can } = useAuth();
  const [history, setHistory] = useState<DecisionHistory | null>(null);
  const [loadError, setLoadError] = useState('');
  const [outcome, setOutcome] = useState<ReviewOutcome | null>(null);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoadError('');
    try {
      setHistory(await decisionHistory(subject, subjectId));
    } catch (err) {
      setLoadError(describeError(err));
    }
  }, [subject, subjectId]);

  useEffect(() => {
    load();
  }, [load]);

  const rule = noteRule(outcome, systemDecision, blocked);
  const noteShort = Boolean(rule) && note.trim().length < MIN_NOTE_CHARS;

  async function submit() {
    if (!outcome) return;
    setBusy(true);
    setError('');
    try {
      const made = await recordDecision(subject, subjectId, outcome, note.trim());
      setOutcome(null);
      setNote('');
      await load();
      onDecided?.(made);
    } catch (err) {
      // The server's refusal says exactly which rule applied; show it as is.
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  const current = history?.current ?? null;
  const earlier = history ? history.decisions.slice(0, -1).reverse() : [];

  return (
    <div className="card p-4">
      <p className="section-title">
        <Gavel className="h-3.5 w-3.5" aria-hidden />
        Human decision
      </p>
      <p className="mt-1 font-mono text-[10px] leading-relaxed text-slate-500">
        The system recommends. A person decides, and it is recorded.
      </p>

      {loadError && (
        <div className="mt-3">
          <Banner>{loadError}</Banner>
        </div>
      )}

      {!history && !loadError && <Skeleton className="mt-3 h-12 w-full" />}

      {history && !history.available && (
        <div className="mt-3">
          <Banner tone="warn">
            The audit store is unreachable, so no decision can be recorded -- and a
            decision that is not recorded has not been made.
          </Banner>
        </div>
      )}

      {history?.available && (
        <>
          {current ? (
            <DecisionCard decision={current} />
          ) : (
            <p className="mt-3 border border-dashed border-slate-600 px-3 py-2.5 font-mono text-[11px] text-slate-400">
              Awaiting a decision.
            </p>
          )}

          {earlier.length > 0 && (
            <details className="mt-2">
              <summary className="flex cursor-pointer list-none items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider text-slate-500 hover:text-slate-300">
                <History className="h-3 w-3" aria-hidden />
                {earlier.length} earlier decision{earlier.length === 1 ? '' : 's'}
              </summary>
              <div className="mt-2 space-y-2 opacity-80">
                {earlier.map((d) => (
                  <DecisionCard key={d.decision_id} decision={d} />
                ))}
              </div>
            </details>
          )}

          {can('review:decide') ? (
            <div className="mt-4 border-t border-cyber-cyan/20 pt-3">
              <p className="label">{current ? 'Record a new decision' : 'Decide'}</p>
              <div className="grid grid-cols-3 gap-1.5">
                {(Object.keys(OUTCOMES) as ReviewOutcome[]).map((key) => {
                  const look = OUTCOMES[key];
                  const Icon = look.icon;
                  const chosen = outcome === key;
                  return (
                    <button
                      key={key}
                      type="button"
                      onClick={() => setOutcome(chosen ? null : key)}
                      disabled={busy}
                      aria-pressed={chosen}
                      className={cx(
                        'flex flex-col items-center gap-1 border px-2 py-2 text-[10px] font-bold uppercase tracking-wider transition-colors',
                        chosen
                          ? look.className
                          : 'border-slate-600 text-slate-400 hover:border-slate-400',
                      )}
                    >
                      <Icon className="h-4 w-4" aria-hidden />
                      {look.verb}
                    </button>
                  );
                })}
              </div>

              <textarea
                value={note}
                onChange={(event) => setNote(event.target.value)}
                rows={3}
                maxLength={2000}
                disabled={busy}
                placeholder={rule ? `${rule} (required)` : 'Note (optional)'}
                className="input mt-2 text-xs"
                aria-label="Decision note"
              />
              {rule && (
                <p
                  className={cx(
                    'mt-1 font-mono text-[10px]',
                    noteShort ? 'text-verdict-review' : 'text-slate-500',
                  )}
                >
                  {rule} At least {MIN_NOTE_CHARS} characters.
                </p>
              )}

              {error && (
                <div className="mt-2">
                  <Banner>{error}</Banner>
                </div>
              )}

              <button
                type="button"
                onClick={submit}
                disabled={!outcome || noteShort || busy}
                className="btn-primary mt-3 w-full"
              >
                {busy && <Spinner />}
                Record decision
              </button>
            </div>
          ) : (
            <p className="mt-3 font-mono text-[10px] leading-relaxed text-slate-500">
              Recording a decision needs the review:decide permission, which
              reviewers and admins hold.
            </p>
          )}
        </>
      )}
    </div>
  );
}

function DecisionCard({ decision }: { decision: ReviewDecision }) {
  const look = OUTCOMES[decision.outcome];
  const Icon = look.icon;
  const flags: Array<{ text: string; tone: string }> = [];
  if (decision.agrees_with_system === true) {
    flags.push({ text: 'agrees with the system', tone: 'text-slate-400' });
  }
  if (decision.agrees_with_system === false) {
    flags.push({ text: 'against the system', tone: 'text-verdict-review' });
  }
  if (decision.overrides_block) {
    flags.push({ text: 'past a blocking finding', tone: 'text-verdict-review' });
  }
  if (decision.self_reviewed) {
    flags.push({ text: 'self-reviewed', tone: 'text-verdict-review' });
  }

  return (
    <div className={cx('mt-3 border px-3 py-2.5', look.className)}>
      <p className="flex items-center gap-1.5 text-xs font-bold uppercase tracking-wider">
        <Icon className="h-4 w-4" aria-hidden />
        {look.label}
      </p>
      <p className="mt-1 font-mono text-[10px] text-slate-400">
        by {decision.reviewer} ({decision.reviewer_role}) &middot;{' '}
        {shortDateTime(decision.decided_at)}
        {decision.system_decision
          ? ` · system said ${DECISION_LABEL[decision.system_decision]}`
          : ''}
      </p>
      {decision.note && (
        <p className="mt-1.5 border-l-2 border-current pl-2 text-[11px] leading-relaxed text-slate-300">
          {decision.note}
        </p>
      )}
      {flags.length > 0 && (
        <p className="mt-1.5 flex flex-wrap gap-x-3 font-mono text-[10px] uppercase tracking-wider">
          {flags.map((f) => (
            <span key={f.text} className={f.tone}>
              {f.text}
            </span>
          ))}
        </p>
      )}
    </div>
  );
}
