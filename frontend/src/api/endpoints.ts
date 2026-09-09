/**
 * Typed wrappers for every VeriShield endpoint.
 * Component kabhi raw URL na likhe -- backend badla to sirf yahi file badle.
 */

import { api, tokens } from './client';
import type {
  Challenge,
  CurrentUser,
  DocumentAnalysis,
  DocumentHistoryResponse,
  DocumentType,
  FaceMatchResponse,
  LivenessCompleteResponse,
  LivenessFrameResponse,
  LivenessStartResponse,
  RecentCasesResponse,
  Role,
  Signal,
  TokenResponse,
  VerificationResult,
} from '../types/api';

const V1 = '/api/v1';

// --- auth ----------------------------------------------------------------

export async function login(
  username: string,
  password: string,
): Promise<TokenResponse> {
  // OAuth2PasswordRequestForm = form-encoded, JSON nahi.
  const body = new URLSearchParams({ username, password });
  const { data } = await api.post<TokenResponse>(`${V1}/auth/login`, body, {
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  });
  tokens.save(data.access_token, data.refresh_token);
  return data;
}

export async function fetchMe(): Promise<CurrentUser> {
  const { data } = await api.get<CurrentUser>(`${V1}/auth/me`);
  return data;
}

export async function logout(): Promise<void> {
  try {
    await api.post(`${V1}/auth/logout`);
  } finally {
    // Server ne suna ya nahi -- client par session khatam hai.
    tokens.clear();
  }
}

export async function changePassword(
  currentPassword: string,
  newPassword: string,
): Promise<void> {
  await api.post(`${V1}/auth/password`, {
    current_password: currentPassword,
    new_password: newPassword,
  });
}

export async function listUsers(): Promise<{ users: CurrentUser[] }> {
  const { data } = await api.get<{ users: CurrentUser[] }>(`${V1}/auth/users`);
  return data;
}

export async function createUser(input: {
  username: string;
  password: string;
  role: Role;
  full_name?: string;
}): Promise<CurrentUser> {
  const { data } = await api.post<CurrentUser>(`${V1}/auth/users`, input);
  return data;
}

// --- verification --------------------------------------------------------

export interface VerifyOptions {
  declaredType?: DocumentType;
  /** OCR ke bajaye text bhejo -- reviewer ke correction ke baad re-run ke liye. */
  ocrText?: string;
  enableCopyMove?: boolean;
  /** Sirf un roles ke liye jinke paas verify:reveal_identifiers hai. */
  revealIdentifiers?: boolean;
  onProgress?: (percent: number) => void;
}

function progressHandler(onProgress?: (percent: number) => void) {
  if (!onProgress) return undefined;
  return (event: { loaded: number; total?: number }) =>
    onProgress(event.total ? Math.round((event.loaded * 100) / event.total) : 0);
}

export async function verifyDocument(
  file: File,
  options: VerifyOptions = {},
): Promise<DocumentAnalysis> {
  const form = new FormData();
  form.append('file', file);
  if (options.declaredType) form.append('declared_type', options.declaredType);
  if (options.ocrText) form.append('ocr_text', options.ocrText);
  form.append('enable_copy_move', String(options.enableCopyMove ?? false));
  form.append('reveal_identifiers', String(options.revealIdentifiers ?? false));

  // Content-Type jaan-bujhkar set nahi kar rahe -- browser khud multipart
  // boundary lagata hai, hum lagayen to backend parse nahi kar payega.
  const { data } = await api.post<DocumentAnalysis>(
    `${V1}/verify/document`,
    form,
    { onUploadProgress: progressHandler(options.onProgress) },
  );
  return data;
}

export async function verifyCase(
  files: File[],
  options: VerifyOptions & { selfie?: File | null } = {},
): Promise<VerificationResult> {
  const form = new FormData();
  // Same field name repeat -- FastAPI ise list[UploadFile] banata hai.
  files.forEach((f) => form.append('files', f));
  if (options.selfie) form.append('selfie', options.selfie);
  form.append('enable_copy_move', String(options.enableCopyMove ?? false));
  form.append('reveal_identifiers', String(options.revealIdentifiers ?? false));

  const { data } = await api.post<VerificationResult>(`${V1}/verify/case`, form, {
    onUploadProgress: progressHandler(options.onProgress),
  });
  return data;
}

/**
 * Compare a presented face against the portrait on a document.
 *
 * Separate from verifyCase on purpose: this runs the face stage only, so it
 * comes back in well under a second instead of taking the whole pipeline's
 * five-plus seconds per document to answer one question.
 */
export async function verifyFaceMatch(
  document: File,
  selfie: File,
  onProgress?: (percent: number) => void,
): Promise<FaceMatchResponse> {
  const form = new FormData();
  form.append('document', document);
  form.append('selfie', selfie);

  const { data } = await api.post<FaceMatchResponse>(`${V1}/verify/face`, form, {
    onUploadProgress: progressHandler(onProgress),
  });
  return data;
}

/** Sirf /verify/mrz istemal karta hai, isliye type yahin rakha hai. */
export interface MrzCheckDigit {
  field: string;
  value: string;
  stated: string;
  computed: string;
  valid: boolean;
  /** false = check filler par tha jo humne daala, document ne nahi kaha. */
  evaluated: boolean;
}

export interface MrzVerifyResponse {
  parsed: Record<string, string | null>;
  line2_reconstructed: boolean;
  reconstruction_note: string | null;
  check_digits: MrzCheckDigit[];
  all_trusted_checks_valid: boolean;
  signals: Signal[];
  risk: import('../types/api').RiskAssessment;
}

export async function verifyMrz(
  line1: string,
  line2: string,
): Promise<MrzVerifyResponse> {
  const form = new FormData();
  form.append('line1', line1);
  form.append('line2', line2);
  const { data } = await api.post<MrzVerifyResponse>(`${V1}/verify/mrz`, form);
  return data;
}

// --- audit ---------------------------------------------------------------

export async function recentCases(limit = 20): Promise<RecentCasesResponse> {
  const { data } = await api.get<RecentCasesResponse>(`${V1}/cases/recent`, {
    params: { limit },
  });
  return data;
}

export async function documentHistory(
  fingerprint: string,
): Promise<DocumentHistoryResponse> {
  const { data } = await api.get<DocumentHistoryResponse>(
    `${V1}/documents/${fingerprint}/history`,
  );
  return data;
}

// --- liveness ------------------------------------------------------------

/**
 * Session kholo. `challenge` jaan-bujhkar NAHI bhejte: challenge server
 * chunta hai aur tabhi batata hai -- yahi poori security property hai.
 * Pehle se record ki hui video me sahi action sahi waqt par nahi ho sakta.
 */
export async function livenessStart(
  challenge?: Challenge,
): Promise<LivenessStartResponse> {
  const { data } = await api.post<LivenessStartResponse>(
    `${V1}/liveness/start`,
    null,
    // scalar param -> query string.
    { params: challenge ? { challenge } : undefined },
  );
  return data;
}

export async function livenessFrame(
  sessionId: string,
  frame: Blob,
): Promise<LivenessFrameResponse> {
  const form = new FormData();
  form.append('file', frame, 'frame.jpg');
  const { data } = await api.post<LivenessFrameResponse>(
    `${V1}/liveness/${sessionId}/frame`,
    form,
  );
  return data;
}

export async function livenessComplete(
  sessionId: string,
): Promise<LivenessCompleteResponse> {
  const { data } = await api.post<LivenessCompleteResponse>(
    `${V1}/liveness/${sessionId}/complete`,
  );
  return data;
}

// --- health --------------------------------------------------------------

/** /health prefix ke bahar hai aur auth-free hai -- connectivity check ke liye. */
export async function health(): Promise<Record<string, unknown>> {
  const { data } = await api.get<Record<string, unknown>>('/health');
  return data;
}
