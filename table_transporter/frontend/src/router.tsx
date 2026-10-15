import { Navigate, Outlet, Route, Routes, useLocation } from 'react-router'
import { AppShell } from './components/layout/AppShell.tsx'
import { Spinner } from './components/ui/index.ts'
import { Cleanup } from './pages/Cleanup.tsx'
import { Columns } from './pages/Columns.tsx'
import { Connect } from './pages/Connect.tsx'
import { History } from './pages/History.tsx'
import { Login } from './pages/Login.tsx'
import { Progress } from './pages/Progress.tsx'
import { Reveal } from './pages/Reveal.tsx'
import { Summary } from './pages/Summary.tsx'
import { Target } from './pages/Target.tsx'
import { Tables } from './pages/Tables.tsx'
import { useAuth } from './state/auth.tsx'

/** Giriş yoksa /login'e yönlendirir; varsa kabuğun içinde alt rotayı gösterir. */
function ProtectedLayout() {
  const { status } = useAuth()
  const location = useLocation()
  if (status === 'loading') {
    return <div className="center-screen"><Spinner label="Oturum denetleniyor" /></div>
  }
  if (status === 'anon') return <Navigate to="/login" replace state={{ from: location.pathname }} />
  return <AppShell><Outlet /></AppShell>
}

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route element={<ProtectedLayout />}>
        <Route path="/connect" element={<Connect />} />
        <Route path="/table" element={<Tables />} />
        <Route path="/columns" element={<Columns />} />
        <Route path="/target" element={<Target />} />
        <Route path="/summary" element={<Summary />} />
        <Route path="/jobs/:id" element={<Progress />} />
        <Route path="/history" element={<History />} />
        <Route path="/reveal" element={<Reveal />} />
        <Route path="/cleanup" element={<Cleanup />} />
      </Route>
      <Route path="*" element={<Navigate to="/connect" replace />} />
    </Routes>
  )
}
