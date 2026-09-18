import { render, screen } from '@testing-library/react';
import QrSignatureBadge from './QrSignatureBadge';
import type { Signal } from '../types/api';

function sig(code: string, evidence: Record<string, unknown> = {}): Signal {
  return {
    code,
    stage: 'database',
    title: 'Aadhaar QR signature',
    status: 'pass',
    severity: 'info',
    confidence: 1,
    reason: 'test',
    evidence,
    regions: [],
    blocking: false,
    created_at: '2026-09-19T00:00:00Z',
  };
}

test('renders nothing for a document with no QR signature signal', () => {
  // A PAN card has no QR; a badge announcing that would only be noise.
  const { container } = render(<QrSignatureBadge signals={[sig('pan.number.format')]} />);
  expect(container).toBeEmptyDOMElement();
});

test('a verified QR names the UIDAI key that signed it', () => {
  render(
    <QrSignatureBadge
      signals={[
        sig('aadhaar.qr.signature.verified', {
          signer: 'DS Unique Identification Authority of India 06',
          generated_on: '2026-09-18',
        }),
      ]}
    />,
  );
  expect(screen.getByText(/uidai signature verified/i)).toBeInTheDocument();
  expect(screen.getByText(/signed by DS Unique Identification Authority of India 06/)).toBeInTheDocument();
});

test('an invalid QR says which key should have signed it', () => {
  render(
    <QrSignatureBadge
      signals={[sig('aadhaar.qr.signature.invalid', { expected_signer: 'DS UIDAI 01' })]}
    />,
  );
  expect(screen.getByText(/qr signature invalid/i)).toBeInTheDocument();
  expect(screen.getByText(/expected DS UIDAI 01/)).toBeInTheDocument();
});

test('a QR from the key gap reads as unverifiable, not as a forgery', () => {
  render(<QrSignatureBadge signals={[sig('aadhaar.qr.signature.unknown_key')]} />);
  expect(screen.getByText(/not verifiable/i)).toBeInTheDocument();
  expect(screen.queryByText(/invalid/i)).not.toBeInTheDocument();
});
