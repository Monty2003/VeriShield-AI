/**
 * The audit trail as a review queue.
 *
 * Three documents cover the states that matter: one a person has decided, one
 * still waiting on a person, and one the system cleared -- which can be decided
 * but must not clog the queue.
 */

import { fireEvent, render, screen, within } from '@testing-library/react';
import CasesPage from './CasesPage';
import { decisionHistory, recentCases, recentDocuments } from '../api/endpoints';
import type { RecordedDocument } from '../types/api';

jest.mock('react-chartjs-2', () => ({ Doughnut: () => <div data-testid="chart" /> }));
jest.mock('../auth/AuthContext', () => ({ useAuth: () => ({ can: () => true }) }));
jest.mock('../api/endpoints', () => ({
  recentDocuments: jest.fn(),
  recentCases: jest.fn(),
  documentHistory: jest.fn(),
  decisionHistory: jest.fn(),
  recordDecision: jest.fn(),
}));

function doc(id: string, filename: string, over: Partial<RecordedDocument> = {}): RecordedDocument {
  return {
    document_id: id,
    filename,
    document_type: 'aadhaar',
    recorded_at: '2026-09-19T00:00:00Z',
    risk: {
      score: 20,
      band: 'low',
      decision: 'manual_review',
      confidence: 1,
      top_reasons: [],
      blocking_codes: [],
      blocking_reasons: [],
      contributions: [],
      coverage: {},
    },
    review: null,
    ...over,
  };
}

const decided = doc('d1', 'decided.jpg', {
  review: {
    decision_id: 'x',
    subject_type: 'document',
    subject_id: 'd1',
    outcome: 'approve',
    note: '',
    reviewer: 'rev',
    reviewer_role: 'reviewer',
    decided_at: '2026-09-19T01:00:00Z',
    system_decision: 'accept',
    system_score: 2,
    system_blocked: false,
    agrees_with_system: true,
    overrides_block: false,
    self_reviewed: false,
  },
});
const waiting = doc('d2', 'waiting.jpg');
const cleared = doc('d3', 'cleared.jpg', {
  risk: { ...doc('x', 'x').risk!, decision: 'accept' },
});

beforeEach(() => {
  (recentDocuments as jest.Mock).mockResolvedValue({
    available: true,
    documents: [decided, waiting, cleared],
  });
  (recentCases as jest.Mock).mockResolvedValue({ available: true, cases: [] });
  (decisionHistory as jest.Mock).mockResolvedValue({
    available: true,
    decisions: [],
    current: null,
  });
});

test('shows who has decided and what is still waiting', async () => {
  render(<CasesPage />);
  await screen.findByText(/waiting\.jpg/);

  expect(screen.getByText('Approved')).toBeInTheDocument();
  // Exactly one row waits: the cleared accept is not queued.
  expect(screen.getAllByText('awaiting review')).toHaveLength(1);

  const queueStat = screen.getByText('Awaiting review', { selector: 'p' }).parentElement!;
  expect(within(queueStat).getByText('1')).toBeInTheDocument();
  const agreementStat = screen.getByText('Reviewer agreement').parentElement!;
  expect(within(agreementStat).getByText('100%')).toBeInTheDocument();
});

test('the queue filter leaves only what needs a person', async () => {
  render(<CasesPage />);
  await screen.findByText(/waiting\.jpg/);

  fireEvent.click(screen.getByRole('button', { name: /awaiting review/i }));
  expect(screen.getByText(/waiting\.jpg/)).toBeInTheDocument();
  expect(screen.queryByText(/decided\.jpg/)).not.toBeInTheDocument();
  expect(screen.queryByText(/cleared\.jpg/)).not.toBeInTheDocument();
});
