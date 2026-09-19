/**
 * What each document needs photographed.
 *
 * The documents differ in where their evidence is printed, and the form has to
 * follow that rather than force one shape on all of them:
 *
 *   Aadhaar   identity on the front; UIDAI's signed QR on the back of most
 *             cards and on the front of some. The back is asked for, never
 *             required -- a front that carries its own QR is checked alone.
 *   PAN       everything on the front, QR included. The back carries no
 *             identity data, so it is not asked for.
 *   Certificate  the page with the marks table and the issuer.
 *   Passport  the photo page, whose two MRZ lines carry check digits.
 */

import type { DocumentType } from '../types/api';

export type DocumentChoice = 'aadhaar' | 'pan' | 'certificate' | 'passport' | 'auto';

export interface Slot {
  label: string;
  dropLabel: string;
  hint: string;
}

export interface DocumentProfile {
  title: string;
  blurb: string;
  front: Slot;
  /** Null when the document needs a single image. */
  back: Slot | null;
  tips: string[];
  /** The type the classifier should report; null when detecting it is the point. */
  expects: DocumentType | null;
}

export const DOCUMENT_CHOICES: DocumentChoice[] = [
  'aadhaar',
  'pan',
  'certificate',
  'passport',
  'auto',
];

export const PROFILES: Record<DocumentChoice, DocumentProfile> = {
  aadhaar: {
    title: 'Aadhaar',
    blurb: 'Front, plus the back when the QR is there',
    front: {
      label: 'Front',
      dropLabel: 'Drop the front of the card',
      hint: 'Photo, name, date of birth and number',
    },
    back: {
      label: 'Back',
      dropLabel: 'Drop the back -- or a close-up of the QR',
      hint: "Needed when the QR is on the back. If your card's QR is on the front, leave this empty.",
    },
    tips: [
      "UIDAI's signed QR is what proves an Aadhaar genuine. Most cards print it on the back, some on the front -- either works.",
      'Card flat and whole in the frame, in focus, with no glare across the QR.',
      'If the QR will not read, a close, sharp photo of just the QR usually will.',
    ],
    expects: 'aadhaar',
  },
  pan: {
    title: 'PAN card',
    blurb: 'Front only -- every detail is on it',
    front: {
      label: 'Front',
      dropLabel: 'Drop the front of the PAN card',
      hint: "Name, father's name, date of birth, PAN and QR",
    },
    back: null,
    tips: [
      'Everything is on the front. The back carries no identity data and is not needed.',
      "Newer cards carry a QR. Its format is not published, so it is reported for a reviewer to scan with the Income Tax Department's PAN QR Code Reader app rather than checked here.",
      'Keep the PAN itself sharp: its structure is checked character by character.',
    ],
    expects: 'pan',
  },
  certificate: {
    title: 'Certificate',
    blurb: 'Marksheet, or a course or internship certificate',
    front: {
      label: 'Certificate page',
      dropLabel: 'Drop the marksheet or certificate',
      hint: 'The whole page, including any QR code or certificate ID',
    },
    back: null,
    tips: [
      "Marksheets: photograph the whole page flat -- each subject's total is checked in digits against the same total in words.",
      'Course, internship, participation and award certificates cannot be verified offline, so they go to a reviewer with what to check. Keep any QR code or certificate ID in frame: that is how the issuer confirms them.',
      'The reverse of a marksheet, with the grading notes, is not needed.',
    ],
    expects: 'certificate',
  },
  passport: {
    title: 'Passport',
    blurb: 'The photo page, with the MRZ lines',
    front: {
      label: 'Photo page',
      dropLabel: 'Drop the passport photo page',
      hint: 'Include the two lines of <<< at the bottom',
    },
    back: null,
    tips: [
      'The two machine-readable lines at the bottom carry check digits -- keep them fully in frame and sharp.',
      'Open the passport flat and tilt it away from the light: the laminate throws glare.',
    ],
    expects: 'passport',
  },
  auto: {
    title: 'Not sure',
    blurb: 'Detect the type automatically',
    front: {
      label: 'Front',
      dropLabel: 'Drop the front -- or the only page',
      hint: 'Aadhaar, PAN, certificate or passport',
    },
    back: {
      label: 'Back',
      dropLabel: 'Drop the back',
      hint: 'Optional. Add it for an Aadhaar whose QR is on the back.',
    },
    tips: ['The type is detected from the printed wording, and the result says what was found.'],
    expects: null,
  },
};
