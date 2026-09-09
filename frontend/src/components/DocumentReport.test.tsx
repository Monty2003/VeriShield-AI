/**
 * Renders a full report against a realistically awkward payload.
 *
 * The interesting cases are the ones a happy-path fixture never has: a
 * blocking failure whose score is LOW, a stage that errored, a check that was
 * skipped, and fields that are simply absent. Those are the shapes the
 * backend genuinely produces, and the ones that crash a UI written against an
 * imagined response.
 */

import { render, screen } from '@testing-library/react';
import DocumentReport from './DocumentReport';
import type { DocumentAnalysis, FieldConfidence } from '../types/api';

// Chart.js needs a real canvas; jsdom has none. The chart is not what this
// test is about, so it is replaced rather than worked around.
jest.mock('react-chartjs-2', () => ({
  Bar: () => <div data-testid="chart" />,
  Doughnut: () => <div data-testid="chart" />,
}));

const blank: FieldConfidence = {
  value: null,
  raw: null,
  confidence: 0,
  source: 'ocr',
  region: null,
};

function field(value: unknown, confidence = 0.9, source = 'ocr'): FieldConfidence {
  return { value, raw: String(value), confidence, source, region: null };
}

const analysis: DocumentAnalysis = {
  document_id: 'doc-1',
  filename: 'aadhaar-front.jpg',
  document_type: 'aadhaar',
  type_confidence: 0.94,
  side: 'front',
  fields: {
    full_name: field('ANNA MARIA'),
    surname: blank,
    given_names: blank,
    date_of_birth: field('1990-04-02'),
    sex: blank,
    father_name: blank,
    address: blank,
    document_number: field('XXXXXXXX2613', 1, 'ocr'),
    nationality: blank,
    issuing_authority: blank,
    issuing_country: blank,
    date_of_issue: blank,
    date_of_expiry: blank,
    place_of_birth: blank,
    mrz_line1: blank,
    mrz_line2: blank,
    raw_text: blank,
  },
  signals: [
    {
      code: 'aadhaar.checksum',
      stage: 'validate',
      title: 'Aadhaar checksum',
      status: 'pass',
      severity: 'critical',
      confidence: 1,
      reason: 'The Verhoeff checksum over the twelve digits is correct.',
      evidence: { algorithm: 'verhoeff' },
      regions: [],
      blocking: false,
      created_at: '2026-09-09T00:00:00Z',
    },
    {
      code: 'document.expired',
      stage: 'validate',
      title: 'Document validity',
      status: 'fail',
      severity: 'low',
      confidence: 1,
      // The point of the fixture: blocking without a high score.
      reason: 'The document expired on 2021-03-04 and cannot be accepted.',
      evidence: { expired_on: '2021-03-04' },
      regions: [{ x: 10, y: 20, width: 100, height: 30, label: 'expiry' }],
      blocking: true,
      created_at: '2026-09-09T00:00:00Z',
    },
    {
      code: 'face.match',
      stage: 'face',
      title: 'Portrait comparison',
      status: 'skip',
      severity: 'medium',
      confidence: 1,
      reason: 'No presented photograph was supplied, so nothing was compared.',
      evidence: {},
      regions: [],
      blocking: false,
      created_at: '2026-09-09T00:00:00Z',
    },
    {
      code: 'ocr.stage_error',
      stage: 'ocr',
      title: 'OCR stage',
      status: 'error',
      severity: 'medium',
      confidence: 1,
      reason: 'The OCR stage failed with an internal error and produced no evidence.',
      evidence: {},
      regions: [],
      blocking: false,
      created_at: '2026-09-09T00:00:00Z',
    },
  ],
  risk: {
    score: 5.4,
    band: 'low',
    decision: 'manual_review',
    confidence: 0.62,
    top_reasons: ['Document validity failed', 'One stage produced no evidence'],
    blocking_codes: ['document.expired'],
    blocking_reasons: ['The document expired on 2021-03-04 and cannot be accepted.'],
    contributions: [
      {
        code: 'document.expired',
        title: 'Document validity',
        stage: 'validate',
        status: 'fail',
        severity: 'low',
        confidence: 1,
        points: 5,
        reason: 'expired',
      },
    ],
    coverage: { validate: 'ran', ocr: 'errored', face: 'skipped' },
  },
  image_width: 800,
  image_height: 500,
  face_region: null,
  processing_ms: { ingest: 12.4, ocr: 2100.2, validate: 3.1 },
  created_at: '2026-09-09T00:00:00Z',
};

test('shows a blocking failure separately from the risk score', () => {
  render(<DocumentReport analysis={analysis} />);

  // A low score and a withheld acceptance must both be visible at once --
  // collapsing them into one number is the mistake this UI exists to avoid.
  expect(screen.getByText('5.4')).toBeInTheDocument();
  expect(screen.getByText(/acceptance withheld/i)).toBeInTheDocument();
  expect(screen.getByText(/manual review/i)).toBeInTheDocument();
});

test('distinguishes a skipped check from an errored one', () => {
  render(<DocumentReport analysis={analysis} />);
  expect(screen.getByText(/could not run/i)).toBeInTheDocument();
  expect(screen.getByText(/not applicable/i)).toBeInTheDocument();
});

test('survives a document with no risk assessment at all', () => {
  render(<DocumentReport analysis={{ ...analysis, risk: null, signals: [] }} />);
  expect(screen.getByText(/no risk assessment was produced/i)).toBeInTheDocument();
});
