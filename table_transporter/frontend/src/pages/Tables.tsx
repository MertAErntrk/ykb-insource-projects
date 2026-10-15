import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import { ApiError } from '../api/client.ts'
import { countRows, listColumns, listSchemas, listTables } from '../api/endpoints.ts'
import type { ColumnInfo, Filter, TablesResponse } from '../api/types.ts'
import { WizardLayout } from '../components/layout/WizardLayout.tsx'
import { Banner, Button, ProgressBar, SearchableSelect, Skeleton, Spinner } from '../components/ui/index.ts'
import { FilterBuilder } from '../components/wizard/FilterBuilder.tsx'
import { scopeError } from '../components/wizard/scopeRules.ts'
import '../components/wizard/wizard.css'
import { useWizard } from '../state/wizard.tsx'

const ROW_LIMIT = 5_000_000
const TABLE_LIMIT = 100
const fmt = (n: number) => n.toLocaleString('tr-TR')

function useDebounced<T>(value: T, ms = 250): T {
  const [v, setV] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms)
    return () => clearTimeout(t)
  }, [value, ms])
  return v
}

function errorText(e: unknown, fallback: string): string {
  if (e instanceof ApiError) {
    if (e.error === 'DB_DISCONNECTED') return 'Kaynak bağlantısı kopmuş. Bağlantılar adımına dönüp yeniden bağlanın.'
    return e.detail || fallback
  }
  return fallback
}

export function Tables() {
  const { source, table, filters, fullTable, fullTableBlocked, count, setTable, setFilters, setFullTable, setFullTableBlocked, setCount } = useWizard()
  const navigate = useNavigate()

  const [schema, setSchema] = useState<string | null>(table?.schema ?? null)
  const [schemaQ, setSchemaQ] = useState('')
  const dSchemaQ = useDebounced(schemaQ)
  const [schemas, setSchemas] = useState<string[]>([])
  const [tableQ, setTableQ] = useState('')
  const dTableQ = useDebounced(tableQ)
  const [list, setList] = useState<TablesResponse | null>(null)
  const [listLoading, setListLoading] = useState(false)
  const [listError, setListError] = useState<string | null>(null)

  const [columns, setColumns] = useState<ColumnInfo[] | null>(null)
  const [columnsError, setColumnsError] = useState<string | null>(null)
  const [filterErrors, setFilterErrors] = useState(false)

  const [running, setRunning] = useState(false)
  const [countError, setCountError] = useState<string | null>(null)
  const [overNote, setOverNote] = useState(false) // tam tablo 5 milyonu aştı (FilterBuilder yeniden kurulunca silinmesin)
  const abortRef = useRef<AbortController | null>(null)

  const sourceUser = source?.user
  const connected = !!source

  // Şema listesi (sunucu süzer); varsayılan: kaynak kullanıcısının şeması.
  useEffect(() => {
    if (!connected) return
    let off = false
    listSchemas('source', dSchemaQ || undefined)
      .then((r) => {
        if (off) return
        setSchemas(r.schemas)
        if (!dSchemaQ) setSchema((cur) => cur ?? (sourceUser && r.schemas.includes(sourceUser.toUpperCase()) ? sourceUser.toUpperCase() : null))
      })
      .catch((e) => { if (!off) setListError(errorText(e, 'Şema listesi alınamadı.')) })
    return () => { off = true }
  }, [connected, dSchemaQ, sourceUser])

  // Tablo listesi.
  useEffect(() => {
    if (!connected || !schema) { setList(null); return }
    let off = false
    setListLoading(true)
    setListError(null)
    listTables('source', { schema, q: dTableQ || undefined, limit: TABLE_LIMIT })
      .then((r) => { if (!off) setList(r) })
      .catch((e) => { if (!off) setListError(errorText(e, 'Tablo listesi alınamadı.')) })
      .finally(() => { if (!off) setListLoading(false) })
    return () => { off = true }
  }, [connected, schema, dTableQ])

  // Seçili tablonun kolonları.
  const tSchema = table?.schema
  const tName = table?.table
  useEffect(() => {
    setColumns(null)
    setColumnsError(null)
    if (!tSchema || !tName) return
    let off = false
    listColumns('source', tSchema, tName)
      .then((r) => { if (!off) setColumns(r.columns) })
      .catch((e) => { if (!off) setColumnsError(errorText(e, 'Kolonlar alınamadı.')) })
    return () => { off = true }
  }, [tSchema, tName])

  const abortCount = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
    setRunning(false)
  }, [])
  useEffect(() => abortCount, [abortCount])

  const selectTable = (name: string) => {
    if (!schema || (table?.schema === schema && table.table === name)) return
    abortCount()
    setCountError(null)
    setOverNote(false)
    setFilterErrors(false)
    setTable({ schema, table: name })
  }

  const onFilters = useCallback((next: Filter[], hasErrors: boolean) => {
    abortCount() // filtre değişince süren sayım ve eski sonuç geçersiz
    setCountError(null)
    setFilterErrors(hasErrors)
    setFilters(next)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [abortCount])

  const onFullTable = (checked: boolean) => {
    abortCount()
    setCountError(null)
    setOverNote(false)
    setFilterErrors(false)
    setFullTable(checked) // filtreleri ve sayımı sıfırlar
  }

  const runCount = async () => {
    if (!table) return
    const key = `${table.schema}.${table.table}`
    const ctl = new AbortController()
    abortRef.current = ctl
    setRunning(true)
    setCountError(null)
    setOverNote(false)
    setCount(null)
    try {
      const res = fullTable
        ? await countRows({ source: table, filters: [], full_table: true }, ctl.signal)
        : await countRows({ source: table, filters }, ctl.signal)
      if (ctl.signal.aborted) return
      if (fullTable && res.over_limit) {
        // Tablo 5 milyonun üstünde: kutu kalkar, bu tablo için devre dışı kalır, filtre gerekir.
        setFullTableBlocked(key)
        setFullTable(false)
        setOverNote(true)
      } else setCount(res)
    } catch (e) {
      if (!ctl.signal.aborted) setCountError(errorText(e, 'Satır sayısı hesaplanamadı. Biraz sonra yeniden deneyin.'))
    } finally {
      if (abortRef.current === ctl) {
        abortRef.current = null
        setRunning(false)
      }
    }
  }

  const schemaOptions = useMemo(() => {
    const names = schema && !schemas.includes(schema) ? [schema, ...schemas] : schemas
    return names.map((s) => ({ value: s, label: s }))
  }, [schemas, schema])

  // Seçili tablo yalnız kendi şeması açıkken gösterilir (başka şemanın tablosu seçenek olmaz).
  const selectedHere = table && table.schema === schema ? table.table : null
  const tableOptions = useMemo(() => {
    const names = (list?.items ?? []).map((t) => t.name)
    if (selectedHere && !names.includes(selectedHere)) names.unshift(selectedHere)
    return names.map((n) => ({ value: n, label: n }))
  }, [list, selectedHere])

  const noScope = scopeError(filters, fullTable)
  const blocked = !!table && fullTableBlocked === `${table.schema}.${table.table}`
  const blockReason = noScope ? (blocked ? noScope : `${noScope} Tablo 5 milyonun altındaysa yukarıdaki kutuyu işaretleyebilirsiniz.`) : (filterErrors ? 'Eksik ya da hatalı filtre satırı var; tamamlayın ya da silin.' : null)
  const countOk = !!count && !count.timed_out && count.count !== null && !count.over_limit
  const nextDisabled = !table || !countOk || filterErrors || !!noScope || running
  const moreTables = !!list && list.total > list.items.length
  const limit = count?.limit ?? ROW_LIMIT

  return (
    <WizardLayout
      step="table"
      title="Tablo seçimi"
      description="Taşınacak kaynak tabloyu seçin; gerekirse filtre ekleyip satır sayısını hesaplayın."
      onBack={() => navigate('/connect')}
      onNext={() => navigate('/columns')}
      nextDisabled={nextDisabled}
    >
      <div className="tables-stack">
        <Banner tone="info">Taşınacak kümeyi filtreyle belirleyin. Tablo 5.000.000 satırın altındaysa tamamını taşıyabilirsiniz; üstündeyse filtre zorunludur.</Banner>
        {!connected ? (
          <Banner tone="warn" title="Kaynak bağlantısı yok" action={<Button size="sm" onClick={() => navigate('/connect')}>Bağlantılara git</Button>}>
            Tabloları listelemek için önce kaynak veritabanına bağlanın.
          </Banner>
        ) : (
          <div className="panel">
            <h2>Kaynak tablolar</h2>
            {listError && <Banner tone="error">{listError}</Banner>}
            <div className="tables-bar">
              <SearchableSelect
                label="Şema"
                options={schemaOptions}
                value={schema}
                clientFilter={false}
                onQueryChange={setSchemaQ}
                onChange={setSchema}
                placeholder="Ara ya da seçin"
                hint="Erişim yetkiniz olan tabloların şemaları"
              />
              <SearchableSelect
                label="Tablo"
                options={tableOptions}
                value={selectedHere}
                clientFilter={false}
                onQueryChange={setTableQ}
                onChange={(n) => { setTableQ(''); selectTable(n) }}
                placeholder="Ara ya da seçin"
                disabled={!schema}
                hint={listLoading ? 'Tablolar yükleniyor…' : moreTables ? `İlk ${TABLE_LIMIT} tablo gösteriliyor; daraltmak için adını yazın.` : undefined}
                emptyText="Bu şemada erişebildiğiniz tablo yok."
              />
            </div>
          </div>
        )}

        {table && (
          <div className="panel">
            <h2>Filtre ve satır sayısı: {table.schema}.{table.table}</h2>
            {columnsError && <Banner tone="error">{columnsError}</Banner>}
            {!columns && !columnsError && <Skeleton width="40%" />}
            <div className="full-table">
              <label className="confirm-check" htmlFor="full-table">
                <input
                  id="full-table"
                  type="checkbox"
                  className="check"
                  checked={fullTable}
                  disabled={blocked || running}
                  aria-describedby="full-table-hint"
                  onChange={(e) => onFullTable(e.target.checked)}
                />
                <span>Tablo 5.000.000 satırın altında; tamamını taşıyacağım</span>
              </label>
              <p className="field-hint" id="full-table-hint">
                {blocked ? 'Tablo 5 milyonun üzerinde; filtre gerekli.' : 'İşaretlerseniz filtre gerekmez; önce tablonun satır sayısı doğrulanır.'}
              </p>
            </div>
            {overNote && <Banner tone="error">Bu tablo 5.000.000 satırın üzerinde; tamamı taşınamaz. Filtre girerek kümeyi daraltın.</Banner>}
            {columns && !fullTable && (
              <FilterBuilder key={`${table.schema}.${table.table}`} columns={columns} filters={filters} onChange={onFilters} />
            )}
            <div className="count-box">
              <div className="count-actions">
                <Button variant="primary" onClick={runCount} disabled={!columns || !!blockReason || running} aria-describedby={blockReason ? 'count-block' : undefined}>{fullTable ? 'Tablonun satır sayısını doğrula' : 'Satır sayısını hesapla'}</Button>
                {running && (
                  <>
                    <span className="count-running"><Spinner label="Satırlar sayılıyor" />Satırlar sayılıyor</span>
                    <Button onClick={abortCount}>İptal</Button>
                  </>
                )}
              </div>
              {blockReason && columns && <p className="field-error" id="count-block">{blockReason}</p>}
              {countError && <Banner tone="error">{countError}</Banner>}
              {count?.timed_out && (
                <Banner tone="warn">{fullTable ? 'Sayım zaman aşımına uğradı; tablo çok büyük olabilir. Filtre girerek deneyin.' : 'Sayım zaman aşımına uğradı. Filtreyi daraltıp yeniden hesaplayın.'}</Banner>
              )}
              <div aria-live="polite">
                {count && !count.timed_out && count.count !== null && (
                  <>
                    <ProgressBar value={(count.count / limit) * 100} label="Seçilen satır sayısı" tone={count.over_limit ? 'danger' : 'brand'} />
                    <p className="count-figure">{fullTable ? `Tablonun tamamı: ${fmt(count.count)} satır` : `${fmt(count.count)} / ${fmt(limit)} satır`}</p>
                  </>
                )}
              </div>
              {count?.over_limit && (
                <Banner tone="error">
                  {count.count === null ? `Seçiminiz ${fmt(limit)} satırı aşıyor; lütfen filtreyi daraltın.` : `Seçiminiz ${fmt(count.count)} satır, sınır ${fmt(limit)}. Lütfen 5 milyonu aşmayacak şekilde filtreyi daraltın.`}
                </Banner>
              )}
              {!count && !running && !countError && <p className="field-hint">İlerlemek için satır sayısını hesaplamanız gerekir.</p>}
            </div>
          </div>
        )}
      </div>
    </WizardLayout>
  )
}
