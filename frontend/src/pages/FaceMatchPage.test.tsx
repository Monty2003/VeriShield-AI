/**
 * The property worth pinning on this screen is what it does with an absent
 * measurement.
 *
 * When no comparison happened, similarity comes back null. Rendering that as
 * 0.00 would put a marker in the "different person" band and show a reviewer a
 * finding the system never made -- the single most damaging thing this page
 * could do.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import FaceMatchPage from './FaceMatchPage';
import { verifyFaceMatch } from '../api/endpoints';
import type { FaceMatchResponse } from '../types/api';

jest.mock('../api/endpoints', () => ({
  verifyFaceMatch: jest.fn(),
}));

const mocked = verifyFaceMatch as jest.MockedFunction<typeof verifyFaceMatch>;

function response(overrides: Partial<FaceMatchResponse>): FaceMatchResponse {
  return {
    outcome: 'match',
    similarity: 0.82,
    thresholds: { strong: 0.45, possible: 0.28, min_face_pixels: 60 },
    engine: 'insightface',
    recognition_available: true,
    document: {
      image_width: 800,
      image_height: 500,
      faces_found: 1,
      region: { x: 10, y: 10, width: 120, height: 120 },
      detection_confidence: 0.93,
      error: null,
    },
    selfie: {
      image_width: 640,
      image_height: 480,
      faces_found: 1,
      region: { x: 40, y: 30, width: 200, height: 200 },
      detection_confidence: 0.97,
      error: null,
    },
    signals: [],
    risk: {
      score: 0,
      band: 'low',
      decision: 'accept',
      confidence: 1,
      top_reasons: [],
      blocking_codes: [],
      blocking_reasons: [],
      contributions: [],
      coverage: { face: 'ran' },
    },
    processing_ms: 240,
    limitations: 'Cosine similarity between ArcFace embeddings.',
    ...overrides,
  };
}

async function submitTwoImages(container: HTMLElement) {
  // The camera is the default source, so these tests switch to Upload -- jsdom
  // has no getUserMedia and a webcam is not what they are about.
  fireEvent.click(screen.getByRole('button', { name: /upload/i }));

  const inputs = container.querySelectorAll<HTMLInputElement>('input[type="file"]');
  expect(inputs).toHaveLength(2);

  fireEvent.change(inputs[0], {
    target: { files: [new File(['doc'], 'aadhaar.jpg', { type: 'image/jpeg' })] },
  });
  fireEvent.change(inputs[1], {
    target: { files: [new File(['me'], 'selfie.jpg', { type: 'image/jpeg' })] },
  });

  const button = await screen.findByRole('button', { name: /compare the faces/i });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
}

beforeEach(() => {
  mocked.mockReset();
  // jsdom has no object URL implementation.
  global.URL.createObjectURL = jest.fn(() => 'blob:preview');
  global.URL.revokeObjectURL = jest.fn();
});

test('the camera is the default source and a live view is not a photograph', async () => {
  const { container } = render(<FaceMatchPage />);

  // Camera mode, so the only file input on the page is the document one.
  expect(container.querySelectorAll('input[type="file"]')).toHaveLength(1);
  expect(screen.getByRole('button', { name: /turn on camera/i })).toBeInTheDocument();

  fireEvent.change(container.querySelector('input[type="file"]')!, {
    target: { files: [new File(['doc'], 'aadhaar.jpg', { type: 'image/jpeg' })] },
  });

  // A document alone is not enough: nothing has been captured yet, and the
  // live preview must never be mistaken for a taken shot.
  expect(await screen.findByRole('button', { name: /compare the faces/i })).toBeDisabled();
  expect(screen.getByText(/capture a shot first/i)).toBeInTheDocument();
  expect(mocked).not.toHaveBeenCalled();
});

test('an unmeasured comparison is never drawn as a zero score', async () => {
  mocked.mockResolvedValue(
    response({ outcome: 'not_compared', similarity: null, recognition_available: false }),
  );

  const { container } = render(<FaceMatchPage />);
  await submitTwoImages(container);

  expect(await screen.findByText(/not compared/i)).toBeInTheDocument();
  expect(screen.getByText(/no similarity measured/i)).toBeInTheDocument();
  // The number that must not appear anywhere.
  expect(screen.queryByText('0.000')).not.toBeInTheDocument();
  expect(
    screen.getByText(/showing this as\s+0\.00 would place it/i),
  ).toBeInTheDocument();
});

test('the undecided band is presented as its own outcome, not a weak yes', async () => {
  mocked.mockResolvedValue(response({ outcome: 'uncertain', similarity: 0.36 }));

  const { container } = render(<FaceMatchPage />);
  await submitTwoImages(container);

  // "Undecided" appears twice on purpose -- as the verdict, and as the band on
  // the scale it fell into -- so this matches both rather than expecting one.
  expect((await screen.findAllByText(/^undecided$/i)).length).toBeGreaterThanOrEqual(2);
  expect(screen.getByText(/does not decide/i)).toBeInTheDocument();
  expect(screen.getByText('0.360')).toBeInTheDocument();
});

test('a strong match reports the similarity it measured', async () => {
  mocked.mockResolvedValue(response({ outcome: 'match', similarity: 0.82 }));

  const { container } = render(<FaceMatchPage />);
  await submitTwoImages(container);

  expect(await screen.findByText(/same person/i)).toBeInTheDocument();
  expect(screen.getByText(/similarity 0\.820/i)).toBeInTheDocument();
});
