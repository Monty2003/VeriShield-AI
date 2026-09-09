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
import { describeError, tokens } from '../api/client';
import * as api from '../api/endpoints';
import type { CurrentUser } from '../types/api';

interface AuthValue {
  user: CurrentUser | null;
  /** True only while the initial "am I already signed in?" check runs. */
  booting: boolean;
  signingIn: boolean;
  error: string;
  signIn: (username: string, password: string) => Promise<boolean>;
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

  const signIn = useCallback(async (username: string, password: string) => {
    setSigningIn(true);
    setError('');
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
