import { useId, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { connectDb, disconnectDb } from '../../api/endpoints.ts'
import { ApiError } from '../../api/client.ts'
import type { Role, TnsAlias } from '../../api/types.ts'
import type { WizardConnection } from '../../state/wizard.tsx'
import { Badge, Banner, Button, PasswordField, SearchableSelect, TextField } from '../ui/index.ts'
import './wizard.css'

export function connectErrorMessage(e: unknown): string {
  if (!(e instanceof ApiError)) return 'İşlem tamamlanamadı. Biraz sonra yeniden deneyin.'
  switch (e.error) {
    case 'DB_CONNECT_FAILED': return e.detail || 'Veritabanına bağlanılamadı. Kullanıcı adı ve şifreyi kontrol edin.'
    case 'TARGET_DENIED': return 'Bu veritabanı hedef olarak kullanılamaz. Canlı ortamlar yalnızca kaynak olabilir.'
    case 'NOT_FOUND': return 'Seçilen veritabanı bulunamadı. Sayfayı yenileyip listeden yeniden seçin.'
    case 'VALIDATION': return 'Bilgiler geçersiz. Veritabanı, kullanıcı adı ve şifreyi kontrol edin.'
    default: return e.detail || 'İşlem tamamlanamadı. Biraz sonra yeniden deneyin.'
  }
}

export interface ConnectionCardProps {
  role: Role
  aliases: TnsAlias[]
  connection: WizardConnection | null
  onConnected: (c: WizardConnection) => void
  onDisconnected: () => void
}

export function ConnectionCard({ role, aliases, connection, onConnected, onDisconnected }: ConnectionCardProps) {
  const headId = useId()
  const title = role === 'source' ? 'Kaynak' : 'Hedef'
  const [alias, setAlias] = useState<string | null>(null)
  const [user, setUser] = useState('')
  const [password, setPassword] = useState('')
  const [errs, setErrs] = useState<{ alias?: string; user?: string; password?: string }>({})
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  // Hedef listesinde yalnız hedef olabilen veritabanları görünür; canlılar kaynakta rozetle işaretlenir.
  const options = useMemo(
    () =>
      aliases
        .filter((a) => role === 'source' || a.target_allowed)
        .map((a) => ({
          value: a.alias,
          label: a.alias,
          description: `${a.host}:${a.port}/${a.service_name}${a.target_allowed ? '' : ' · Canlı · yalnız kaynak'}`,
        })),
    [aliases, role],
  )

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault()
    const name = user.trim()
    const next = {
      alias: alias ? undefined : 'Bir veritabanı seçin.',
      user: name ? undefined : 'Kullanıcı adını girin.',
      password: password ? undefined : 'Şifreyi girin.',
    }
    setErrs(next)
    setError(null)
    if (next.alias || next.user || next.password || !alias) return
    setBusy(true)
    try {
      const info = await connectDb({ role, alias, user: name, password })
      onConnected({ alias: info.alias, user: info.user, info })
      setUser('')
      setAlias(null)
    } catch (err) {
      setError(connectErrorMessage(err))
    } finally {
      setPassword('') // şifre hiçbir yerde tutulmaz
      setBusy(false)
    }
  }

  const onDisconnect = async () => {
    setBusy(true)
    setError(null)
    try {
      await disconnectDb(role)
      onDisconnected()
    } catch (err) {
      setError(connectErrorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  if (connection) {
    const i = connection.info
    return (
      <section className="conn-card" aria-labelledby={headId}>
        <div className="conn-card-head">
          <h2 id={headId}>{title}</h2>
          <Badge tone="success">Bağlı</Badge>
        </div>
        {error && <Banner tone="error">{error}</Banner>}
        <dl className="conn-summary">
          <dt>Veritabanı</dt><dd>{i.alias}</dd>
          <dt>Kullanıcı</dt><dd>{i.user}</dd>
          <dt>Sürüm</dt><dd>{i.db_version}</dd>
          <dt>İstemci modu</dt><dd>{i.mode === 'thick' ? 'Thick' : 'Thin'}</dd>
          <dt>Hızlı yükleme</dt><dd>{i.direct_path ? 'Açık' : 'Kapalı'}</dd>
        </dl>
        <div>
          <Button onClick={onDisconnect} loading={busy}>Bağlantıyı değiştir</Button>
        </div>
      </section>
    )
  }

  return (
    <section className="conn-card" aria-labelledby={headId}>
      <div className="conn-card-head">
        <h2 id={headId}>{title}</h2>
        <Badge>Bağlı değil</Badge>
      </div>
      <form className="conn-form" onSubmit={onSubmit} noValidate aria-label={`${title} bağlantısı`}>
        {error && <Banner tone="error">{error}</Banner>}
        <SearchableSelect
          label="Veritabanı"
          options={options}
          value={alias}
          onChange={setAlias}
          placeholder="Ara ya da seçin"
          error={errs.alias}
          emptyText="Eşleşen veritabanı yok."
        />
        <TextField
          label="Kullanıcı adı"
          hint="Oracle veritabanı kullanıcınız"
          autoComplete="off"
          autoCapitalize="none"
          spellCheck={false}
          value={user}
          error={errs.user}
          onChange={(e) => setUser(e.target.value)}
        />
        <PasswordField label="Şifre" autoComplete="off" value={password} error={errs.password} onChange={(e) => setPassword(e.target.value)} />
        <div>
          <Button type="submit" variant="primary" loading={busy}>{busy ? 'Bağlanıyor' : 'Bağlan'}</Button>
        </div>
      </form>
    </section>
  )
}
