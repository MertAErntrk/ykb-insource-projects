import { useEffect, useId, useState } from 'react'
import { checkTracked, getDropScript, listCleanup } from '../api/endpoints.ts'
import type { TableStatus, TrackedTable } from '../api/types.ts'
import { fmtDateTime } from '../components/jobs/JobStatusBadge.tsx'
import { Badge, Banner, Button, DataTable, useToast } from '../components/ui/index.ts'
import type { Column, Tone } from '../components/ui/index.ts'
import { useAuth } from '../state/auth.tsx'
import '../components/wizard/wizard.css'
import '../components/jobs/jobs.css'

const STATUS_TEXT: Record<TableStatus, string> = {
  active: 'Süresi dolmadı', expired_present: 'Silinmeli', deleted_verified: 'Silindi', unverifiable: 'Doğrulanamadı',
}
const STATUS_TONE: Record<TableStatus, Tone> = {
  active: 'neutral', expired_present: 'warn', deleted_verified: 'success', unverifiable: 'danger',
}
const STATUS_HINT: Partial<Record<TableStatus, string>> = {
  unverifiable: 'Tablonun varlığı kontrol edilemedi; bağlantı ya da yetki sorunu olabilir.',
}
const DAY = 86_400_000

function remaining(expiresAt: string): string {
  const days = Math.ceil((Date.parse(expiresAt) - Date.now()) / DAY)
  return days <= 0 ? 'Süresi doldu' : `${days.toLocaleString('tr-TR')} gün kaldı`
}

export function Cleanup() {
  const { user } = useAuth()
  const toast = useToast()
  const isAdmin = user?.is_admin === true
  const [items, setItems] = useState<TrackedTable[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [filter, setFilter] = useState<TableStatus | ''>('')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [checking, setChecking] = useState<string | null>(null)
  const [downloading, setDownloading] = useState(false)
  const filterId = useId()

  useEffect(() => {
    let alive = true
    listCleanup()
      .then((r) => { if (alive) setItems(r.items) })
      .catch(() => { if (alive) setError(true) })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [])

  const rows = filter ? items.filter((t) => t.table_status === filter) : items

  const toggle = (jobId: string, on: boolean) =>
    setSelected((s) => { const n = new Set(s); if (on) n.add(jobId); else n.delete(jobId); return n })

  const check = async (t: TrackedTable) => {
    setChecking(t.job_id)
    try {
      const u = await checkTracked(t.job_id)
      setItems((l) => l.map((x) => (x.id === u.id ? u : x)))
      if (u.table_status !== 'expired_present') setSelected((s) => { const n = new Set(s); n.delete(u.job_id); return n })
      toast.push(`${u.schema}.${u.table}: ${STATUS_TEXT[u.table_status]}.`, u.table_status === 'unverifiable' ? 'error' : 'success')
    } catch {
      toast.push('Kontrol yapılamadı. Yeniden deneyin.', 'error')
    } finally {
      setChecking(null)
    }
  }

  const download = async () => {
    setDownloading(true)
    try {
      const script = await getDropScript([...selected])
      const url = URL.createObjectURL(new Blob([script], { type: 'text/plain;charset=utf-8' }))
      const a = document.createElement('a')
      a.href = url
      a.download = 'drop_script.sql'
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
    } catch {
      toast.push('DROP script üretilemedi. Yeniden deneyin.', 'error')
    } finally {
      setDownloading(false)
    }
  }

  const columns: Column<TrackedTable>[] = [
    {
      key: 'select', header: 'Seç',
      render: (t) => t.table_status === 'expired_present' ? (
        <input
          type="checkbox" className="check row-check"
          aria-label={`${t.schema}.${t.table} seç`}
          checked={selected.has(t.job_id)}
          onChange={(e) => toggle(t.job_id, e.target.checked)}
        />
      ) : null,
    },
    { key: 'table', header: 'Tablo', render: (t) => `${t.alias} · ${t.schema}.${t.table}` },
    ...(isAdmin ? [{ key: 'owner', header: 'Sahip', render: (t: TrackedTable) => t.owner }] : []),
    { key: 'created', header: 'Oluşturma', render: (t) => fmtDateTime(t.object_created) },
    {
      key: 'expires', header: 'Son kullanma',
      render: (t) => (<>{fmtDateTime(t.expires_at)}<span className="cell-sub">{remaining(t.expires_at)}</span></>),
    },
    {
      key: 'status', header: 'Durum',
      render: (t) => (
        <>
          <Badge tone={STATUS_TONE[t.table_status]}>{STATUS_TEXT[t.table_status]}</Badge>
          {STATUS_HINT[t.table_status] && <span className="cell-sub">{STATUS_HINT[t.table_status]}</span>}
        </>
      ),
    },
    { key: 'checked', header: 'Son kontrol', render: (t) => fmtDateTime(t.last_checked_at) },
    {
      key: 'action', header: 'İşlem',
      render: (t) => (
        <Button size="sm" onClick={() => check(t)} loading={checking === t.job_id} disabled={checking !== null}>
          Şimdi kontrol et{' '}<span className="sr-only">{t.schema}.{t.table}</span>
        </Button>
      ),
    },
  ]

  return (
    <div className="page" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
      <h1 className="page-title" style={{ margin: 0 }}>Temizlik</h1>
      <Banner tone="info">
        Uygulama hiçbir tabloyu silmez. Süresi dolan tablolar için DROP script üretilir; script'i DBA gözden geçirip elle çalıştırır.
      </Banner>
      <div className="list-toolbar">
        <label htmlFor={filterId}>
          Durum
          <select id={filterId} value={filter} onChange={(e) => setFilter(e.target.value as TableStatus | '')}>
            <option value="">Tümü</option>
            {(Object.keys(STATUS_TEXT) as TableStatus[]).map((s) => <option key={s} value={s}>{STATUS_TEXT[s]}</option>)}
          </select>
        </label>
        <Button variant="primary" onClick={download} disabled={selected.size === 0} loading={downloading}>
          DROP script indir{selected.size > 0 ? ` (${selected.size.toLocaleString('tr-TR')})` : ''}
        </Button>
      </div>
      {error && <Banner tone="error">Temizlik listesi alınamadı. Sayfayı yenileyin.</Banner>}
      <DataTable
        caption="İzlenen tablolar"
        columns={columns}
        rows={rows}
        rowKey={(t) => String(t.id)}
        loading={loading}
        maxHeight="none"
        emptyText="İzlenen tablo yok. Yeni tablo modunda taşıdığınız tablolar burada izlenir."
      />
    </div>
  )
}
