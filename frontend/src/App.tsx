/**
 * Routing.
 *
 * Permission gates are nested rather than checked inside each page, so a new
 * screen cannot be added without deciding what it requires -- forgetting a
 * check is not possible when the check is the route.
 */

import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { AuthProvider } from './auth/AuthContext';
import AppShell from './components/AppShell';
import ProtectedRoute from './components/ProtectedRoute';
import LoginPage from './pages/LoginPage';
import VerifyPage from './pages/VerifyPage';
import CasePage from './pages/CasePage';
import FaceMatchPage from './pages/FaceMatchPage';
import LivenessPage from './pages/LivenessPage';
import MrzPage from './pages/MrzPage';
import CasesPage from './pages/CasesPage';
import UsersPage from './pages/UsersPage';
import CapabilitiesPage from './pages/CapabilitiesPage';

import SettingsPage from './pages/SettingsPage';

function NotFound() {
  return (
    <div className="mx-auto max-w-md py-20 text-center">
      <p className="font-display text-[14px] uppercase tracking-[0.2em] text-cyber-magenta animate-pulse">
        ERROR 404
      </p>
      <h1 className="mt-3 text-2xl font-bold font-display text-white uppercase tracking-widest">Sector Not Found</h1>
      <p className="mt-2 font-mono text-sm text-slate-500">
        The requested neural pathway does not exist. Please recalibrate your navigation array.
      </p>
    </div>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />

          <Route element={<ProtectedRoute />}>
            <Route element={<AppShell />}>
              <Route index element={<Navigate to="/verify" replace />} />

              <Route element={<ProtectedRoute permission="verify:submit" />}>
                <Route path="verify" element={<VerifyPage />} />
                <Route path="case" element={<CasePage />} />
                <Route path="face" element={<FaceMatchPage />} />
                <Route path="liveness" element={<LivenessPage />} />
                <Route path="mrz" element={<MrzPage />} />
              </Route>

              <Route element={<ProtectedRoute permission="audit:read" />}>
                <Route path="cases" element={<CasesPage />} />
              </Route>

              <Route element={<ProtectedRoute permission="users:manage" />}>
                <Route path="users" element={<UsersPage />} />
              </Route>

              <Route path="capabilities" element={<CapabilitiesPage />} />
              <Route path="settings" element={<SettingsPage />} />
              <Route path="*" element={<NotFound />} />
            </Route>
          </Route>
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}
