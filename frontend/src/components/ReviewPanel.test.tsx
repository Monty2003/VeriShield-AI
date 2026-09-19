import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import ReviewPanel, { noteRule } from './ReviewPanel';
import { decisionHistory, recordDecision } from '../api/endpoints';
import type { DecisionHistory, ReviewDecision } from '../types/api';

let mockPermissions: string[] = [];
jest.mock('../auth/AuthContext', () => ({
  useAuth: () => ({ can: (p: string) => mockPermissions.includes(p) }),
}));
jest.mock('../api/endpoints', () => ({
  decisionHistory: jest.fn(),
  recordDecision: jest.fn(),
}));

const history = decisionHistory as jest.MockedFunction<typeof decisionHistory>;
const record = recordDecision as jest.MockedFunction<typeof recordDecision>;

function decision(over: Partial<ReviewDecision> = {}): ReviewDecision {
  return {
    decision_id: 'x1',
    subject_type: 'document',
    subject_id: 'd1',
    outcome: 'approve',
    note: '',
    reviewer: 'rev',
    reviewer_role: 'reviewer',
    decided_at: '2026-09-19T00:00:00Z',
    system_decision: 'accept',
    system_score: 3,
    system_blocked: false,
    agrees_with_system: true,
    overrides_block: false,
    self_reviewed: false,
    ...over,
  };
}

function withHistory(h: Partial<DecisionHistory>) {
  history.mockResolvedValue({ available: true, decisions: [], current: null, ...h });
}

beforeEach(() => {
  history.mockReset();
  record.mockReset();
  mockPermissions = [];
});

test('without review:decide the decision is shown but cannot be made', async () => {
  withHistory({ decisions: [decision()], current: decision() });
  render(<ReviewPanel subject="document" subjectId="d1" systemDecision="accept" />);
  expect(await screen.findByText('Approved')).toBeInTheDocument();
  expect(screen.getByText(/needs the review:decide permission/i)).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /record decision/i })).not.toBeInTheDocument();
});

test('a rejection cannot be recorded without a reason', async () => {
  mockPermissions = ['review:decide'];
  withHistory({});
  record.mockResolvedValue(decision({ outcome: 'reject' }));
  render(<ReviewPanel subject="document" subjectId="d1" systemDecision="reject" />);

  expect(await screen.findByText(/awaiting a decision/i)).toBeInTheDocument();
  const submit = screen.getByRole('button', { name: /record decision/i });
  expect(submit).toBeDisabled(); // nothing chosen yet

  fireEvent.click(screen.getByRole('button', { name: /^reject$/i }));
  expect(submit).toBeDisabled(); // chosen, but no reason
  expect(screen.getByText(/a rejection needs a reason/i)).toBeInTheDocument();

  fireEvent.change(screen.getByLabelText(/decision note/i), {
    target: { value: '  Photo is not the holder.  ' },
  });
  expect(submit).toBeEnabled();
  fireEvent.click(submit);

  await waitFor(() =>
    expect(record).toHaveBeenCalledWith('document', 'd1', 'reject', 'Photo is not the holder.'),
  );
  expect(history).toHaveBeenCalledTimes(2); // reloaded after recording
});

test('the flags that matter later are visible on the decision', async () => {
  const d = decision({
    agrees_with_system: false,
    overrides_block: true,
    self_reviewed: true,
    system_decision: 'reject',
    note: 'Card re-checked in person.',
  });
  withHistory({ decisions: [d], current: d });
  render(<ReviewPanel subject="document" subjectId="d1" />);
  expect(await screen.findByText('against the system')).toBeInTheDocument();
  expect(screen.getByText('past a blocking finding')).toBeInTheDocument();
  expect(screen.getByText('self-reviewed')).toBeInTheDocument();
  expect(screen.getByText(/system said Reject/)).toBeInTheDocument();
});

test('with the audit store down nothing pretends to be recordable', async () => {
  mockPermissions = ['review:decide'];
  history.mockResolvedValue({ available: false, decisions: [], current: null });
  render(<ReviewPanel subject="document" subjectId="d1" />);
  expect(await screen.findByText(/has not been made/i)).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /record decision/i })).not.toBeInTheDocument();
});

test('the note rules match the server', () => {
  expect(noteRule('approve', 'accept', false)).toBe('');
  expect(noteRule('approve', 'manual_review', false)).toBe('');
  expect(noteRule('approve', 'reject', false)).toMatch(/against the system/);
  expect(noteRule('approve', 'accept', true)).toMatch(/blocking finding/);
  expect(noteRule('reject', 'reject', false)).toMatch(/reason/);
  expect(noteRule('needs_info', 'accept', false)).toMatch(/information/);
});
