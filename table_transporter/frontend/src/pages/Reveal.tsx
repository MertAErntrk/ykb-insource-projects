import { useId, useMemo, useState } from 'react'
import { ApiError } from '../api/client.ts'
import { revealValues } from '../api/endpoints.ts'
import type { RevealItem, RevealRule } from '../api/types.ts'
import { Banner, Button, DataTable, Field, describedBy, useToast } from '../components/ui/index.ts'
import type { Column } from '../components/ui/index.ts'
import { RULE_LABELS } from '../components/wizard/ruleSuggest.ts'
import '../components/wizard/wizard.css'
import './reveal.css'

const MAX_VALUES = 100
const MAX_LEN = 4000
const HINT = 'Her satıra bir değer yazın. Tekrarlayan satırlar bir kez çözülür.'

const RULES: { value: RevealRule; hint: string }[] = [
  { value: 'num_token', hint: 'NUMBER kolonlar için.' },
  { value: 'text_token', hint: 'VARCHAR2 / CHAR kolonlar için.' },
  { value: 'date_token', hint: 'DATE / TIMESTAMP kolonlar için. Tarih biçimi: YYYY-AA-GG, GG.AA.YYYY ya da GG/AA/YYYY; isterseniz saat ekleyin (SS:DD:ss).' },
]

export function Reveal() {
  const toast = useToast()
  const [rule, setRule] = useState<RevealRule>('num_token')
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [items, setItems] = useState<RevealItem[] | null>(null)
  const ruleId = useId()
  const textId = useId()

  const values = useMemo(() => [...new Set(text.split(/\r?\n/).map((v) => (rule === 'text_token' ? v : v.trim())).filter((v) => v.trim() !== ''))], [text, rule])
  const tooMany = values.length > MAX_VALUES
  const tooLong = values.some((v) => v.length > MAX_LEN)
  const textError = tooMany
    ? `En çok ${MAX_VALUES} değer çözülebilir.`
    : tooLong ? `Her değer en çok ${MAX_LEN} karakter olabilir.` : null
  const canRun = values.length > 0 && !textError
  const hint = RULES.find((r) => r.value === rule)?.hint

  const run = async () => {
    setBusy(true)
    setError(null)
    try {
      const r = await revealValues({ rule, values })
      setItems(r.items)
    } catch (e) {
      setItems(null)
      setError(e instanceof ApiError ? e.detail : 'Değerler çözülemedi. Yeniden deneyin.')
    } finally {
      setBusy(false)
    }
  }

  const copy = async () => {
    const out = (items ?? []).map((i) => i.original ?? '').join('\n')
    try {
      await navigator.clipboard.writeText(out)
      toast.push('Gerçek değerler kopyalandı.', 'success')
    } catch {
      toast.push('Panoya kopyalanamadı.', 'error')
    }
  }

  const tableColumns: Column<RevealItem>[] = [
    { key: 'value', header: 'Anonim değer', render: (i) => i.value },
    {
      key: 'original', header: 'Gerçek değer',
      render: (i) => (i.error ? <span className="reveal-error">{i.error}</span> : i.original),
    },
  ]

  return (
    <div className="page reveal-page">
      <h1 className="page-title" style={{ margin: 0 }}>Değer çöz</h1>
      <p>Anonimleştirilmiş değerin gerçek karşılığını gösterir: kuralı seçin, değerleri yapıştırın.</p>
      <Field id={ruleId} label="Kural" hint={hint}>
        <select
          id={ruleId}
          className="input"
          value={rule}
          aria-describedby={describedBy(ruleId, hint, null)}
          onChange={(e) => { setRule(e.target.value as RevealRule); setItems(null); setError(null) }}
        >
          {RULES.map((r) => <option key={r.value} value={r.value}>{RULE_LABELS[r.value]}</option>)}
        </select>
      </Field>
      <Field id={textId} label="Anonim değerler" hint={HINT} error={textError}>
        <textarea
          id={textId}
          className="input mono reveal-values"
          rows={8}
          value={text}
          placeholder="Her satıra bir değer (en çok 100)"
          spellCheck={false}
          autoComplete="off"
          aria-invalid={textError ? true : undefined}
          aria-describedby={describedBy(textId, HINT, textError)}
          onChange={(e) => setText(e.target.value)}
        />
      </Field>
      <p className="reveal-counter" aria-live="polite">{values.length} / {MAX_VALUES}</p>
      <div>
        <Button variant="primary" loading={busy} disabled={!canRun} onClick={() => void run()}>Çöz</Button>
      </div>
      <p className="reveal-counter">Çözme işlemi kayıt altına alınır; değerler kaydedilmez.</p>
      {error && <Banner tone="error">{error}</Banner>}
      <div aria-live="polite">
        {items && (
          <>
            <p>{items.length} değer çözüldü.</p>
            <DataTable caption="Çözülen değerler" columns={tableColumns} rows={items} rowKey={(i) => i.value} maxHeight="none" />
            <div className="reveal-actions">
              <Button onClick={() => void copy()}>Gerçek değerleri kopyala</Button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
