/**
 * What this deployment can actually do.
 *
 * /health reports capability, not just liveness, and every degradation names
 * what stops working rather than what is missing. "Face recognition
 * unavailable" is a fact about the server; "whether the presenter is the
 * holder cannot be verified" is what an operator has to act on -- so the
 * degradation text is shown prominently rather than folded away.
 */

import { useCallback, useEffect, useState } from 'react';
import { CheckCircle2, MinusCircle, RefreshCw, ShieldCheck } from 'lucide-react';
import { Banner, SectionHeading, Skeleton } from '../components/ui';
import { describeError } from '../api/client';
import { health } from '../api/endpoints';
import { cx } from '../lib/format';

interface HealthResponse {
  status?: string;
  service?: string;
  capabilities?: Record<string, unknown>;
  document_types_with_rulebook?: string[];
  layers?: Record<string, string>;
  degraded?: string[];
}

const CAPABILITY_LABEL: Record<string, string> = {
  image_decoding: 'Image decoding',
  heic: 'HEIC / HEIF photos',
  ocr: 'OCR engine',
  rule_validation: 'Rule validation',
  image_forensics: 'Image forensics',
  face_detection: 'Face detection',
  face_recognition: 'Face recognition',
  liveness_challenge_response: 'Liveness (challenge-response)',
  liveness_passive_classifier: 'Liveness (passive classifier)',
  authority_registry: 'Authority registry',
  audit_trail: 'Audit trail',
  object_storage: 'Object storage',
  gpu: 'GPU',
};

function renderValue(value: unknown) {
  if (value === true) {
    return (
      <span className="flex items-center gap-1.5 text-xs text-verdict-accept">
        <CheckCircle2 className="h-3.5 w-3.5" aria-hidden />
        available
      </span>
    );
  }
  if (value === false) {
    return (
      <span className="flex items-center gap-1.5 text-xs text-slate-500">
        <MinusCircle className="h-3.5 w-3.5" aria-hidden />
        not available
      </span>
    );
  }
  return <span className="font-mono text-xs text-slate-300">{String(value)}</span>;
}

export default function CapabilitiesPage() {
  const [data, setData] = useState<HealthResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      setData((await health()) as HealthResponse);
    } catch (err) {
      setError(describeError(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const capabilities = Object.entries(data?.capabilities ?? {});
  const layers = Object.entries(data?.layers ?? {});
  const degraded = data?.degraded ?? [];

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-2.5 text-xl font-semibold text-white">
            <ShieldCheck className="h-5 w-5 text-slate-500" aria-hidden />
            Capabilities
          </h1>
          <p className="mt-1 text-sm text-slate-500">
            What this particular deployment can and cannot do, right now.
          </p>
        </div>
        <button onClick={load} disabled={loading} className="btn-ghost">
          <RefreshCw className={cx('h-3.5 w-3.5', loading && 'animate-spin')} aria-hidden />
          Refresh
        </button>
      </header>

      {error && <Banner>{error}</Banner>}

      {loading && !data && (
        <div className="card space-y-3 p-4">
          <Skeleton className="h-8" />
          <Skeleton className="h-8" />
          <Skeleton className="h-8" />
        </div>
      )}

      {degraded.length > 0 && (
        <div>
          <SectionHeading
            title="Running degraded"
            hint="Each line says what stops working, not just what is missing."
          />
          <div className="space-y-2">
            {degraded.map((line, index) => (
              <Banner key={index} tone="warn">
                {line}
              </Banner>
            ))}
          </div>
        </div>
      )}

      {data && degraded.length === 0 && (
        <Banner tone="info" title="No degradation reported">
          Every component this build expects is reachable.
        </Banner>
      )}

      {capabilities.length > 0 && (
        <div>
          <SectionHeading title="Components" />
          <div className="card divide-y divide-ink-700/60">
            {capabilities.map(([key, value]) => (
              <div key={key} className="flex items-center justify-between gap-4 px-4 py-2.5">
                <span className="text-xs text-slate-300">
                  {CAPABILITY_LABEL[key] ?? key.replace(/_/g, ' ')}
                </span>
                {renderValue(value)}
              </div>
            ))}
          </div>
        </div>
      )}

      {layers.length > 0 && (
        <div>
          <SectionHeading
            title="Pipeline layers"
            hint="Reported by the server, including the ones deliberately switched off."
          />
          <div className="card divide-y divide-ink-700/60">
            {layers.map(([key, value]) => (
              <div
                key={key}
                className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 px-4 py-2.5"
              >
                <span className="font-mono text-[11px] uppercase tracking-wider text-slate-500">
                  {key.replace(/_/g, ' ')}
                </span>
                <span
                  className={cx(
                    'text-xs',
                    value === 'ready' ? 'text-verdict-accept' : 'text-slate-300',
                  )}
                >
                  {value}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {(data?.document_types_with_rulebook ?? []).length > 0 && (
        <div>
          <SectionHeading title="Document types with a rulebook" />
          <div className="flex flex-wrap gap-2">
            {data?.document_types_with_rulebook?.map((type) => (
              <span key={type} className="chip bg-ink-700 text-slate-300">
                {type}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
