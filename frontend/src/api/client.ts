/**
 * Axios client.
 *
 * Teen kaam:
 *   1. har request par Bearer token lagana
 *   2. 401 par EK BAAR refresh try karna, phir login par bhej dena
 *   3. FastAPI ke error shapes (string detail, 422 array, 429 Retry-After)
 *      ko ek readable line me badalna
 */

import axios, {
  AxiosError,
  AxiosInstance,
  InternalAxiosRequestConfig,
} from 'axios';
import type { TokenResponse } from '../types/api';

const BASE_URL = process.env.REACT_APP_API_URL ?? '';

const ACCESS_KEY = 'vs_access_token';
const REFRESH_KEY = 'vs_refresh_token';

// --- token storage -------------------------------------------------------

export const tokens = {
  access: () => localStorage.getItem(ACCESS_KEY),
  refresh: () => localStorage.getItem(REFRESH_KEY),
  save(access: string, refresh?: string | null) {
    localStorage.setItem(ACCESS_KEY, access);
    if (refresh) localStorage.setItem(REFRESH_KEY, refresh);
  },
  clear() {
    localStorage.removeItem(ACCESS_KEY);
    localStorage.removeItem(REFRESH_KEY);
  },
};

// --- instance ------------------------------------------------------------

export const api: AxiosInstance = axios.create({
  baseURL: BASE_URL,
  // OCR + face models chalte hain, ek document ~5-6s. Pehla request (cold
  // start, model load) aur slow hota hai -- isliye 120s.
  timeout: 120_000,
});

api.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = tokens.access();
  if (token) config.headers.set('Authorization', `Bearer ${token}`);
  return config;
});

// --- 401 -> ek baar refresh ----------------------------------------------

type Retriable = InternalAxiosRequestConfig & { _retried?: boolean };

/** Ek hi refresh chale, chahe 5 request ek saath 401 khayen. */
let refreshing: Promise<string | null> | null = null;

function onSessionLost() {
  tokens.clear();
  // Router ke bahar hain (interceptor React tree me nahi hai), isliye hard
  // redirect. ?next= isliye ki login ke baad user wahin wapas aa jaye.
  if (window.location.pathname !== '/login') {
    const next = encodeURIComponent(
      window.location.pathname + window.location.search,
    );
    window.location.assign(`/login?next=${next}`);
  }
}

async function refreshAccessToken(): Promise<string | null> {
  const refresh = tokens.refresh();
  if (!refresh) return null;
  try {
    // Backend me `refresh_token: str` ek plain scalar param hai, isliye
    // FastAPI use QUERY parameter maanta hai -- body me bhejoge to 422.
    const { data } = await axios.post<TokenResponse>(
      `${BASE_URL}/api/v1/auth/refresh`,
      null,
      { params: { refresh_token: refresh }, timeout: 20_000 },
    );
    tokens.save(data.access_token, data.refresh_token);
    return data.access_token;
  } catch {
    return null;
  }
}

api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const config = error.config as Retriable | undefined;

    if (error.response?.status !== 401 || !config || config._retried) {
      return Promise.reject(error);
    }

    // login/refresh khud 401 de rahe hain to refresh karna bekaar hai --
    // warna galat password par bhi ek fizool refresh round-trip hoga.
    const url = config.url ?? '';
    if (url.includes('/auth/login') || url.includes('/auth/refresh')) {
      return Promise.reject(error);
    }

    config._retried = true;

    if (!refreshing) {
      refreshing = refreshAccessToken().finally(() => {
        refreshing = null;
      });
    }
    const fresh = await refreshing;

    if (!fresh) {
      onSessionLost();
      return Promise.reject(error);
    }

    config.headers.set('Authorization', `Bearer ${fresh}`);
    return api.request(config);
  },
);

// --- errors --------------------------------------------------------------

/**
 * Kisi bhi error ko ek line me badlo jo UI me dikhayi ja sake.
 *
 * Backend ke error messages jaan-bujhkar informative likhe gaye hain
 * ("MRZ padha nahi gaya", "is role ke paas ye permission nahi hai") --
 * unhe "Something went wrong" se replace karna information phenk dena hai.
 */
export function describeError(error: unknown): string {
  if (!axios.isAxiosError(error)) {
    return error instanceof Error ? error.message : 'Kuch galat ho gaya.';
  }

  const err = error as AxiosError<{ detail?: unknown }>;

  if (err.code === 'ECONNABORTED') {
    return 'Request timed out. Pehla document model load hone ki wajah se slow hota hai -- dobara try karo.';
  }
  if (!err.response) {
    return `Backend tak nahi pahunch paye (${BASE_URL || 'same origin'}). Kya uvicorn chal raha hai?`;
  }

  const { status, statusText, data, headers } = err.response;
  const detail = data?.detail;

  if (typeof detail === 'string') return detail;

  // Pydantic 422 -- detail ek array of {loc, msg} hota hai.
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        const entry = item as { loc?: unknown[]; msg?: string };
        const loc = Array.isArray(entry.loc) ? entry.loc.slice(1).join('.') : '';
        return loc ? `${loc}: ${entry.msg}` : String(entry.msg ?? '');
      })
      .join('; ');
  }

  if (status === 429) {
    const retry = headers?.['retry-after'];
    return retry
      ? `Rate limit lag gaya. ${retry}s baad try karo.`
      : 'Rate limit lag gaya.';
  }

  return `${status} ${statusText}`;
}
