/**
 * The Verify page follows the document.
 *
 * Aadhaar, PAN, certificates and passports keep their evidence in different
 * places. What matters here is that the form asks each for what it has --
 * and that every result which needs something more from the user turns into
 * an action: add the back, retake the front, add the front to a back, or
 * check it with the rules of the document the user said it was.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import VerifyPage from './VerifyPage';
import { decisionHistory, verifyCase, verifyDocument } from '../api/endpoints';
import type {
  DocumentAnalysis,
  DocumentSide,
  DocumentType,
  ExtractedFields,
  FieldConfidence,
  RiskAssessment,
  VerificationResult,
} from '../types/api';

jest.mock('react-chartjs-2', () => ({
  Bar: () => <div data-testid="chart" />,
  Doughnut: () => <div data-testid="chart" />,
}));
jest.mock('../auth/AuthContext', () => ({ useAuth: () => ({ can: () => true }) }));
jest.mock('../api/endpoints', () => ({
  verifyDocument: jest.fn(),
  verifyCase: jest.fn(),
  decisionHistory: jest.fn(),
  recordDecision: jest.fn(),
}));

const mockedDocument = verifyDocument as jest.MockedFunction<typeof verifyDocument>;
const mockedCase = verifyCase as jest.MockedFunction<typeof verifyCase>;

const blank: FieldConfidence = { value: null, raw: null, confidence: 0, source: 'ocr', region: null };
const fields = Object.fromEntries(
  [
    'full_name', 'surname', 'given_names', 'date_of_birth', 'sex', 'father_name',
    'address', 'document_number', 'nationality', 'issuing_authority',
    'issuing_country', 'date_of_issue', 'date_of_expiry', 'place_of_birth',
    'mrz_line1', 'mrz_line2', 'raw_text',
  ].map((name) => [name, blank]),
) as unknown as ExtractedFields;

function risk(blocking_codes: string[] = []): RiskAssessment {
  return {
    score: 8,
    band: 'low',
    decision: blocking_codes.length ? 'manual_review' : 'accept',
    confidence: 1,
    top_reasons: [],
    blocking_codes,
    blocking_reasons: blocking_codes.map(() => 'held'),
    contributions: [],
    coverage: {},
  };
}

function analysis(
  blocking_codes: string[] = [],
  document_type: DocumentType = 'aadhaar',
  side: DocumentSide = 'front',
): DocumentAnalysis {
  return {
    document_id: 'doc-1',
    filename: 'image.jpg',
    document_type,
    type_confidence: 1,
    side,
    fields,
    signals: [],
    risk: risk(blocking_codes),
    face_region: null,
    processing_ms: {},
  } as unknown as DocumentAnalysis;
}

function caseResult(blocking_codes: string[] = []): VerificationResult {
  return {
    case_id: 'case-1',
    documents: [],
    cross_document_signals: [],
    overall_risk: risk(blocking_codes),
    started_at: '2026-09-19T00:00:00Z',
    completed_at: '2026-09-19T00:00:05Z',
  };
}

const FRONT = new File(['front'], 'front.jpg', { type: 'image/jpeg' });
const BACK = new File(['back'], 'back.jpg', { type: 'image/jpeg' });

function fileInputs(container: HTMLElement) {
  return container.querySelectorAll<HTMLInputElement>('input[type="file"]');
}

function choose(name: RegExp) {
  fireEvent.click(screen.getByRole('button', { name }));
}

async function submitFront(container: HTMLElement) {
  fireEvent.change(fileInputs(container)[0], { target: { files: [FRONT] } });
  fireEvent.click(screen.getByRole('button', { name: /assess this document/i }));
}

beforeEach(() => {
  jest.clearAllMocks();
  (decisionHistory as jest.Mock).mockResolvedValue({ available: true, decisions: [], current: null });
  global.URL.createObjectURL = jest.fn(() => 'blob:preview');
  global.URL.revokeObjectURL = jest.fn();
});

// ------------------------------------------------------------- the form --

test('a PAN card is asked for its front only', () => {
  const { container } = render(<VerifyPage />);
  choose(/PAN card/);
  expect(fileInputs(container)).toHaveLength(1);
  expect(screen.getByText(/back carries no identity data/i)).toBeInTheDocument();
});

test('an Aadhaar is asked for its front, and its back when the QR is there', () => {
  const { container } = render(<VerifyPage />);
  choose(/^Aadhaar/);
  expect(fileInputs(container)).toHaveLength(2);
  expect(screen.getByText(/if your card's qr is on the front, leave this empty/i)).toBeInTheDocument();
});

test('switching to a one-image document drops a back already added', async () => {
  mockedDocument.mockResolvedValue(analysis([], 'pan'));
  const { container } = render(<VerifyPage />);
  fireEvent.change(fileInputs(container)[1], { target: { files: [BACK] } });
  choose(/PAN card/);
  await submitFront(container);

  await waitFor(() => expect(mockedDocument).toHaveBeenCalledTimes(1));
  expect(mockedCase).not.toHaveBeenCalled();
});

// --------------------------------------------------------- the Aadhaar QR --

test('an unchecked front offers the back, and keeps the front it already has', async () => {
  mockedDocument.mockResolvedValue(analysis(['aadhaar.qr.unchecked']));
  mockedCase.mockResolvedValue(caseResult());
  const { container } = render(<VerifyPage />);
  await submitFront(container);

  expect(await screen.findByText(/the qr was not verified/i)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /add the back side/i }));

  // The form is back, with the front still chosen: only the back is missing.
  expect(screen.getByRole('button', { name: /assess this document/i })).toBeEnabled();
  fireEvent.change(fileInputs(container)[1], { target: { files: [BACK] } });
  fireEvent.click(await screen.findByRole('button', { name: /assess front and back/i }));

  await waitFor(() => expect(mockedCase).toHaveBeenCalledTimes(1));
  expect(mockedCase.mock.calls[0][0]).toEqual([FRONT, BACK]);
  expect(await screen.findByText('case-1')).toBeInTheDocument();
});

test('a card whose QR is on the front can be retaken instead', async () => {
  mockedDocument.mockResolvedValue(analysis(['aadhaar.qr.unchecked']));
  const { container } = render(<VerifyPage />);
  await submitFront(container);

  fireEvent.click(await screen.findByRole('button', { name: /retake the front/i }));
  // The old photograph is gone, so nothing can be assessed until a new one is added.
  expect(screen.getByRole('button', { name: /assess this document/i })).toBeDisabled();
});

test('a front whose QR was checked is not told to add anything', async () => {
  mockedDocument.mockResolvedValue(analysis());
  const { container } = render(<VerifyPage />);
  await submitFront(container);

  await screen.findByRole('button', { name: /new document/i });
  expect(screen.queryByText(/the qr was not verified/i)).not.toBeInTheDocument();
});

test('a back whose QR did not read is not presented as a complete pair', async () => {
  mockedCase.mockResolvedValue(caseResult(['aadhaar.qr.unchecked']));
  const { container } = render(<VerifyPage />);

  fireEvent.change(fileInputs(container)[0], { target: { files: [FRONT] } });
  fireEvent.change(fileInputs(container)[1], { target: { files: [BACK] } });
  fireEvent.click(await screen.findByRole('button', { name: /assess front and back/i }));

  expect(await screen.findByText(/the qr on the back could not be read/i)).toBeInTheDocument();
  expect(mockedDocument).not.toHaveBeenCalled();
});

// ---------------------------------------------------- results that redirect --

test('an Aadhaar back sent alone becomes the back, and the front is asked for', async () => {
  mockedDocument.mockResolvedValue(analysis(['classify.reverse_side'], 'aadhaar', 'back'));
  mockedCase.mockResolvedValue(caseResult());
  const { container } = render(<VerifyPage />);
  await submitFront(container); // the user put the back in the first slot

  fireEvent.click(await screen.findByRole('button', { name: /add the front/i }));
  const second = new File(['real front'], 'real-front.jpg', { type: 'image/jpeg' });
  fireEvent.change(fileInputs(container)[0], { target: { files: [second] } });
  fireEvent.click(await screen.findByRole('button', { name: /assess front and back/i }));

  await waitFor(() => expect(mockedCase).toHaveBeenCalledTimes(1));
  expect(mockedCase.mock.calls[0][0]).toEqual([second, FRONT]);
});

test('a document that is not what was chosen says so, and can be checked as chosen', async () => {
  mockedDocument.mockResolvedValueOnce(analysis([], 'aadhaar')).mockResolvedValueOnce(analysis([], 'pan'));
  const { container } = render(<VerifyPage />);
  choose(/PAN card/);
  await submitFront(container);

  expect(await screen.findByText(/identified as aadhaar/i)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /check it as pan card/i }));

  await waitFor(() => expect(mockedDocument).toHaveBeenCalledTimes(2));
  expect(mockedDocument.mock.calls[1][1]).toMatchObject({ declaredType: 'pan' });
  // Declared, so no longer a disagreement to point out.
  await waitFor(() => expect(screen.queryByText(/identified as aadhaar/i)).not.toBeInTheDocument());
});
