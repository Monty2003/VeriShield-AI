/**
 * VeriShield API types.
 * Backend ke Pydantic schemas ka 1:1 mirror -- app/schemas/signals.py aur
 * app/schemas/document.py. Backend badle to yahi file pehle update hogi.
 */

// --- enums (backend me str Enum hain, wire par plain string aate hain) ---

export type DocumentType =
  | 'passport'
  | 'visa'
  | 'aadhaar'
  | 'pan'
  | 'driving_licence'
  | 'voter_id'
  | 'certificate'
  | 'unknown';

export type DocumentSide = 'front' | 'back' | 'unknown';

export type RiskBand = 'low' | 'medium' | 'high';

/** Kabhi bhi "genuine"/"fake" nahi -- sirf recommendation. */
export type Decision = 'accept' | 'manual_review' | 'reject';

/**
 * skip = check laagu hi nahi hua (PAN par face nahi hota)
 * error = check chal hi nahi paya (model down, corrupt image)
 * Dono alag hain aur UI me alag dikhne chahiye.
 */
export type SignalStatus = 'pass' | 'warn' | 'fail' | 'skip' | 'error';

export type Severity = 'info' | 'low' | 'medium' | 'high' | 'critical';

export type Stage =
  | 'ingest'
  | 'classify'
  | 'ocr'
  | 'extract'
  | 'validate'
  | 'forensics'
  | 'face'
  | 'cross_doc'
  | 'database';

export type Challenge = 'blink' | 'turn_left' | 'turn_right' | 'look_up';

export type Role = 'operator' | 'reviewer' | 'admin';

// --- core models ---

/** Image par ek box, absolute pixels me. Overlay draw karne ke liye. */
export interface Region {
  x: number;
  y: number;
  width: number;
  height: number;
  label?: string | null;
  /** 0-1 tampering suspicion, heatmap tint ke liye. */
  suspicion?: number | null;
}

/** Ek piece of evidence. Poora dashboard inhi par khada hai. */
export interface Signal {
  code: string;
  stage: Stage;
  title: string;
  status: SignalStatus;
  severity: Severity;
  /** Verdict par kitna bharosa hai (0-1) -- document kitna accha hai wo NAHI. */
  confidence: number;
  reason: string;
  evidence: Record<string, unknown>;
  regions: Region[];
  /** FAIL/ERROR hua to accept nahi ho sakta, chahe risk score kitna bhi kam ho. */
  blocking: boolean;
  created_at: string;
}

export interface FieldConfidence {
  value: unknown;
  raw: string | null;
  confidence: number;
  /** ocr | mrz | barcode | qr | layout | manual */
  source: string;
  region: Region | null;
}

export interface ExtractedFields {
  full_name: FieldConfidence;
  surname: FieldConfidence;
  given_names: FieldConfidence;
  date_of_birth: FieldConfidence;
  sex: FieldConfidence;
  father_name: FieldConfidence;
  address: FieldConfidence;
  document_number: FieldConfidence;
  nationality: FieldConfidence;
  issuing_authority: FieldConfidence;
  issuing_country: FieldConfidence;
  date_of_issue: FieldConfidence;
  date_of_expiry: FieldConfidence;
  place_of_birth: FieldConfidence;
  mrz_line1: FieldConfidence;
  mrz_line2: FieldConfidence;
  raw_text: FieldConfidence;
}

export interface RiskContribution {
  /** Backend list[dict] bhejta hai; keys code/title/points type ke hisaab se. */
  [key: string]: unknown;
}

export interface RiskAssessment {
  /** 0 = clean, 100 = maximal risk */
  score: number;
  band: RiskBand;
  decision: Decision;
  /** Evidence kitna complete tha (0-1) -- risk se alag cheez hai. */
  confidence: number;
  top_reasons: string[];
  /** Blocking failures ke signal codes -- prose match mat karna, code match karo. */
  blocking_codes: string[];
  blocking_reasons: string[];
  contributions: RiskContribution[];
  /** stage -> "ran" | "skipped" | "errored" */
  coverage: Record<string, string>;
}

export interface DocumentAnalysis {
  document_id: string;
  filename: string;
  document_type: DocumentType;
  type_confidence: number;
  side: DocumentSide;
  fields: ExtractedFields;
  signals: Signal[];
  risk: RiskAssessment | null;
  image_width: number | null;
  image_height: number | null;
  face_region: Region | null;
  /** stage -> milliseconds, perf panel ke liye. */
  processing_ms: Record<string, number>;
  created_at: string;
}

export interface VerificationResult {
  case_id: string;
  documents: DocumentAnalysis[];
  cross_document_signals: Signal[];
  overall_risk: RiskAssessment | null;
  started_at: string;
  completed_at: string | null;
}

// --- auth ---

export interface TokenResponse {
  access_token: string;
  refresh_token: string | null;
  token_type: string;
  expires_in: number;
  role: Role;
}

export interface CurrentUser {
  username: string;
  role: Role;
  full_name: string;
  disabled: boolean;
  locked: boolean;
  permissions: string[];
}

// --- face match ---

/** What the model found in one of the two submitted images. */
export interface FaceSide {
  image_width: number | null;
  image_height: number | null;
  faces_found: number;
  region: Region | null;
  detection_confidence: number | null;
  error: string | null;
}

/**
 * Three outcomes, not two. `uncertain` is the band between the thresholds
 * where similarity genuinely does not decide, and `not_compared` means no
 * comparison happened at all -- which must never be displayed as a low score.
 */
export type FaceMatchOutcome = 'match' | 'uncertain' | 'mismatch' | 'not_compared';

export interface FaceMatchResponse {
  outcome: FaceMatchOutcome;
  /** null when nothing was compared. Not zero -- zero would read as a mismatch. */
  similarity: number | null;
  thresholds: {
    strong: number;
    possible: number;
    min_face_pixels: number;
  };
  engine: string;
  recognition_available: boolean;
  document: FaceSide;
  selfie: FaceSide;
  signals: Signal[];
  risk: RiskAssessment;
  processing_ms: number;
  limitations: string;
}

// --- liveness ---

export interface LivenessStartResponse {
  session_id: string;
  challenge: Challenge;
  prompt: string;
  instructions: string;
  max_frames: number;
  caller_chose_challenge: boolean;
}

export interface LivenessFrameResponse {
  frame: number;
  face_detected: boolean;
  eye_openness: number | null;
  yaw: number | null;
  pitch: number | null;
  frames_collected: number;
}

export interface LivenessCompleteResponse {
  session_id: string;
  challenge: Challenge;
  passed: boolean;
  frames_submitted: number;
  frames_with_face: number;
  signals: Signal[];
  limitations: string;
}

// --- audit ---

export interface RecentCasesResponse {
  available: boolean;
  reason?: string;
  cases: Record<string, unknown>[];
}

export interface DocumentHistoryResponse {
  available: boolean;
  submissions: Record<string, unknown>[];
  resubmission?: boolean;
}
