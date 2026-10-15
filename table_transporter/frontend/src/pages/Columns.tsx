import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import { getJob, listAliases, listColumns, listJobs, previewRules } from '../api/endpoints.ts'
import { ApiError } from '../api/client.ts'
import type { ColumnInfo, Job, PreviewResponse, Rule, RuleType } from '../api/types.ts'
import { AnonRuleRows, rowIssue } from '../components/wizard/AnonRuleRows.tsx'
import type { AnonRow } from '../components/wizard/AnonRuleRows.tsx'
import { PreviewPanel } from '../components/wizard/PreviewPanel.tsx'
import { RuleEditor } from '../components/wizard/RuleEditor.tsx'
import {
  DEFAULT_MAX_DAYS, MAX_DAYS_LIMIT, defaultAnchor, defaultRule, ruleBlockReason, ruleParamError, suggestRule,
} from '../components/wizard/ruleSuggest.ts'
import type { DateShiftSetting } from '../components/wizard/ruleSuggest.ts'
import { WizardLayout } from '../components/layout/WizardLayout.tsx'
import { Banner, Button, Dialog, Field, Skeleton, TextField, describedBy, useToast } from '../components/ui/index.ts'
import { useWizard } from '../state/wizard.tsx'
import '../components/wizard/rules.css'

const errText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.detail || fallback : fallback)
const isValidDays = (n: number) => Number.isInteger(n) && n >= 1 && n <= MAX_DAYS_LIMIT
const isAnon = (r: Rule) => r.type !== 'keep' && r.type !== 'exclude'

/** Önceki işten gelen parametreleri Rule şekline çevirir; geçersizse null. */
function ruleFromJobColumn(col: ColumnInfo, type: RuleType, params: Record<string, unknown>): Rule | null {
  let rule: Rule
  switch (type) {
    case 'constant': rule = { column: col.name, type, params: { value: String(params.value ?? '') } }; break
    case 'mask': rule = { column: col.name, type, params: { show_last: Number(params.show_last ?? 4) } }; break
    case 'date_shift': rule = { column: col.name, type, params: { anchor: String(params.anchor ?? ''), max_days: Number(params.max_days ?? DEFAULT_MAX_DAYS) } }; break
    default: rule = defaultRule(col, type)
  }
  return ruleBlockReason(col, type) === null && ruleParamError(col, rule) === null ? rule : null
}

export function Columns() {
  const wizard = useWizard()
  const { table, source, filters, fullTable, rules, anonymize, setRules, setAnonymize } = wizard
  const navigate = useNavigate()
  const toast = useToast()
  const anchorId = 'date-anchor'

  const [columns, setColumns] = useState<ColumnInfo[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [reload, setReload] = useState(0)
  const [moved, setMoved] = useState<Set<string>>(new Set())
  const [rows, setRows] = useState<AnonRow[]>([])
  const [anchor, setAnchor] = useState('')
  const [maxDays, setMaxDays] = useState(String(DEFAULT_MAX_DAYS))
  const [live, setLive] = useState(false)
  const [preview, setPreview] = useState<PreviewResponse | null>(null)
  const [previewBusy, setPreviewBusy] = useState(false)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [copyOpen, setCopyOpen] = useState(false)
  const [copyJobs, setCopyJobs] = useState<Job[] | null>(null)
  const [copyError, setCopyError] = useState<string | null>(null)
  const [copyChoice, setCopyChoice] = useState<string | null>(null)
  const [copyBusy, setCopyBusy] = useState(false)
  const [focusRow, setFocusRow] = useState<string | null>(null)

  const rulesRef = useRef(rules)
  rulesRef.current = rules
  const anonymizeRef = useRef(anonymize)
  anonymizeRef.current = anonymize
  const setRulesRef = useRef(setRules)
  setRulesRef.current = setRules
  const rowSeq = useRef(0)
  const rowsBox = useRef<HTMLDivElement>(null)
  const toolbar = useRef<HTMLDivElement>(null)
  const nextId = () => `r${++rowSeq.current}`

  const tableKey = table ? `${table.schema}.${table.table}` : ''
  useEffect(() => {
    if (!table) return
    let cancelled = false
    setColumns(null)
    setLoadError(null)
    listColumns('source', table.schema, table.table)
      .then((res) => {
        if (cancelled) return
        const cols = res.columns
        const existing = new Map(rulesRef.current.map((r) => [r.column, r]))
        const dateRule = rulesRef.current.find((r) => r.type === 'date_shift')
        const setting: DateShiftSetting = dateRule && dateRule.type === 'date_shift' && isValidDays(dateRule.params.max_days)
          ? dateRule.params
          : { anchor: defaultAnchor(cols), max_days: DEFAULT_MAX_DAYS }
        // Kalıcı kurallardan yeniden kur: exclude -> taşınmaz; keep -> taşınır; diğerleri -> satır.
        const mv = new Set<string>()
        const rs: AnonRow[] = []
        for (const c of cols) {
          if (!c.supported) continue
          const prev = existing.get(c.name)
          const valid = prev && ruleBlockReason(c, prev.type) === null && ruleParamError(c, prev) === null
          if (valid && prev.type === 'exclude') continue
          mv.add(c.name)
          if (valid && anonymizeRef.current && isAnon(prev)) rs.push({ id: nextId(), column: c.name, rule: prev })
        }
        setColumns(cols)
        setAnchor(setting.anchor)
        setMaxDays(String(setting.max_days))
        setMoved(mv)
        setRows(rs)
      })
      .catch((e) => { if (!cancelled) setLoadError(errText(e, 'Kolonlar okunamadı. Bağlantıyı kontrol edip yeniden deneyin.')) })
    return () => { cancelled = true }
    // yalnız tablo ve yeniden deneme tetikler.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tableKey, reload])

  const sourceAlias = source?.alias
  useEffect(() => {
    if (!sourceAlias) return
    let cancelled = false
    listAliases().then((r) => { if (!cancelled) setLive(r.aliases.some((a) => a.alias === sourceAlias && !a.target_allowed)) }).catch(() => {})
    return () => { cancelled = true }
  }, [sourceAlias])

  const daysNum = Number(maxDays)
  const daysError = maxDays.trim() === '' || !isValidDays(daysNum) ? `Gün sayısı 1 ile ${MAX_DAYS_LIMIT} arasında tam sayı olmalı.` : null
  const effective: DateShiftSetting = { anchor, max_days: daysError ? DEFAULT_MAX_DAYS : daysNum }
  const byName = useMemo(() => new Map((columns ?? []).map((c) => [c.name, c])), [columns])
  const hasShift = anonymize && rows.some((r) => r.rule?.type === 'date_shift')

  // Sihirbaza giden tam kural dizisi: taşınmayan exclude; kapalı/satırsız/eksik satır keep; aksi halde satırdaki kural.
  const built = useMemo<Rule[]>(() => {
    if (!columns) return []
    const rowBy = new Map(rows.filter((r) => r.rule).map((r) => [r.column, r.rule as Rule]))
    return columns.map((c) => {
      if (!c.supported || !moved.has(c.name)) return defaultRule(c, 'exclude')
      const r = anonymize ? rowBy.get(c.name) : undefined
      if (!r) return defaultRule(c, 'keep')
      return r.type === 'date_shift' ? { ...r, params: { anchor, max_days: daysError ? DEFAULT_MAX_DAYS : daysNum } } : r
    })
  }, [columns, moved, rows, anonymize, anchor, daysNum, daysError])
  const builtKey = JSON.stringify(built)
  useEffect(() => {
    if (!columns) return
    if (JSON.stringify(rulesRef.current) !== builtKey) setRulesRef.current(JSON.parse(builtKey) as Rule[])
  }, [columns, builtKey])

  const resetPreview = () => {
    setPreview(null)
    setPreviewError(null)
  }

  // --- Taşınacak kolonlar ---
  const toggleMove = (name: string) => {
    resetPreview()
    setMoved((s) => {
      const n = new Set(s)
      if (!n.delete(name)) n.add(name)
      return n
    })
  }
  const toggleAllMove = () => {
    resetPreview()
    const sup = (columns ?? []).filter((c) => c.supported).map((c) => c.name)
    setMoved((s) => (sup.length > 0 && sup.every((n) => s.has(n)) ? new Set() : new Set(sup)))
  }

  // --- Anonimleştirme satırları ---
  const usable = (columns ?? []).filter((c) => c.supported && moved.has(c.name) && !rows.some((r) => r.column === c.name))
  const addRow = () => {
    const id = nextId()
    resetPreview()
    setRows((rs) => [...rs, { id, column: '', rule: null }])
    setFocusRow(id)
  }
  const changeRow = (row: AnonRow) => {
    resetPreview()
    setRows((rs) => rs.map((r) => (r.id === row.id ? row : r)))
  }
  const removeRow = (id: string) => {
    resetPreview()
    setRows((rs) => rs.filter((r) => r.id !== id))
    setFocusRow(null)
    // Odak, silinen satırla birlikte kaybolmasın.
    setTimeout(() => toolbar.current?.querySelector<HTMLButtonElement>('button')?.focus(), 0)
  }
  useEffect(() => {
    if (!focusRow) return
    rowsBox.current?.querySelector<HTMLInputElement>(`[data-row-id="${focusRow}"] input[role="combobox"]`)?.focus()
    setFocusRow(null)
  }, [focusRow, rows])

  const changeDate = (nextAnchor: string, nextDays: string) => {
    resetPreview()
    setAnchor(nextAnchor)
    setMaxDays(nextDays)
  }

  const suggestions = useMemo(() => {
    const out: Rule[] = []
    for (const c of usable) {
      const s = suggestRule(c, effective)
      if (s && s.type !== 'keep') out.push(s)
    }
    return out
    // effective her render yeni nesne; içerik anchor/daysNum/daysError'a bağlı.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [usable.map((c) => c.name).join('|'), columns, anchor, daysNum, daysError])
  const addSuggestions = () => {
    resetPreview()
    setRows((rs) => [...rs, ...suggestions.map((rule) => ({ id: nextId(), column: rule.column, rule }))])
    toast.push(`${suggestions.length.toLocaleString('tr-TR')} kolon için öneri eklendi`, 'success')
  }

  const openCopy = () => {
    if (!table) return
    setCopyOpen(true)
    setCopyJobs(null)
    setCopyError(null)
    setCopyChoice(null)
    listJobs(true)
      .then((r) => setCopyJobs(r.items.filter((j) => j.source_schema === table.schema && j.source_table === table.table)))
      .catch((e) => setCopyError(errText(e, 'Önceki işler okunamadı.')))
  }
  const applyCopy = async () => {
    if (!copyChoice || !columns) return
    setCopyBusy(true)
    try {
      const job = await getJob(copyChoice)
      const map = new Map<string, Rule>()
      let skipped = 0
      let setting: DateShiftSetting | null = null
      for (const jc of job.columns ?? []) {
        const col = byName.get(jc.column_name)
        if (!col) continue
        const rule = ruleFromJobColumn(col, jc.rule_type, jc.params)
        if (!rule) { skipped++; continue }
        if (rule.type === 'date_shift' && !setting && byName.has(rule.params.anchor)) setting = rule.params
        map.set(col.name, rule)
      }
      const mv = new Set(moved)
      let nextRows = rows.filter((r) => !map.has(r.column))
      for (const [name, rule] of map) {
        if (rule.type === 'exclude') { mv.delete(name); continue }
        mv.add(name)
        if (isAnon(rule)) nextRows = [...nextRows, { id: nextId(), column: name, rule }]
      }
      if (setting) {
        setAnchor(setting.anchor)
        setMaxDays(String(setting.max_days))
      }
      resetPreview()
      setMoved(mv)
      setRows(nextRows)
      setCopyOpen(false)
      toast.push(skipped > 0 ? `${map.size.toLocaleString('tr-TR')} kolonun kuralı kopyalandı; ${skipped.toLocaleString('tr-TR')} kolon uygun değildi` : `${map.size.toLocaleString('tr-TR')} kolonun kuralı kopyalandı`, skipped > 0 ? 'info' : 'success')
    } catch (e) {
      setCopyError(errText(e, 'İş ayrıntısı okunamadı.'))
    } finally {
      setCopyBusy(false)
    }
  }

  // İleri'yi kapatan ilk neden; anonimleştirme kapalıyken satırlar sayılmaz.
  const rowProblem = useMemo(() => {
    if (!anonymize) return null
    for (const r of rows) {
      const issue = rowIssue(r, byName, moved)
      if (issue) return `Anonimleştirme satırı tamamlanmadı: ${issue}`
      const col = byName.get(r.column)
      if (col && r.rule) {
        const pe = ruleParamError(col, r.rule)
        if (pe) return `${r.column} kuralında hata var: ${pe}`
      }
    }
    return null
  }, [anonymize, rows, byName, moved])
  const blockReason = columns === null ? null
    : moved.size === 0 ? 'En az bir kolonu "Taşı" ile işaretleyin.'
      : rowProblem ?? (hasShift && daysError ? daysError : null)
  const ready = columns !== null && blockReason === null
  const ruleRows = rows.filter((r) => r.rule).length

  const onPreview = async () => {
    if (!table) return
    setPreviewBusy(true)
    setPreviewError(null)
    try {
      setPreview(await previewRules({ source: table, filters, full_table: fullTable, rules: built }))
    } catch (e) {
      setPreview(null)
      setPreviewError(errText(e, 'Önizleme alınamadı. Bağlantıyı kontrol edip yeniden deneyin.'))
    } finally {
      setPreviewBusy(false)
    }
  }

  if (!table) {
    return (
      <WizardLayout step="columns" title="Kolonlar ve kurallar" onBack={() => navigate('/table')}>
        <Banner tone="warn">Önce bir tablo seçmelisiniz. Geri dönüp tablo adımını tamamlayın.</Banner>
      </WizardLayout>
    )
  }

  const addDisabledReason = usable.length > 0 ? null
    : moved.size === 0 ? 'Önce taşınacak kolon işaretleyin.' : 'Uygun tüm kolonlar için satır eklendi.'

  return (
    <WizardLayout
      step="columns"
      title="Kolonlar ve kurallar"
      description={`${table.schema}.${table.table} tablosundan taşınacak kolonları seçin; istenirse bazıları anonimleştirilir.`}
      onBack={() => navigate('/table')}
      onNext={() => navigate('/target')}
      nextDisabled={!ready}
    >
      {live && (
        <Banner tone="warn">Kaynak canlı ortam. Anonimleştirilmeyen kolonlar için Özet adımında ek onay istenecek.</Banner>
      )}
      {loadError && (
        <Banner tone="error" title="Kolonlar yüklenemedi" action={<Button size="sm" onClick={() => setReload((n) => n + 1)}>Yeniden dene</Button>}>{loadError}</Banner>
      )}
      {!columns && !loadError && (
        <div role="status" aria-label="Kolonlar yükleniyor"><Skeleton width="60%" /><Skeleton /><Skeleton width="80%" /></div>
      )}
      {columns && (
        <>
          <RuleEditor columns={columns} moved={moved} onToggle={toggleMove} onToggleAll={toggleAllMove} />

          <section className="col-panel" aria-labelledby="anon-title">
            <div className="col-panel-head">
              <h2 id="anon-title">Anonimleştirme</h2>
              <label className="anon-toggle">
                <input
                  type="checkbox"
                  className="check"
                  checked={anonymize}
                  onChange={(e) => { resetPreview(); setAnonymize(e.target.checked) }}
                />
                Anonimleştirme yapacağım
              </label>
            </div>
            {!anonymize && <p className="rule-desc">Kapalıyken seçili kolonlar olduğu gibi taşınır.</p>}
            {anonymize && (
              <div className="anon-body">
                <Banner tone="info" title="Hangi alan nasıl anonimleştirilir?">
                  <table className="std-table">
                    <caption className="anon-label">Standart kurallar</caption>
                    <thead><tr><th scope="col">Veri tipi</th><th scope="col">Kural</th><th scope="col">Ne korunur</th><th scope="col">Geri çevrilir</th></tr></thead>
                    <tbody>
                      <tr><td>Sayı (NUMBER)</td><td>Sayı belirteci</td><td>basamak sayısı, işaret, ondalık</td><td>Evet</td></tr>
                      <tr><td>Metin (VARCHAR2/CHAR)</td><td>Metin belirteci</td><td>uzunluk, harf/rakam türü, boşluk ve noktalama</td><td>Evet</td></tr>
                      <tr><td>Tarih (DATE/TIMESTAMP)</td><td>Tarih belirteci</td><td>yıl; saat yoksa gece yarısı</td><td>Evet</td></tr>
                    </tbody>
                  </table>
                  <p>Çözmek için Değer çöz ekranında kuralı seçip değerleri yapıştırın. Tek yönlü (isteğe bağlı): HMAC, maske, sabit, boş, rastgele, tarih kaydırma.</p>
                </Banner>

                <div className="rules-toolbar" role="group" aria-label="Anonimleştirme işlemleri" ref={toolbar}>
                  <div className="toolbar-group">
                    <Button variant="primary" onClick={addRow} disabled={usable.length === 0} aria-describedby={addDisabledReason ? 'anon-add-reason' : undefined}>+ Kolon ekle</Button>
                    <Button onClick={addSuggestions} disabled={suggestions.length === 0}>Önerileri ekle ({suggestions.length.toLocaleString('tr-TR')})</Button>
                    <Button onClick={openCopy}>Önceki işten kopyala</Button>
                  </div>
                  <span className="toolbar-sep" />
                  <span role="status" aria-live="polite">{rows.length.toLocaleString('tr-TR')} kolon için kural</span>
                </div>
                {addDisabledReason && <p className="rule-desc" id="anon-add-reason">{addDisabledReason}</p>}

                <div ref={rowsBox}>
                  {rows.length > 0 && (
                    <AnonRuleRows columns={columns} moved={moved} rows={rows} dateSetting={effective} onChange={changeRow} onRemove={removeRow} />
                  )}
                </div>

                {hasShift && (
                  <section className="date-block" aria-labelledby="date-block-title">
                    <h3 id="date-block-title">Tarih kaydırma ayarı</h3>
                    <Field id={anchorId} label="Kaynak kolon" hint="Kaydırma miktarı bu kolonun değerinden türetilir; aynı değer her zaman aynı kaymayı alır.">
                      <select id={anchorId} className="input" aria-describedby={describedBy(anchorId, 'x')} value={anchor} onChange={(e) => changeDate(e.target.value, maxDays)}>
                        {columns.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
                      </select>
                    </Field>
                    <TextField
                      label="En çok gün"
                      hint={`1 ile ${MAX_DAYS_LIMIT.toLocaleString('tr-TR')} arası; tarih bu kadar gün öne ya da arkaya kayar.`}
                      type="number"
                      inputMode="numeric"
                      min={1}
                      max={MAX_DAYS_LIMIT}
                      value={maxDays}
                      error={daysError}
                      onChange={(e) => changeDate(anchor, e.target.value)}
                    />
                  </section>
                )}

                {ruleRows > 0 && (
                  <>
                    <div>
                      <Button variant="primary" onClick={onPreview} loading={previewBusy} disabled={blockReason !== null}>Önizle</Button>
                    </div>
                    <PreviewPanel data={preview} loading={previewBusy} error={previewError} />
                  </>
                )}
              </div>
            )}
          </section>

          {blockReason && <p className="rule-desc block-reason" role="status">İleri için: {blockReason}</p>}
        </>
      )}

      <Dialog
        open={copyOpen}
        title="Önceki işten kopyala"
        onClose={() => setCopyOpen(false)}
        actions={
          <>
            <Button onClick={() => setCopyOpen(false)}>Vazgeç</Button>
            <Button variant="primary" onClick={applyCopy} disabled={!copyChoice} loading={copyBusy}>Kuralları uygula</Button>
          </>
        }
      >
        {copyError && <Banner tone="error">{copyError}</Banner>}
        {!copyJobs && !copyError && <div role="status" aria-label="İşler yükleniyor"><Skeleton width="70%" /><Skeleton /></div>}
        {copyJobs && copyJobs.length === 0 && <p>Bu tablo için daha önce çalıştırdığınız bir iş yok.</p>}
        {copyJobs && copyJobs.length > 0 && (
          <fieldset className="copy-list">
            <legend>Kuralları alınacak iş</legend>
            {copyJobs.map((j) => (
              <label key={j.id}>
                <input type="radio" name="copy-job" className="check" checked={copyChoice === j.id} onChange={() => setCopyChoice(j.id)} />
                <span>
                  {j.target_schema}.{j.target_table}
                  <small>{new Date(j.created_at).toLocaleString('tr-TR')}</small>
                </span>
              </label>
            ))}
          </fieldset>
        )}
        <p className="rule-desc">Kolonlar ada göre eşleşir; bu tabloda geçersiz olan kurallar atlanır.</p>
      </Dialog>
    </WizardLayout>
  )
}
