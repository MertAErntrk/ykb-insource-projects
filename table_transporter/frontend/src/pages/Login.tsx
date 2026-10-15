import { useState } from 'react'
import type { FormEvent } from 'react'
import { Navigate, useNavigate } from 'react-router'
import { ApiError } from '../api/client.ts'
import { BrandLogo } from '../components/BrandLogo.tsx'
import { Banner, Button, PasswordField, TextField } from '../components/ui/index.ts'
import { useAuth } from '../state/auth.tsx'
import { useWizard } from '../state/wizard.tsx'

const USERNAME_RE = /^[A-Za-z0-9._-]{1,64}$/

const LOGIN_ERRORS: Record<string, string> = {
  AUTH_INVALID: 'Kullanıcı adı veya şifre hatalı. Bilgilerinizi kontrol edip yeniden deneyin.',
  AUTH_FORBIDDEN: 'Giriş bilgileriniz doğru ancak bu uygulamayı kullanma yetkiniz yok. Erişim için ekip yöneticinizle iletişime geçin.',
  AUTH_THROTTLED: 'Çok sayıda başarısız deneme yapıldı. Yaklaşık 5 dakika bekleyip yeniden deneyin.',
  LDAP_UNAVAILABLE: 'Kimlik doğrulama sunucusuna ulaşılamadı. Biraz sonra yeniden deneyin; sorun sürerse BT destek ile iletişime geçin.',
  VALIDATION: 'Kullanıcı adı yalnızca harf, rakam, nokta, tire ve alt çizgi içerebilir.',
}

export function loginErrorMessage(e: unknown): string {
  if (e instanceof ApiError) return LOGIN_ERRORS[e.error] ?? 'Giriş yapılamadı. Biraz sonra yeniden deneyin.'
  return 'Giriş yapılamadı. Biraz sonra yeniden deneyin.'
}

export function Login() {
  const { status, login } = useAuth()
  const { reset } = useWizard()
  const navigate = useNavigate()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [fieldErrors, setFieldErrors] = useState<{ username?: string; password?: string }>({})
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  if (status === 'authed' && !busy) return <Navigate to="/connect" replace />

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault()
    const name = username.trim()
    const errs: { username?: string; password?: string } = {}
    if (!name) errs.username = 'Kullanıcı adınızı girin.'
    else if (!USERNAME_RE.test(name)) errs.username = 'Yalnızca harf, rakam, nokta, tire ve alt çizgi kullanın.'
    if (!password.trim()) errs.password = 'Şifrenizi girin.'
    setFieldErrors(errs)
    setError(null)
    if (errs.username || errs.password) return
    setBusy(true)
    try {
      await login(name, password)
      reset() // önceki oturumdan kalan sihirbaz durumu yeni girişe taşınmaz
      navigate('/connect', { replace: true })
    } catch (err) {
      setError(loginErrorMessage(err))
      setPassword('')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-page">
      <div className="login-box">
        <div className="login-intro">
          <BrandLogo height={48} />
          <h1>VeriKöprü</h1>
          <p>Oracle veritabanları arasında anonimleştirerek tablo taşıyın.</p>
        </div>
        <form className="login-form" onSubmit={onSubmit} noValidate aria-label="Giriş">
          {error && <Banner tone="error">{error}</Banner>}
          <TextField
            label="Kullanıcı adı"
            hint="KFS kullanıcı adınız"
            autoComplete="username"
            autoCapitalize="none"
            spellCheck={false}
            autoFocus
            value={username}
            error={fieldErrors.username}
            onChange={(e) => setUsername(e.target.value)}
          />
          <PasswordField
            label="Şifre"
            autoComplete="current-password"
            value={password}
            error={fieldErrors.password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <Button type="submit" variant="primary" loading={busy}>{busy ? 'Giriş yapılıyor' : 'Giriş'}</Button>
        </form>
      </div>
    </div>
  )
}
