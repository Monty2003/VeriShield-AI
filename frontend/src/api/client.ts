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
const REMEMBER_KEY = 'vs_remember_device';

// --- token storage -------------------------------------------------------
//
// A sign-in lasts as long as the browser tab, unless the user asks this device
// to remember them. Tokens used to live in localStorage unconditionally: the
// refresh token kept renewing them, so anyone who opened the app on that
// computer -- days later, in a new browser session -- was already inside a
// console that handles identity documents.
//
// Storage can throw (private windows, blocked site data); every access is
// guarded, and a failure reads as "no session", which fails closed.

function storage(kind: 'local' | 'session'): Storage | null {
  try {
    return kind === 'local' ? window.localStorage : window.sessionStorage;
  } catch {
    return null;
  }
}

function remembered(): boolean {
  try {
    return storage('local')?.getItem(REMEMBER_KEY) === '1';
  } catch {
    return false;
  }
}

function active(): Storage | null {
  return storage(remembered() ? 'local' : 'session');
}

function read(key: string): string | null {
  try {
    return active()?.getItem(key) ?? null;
  } catch {
    return null;
  }
}

export const tokens = {
  access: () => read(ACCESS_KEY),
  refresh: () => read(REFRESH_KEY),
  save(access: string, refresh?: string | null) {
    try {
      active()?.setItem(ACCESS_KEY, access);
      if (refresh) active()?.setItem(REFRESH_KEY, refresh);
    } catch {
      // Unstorable means signed in for this page only -- not an error.
    }
  },
  /** Everywhere, so signing out leaves nothing behind in either store. */
  clear() {
    for (const kind of ['local', 'session'] as const) {
      try {
        storage(kind)?.removeItem(ACCESS_KEY);
        storage(kind)?.removeItem(REFRESH_KEY);
      } catch {
        // nothing stored there to leave behind
      }
    }
  },
  /** Where the next sign-in is kept: this device (true) or this tab (false). */
  rememberDevice(yes: boolean) {
    try {
      if (yes) storage('local')?.setItem(REMEMBER_KEY, '1');
      else storage('local')?.removeItem(REMEMBER_KEY);
    } catch {
      // falls back to the tab, the safer of the two
    }
  },
  remembersDevice: () => remembered(),
  /**
   * An access token left in localStorage by the old always-remember
   * behaviour, removed as it is returned so it can be revoked once.
   */
  takeLegacy(): string | null {
    if (remembered()) return null;
    try {
      const local = storage('local');
      const legacy = local?.getItem(ACCESS_KEY) ?? null;
      local?.removeItem(ACCESS_KEY);
      local?.removeItem(REFRESH_KEY);
      return legacy;
    } catch {
      return null;
    }
  },
};

/**
 * End a session on the server given only its access token.
 *
 * Plain axios, not `api`: this must not pick up the current tab's token or
 * trigger the refresh-and-redirect handling on a 401 -- an already-expired
 * leftover is the expected case, and it needs no handling at all.
 */
export async function revokeSession(accessToken: string): Promise<void> {
  try {
    await axios.post(`${BASE_URL}/api/v1/auth/logout`, null, {
      headers: { Authorization: `Bearer ${accessToken}` },
      timeout: 10_000,
    });
  } catch {
    // Expired or already revoked: nothing left to end.
  }
}

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
