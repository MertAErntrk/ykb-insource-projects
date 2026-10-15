import { useState } from 'react'
import type { ReactNode } from 'react'
import { Link, NavLink, useNavigate } from 'react-router'
import { ApiError } from '../../api/client.ts'
import { useAuth } from '../../state/auth.tsx'
import { useWizard } from '../../state/wizard.tsx'
import { BrandLogo } from '../BrandLogo.tsx'
import { Banner, Button, Dialog } from '../ui/index.ts'

export function AppShell({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth()
  const { reset } = useWizard()
  const navigate = useNavigate()
  const [busy, setBusy] = useState(false)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const doLogout = async (cancelRunning = false) => {
    setBusy(true)
    setError(null)
    try {
      await logout(cancelRunning)
      reset()
      navigate('/login', { replace: true })
    } catch (e) {
      if (e instanceof ApiError && e.error === 'JOB_RUNNING') setRunning(true)
      else setError(e instanceof ApiError ? e.detail : 'Çıkış yapılamadı. Yeniden deneyin.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <a className="skip-link" href="#icerik">İçeriğe geç</a>
      <header className="app-header">
        <Link className="app-brand" to="/connect">
          <BrandLogo height={32} />
          <span className="app-brand-name">VeriKöprü</span>
        </Link>
        <nav className="app-nav" aria-label="Ana gezinme">
          <NavLink to="/connect">Yeni taşıma</NavLink>
          <NavLink to="/history">Geçmiş</NavLink>
          <NavLink to="/reveal">Değer çöz</NavLink>
          <NavLink to="/cleanup">Temizlik</NavLink>
        </nav>
        <div className="app-user">
          <span>{user?.display_name || user?.username}</span>
          <Button size="sm" loading={busy} onClick={() => void doLogout()}>Çıkış</Button>
        </div>
      </header>
      <main id="icerik" className="app-main">
        {error && <Banner tone="error">{error}</Banner>}
        {children}
      </main>
      <Dialog
        open={running}
        title="Devam eden işiniz var"
        onClose={() => setRunning(false)}
        actions={
          <>
            <Button onClick={() => setRunning(false)}>Vazgeç</Button>
            <Button variant="danger" loading={busy} onClick={() => void doLogout(true).then(() => setRunning(false))}>İşi iptal et ve çık</Button>
          </>
        }
      >
        Çıkış yaparsanız çalışan taşıma işi iptal edilir ve hedef tabloda yarım veri kalabilir. İptal edip çıkmak istiyor musunuz?
      </Dialog>
    </>
  )
}
