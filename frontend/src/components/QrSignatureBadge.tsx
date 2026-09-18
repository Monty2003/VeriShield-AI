/**
 * Whether the Aadhaar QR is UIDAI's, stated where it cannot be missed.
 *
 * The signal is in the evidence list either way, but it decides how much every
 * other QR check is worth: a name that matches a VERIFIED QR matches UIDAI's
 * record, while a name that matches an unverified one matches whatever the QR
 * happens to say. That difference belongs at the top of the report, not
 * fifteen rows down.
 *
 * Renders nothing when there is no QR signature signal -- a PAN card or a
 * certificate has no QR, and a badge saying so would be noise.
 */

import type { LucideIcon } from 'lucide-react';
import { ShieldAlert, ShieldCheck, ShieldOff, ShieldQuestion, ShieldX } from 'lucide-react';
import type { Signal } from '../types/api';
import { cx } from '../lib/format';

interface Look {
  label: string;
  icon: LucideIcon;
  className: string;
}

const LOOKS: Record<string, Look> = {
  'aadhaar.qr.signature.verified': {
    label: 'UIDAI signature verified',
    icon: ShieldCheck,
    className: 'border-verdict-accept/60 bg-verdict-accept/10 text-verdict-accept',
  },
  'aadhaar.qr.signature.invalid': {
    label: 'QR signature invalid',
    icon: ShieldX,
    className: 'border-verdict-reject/60 bg-verdict-reject/10 text-verdict-reject',
  },
  'aadhaar.qr.signature.unknown_key': {
    label: 'QR signature not verifiable',
    icon: ShieldQuestion,
    className: 'border-verdict-review/60 bg-verdict-review/10 text-verdict-review',
  },
  'aadhaar.qr.signature.unavailable': {
    label: 'QR signature not checked',
    icon: ShieldAlert,
    className: 'border-purple-500/60 bg-purple-500/10 text-purple-300',
  },
  'aadhaar.qr.signature.absent': {
    label: 'QR carries no signature',
    icon: ShieldOff,
    className: 'border-slate-500/60 bg-slate-500/10 text-slate-400',
  },
};

export function findQrSignature(signals: Signal[]): Signal | undefined {
  return signals.find((s) => s.code.startsWith('aadhaar.qr.signature.'));
}

export default function QrSignatureBadge({ signals }: { signals: Signal[] }) {
  const found = findQrSignature(signals);
  if (!found) return null;

  const look = LOOKS[found.code];
  if (!look) return null;

  const signer = typeof found.evidence.signer === 'string' ? found.evidence.signer : null;
  const expected =
    typeof found.evidence.expected_signer === 'string' ? found.evidence.expected_signer : null;
  const generated =
    typeof found.evidence.generated_on === 'string' ? found.evidence.generated_on : null;
  // A key recovered from QR signatures has no certificate behind it. Still a
  // verification -- but a reviewer should know which kind it was.
  const recovered = found.evidence.certified === false;
  const Icon = look.icon;

  return (
    <div
      className={cx('flex items-start gap-3 border px-3 py-2.5', look.className)}
      title={found.reason}
    >
      <Icon className="mt-0.5 h-5 w-5 shrink-0" aria-hidden />
      <div className="min-w-0">
        <p className="font-display text-xs font-bold uppercase tracking-wider">{look.label}</p>
        <p className="mt-0.5 truncate font-mono text-[10px] opacity-80">
          {signer
            ? `signed by ${signer}`
            : expected
              ? `expected ${expected}`
              : 'Aadhaar Secure QR'}
          {recovered ? ' · recovered key, no certificate' : ''}
          {generated ? ` · QR generated ${generated}` : ''}
        </p>
      </div>
    </div>
  );
}
