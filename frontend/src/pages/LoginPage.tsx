/**
 * Sign in.
 *
 * The backend returns an identical message for every login failure so that
 * usernames cannot be enumerated. That message is shown verbatim -- softening
 * it or guessing at a friendlier cause would undo the property.
 *
 * A 503 is different in kind and is called out separately: it means the user
 * store is unreachable, which is a deployment problem, not a wrong password.
 */

import { useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Eye, EyeOff, ShieldCheck } from 'lucide-react';
import { useAuth } from '../auth/AuthContext';
import { Banner, Spinner } from '../components/ui';

const HIGHLIGHTS = [
  ['Evidence, not verdicts', 'Every stage emits signals with reasons a person can read.'],
  ['Blocking is separate', 'An expired document is genuine and still not acceptable.'],
  ['The score is arithmetic', 'Each point traces back to the signal that produced it.'],
];

export default function LoginPage() {
  const { signIn, signingIn, error, user, clearError } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [reveal, setReveal] = useState(false);

  const from = (location.state as { from?: string } | null)?.from ?? '/verify';

  useEffect(() => {
    if (user) navigate(from, { replace: true });
  }, [user, from, navigate]);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    const ok = await signIn(username.trim(), password);
    if (ok) navigate(from, { replace: true });
  }

  const storeDown = error.toLowerCase().includes('user store');

  return (
    <div className="flex min-h-screen bg-ink-900 bg-grid">
      <div className="hidden flex-1 flex-col justify-between border-r border-ink-700 p-12 lg:flex">
        <div className="flex items-center gap-3">
          <div className="rounded-xl bg-white p-2">
            <ShieldCheck className="h-5 w-5 text-ink-900" aria-hidden />
          </div>
          <div>
            <p className="text-lg font-semibold tracking-tight text-white">VeriShield AI</p>
            <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-slate-500">
              document risk assessment
            </p>
          </div>
        </div>

        <div className="max-w-md">
          <h1 className="text-3xl font-semibold leading-tight text-white">
            It does not tell you a document is genuine.
          </h1>
          <p className="mt-4 text-sm leading-relaxed text-slate-400">
            It tells you what it checked, what it found, and how much of that it
            could not check -- then hands the decision to you.
          </p>

          <dl className="mt-10 space-y-5">
            {HIGHLIGHTS.map(([title, body]) => (
              <div key={title} className="border-l-2 border-ink-600 pl-4">
                <dt className="text-sm font-medium text-slate-200">{title}</dt>
                <dd className="mt-1 text-xs leading-relaxed text-slate-500">{body}</dd>
              </div>
            ))}
          </dl>
        </div>

        <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-slate-700">
          aadhaar &middot; pan &middot; certificates
        </p>
      </div>

      <div className="flex w-full items-center justify-center p-6 lg:w-[28rem]">
        <form onSubmit={onSubmit} className="w-full max-w-sm animate-fade-up">
          <div className="mb-8 lg:hidden">
            <div className="mb-3 inline-flex rounded-xl bg-white p-2">
              <ShieldCheck className="h-5 w-5 text-ink-900" aria-hidden />
            </div>
            <p className="text-lg font-semibold text-white">VeriShield AI</p>
          </div>

          <h2 className="text-xl font-semibold text-white">Sign in</h2>
          <p className="mt-1 text-sm text-slate-500">
            Accounts are created by an administrator.
          </p>

          <div className="mt-7 space-y-4">
            <div>
              <label className="label" htmlFor="username">
                Username
              </label>
              <input
                id="username"
                className="input"
                value={username}
                autoComplete="username"
                autoFocus
                onChange={(event) => {
                  setUsername(event.target.value);
                  if (error) clearError();
                }}
                required
              />
            </div>

            <div>
              <label className="label" htmlFor="password">
                Password
              </label>
              <div className="relative">
                <input
                  id="password"
                  type={reveal ? 'text' : 'password'}
                  className="input pr-10"
                  value={password}
                  autoComplete="current-password"
                  onChange={(event) => {
                    setPassword(event.target.value);
                    if (error) clearError();
                  }}
                  required
                />
                <button
                  type="button"
                  onClick={() => setReveal((value) => !value)}
                  className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1.5 text-slate-500 hover:text-slate-300"
                  aria-label={reveal ? 'Hide password' : 'Show password'}
                >
                  {reveal ? (
                    <EyeOff className="h-4 w-4" aria-hidden />
                  ) : (
                    <Eye className="h-4 w-4" aria-hidden />
                  )}
                </button>
              </div>
            </div>
          </div>

          {error && (
            <div className="mt-4">
              <Banner tone={storeDown ? 'warn' : 'error'} title={storeDown ? 'Backend not ready' : undefined}>
                {error}
                {storeDown && (
                  <span className="mt-2 block font-mono text-[11px] leading-relaxed opacity-90">
                    set VERISHIELD_MONGO_URL in backend/.env, then run
                    scripts/bootstrap_admin.py to create the first account and
                    generate the signing key.
                  </span>
                )}
              </Banner>
            </div>
          )}

          <button
            type="submit"
            disabled={signingIn || !username || !password}
            className="btn-primary mt-6 w-full py-2.5"
          >
            {signingIn && <Spinner />}
            {signingIn ? 'Signing in' : 'Sign in'}
          </button>

          {/* Careful with this wording: /auth/logout returns revoked:true but
              the current server does not add the token id to its revocation
              set, so signing out discards the tokens here. Claiming
              server-side revocation would be a security promise the
              deployment does not keep. */}
          <p className="mt-6 text-center text-[11px] leading-relaxed text-slate-600">
            Access tokens are short-lived and refreshed automatically. Signing
            out discards them from this browser.
          </p>
        </form>
      </div>
    </div>
  );
}
