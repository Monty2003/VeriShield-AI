/**
 * Confirming a course or internship certificate with its issuer.
 *
 * Such a certificate carries nothing that can be verified offline; the proof
 * is on the issuer's own page, which its QR links to. The backend does not
 * open that page -- issuer sites block automated requests or build the page in
 * the browser -- so the reviewer's browser does, and this panel says what to
 * compare when it does.
 *
 * The address comes from a QR in an uploaded image, so it is treated as
 * hostile: only http and https are ever linked, and it opens in a new tab with
 * no referrer and no handle back to this page.
 */

import type { LucideIcon } from 'lucide-react';
import { CheckCircle2, ExternalLink, Link2, ShieldAlert } from 'lucide-react';
import type { Signal } from '../types/api';
import { cx } from '../lib/format';

export function issuerLink(signals: Signal[]): { url: string; domain: string } | null {
  const raw = signals.find((s) => s.code === 'certificate.qr.link')?.evidence?.url;
  if (typeof raw !== 'string') return null;
  try {
    const parsed = new URL(raw);
    if (parsed.protocol !== 'https:' && parsed.protocol !== 'http:') return null;
    return { url: parsed.href, domain: parsed.hostname.replace(/^www\./, '') };
  } catch {
    return null;
  }
}

interface Finding {
  code: string;
  good: boolean;
  text: string;
}

const FINDINGS: Finding[] = [
  { code: 'certificate.qr.issuer_site', good: true, text: 'Leads to the issuer the certificate names' },
  { code: 'certificate.qr.carries_id', good: true, text: "The link includes this certificate's ID" },
  { code: 'certificate.qr.unrelated_site', good: false, text: 'Leads to a site not named in the certificate text' },
  { code: 'certificate.qr.redirect', good: false, text: 'Goes through a redirect service -- check where it lands' },
];

export default function IssuerLinkPanel({ signals }: { signals: Signal[] }) {
  const link = issuerLink(signals);
  if (!link) return null;

  const present = new Set(signals.map((s) => s.code));
  const findings = FINDINGS.filter((f) => present.has(f.code));
  const idSignal = signals.find((s) => s.code === 'certificate.reference.found');
  const idEnding = idSignal?.reason.match(/ending (\S{1,4})\)/)?.[1];

  const checks = [
    "The holder's name is the one printed on the certificate",
    idEnding ? `The ID ends in ${idEnding}` : 'Any ID shown matches the certificate',
    'The course or programme, and its dates, are the same',
  ];

  return (
    <div className="card p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="section-title flex items-center gap-2">
            <Link2 className="h-4 w-4 text-cyber-cyan" aria-hidden />
            Confirm with the issuer
          </p>
          <p className="mt-1 text-[11px] leading-relaxed text-slate-400">
            This certificate's QR links to <span className="font-mono text-slate-200">{link.domain}</span>.
            Nothing on the certificate itself can prove it genuine; the issuer's page can.
          </p>
        </div>
        <a
          href={link.url}
          target="_blank"
          rel="noopener noreferrer nofollow"
          className="btn-primary shrink-0"
        >
          Open {link.domain}
          <ExternalLink className="h-3.5 w-3.5" aria-hidden />
        </a>
      </div>

      {findings.length > 0 && (
        <ul className="mt-3 flex flex-wrap gap-2">
          {findings.map((finding) => {
            const Icon: LucideIcon = finding.good ? CheckCircle2 : ShieldAlert;
            return (
              <li
                key={finding.code}
                className={cx(
                  'flex items-center gap-1.5 border px-2 py-1 text-[11px]',
                  finding.good
                    ? 'border-verdict-accept/40 text-verdict-accept'
                    : 'border-verdict-review/50 text-verdict-review',
                )}
              >
                <Icon className="h-3.5 w-3.5" aria-hidden />
                {finding.text}
              </li>
            );
          })}
        </ul>
      )}

      <p className="mt-4 text-[11px] font-medium uppercase tracking-wider text-slate-500">
        On that page, check that
      </p>
      <ul className="mt-2 space-y-1.5">
        {checks.map((check) => (
          <li key={check} className="flex gap-2 text-xs text-slate-300">
            <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-cyber-cyan/70" aria-hidden />
            {check}
          </li>
        ))}
      </ul>
      <p className="mt-3 text-[11px] leading-relaxed text-slate-500">
        Then record the decision below. If the page shows a different name, ID or
        course -- or no certificate at all -- reject it.
      </p>
    </div>
  );
}
