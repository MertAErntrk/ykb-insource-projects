import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router'
import { dbStatus, listAliases } from '../api/endpoints.ts'
import type { Role, TnsAlias } from '../api/types.ts'
import { ConnectionCard, connectErrorMessage } from '../components/wizard/ConnectionCard.tsx'
import { WizardLayout } from '../components/layout/WizardLayout.tsx'
import { Banner } from '../components/ui/index.ts'
import { useWizard } from '../state/wizard.tsx'
import type { WizardConnection } from '../state/wizard.tsx'

export function Connect() {
  const { source, target, table, setConnection, setTable } = useWizard()
  const navigate = useNavigate()
  const [aliases, setAliases] = useState<TnsAlias[]>([])
  const [loadError, setLoadError] = useState<string | null>(null)

  useEffect(() => {
    let off = false
    listAliases()
      .then((r) => { if (!off) setAliases(r.aliases) })
      .catch((e) => { if (!off) setLoadError(connectErrorMessage(e)) })
    // Sunucudaki bağlı roller sihirbaza geri yüklenir (sayfa yenileme).
    dbStatus()
      .then((st) => {
        if (off) return
        for (const role of ['source', 'target'] as const) {
          const info = st[role]
          setConnection(role, info ? { alias: info.alias, user: info.user, info } : null)
        }
      })
      .catch(() => { /* durum alınamazsa kartlar boş görünür; kullanıcı yeniden bağlanabilir */ })
    return () => { off = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const change = (role: Role, value: WizardConnection | null) => {
    setConnection(role, value)
    if (role === 'source' && table) setTable(null) // kaynak değişince seçili tablo geçersiz
  }

  const both = !!source && !!target
  const sameAlias = both && source.alias === target.alias

  return (
    <WizardLayout
      step="connect"
      title="Bağlantılar"
      description="Verinin okunacağı kaynağa ve yazılacağı hedefe bağlanın. Şifreler saklanmaz; yalnızca bağlantı süresince kullanılır."
      onNext={() => navigate('/table')}
      nextDisabled={!both}
    >
      <div className="conn-stack">
        {loadError && <Banner tone="error" title="Veritabanı listesi alınamadı">{loadError}</Banner>}
        {sameAlias && (
          <Banner tone="info">
            Kaynak ve hedef aynı veritabanı ({source.alias}). Bu izinlidir; hedef şemayı ya da tablo adını farklı seçmeniz yeterli.
          </Banner>
        )}
        <div className="conn-grid">
          <ConnectionCard role="source" aliases={aliases} connection={source} onConnected={(c) => change('source', c)} onDisconnected={() => change('source', null)} />
          <ConnectionCard role="target" aliases={aliases} connection={target} onConnected={(c) => change('target', c)} onDisconnected={() => change('target', null)} />
        </div>
      </div>
    </WizardLayout>
  )
}
