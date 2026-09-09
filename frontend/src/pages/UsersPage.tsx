/**
 * Accounts.
 *
 * The permission list shown per role is fetched, not hard-coded here: a
 * frontend copy of the role table drifts, and a drifted copy tells an
 * administrator someone can do something the server will refuse.
 */

import { useCallback, useEffect, useState } from 'react';
import { KeyRound, ShieldCheck, UserPlus, Users } from 'lucide-react';
import { Banner, SectionHeading, Skeleton, Spinner } from '../components/ui';
import { describeError } from '../api/client';
import { changePassword, createUser, listUsers } from '../api/endpoints';
import { useAuth } from '../auth/AuthContext';
import type { CurrentUser, Role } from '../types/api';
import { cx } from '../lib/format';

const ROLES: Array<{ value: Role; label: string; blurb: string }> = [
  { value: 'operator', label: 'Operator', blurb: 'Submit documents and read the result.' },
  {
    value: 'reviewer',
    label: 'Reviewer',
    blurb: 'Also reads the audit trail and may reveal full identifiers.',
  },
  { value: 'admin', label: 'Admin', blurb: 'Also manages accounts.' },
];

const ROLE_CLASS: Record<Role, string> = {
  operator: 'bg-slate-500/10 text-slate-300',
  reviewer: 'bg-sky-500/10 text-sky-400',
  admin: 'bg-amber-500/10 text-amber-400',
};

export default function UsersPage() {
  const { user } = useAuth();
  const [users, setUsers] = useState<CurrentUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const [form, setForm] = useState({
    username: '',
    password: '',
    full_name: '',
    role: 'operator' as Role,
  });
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState('');
  const [created, setCreated] = useState('');

  const [passwords, setPasswords] = useState({ current: '', next: '' });
  const [changing, setChanging] = useState(false);
  const [passwordNote, setPasswordNote] = useState('');
  const [passwordError, setPasswordError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const response = await listUsers();
      setUsers(response.users ?? []);
    } catch (err) {
      setError(describeError(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function submitCreate(event: React.FormEvent) {
    event.preventDefault();
    setCreating(true);
    setCreateError('');
    setCreated('');
    try {
      const account = await createUser({
        username: form.username.trim(),
        password: form.password,
        role: form.role,
        full_name: form.full_name.trim(),
      });
      setCreated(`Created ${account.username} as ${account.role}.`);
      setForm({ username: '', password: '', full_name: '', role: 'operator' });
      await load();
    } catch (err) {
      setCreateError(describeError(err));
    } finally {
      setCreating(false);
    }
  }

  async function submitPassword(event: React.FormEvent) {
    event.preventDefault();
    setChanging(true);
    setPasswordError('');
    setPasswordNote('');
    try {
      await changePassword(passwords.current, passwords.next);
      setPasswordNote('Password changed.');
      setPasswords({ current: '', next: '' });
    } catch (err) {
      setPasswordError(describeError(err));
    } finally {
      setChanging(false);
    }
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <header>
        <h1 className="flex items-center gap-2.5 text-xl font-semibold text-white">
          <Users className="h-5 w-5 text-slate-500" aria-hidden />
          Accounts
        </h1>
        <p className="mt-1 text-sm text-slate-500">
          Roles map to permissions in one table on the server. Nothing here can
          grant more than that table allows.
        </p>
      </header>

      {error && <Banner>{error}</Banner>}

      <div className="grid gap-4 lg:grid-cols-[1fr_20rem]">
        <div>
          <SectionHeading title="Existing accounts" hint={`${users.length} total`} />
          {loading ? (
            <div className="card space-y-3 p-4">
              <Skeleton className="h-10" />
              <Skeleton className="h-10" />
            </div>
          ) : (
            <div className="card divide-y divide-ink-700/60">
              {users.map((account) => (
                <div key={account.username} className="flex items-center gap-3 px-4 py-3">
                  <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-ink-700 font-mono text-[11px] font-semibold uppercase text-slate-300">
                    {account.username.slice(0, 2)}
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm text-slate-100">
                      {account.full_name || account.username}
                      {account.username === user?.username && (
                        <span className="ml-2 font-mono text-[10px] text-slate-500">you</span>
                      )}
                    </p>
                    <p className="truncate font-mono text-[11px] text-slate-500">
                      {account.username}
                    </p>
                  </div>
                  {account.disabled && (
                    <span className="chip bg-slate-500/10 text-slate-400">disabled</span>
                  )}
                  {account.locked && (
                    <span className="chip bg-verdict-reject/10 text-verdict-reject">
                      locked
                    </span>
                  )}
                  <span className={cx('chip shrink-0', ROLE_CLASS[account.role])}>
                    {account.role}
                  </span>
                </div>
              ))}
            </div>
          )}

          <div className="card mt-4 p-4">
            <p className="section-title">Your permissions</p>
            <div className="mt-3 flex flex-wrap gap-1.5">
              {(user?.permissions ?? []).map((permission) => (
                <span key={permission} className="chip bg-ink-700 text-slate-300">
                  {permission}
                </span>
              ))}
            </div>
            <p className="mt-3 text-[11px] leading-relaxed text-slate-500">
              Reported by the server for this session. The UI hides what these do
              not cover; the server refuses it regardless.
            </p>
          </div>
        </div>

        <div className="space-y-4">
          <form onSubmit={submitCreate} className="card p-4">
            <p className="section-title flex items-center gap-2">
              <UserPlus className="h-3.5 w-3.5" aria-hidden />
              New account
            </p>

            <div className="mt-4 space-y-3">
              <div>
                <label className="label" htmlFor="new-username">
                  Username
                </label>
                <input
                  id="new-username"
                  className="input"
                  minLength={3}
                  required
                  value={form.username}
                  onChange={(event) => setForm({ ...form, username: event.target.value })}
                />
              </div>
              <div>
                <label className="label" htmlFor="new-fullname">
                  Full name
                </label>
                <input
                  id="new-fullname"
                  className="input"
                  value={form.full_name}
                  onChange={(event) => setForm({ ...form, full_name: event.target.value })}
                />
              </div>
              <div>
                <label className="label" htmlFor="new-password">
                  Password
                </label>
                <input
                  id="new-password"
                  type="password"
                  className="input"
                  minLength={12}
                  required
                  value={form.password}
                  onChange={(event) => setForm({ ...form, password: event.target.value })}
                />
                <p className="mt-1 text-[10px] text-slate-500">At least 12 characters.</p>
              </div>
              <div>
                <label className="label">Role</label>
                <div className="space-y-1.5">
                  {ROLES.map((option) => (
                    <label
                      key={option.value}
                      className={cx(
                        'flex cursor-pointer gap-2.5 rounded-lg border px-3 py-2 transition',
                        form.role === option.value
                          ? 'border-slate-400 bg-ink-700'
                          : 'border-ink-600 hover:border-ink-500',
                      )}
                    >
                      <input
                        type="radio"
                        name="role"
                        className="mt-0.5 h-3.5 w-3.5 shrink-0 accent-slate-300"
                        checked={form.role === option.value}
                        onChange={() => setForm({ ...form, role: option.value })}
                      />
                      <span>
                        <span className="block text-xs font-medium text-slate-200">
                          {option.label}
                        </span>
                        <span className="mt-0.5 block text-[10px] leading-relaxed text-slate-500">
                          {option.blurb}
                        </span>
                      </span>
                    </label>
                  ))}
                </div>
              </div>
            </div>

            {createError && (
              <p className="mt-3 text-[11px] text-verdict-reject">{createError}</p>
            )}
            {created && <p className="mt-3 text-[11px] text-verdict-accept">{created}</p>}

            <button type="submit" disabled={creating} className="btn-primary mt-4 w-full">
              {creating ? <Spinner /> : <ShieldCheck className="h-4 w-4" aria-hidden />}
              Create account
            </button>
          </form>

          <form onSubmit={submitPassword} className="card p-4">
            <p className="section-title flex items-center gap-2">
              <KeyRound className="h-3.5 w-3.5" aria-hidden />
              Change your password
            </p>
            <div className="mt-4 space-y-3">
              <div>
                <label className="label" htmlFor="current-password">
                  Current
                </label>
                <input
                  id="current-password"
                  type="password"
                  className="input"
                  required
                  value={passwords.current}
                  onChange={(event) =>
                    setPasswords({ ...passwords, current: event.target.value })
                  }
                />
              </div>
              <div>
                <label className="label" htmlFor="next-password">
                  New
                </label>
                <input
                  id="next-password"
                  type="password"
                  className="input"
                  minLength={12}
                  required
                  value={passwords.next}
                  onChange={(event) =>
                    setPasswords({ ...passwords, next: event.target.value })
                  }
                />
              </div>
            </div>

            <p className="mt-3 text-[10px] leading-relaxed text-slate-500">
              The current password is required even though you are signed in: a
              stolen token should not be enough to take over the account.
            </p>

            {passwordError && (
              <p className="mt-2 text-[11px] text-verdict-reject">{passwordError}</p>
            )}
            {passwordNote && (
              <p className="mt-2 text-[11px] text-verdict-accept">{passwordNote}</p>
            )}

            <button type="submit" disabled={changing} className="btn-ghost mt-4 w-full">
              {changing && <Spinner />}
              Update password
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
