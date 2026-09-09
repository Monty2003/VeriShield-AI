/**
 * Display vocabulary.
 *
 * Every label and colour the UI uses for a backend enum lives here, so a
 * status can never be rendered green in one panel and amber in another.
 */

import type {
  Decision,
  DocumentType,
  RiskBand,
  Severity,
  SignalStatus,
  Stage,
} from '../types/api';

// --- decisions -----------------------------------------------------------

export const DECISION_LABEL: Record<Decision, string> = {
  accept: 'Accept',
  manual_review: 'Manual review',
  reject: 'Reject',
};

/**
 * Deliberately not "genuine / fake". The backend assesses risk and hands
 * evidence to a person; the UI must not quietly upgrade that into a verdict
 * about authenticity.
 */
export const DECISION_BLURB: Record<Decision, string> = {
  accept: 'Nothing found that warrants holding this back.',
  manual_review: 'Evidence is mixed or incomplete. A person should look.',
  reject: 'Strong evidence of a problem. Do not proceed on this alone.',
};

// Kept in step with tailwind.config.js by hand: SVG strokes and Chart.js
// datasets cannot read a Tailwind class, and a gauge drawn in the old
// palette next to a chip drawn in the new one looks like a bug.
export const DECISION_HEX: Record<Decision, string> = {
  accept: '#00ff9f',
  manual_review: '#ffb800',
  reject: '#ff003c',
};

export const DECISION_CLASS: Record<Decision, string> = {
  accept: 'text-verdict-accept bg-verdict-accept/10 border-verdict-accept/30',
  manual_review: 'text-verdict-review bg-verdict-review/10 border-verdict-review/30',
  reject: 'text-verdict-reject bg-verdict-reject/10 border-verdict-reject/30',
};

// --- risk bands ----------------------------------------------------------

export const BAND_LABEL: Record<RiskBand, string> = {
  low: 'Low risk',
  medium: 'Medium risk',
  high: 'High risk',
};

export const BAND_HEX: Record<RiskBand, string> = {
  low: '#00ff9f',
  medium: '#ffb800',
  high: '#ff003c',
};

// --- signal statuses -----------------------------------------------------

export interface StatusMeta {
  label: string;
  hex: string;
  /** Full class string -- Tailwind's scanner needs literals, not built strings. */
  chip: string;
  dot: string;
  blurb: string;
}

export const STATUS_META: Record<SignalStatus, StatusMeta> = {
  pass: {
    label: 'Pass',
    hex: '#00ff9f',
    chip: 'bg-verdict-accept/10 text-verdict-accept',
    dot: 'bg-verdict-accept',
    blurb: 'The check ran and the document satisfied it.',
  },
  warn: {
    label: 'Warn',
    hex: '#ffb800',
    chip: 'bg-verdict-review/10 text-verdict-review',
    dot: 'bg-verdict-review',
    blurb: 'The check ran. Suspicious, but not disqualifying.',
  },
  fail: {
    label: 'Fail',
    hex: '#ff003c',
    chip: 'bg-verdict-reject/10 text-verdict-reject',
    dot: 'bg-verdict-reject',
    blurb: 'The check ran and the document failed it.',
  },
  // Skip and error look different on purpose. A skipped check is not evidence
  // of anything; an errored one means evidence we expected is missing.
  skip: {
    label: 'Skip',
    hex: '#64748b',
    chip: 'bg-slate-500/10 text-slate-400',
    dot: 'bg-slate-500',
    blurb: 'The check did not apply to this document.',
  },
  error: {
    label: 'Error',
    hex: '#c084fc',
    chip: 'bg-purple-500/10 text-purple-400',
    dot: 'bg-purple-500',
    blurb: 'The check could not run. Expected evidence is missing.',
  },
};

export const SEVERITY_LABEL: Record<Severity, string> = {
  info: 'Info',
  low: 'Low',
  medium: 'Medium',
  high: 'High',
  critical: 'Critical',
};

export const SEVERITY_RANK: Record<Severity, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
  info: 4,
};

export const STATUS_RANK: Record<SignalStatus, number> = {
  fail: 0,
  error: 1,
  warn: 2,
  pass: 3,
  skip: 4,
};

// --- pipeline stages -----------------------------------------------------

/** Order mirrors the architecture layers, so the trace reads top to bottom. */
export const STAGE_ORDER: Stage[] = [
  'ingest',
  'classify',
  'ocr',
  'extract',
  'validate',
  'forensics',
  'face',
  'cross_doc',
  'database',
];

export const STAGE_LABEL: Record<Stage, string> = {
  ingest: 'Ingest',
  classify: 'Classify',
  ocr: 'OCR',
  extract: 'Extract',
  validate: 'Validate',
  forensics: 'Forensics',
  face: 'Face',
  cross_doc: 'Cross-document',
  database: 'Registry',
};

export const STAGE_BLURB: Record<Stage, string> = {
  ingest: 'Decode the image, fix orientation, check it is usable.',
  classify: 'Decide what kind of document this is.',
  ocr: 'Read the text off the image.',
  extract: 'Turn read text into named fields.',
  validate: 'Apply the rulebook: checksums, formats, dates.',
  forensics: 'Look for signs the image itself was edited.',
  face: 'Find and compare the portrait.',
  cross_doc: 'Do these documents describe the same person?',
  database: 'Check the identifier against a registry.',
};

export const DOCUMENT_TYPE_LABEL: Record<DocumentType, string> = {
  passport: 'Passport',
  visa: 'Visa',
  aadhaar: 'Aadhaar',
  pan: 'PAN card',
  driving_licence: 'Driving licence',
  voter_id: 'Voter ID',
  certificate: 'Certificate',
  unknown: 'Unknown',
};

/** Types this project supports end to end. Others still run through it. */
export const SUPPORTED_TYPES: DocumentType[] = ['aadhaar', 'pan', 'certificate'];

export const ALL_DOCUMENT_TYPES: DocumentType[] = [
  'aadhaar',
  'pan',
  'certificate',
  'passport',
  'visa',
  'driving_licence',
  'voter_id',
  'unknown',
];

export const COVERAGE_CLASS: Record<string, string> = {
  ran: 'text-verdict-accept',
  skipped: 'text-slate-500',
  errored: 'text-purple-400',
};

// --- value formatting ----------------------------------------------------

const FIELD_LABELS: Record<string, string> = {
  full_name: 'Full name',
  surname: 'Surname',
  given_names: 'Given names',
  date_of_birth: 'Date of birth',
  sex: 'Sex',
  father_name: 'Father name',
  address: 'Address',
  document_number: 'Document number',
  nationality: 'Nationality',
  issuing_authority: 'Issuing authority',
  issuing_country: 'Issuing country',
  date_of_issue: 'Date of issue',
  date_of_expiry: 'Date of expiry',
  place_of_birth: 'Place of birth',
  mrz_line1: 'MRZ line 1',
  mrz_line2: 'MRZ line 2',
  raw_text: 'Full OCR text',
};

export function fieldLabel(key: string): string {
  return FIELD_LABELS[key] ?? key.replace(/_/g, ' ');
}

export function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '--';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return JSON.stringify(value);
}

export function percent(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '--';
  return `${(value * 100).toFixed(digits)}%`;
}

export function ms(value: number | null | undefined): string {
  if (value === null || value === undefined) return '--';
  return value >= 1000 ? `${(value / 1000).toFixed(2)}s` : `${Math.round(value)}ms`;
}

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return '--';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '--';
  const seconds = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (seconds < 60) return 'just now';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

export function shortDateTime(iso: string | null | undefined): string {
  if (!iso) return '--';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '--';
  return date.toLocaleString(undefined, {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function bytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(0)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(' ');
}
