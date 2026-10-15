import { Fragment, useEffect, useId, useState } from 'react'
import { useNavigate } from 'react-router'
import { listAliases, startTransfer } from '../api/endpoints.ts'
import { ApiError } from '../api/client.ts'
import type { ErrorCode, Filter, FilterOp, RuleType } from '../api/types.ts'
import { WizardLayout } from '../components/layout/WizardLayout.tsx'
import { Banner, Button, TextField } from '../components/ui/index.ts'
import { RULE_LABELS } from '../components/wizard/ruleSuggest.ts'
import { useWizard } from '../state/wizard.tsx'
import '../components/wizard/wizard.css'
import '../components/wizard/summary.css'

const OP_TEXT: Partial<Record<FilterOp, string>> = {
  eq: '=', ne: '<>', lt: '<', le: '<=', gt: '>', ge: '>=', like: 'içerir (LIKE)', in: 'şunlardan biri', between: 'arasında',
}
const fmt = (v: string | number) => (typeof v === 'number' ? v.toLocaleString('tr-TR') : v)

/** Filtreyi insan diliyle yazar: "BOLGE = IST". */
export function filterText(f: Filter): string {
  if (f.op === 'is_null') return `${f.column} boş`
  if (f.op === 'is_not_null') return `${f.column} dolu`
  if (f.op === 'in') return `${f.column} ${OP_TEXT.in}: ${(f.values ?? []).map(fmt).join(', ')}`
  if (f.op === 'between') return `${f.column} ${fmt(f.values?.[0] ?? '')} ile ${fmt(f.values?.[1] ?? '')} ${OP_TEXT.between}`
  return `${f.column} ${OP_TEXT[f.op]} ${f.value === undefined ? '' : fmt(f.value)}`.trim()
}

type Back = '/table' | '/columns' | '/connect'
const ERROR_TEXT: Partial<Record<ErrorCode, { text: string; back?: { to: Back; label: string } }>> = {
  LIMIT_EXCEEDED: { text: 'Satır sayısı sınırı aşıldı. Filtre ekleyerek taşınacak veri miktarını azaltın.', back: { to: '/table', label: 'Tablo adımına dön' } },
  TABLE_EXISTS: { text: 'Bu adla bir tablo zaten var. Hedef adımında başka bir ad seçin.' },
  TARGET_LOCKED: { text: 'Bu hedefe başka bir taşıma sürüyor. Bitmesini bekleyin ya da başka bir hedef seçin.' },
  INCOMPATIBLE: { text: 'Kaynak ve hedef tablo uyumsuz. Hedef adımında uyumluluk raporunu inceleyin.' },
  RULE_INVALID: { text: 'Bir kural geçersiz. Kolonlar adımında kuralları düzeltin.', back: { to: '/columns', label: 'Kolonlar adımına dön' } },
  TRIGGER_PRESENT: { text: 'Hedef tabloda etkin trigger var; güvenlik nedeniyle bu tabloya eklenemez. Başka bir hedef seçin.' },
  TARGET_DENIED: { text: 'Bu hedefe yazma izniniz yok. Başka bir hedef bağlantısı ya da şema seçin.' },
  DB_DISCONNECTED: { text: 'Veritabanı bağlantısı koptu. Bağlantıları yeniden kurun.', back: { to: '/connect', label: 'Bağlantılar adımına dön' } },
}

export function Summary() {
  const { source, target, table, filters, fullTable, count, rules, targetSetting, setJobId } = useWizard()
  const navigate = useNavigate()
  const confirmId = useId()

  const [live, setLive] = useState(false)
  const [typed, setTyped] = useState('')
  const [unmasked, setUnmasked] = useState(false)
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<{ code: ErrorCode | null; detail: string } | null>(null)

  const sourceAlias = source?.alias
  useEffect(() => {
    if (!sourceAlias) return
    let cancelled = false
    listAliases().then((r) => { if (!cancelled) setLive(r.aliases.some((a) => a.alias === sourceAlias && !a.target_allowed)) }).catch(() => {})
    return () => { cancelled = true }
  }, [sourceAlias])

  if (!source || !target || !table || !targetSetting || rules.length === 0) {
    return (
      <WizardLayout step="summary" title="Özet ve onay" onBack={() => navigate('/target')}>
        <Banner tone="warn">Özet için önceki adımların tamamlanması gerekir. Geri dönüp eksik adımı tamamlayın.</Banner>
      </WizardLayout>
    )
  }

  const expected = `${targetSetting.schema}.${targetSetting.table}`
  const hasKeep = rules.some((r) => r.type === 'keep')
  const needUnmasked = live && hasKeep
  const counts = new Map<RuleType, number>()
  for (const r of rules) counts.set(r.type, (counts.get(r.type) ?? 0) + 1)
  const kept = rules.filter((r) => r.type === 'keep').map((r) => r.column)
  const excluded = rules.filter((r) => r.type === 'exclude').map((r) => r.column)
  const canStart = typed === expected && (!needUnmasked || unmasked) && !busy

  const start = async () => {
    if (!canStart) return
    setBusy(true)
    setFailure(null)
    try {
      const res = await startTransfer({
        source: table,
        target: { schema: targetSetting.schema, table: targetSetting.table, mode: targetSetting.mode },
        rules,
        filters,
        full_table: fullTable,
        confirm_target: typed,
        confirm_unmasked_live: needUnmasked ? unmasked : false,
      })
      setJobId(res.job_id)
      navigate(`/jobs/${res.job_id}`)
    } catch (e) {
      setFailure(e instanceof ApiError ? { code: e.error, detail: e.detail } : { code: null, detail: '' })
      setBusy(false)
    }
  }

  const known = failure?.code ? ERROR_TEXT[failure.code] : undefined
  const failureText = known?.text ?? (failure?.code === 'VALIDATION' ? `Girilen bilgiler geçersiz${failure.detail ? `: ${failure.detail}` : '.'}` : failure ? failure.detail || 'Taşıma başlatılamadı. Yeniden deneyin.' : '')

  return (
    <WizardLayout step="summary" title="Özet ve onay" description="Bilgileri son kez gözden geçirin. Başlattıktan sonra taşıma arka planda çalışır." onBack={() => navigate('/target')}>
      <div className="summary-grid">
        <section className="summary-card" aria-labelledby="sum-source">
          <h2 id="sum-source">Kaynak</h2>
          <dl className="summary-list">
            <dt>Bağlantı</dt><dd>{source.alias}</dd>
            <dt>Tablo</dt><dd>{table.schema}.{table.table}</dd>
            <dt>Satır sayısı</dt><dd>{count?.count != null ? count.count.toLocaleString('tr-TR') : 'Bilinmiyor'}</dd>
            <dt>Kapsam</dt>
            <dd>
              {fullTable ? `Tablonun tamamı (${count?.count != null ? count.count.toLocaleString('tr-TR') : '?'} satır)` : filters.length === 0 ? 'Filtre yok' : (
                <ul className="summary-bullets">{filters.map((f, i) => <li key={i}>{filterText(f)}</li>)}</ul>
              )}
            </dd>
          </dl>
        </section>

        <section className="summary-card" aria-labelledby="sum-target">
          <h2 id="sum-target">Hedef</h2>
          <dl className="summary-list">
            <dt>Bağlantı</dt><dd>{target.alias}</dd>
            <dt>Tür</dt><dd>{targetSetting.mode === 'create' ? 'Yeni tablo oluştur' : 'Mevcut tabloya ekle'}</dd>
            <dt>Tablo</dt><dd>{expected}</dd>
          </dl>
        </section>

        <section className="summary-card wide" aria-labelledby="sum-rules">
          <h2 id="sum-rules">Kurallar</h2>
          <dl className="summary-list">
            {[...counts].map(([t, n]) => (<Fragment key={t}><dt>{RULE_LABELS[t]}</dt><dd>{n.toLocaleString('tr-TR')} kolon</dd></Fragment>))}
          </dl>
          {kept.length > 0 && <p>Olduğu gibi taşınan kolonlar: <strong>{kept.join(', ')}</strong></p>}
          {excluded.length > 0 && <p>Taşınmayan kolonlar: <strong>{excluded.join(', ')}</strong></p>}
        </section>
      </div>

      <div className="panel">
        <h2>Onay</h2>
        <TextField
          label="Onaylamak için hedefi yazın"
          hint={`Şunu yazın: ${expected}`}
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          autoComplete="off"
          spellCheck={false}
        />
        {needUnmasked && (
          <label className="confirm-check" htmlFor={confirmId}>
            <input id={confirmId} type="checkbox" className="check" checked={unmasked} onChange={(e) => setUnmasked(e.target.checked)} />
            <span>Canlı ortamdan anonimleştirilmemiş kolonlar taşındığını biliyorum ve onaylıyorum</span>
          </label>
        )}
        {failure && (
          <Banner
            tone="error"
            title="Taşıma başlatılamadı"
            action={known?.back ? <Button size="sm" onClick={() => navigate(known.back!.to)}>{known.back.label}</Button> : undefined}
          >
            {failureText}
          </Banner>
        )}
        <div>
          <Button variant="primary" onClick={start} disabled={!canStart} loading={busy}>Taşımayı başlat</Button>
        </div>
      </div>
    </WizardLayout>
  )
}
