/**
 * Navigation shell.
 *
 * Links are gated on the permissions the SERVER reported, not on the role
 * string. Hiding a link the caller cannot use is a courtesy; the backend
 * refusing the request is the real control, and the two must not disagree
 * because the frontend guessed at the permission table.
 */

import { useEffect, useState } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import {
  Activity,
  FileSearch,
  Layers3,
  LogOut,
  Menu,
  ScanFace,
  ScanLine,
  Settings,
  ShieldCheck,
  Users,
  Video,
  X,
} from 'lucide-react';
import { useAuth } from '../auth/AuthContext';
import { health } from '../api/endpoints';
import { cx } from '../lib/format';

interface NavItem {
  to: string;
  label: string;
  hint: string;
  icon: typeof ShieldCheck;
  permission?: string;
}

const NAV: NavItem[] = [
  {
    to: '/verify',
    label: 'Verify document',
    hint: 'One document, full evidence trail',
    icon: FileSearch,
    permission: 'verify:submit',
  },
  {
    to: '/case',
    label: 'Case workspace',
    hint: 'Several documents plus a selfie',
    icon: Layers3,
    permission: 'verify:submit',
  },
  {
    to: '/face',
    label: 'Face match',
    hint: 'Is the presenter the document holder?',
    icon: ScanFace,
    permission: 'verify:submit',
  },
  {
    to: '/liveness',
    label: 'Liveness',
    hint: 'Challenge-response with the camera',
    icon: Video,
    permission: 'verify:submit',
  },
  {
    to: '/mrz',
    label: 'MRZ lab',
    hint: 'Check digits, no image needed',
    icon: ScanLine,
    permission: 'verify:submit',
  },
  {
    to: '/cases',
    label: 'Audit trail',
    hint: 'What was decided, and when',
    icon: Activity,
    permission: 'audit:read',
  },
  {
    to: '/users',
    label: 'Accounts',
    hint: 'Roles and access',
    icon: Users,
    permission: 'users:manage',
  },
  {
    to: '/capabilities',
    label: 'Capabilities',
    hint: 'What this deployment can do',
    icon: ShieldCheck,
  },
  {
    to: '/settings',
    label: 'System Settings',
    hint: 'Accent, motion, density and diagnostics',
    icon: Settings,
  },
];

export default function AppShell() {
  const { user, signOut, can } = useAuth();
  const location = useLocation();
  const [open, setOpen] = useState(false);
  const [online, setOnline] = useState<boolean | null>(null);

  useEffect(() => setOpen(false), [location.pathname]);

  useEffect(() => {
    let cancelled = false;
    const check = async () => {
      try {
        await health();
        if (!cancelled) setOnline(true);
      } catch {
        if (!cancelled) setOnline(false);
      }
    };
    check();
    const timer = window.setInterval(check, 30_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  const visible = NAV.filter((item) => !item.permission || can(item.permission));

  return (
    <div className="flex min-h-screen bg-ink-900 bg-grid text-slate-200 selection:bg-cyber-cyan/30">
      <aside
        className={cx(
          'fixed inset-y-0 left-0 z-40 flex w-72 flex-col border-r-2 border-cyber-cyan/20 bg-ink-900/90 backdrop-blur-md transition-transform lg:static lg:translate-x-0 shadow-[4px_0_24px_rgb(var(--accent)/0.05)]',
          open ? 'translate-x-0' : '-translate-x-full',
        )}
      >
        <div className="flex items-center gap-3 border-b-2 border-cyber-cyan/20 px-6 py-5 relative overflow-hidden">
          <div className="absolute top-0 left-0 w-full h-[1px] bg-gradient-to-r from-transparent via-cyber-cyan/50 to-transparent"></div>
          <div className="rounded-none bg-cyber-cyan p-2 shadow-[0_0_10px_rgb(var(--accent)/0.8)] relative">
            <div className="absolute inset-0 border border-white/50 m-0.5 animate-pulse"></div>
            <ShieldCheck className="h-5 w-5 text-ink-900" aria-hidden />
          </div>
          <div className="min-w-0 z-10">
            <p className="text-lg font-display font-bold tracking-widest text-white uppercase drop-shadow-[0_0_5px_rgba(255,255,255,0.5)]">VeriShield</p>
            <p className="font-mono text-[10px] uppercase tracking-[0.25em] text-cyber-cyan">
              Risk // Not Verdicts
            </p>
          </div>
          <button
            onClick={() => setOpen(false)}
            className="ml-auto rounded border border-cyber-cyan/30 p-1.5 text-cyber-cyan hover:bg-cyber-cyan/10 hover:shadow-[0_0_8px_rgb(var(--accent)/0.4)] lg:hidden transition-all"
            aria-label="Close navigation"
          >
            <X className="h-4 w-4" aria-hidden />
          </button>
        </div>

        <nav className="flex-1 space-y-1.5 overflow-y-auto p-4 scrollbar-none">
          {visible.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                cx(
                  'group flex items-start gap-3 px-4 py-3 transition-all duration-300 relative',
                  isActive
                    ? 'text-cyber-cyan bg-cyber-cyan/10 border-l-2 border-cyber-cyan shadow-[inset_4px_0_10px_rgb(var(--accent)/0.1)]'
                    : 'text-slate-400 hover:bg-ink-800 hover:text-slate-200 border-l-2 border-transparent hover:border-cyber-cyan/30',
                )
              }
            >
              {({ isActive }) => (
                <>
                  {isActive && <div className="absolute left-0 top-0 bottom-0 w-px bg-cyber-cyan shadow-[0_0_8px_rgb(var(--accent)/1)]"></div>}
                  <item.icon
                    className={cx(
                      'mt-0.5 h-4 w-4 shrink-0 transition-colors',
                      isActive ? 'text-cyber-cyan drop-shadow-[0_0_5px_rgb(var(--accent)/0.8)]' : 'text-slate-500 group-hover:text-cyber-cyan/70',
                    )}
                    aria-hidden
                  />
                  <span className="min-w-0">
                    <span className={cx("block text-sm font-bold uppercase tracking-wider", isActive ? "text-white" : "")}>{item.label}</span>
                    <span className="mt-0.5 block font-mono text-[10px] leading-snug text-slate-500 group-hover:text-slate-400">
                      {item.hint}
                    </span>
                  </span>
                </>
              )}
            </NavLink>
          ))}
        </nav>

        <div className="border-t-2 border-cyber-cyan/20 p-4 bg-ink-900/50 backdrop-blur-md">
          <div className="flex items-center gap-2 px-2 pb-3">
            <span
              className={cx(
                'h-2 w-2 rounded-none rotate-45 transition-all duration-500',
                online === null
                  ? 'bg-slate-600'
                  : online
                    ? 'animate-pulse-ring bg-cyber-cyan shadow-[0_0_8px_rgb(var(--accent)/0.8)]'
                    : 'bg-verdict-reject shadow-[0_0_8px_rgba(255,0,60,0.8)]',
              )}
              aria-hidden
            />
            <span className={cx(
              "font-mono text-[10px] font-bold uppercase tracking-[0.2em]",
              online ? "text-cyber-cyan" : "text-slate-500"
            )}>
              {online === null ? 'SYNCING...' : online ? 'UPLINK ESTABLISHED' : 'CONNECTION LOST'}
            </span>
          </div>

          <div className="flex items-center gap-3 border border-cyber-cyan/30 bg-ink-800 px-3 py-2.5 relative overflow-hidden group">
            <div className="absolute inset-0 bg-cyber-cyan/5 opacity-0 group-hover:opacity-100 transition-opacity"></div>
            <div className="flex h-8 w-8 shrink-0 items-center justify-center border border-cyber-cyan/50 bg-ink-900 font-display text-[12px] font-bold uppercase text-cyber-cyan shadow-[inset_0_0_5px_rgb(var(--accent)/0.3)]">
              {user?.username.slice(0, 2) ?? '--'}
            </div>
            <div className="min-w-0 flex-1 z-10">
              <p className="truncate text-xs font-bold text-slate-200 uppercase tracking-wider">
                {user?.full_name || user?.username}
              </p>
              <p className="font-mono text-[9px] uppercase tracking-[0.15em] text-cyber-magenta mt-0.5">
                ROLE: {user?.role}
              </p>
            </div>
            <button
              onClick={() => signOut()}
              className="shrink-0 border border-cyber-cyan/30 p-2 text-cyber-cyan transition-all hover:bg-verdict-reject/10 hover:border-verdict-reject hover:text-verdict-reject hover:shadow-[0_0_8px_rgba(255,0,60,0.4)] z-10"
              aria-label="Sign out"
              title="Sign out"
            >
              <LogOut className="h-3.5 w-3.5" aria-hidden />
            </button>
          </div>
        </div>
      </aside>

      {open && (
        <div
          className="fixed inset-0 z-30 bg-ink-900/80 backdrop-blur-sm lg:hidden"
          onClick={() => setOpen(false)}
        />
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between border-b border-cyber-cyan/30 bg-ink-900/80 backdrop-blur-md px-4 py-3 lg:hidden shadow-[0_4px_15px_rgb(var(--accent)/0.05)] sticky top-0 z-20">
          <div className="flex items-center gap-3">
            <button
              onClick={() => setOpen(true)}
              className="border border-cyber-cyan/30 p-1.5 text-cyber-cyan hover:bg-cyber-cyan/10 hover:shadow-[0_0_8px_rgb(var(--accent)/0.3)] transition-all"
              aria-label="Open navigation"
            >
              <Menu className="h-5 w-5" aria-hidden />
            </button>
            <p className="text-sm font-display font-bold uppercase tracking-widest text-white drop-shadow-[0_0_5px_rgba(255,255,255,0.5)]">VeriShield</p>
          </div>
          <div className="h-2 w-2 rotate-45 bg-cyber-cyan shadow-[0_0_8px_rgb(var(--accent)/0.8)] animate-pulse"></div>
        </header>

        <main className="min-w-0 flex-1 p-4 sm:p-6 lg:p-8 relative">
          <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_top,_var(--tw-gradient-stops))] from-cyber-cyan/5 via-ink-900 to-ink-900 pointer-events-none z-[-1]"></div>
          <Outlet />
        </main>
      </div>
    </div>
  );
}
