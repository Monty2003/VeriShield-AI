/** Route gate. Refuses on every uncertainty, exactly as the backend does. */

import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { Lock, ShieldCheck } from 'lucide-react';
import { useAuth } from '../auth/AuthContext';

export default function ProtectedRoute({ permission }: { permission?: string }) {
  const { user, booting, can } = useAuth();
  const location = useLocation();

  if (booting) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-3 bg-ink-900">
        <ShieldCheck className="h-6 w-6 animate-pulse-ring text-slate-500" aria-hidden />
        <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-slate-600">
          restoring session
        </p>
      </div>
    );
  }

  if (!user) {
    // Remember where they were headed so the login can send them back.
    return <Navigate to="/login" state={{ from: location.pathname }} replace />;
  }

  if (permission && !can(permission)) {
    return (
      <div className="mx-auto max-w-lg py-20 text-center">
        <Lock className="mx-auto h-6 w-6 text-slate-600" aria-hidden />
        <h1 className="mt-4 text-lg font-semibold text-white">Not your permission</h1>
        <p className="mt-2 text-sm text-slate-400">
          This screen needs <span className="font-mono text-slate-300">{permission}</span>,
          which the <span className="font-mono text-slate-300">{user.role}</span> role does
          not hold. An administrator can grant it by changing your role.
        </p>
      </div>
    );
  }

  return <Outlet />;
}
