/**
 * Who is signed in, and what they may do.
 *
 * Permissions come from the server (`GET /auth/me`), never from the role
 * string alone. The UI hiding a button is a courtesy; the backend refusing
 * the request is the actual control, and the two must not disagree because
 * the frontend guessed at a permission table.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react';
import type { ReactNode } from 'react';
import { describeError, revokeSession, tokens } from '../api/client';
import * as api from '../api/endpoints';
import type { CurrentUser } from '../types/api';

interface AuthValue {
  user: CurrentUser | null;
  /** True only while the initial "am I already signed in?" check runs. */
  booting: boolean;
  signingIn: boolean;
  error: string;
  /** `remember` keeps the session on this device; otherwise it ends with the tab. */
  signIn: (username: string, password: string, remember?: boolean) => Promise<boolean>;
  signOut: () => Promise<void>;
  can: (permission: string) => boolean;
  clearError: () => void;
}

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [booting, setBooting] = useState(true);
  const [signingIn, setSigningIn] = useState(false);
  const [error, setError] = useState('');

  // A token in localStorage is a claim, not proof -- it may be expired, the
  // account may be gone. Ask the server before trusting it.
  useEffect(() => {
    let cancelled = false;

    async function restore() {
      // A session the old behaviour kept on this device without asking:
      // end it on the server too, so the token is dead, not just forgotten.
      const legacy = tokens.takeLegacy();
      if (legacy) void revokeSession(legacy);

      if (!tokens.access()) {
        if (!cancelled) setBooting(false);
        return;
      }
      try {
        const me = await api.fetchMe();
        if (!cancelled) setUser(me);
      } catch {
        tokens.clear();
      } finally {
        if (!cancelled) setBooting(false);
      }
    }

    restore();
    return () => {
      cancelled = true;
    };
  }, []);

  const signIn = useCallback(async (username: string, password: string, remember = false) => {
    setSigningIn(true);
    setError('');
    tokens.clear();
    tokens.rememberDevice(remember);
    try {
      await api.login(username, password);
      setUser(await api.fetchMe());
      return true;
    } catch (err) {
      // The backend returns the same message for every login failure on
      // purpose (so usernames cannot be enumerated); pass it through as-is
      // rather than inventing a more specific one.
      setError(describeError(err));
      tokens.clear();
      return false;
    } finally {
      setSigningIn(false);
    }
  }, []);

  const signOut = useCallback(async () => {
    await api.logout();
    // The next person to sign in on this device decides afresh.
    tokens.rememberDevice(false);
    setUser(null);
  }, []);

  const can = useCallback(
    (permission: string) => Boolean(user?.permissions?.includes(permission)),
    [user],
  );

  const value = useMemo(
    () => ({
      user,
      booting,
      signingIn,
      error,
      signIn,
      signOut,
      can,
      clearError: () => setError(''),
    }),
    [user, booting, signingIn, error, signIn, signOut, can],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used inside <AuthProvider>.');
  }
  return context;
}
