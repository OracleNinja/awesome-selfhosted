import { Navigate, Route, Routes } from 'react-router-dom';
import { isConfigured } from './lib/supabase';
import { useSession } from './state/session';
import { Notice, Spinner } from './ui';
import SignIn from './routes/SignIn';
import Pending from './routes/Pending';
import Home from './routes/Home';
import RecordTransfer from './routes/RecordTransfer';
import Confirm from './routes/Confirm';
import History from './routes/History';
import TransferDetail from './routes/TransferDetail';
import AdminHome from './routes/admin/AdminHome';
import Users from './routes/admin/Users';
import UserDetail from './routes/admin/UserDetail';
import Locations from './routes/admin/Locations';
import Catalog from './routes/admin/Catalog';
import Activity from './routes/admin/Activity';
import Balances from './routes/admin/Balances';
import Audit from './routes/admin/Audit';

export default function App() {
  const { session, profile, loading, error } = useSession();

  if (!isConfigured) return <Setup />;
  if (loading) return <Spinner />;
  if (!session) return <SignIn />;

  if (error && !profile) {
    return (
      <div className="center-page">
        <div className="auth">
          <Notice>{error}</Notice>
        </div>
      </div>
    );
  }

  // An account exists but has not been approved, or has been switched off.
  if (!profile || profile.status !== 'active') return <Pending />;

  const isAdmin = profile.role === 'admin';

  return (
    <Routes>
      <Route path="/" element={<Home />} />
      <Route path="/record/:kind" element={<RecordTransfer />} />
      <Route path="/confirm" element={<Confirm />} />
      <Route path="/history" element={<History />} />
      <Route path="/transfer/:id" element={<TransferDetail />} />
      {isAdmin && (
        <>
          <Route path="/admin" element={<AdminHome />} />
          <Route path="/admin/users" element={<Users />} />
          <Route path="/admin/users/:id" element={<UserDetail />} />
          <Route path="/admin/locations" element={<Locations />} />
          <Route path="/admin/catalog" element={<Catalog />} />
          <Route path="/admin/activity" element={<Activity />} />
          <Route path="/admin/balances" element={<Balances />} />
          <Route path="/admin/audit" element={<Audit />} />
        </>
      )}
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

/** Shown when the deployment has no Supabase credentials yet. */
function Setup() {
  return (
    <div className="center-page">
      <div className="auth">
        <h1 className="auth__brand">Almost there</h1>
        <p className="auth__tagline">
          This build has no Supabase project attached. Set VITE_SUPABASE_URL and
          VITE_SUPABASE_ANON_KEY, then reload. The deployment guide in the repository walks
          through it.
        </p>
      </div>
    </div>
  );
}
