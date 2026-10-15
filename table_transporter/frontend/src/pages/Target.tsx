import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router'
import { checkCompat, listSchemas, listTables } from '../api/endpoints.ts'
import { ApiError } from '../api/client.ts'
import type { CompatResponse, TransferMode } from '../api/types.ts'
import { WizardLayout } from '../components/layout/WizardLayout.tsx'
import { Banner, Skeleton } from '../components/ui/index.ts'
import type { SelectOption } from '../components/ui/index.ts'
import { CompatReport, compatBlocked } from '../components/wizard/CompatReport.tsx'
import { TargetForm, defaultTableName, identError } from '../components/wizard/TargetForm.tsx'
import { useWizard } from '../state/wizard.tsx'
import '../components/wizard/wizard.css'
import '../components/wizard/summary.css'

const errText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.detail || fallback : fallback)

export function Target() {
  const { source, target, table, rules, targetSetting, setTargetSetting } = useWizard()
  const navigate = useNavigate()

  const userSchema = (target?.user ?? '').toUpperCase()
  const maxLen = target?.info.max_ident_len ?? 128
  const sourceTable = table?.table ?? ''

  const [mode, setMode] = useState<TransferMode>(targetSetting?.mode ?? 'create')
  const [name, setName] = useState(() => (targetSetting?.mode === 'create' ? targetSetting.table : defaultTableName(sourceTable, maxLen)))
  const [taken, setTaken] = useState<boolean | null>(null)
  const [nameFailure, setNameFailure] = useState<string | null>(null)

  const [schemas, setSchemas] = useState<string[] | null>(null)
  const [schemaError, setSchemaError] = useState<string | null>(null)
  const [schema, setSchema] = useState<string | null>(targetSetting?.mode === 'append' ? targetSetting.schema : null)
  const [tables, setTables] = useState<string[]>([])
  const [tableError, setTableError] = useState<string | null>(null)
  const [tableQuery, setTableQuery] = useState('')
  const [tableName, setTableName] = useState<string | null>(targetSetting?.mode === 'append' ? targetSetting.table : null)
  const [compat, setCompat] = useState<CompatResponse | null>(null)
  const [compatBusy, setCompatBusy] = useState(false)
  const [compatError, setCompatError] = useState<string | null>(null)

  const formatError = identError(name, maxLen)

  // Yeni tablo: ad biçimi geçerliyse hedefte aynı adın olup olmadığına bakılır.
  useEffect(() => {
    setTaken(null)
    setNameFailure(null)
    if (mode !== 'create' || !target || formatError) return
    let cancelled = false
    const timer = setTimeout(() => {
      listTables('target', { schema: userSchema, q: name, limit: 50 })
        .then((r) => { if (!cancelled) setTaken(r.items.some((t) => t.name === name)) })
        .catch((e) => { if (!cancelled) setNameFailure(errText(e, 'Ad denetlenemedi. Bağlantıyı kontrol edip yeniden deneyin.')) })
    }, 300)
    return () => { cancelled = true; clearTimeout(timer) }
    // formatError name ve maxLen'den türer.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, name, userSchema])

  // Mevcut tablo: şema listesi.
  useEffect(() => {
    if (mode !== 'append' || schemas || !target) return
    let cancelled = false
    listSchemas('target')
      .then((r) => { if (!cancelled) setSchemas(r.schemas) })
      .catch((e) => { if (!cancelled) setSchemaError(errText(e, 'Şemalar okunamadı. Bağlantıyı kontrol edip yeniden deneyin.')) })
    return () => { cancelled = true }
  }, [mode, schemas, target])

  // Mevcut tablo: seçilen şemadaki tablolar (sunucu süzer).
  useEffect(() => {
    if (mode !== 'append' || !schema) return
    let cancelled = false
    setTableError(null)
    const timer = setTimeout(() => {
      listTables('target', { schema, q: tableQuery || undefined, limit: 100 })
        .then((r) => { if (!cancelled) setTables(r.items.map((t) => t.name)) })
        .catch((e) => { if (!cancelled) setTableError(errText(e, 'Tablolar okunamadı. Bağlantıyı kontrol edip yeniden deneyin.')) })
    }, tableQuery ? 250 : 0)
    return () => { cancelled = true; clearTimeout(timer) }
  }, [mode, schema, tableQuery])

  // Mevcut tablo: uyumluluk denetimi.
  useEffect(() => {
    setCompat(null)
    setCompatError(null)
    if (mode !== 'append' || !schema || !tableName || !table) return
    let cancelled = false
    setCompatBusy(true)
    checkCompat({ source: table, target: { schema, table: tableName }, rules })
      .then((r) => { if (!cancelled) setCompat(r) })
      .catch((e) => { if (!cancelled) setCompatError(errText(e, 'Uyumluluk denetlenemedi. Yeniden deneyin.')) })
      .finally(() => { if (!cancelled) setCompatBusy(false) })
    return () => { cancelled = true }
    // rules adım boyunca değişmez; yalnız hedef seçimi denetimi yeniden tetikler.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, schema, tableName])

  const tableOptions = useMemo<SelectOption[]>(() => {
    const names = tableName && !tables.includes(tableName) ? [tableName, ...tables] : tables
    return names.map((n) => ({ value: n, label: n }))
  }, [tables, tableName])
  const schemaOptions = useMemo<SelectOption[]>(() => (schemas ?? []).map((s) => ({ value: s, label: s })), [schemas])

  if (!table || !source || !target) {
    return (
      <WizardLayout step="target" title="Hedef" onBack={() => navigate('/columns')}>
        <Banner tone="warn">Önce kaynak, hedef bağlantısı ve tablo seçmelisiniz. Geri dönüp önceki adımları tamamlayın.</Banner>
      </WizardLayout>
    )
  }

  const nameError = formatError ?? (taken ? 'Bu adla bir tablo var; başka bir ad seçin.' : nameFailure)
  const blocked = compat ? compatBlocked(compat) : false
  const ready = mode === 'create'
    ? !formatError && taken === false
    : !!schema && !!tableName && compat !== null && !compatBusy && !blocked
  const otherSchema = mode === 'append' && !!schema && schema !== userSchema

  const next = () => {
    if (!ready) return
    setTargetSetting(mode === 'create' ? { mode, schema: userSchema, table: name } : { mode, schema: schema!, table: tableName! })
    navigate('/summary')
  }

  return (
    <WizardLayout
      step="target"
      title="Hedef"
      description={`${table.schema}.${table.table} verisinin yazılacağı yeri seçin.`}
      onBack={() => navigate('/columns')}
      onNext={next}
      nextDisabled={!ready}
    >
      <TargetForm
        mode={mode}
        onModeChange={setMode}
        userSchema={userSchema}
        maxLen={maxLen}
        name={name}
        onNameChange={setName}
        nameError={nameError}
        nameChecking={mode === 'create' && !formatError && taken === null && !nameFailure}
        schemas={schemaOptions}
        schema={schema}
        onSchemaChange={(s) => { setSchema(s); setTableName(null); setTables([]); setTableQuery('') }}
        schemaError={schemaError}
        tables={tableOptions}
        tableName={tableName}
        onTableChange={setTableName}
        onTableQuery={setTableQuery}
        tableError={tableError}
      />
      {mode === 'append' && (
        <>
          {otherSchema && <Banner tone="warn">Başka bir şemaya yazıyorsunuz ({schema}). Hedef tablonun doğru olduğundan emin olun.</Banner>}
          {compatBusy && <div role="status" aria-label="Uyumluluk denetleniyor"><Skeleton width="60%" /><Skeleton /></div>}
          {compatError && <Banner tone="error">{compatError}</Banner>}
          {compat && (
            <div className="panel">
              <h2>Uyumluluk</h2>
              {compat.has_enabled_trigger && <Banner tone="error">Hedef tabloda etkin trigger var; güvenlik nedeniyle bu tabloya eklenemez.</Banner>}
              {!compat.target_is_table && <Banner tone="error">Hedef bir tablo değil (view/synonym). Başka bir hedef seçin.</Banner>}
              <CompatReport result={compat} />
            </div>
          )}
        </>
      )}
    </WizardLayout>
  )
}
