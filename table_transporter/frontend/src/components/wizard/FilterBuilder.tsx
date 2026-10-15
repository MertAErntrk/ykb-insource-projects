import { useEffect, useId, useMemo, useRef, useState } from 'react'
import type { ColumnInfo, Filter, FilterOp, FilterScalar } from '../../api/types.ts'
import { Button, Field, SearchableSelect, TextField, describedBy } from '../ui/index.ts'
import { scopeError, tautologyReason } from './scopeRules.ts'
import './wizard.css'

type Kind = 'text' | 'number' | 'date'
type Shape = 'none' | 'one' | 'list' | 'two'

const OP_LABEL: Record<FilterOp, string> = {
  eq: 'eşittir', ne: 'eşit değildir', lt: 'küçüktür', le: 'küçük veya eşittir', gt: 'büyüktür', ge: 'büyük veya eşittir',
  like: 'benzer (LIKE)', in: 'listede', between: 'arasında', is_null: 'boş', is_not_null: 'boş değil',
}
const TEXT_OPS: FilterOp[] = ['eq', 'ne', 'like', 'in', 'is_null', 'is_not_null']
const ORDERED_OPS: FilterOp[] = ['eq', 'ne', 'lt', 'le', 'gt', 'ge', 'between', 'in', 'is_null', 'is_not_null']
export const MAX_IN_VALUES = 1000

const shapeOf = (op: FilterOp): Shape => (op === 'is_null' || op === 'is_not_null' ? 'none' : op === 'in' ? 'list' : op === 'between' ? 'two' : 'one')

export function kindOf(c: ColumnInfo): Kind {
  const t = c.data_type.toUpperCase()
  if (/^(NUMBER|FLOAT|INTEGER|DECIMAL|NUMERIC|BINARY_FLOAT|BINARY_DOUBLE)$/.test(t)) return 'number'
  if (t === 'DATE' || t.startsWith('TIMESTAMP')) return 'date'
  return 'text'
}

const DATE_RE = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d{1,9})?)?)?$/
function validDate(t: string): boolean {
  const m = DATE_RE.exec(t)
  if (!m) return false
  const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])]
  const dt = new Date(Date.UTC(y, mo - 1, d))
  if (dt.getUTCFullYear() !== y || dt.getUTCMonth() !== mo - 1 || dt.getUTCDate() !== d) return false
  return (m[4] === undefined || Number(m[4]) < 24) && (m[5] === undefined || Number(m[5]) < 60) && (m[6] === undefined || Number(m[6]) < 60)
}

type Parsed = { value: FilterScalar } | { error: string }

/** Tek değeri tipine göre çözer. Sayıda tek değerde virgül ondalık ayracı sayılır; listede yalnız nokta. */
function parseScalar(kind: Kind, raw: string, inList: boolean): Parsed {
  const t = raw.trim()
  if (!t) return { error: 'Bir değer girin.' }
  if (kind === 'number') {
    const n = inList ? t : t.replace(',', '.')
    if (!/^-?\d+(\.\d+)?$/.test(n)) return { error: 'Geçerli bir sayı girin (örn. 1250 veya 12.5).' }
    return { value: Number(n) }
  }
  if (kind === 'date') {
    if (!validDate(t)) return { error: 'Tarihi 2024-01-31 veya 2024-01-31T14:30:00 biçiminde girin (saat dilimi olmadan).' }
    return { value: t.replace(' ', 'T') }
  }
  return { value: raw }
}

interface Row {
  id: number
  column: string | null
  op: FilterOp
  a: string
  b: string
  touched: boolean
}
interface RowResult {
  filter?: Filter
  errA?: string
  errB?: string
  /** Tüm satırları seçen koşul (scopeRules); dokunulmasa da gösterilir. */
  errT?: string
}

function evaluate(row: Row, col: ColumnInfo | undefined): RowResult {
  const r = evaluateShape(row, col)
  if (!r.filter) return r
  const errT = tautologyReason(r.filter, col)
  return errT ? { errT } : r
}

function evaluateShape(row: Row, col: ColumnInfo | undefined): RowResult {
  if (!row.column || !col) return { errA: 'Kolon bulunamadı; satırı silin.' }
  const kind = kindOf(col)
  const shape = shapeOf(row.op)
  const column = row.column
  if (shape === 'none') return { filter: { column, op: row.op } }
  if (shape === 'one') {
    const p = parseScalar(kind, row.a, false)
    return 'error' in p ? { errA: p.error } : { filter: { column, op: row.op, value: p.value } }
  }
  if (shape === 'two') {
    const pa = parseScalar(kind, row.a, false)
    const pb = parseScalar(kind, row.b, false)
    if ('error' in pa || 'error' in pb) return { errA: 'error' in pa ? pa.error : undefined, errB: 'error' in pb ? pb.error : undefined }
    if (kind === 'number' && Number(pa.value) > Number(pb.value)) return { errB: 'Üst sınır alt sınırdan küçük olamaz.' }
    if (kind !== 'number' && String(pa.value) > String(pb.value)) return { errB: 'Üst sınır alt sınırdan önce olamaz.' }
    return { filter: { column, op: 'between', values: [pa.value, pb.value] } }
  }
  const parts = row.a.split(',').map((s) => s.trim()).filter(Boolean)
  if (parts.length === 0) return { errA: 'En az bir değer girin (virgülle ayırın).' }
  if (parts.length > MAX_IN_VALUES) return { errA: `En çok 1.000 değer girilebilir (şu an ${parts.length.toLocaleString('tr-TR')}).` }
  const values: FilterScalar[] = []
  for (const p of parts) {
    const r = parseScalar(kind, p, true)
    if ('error' in r) return { errA: `"${p}": ${r.error}` }
    values.push(r.value)
  }
  return { filter: { column, op: 'in', values } }
}

function rowFromFilter(f: Filter, id: number): Row {
  const shape = shapeOf(f.op)
  const a = shape === 'list' ? (f.values ?? []).join(',') : shape === 'two' ? String(f.values?.[0] ?? '') : String(f.value ?? '')
  return { id, column: f.column, op: f.op, a, b: shape === 'two' ? String(f.values?.[1] ?? '') : '', touched: false }
}

export interface FilterBuilderProps {
  columns: ColumnInfo[]
  /** Başlangıç filtreleri (tablo değişince bileşen key ile yeniden kurulmalı). */
  filters: Filter[]
  /** Yalnız geçerli satırlar iletilir; hasErrors: eksik/hatalı satır var (sayım yapılmamalı). */
  onChange: (filters: Filter[], hasErrors: boolean) => void
}

export function FilterBuilder({ columns, filters, onChange }: FilterBuilderProps) {
  const supported = useMemo(() => columns.filter((c) => c.supported), [columns])
  const byName = useMemo(() => new Map(columns.map((c) => [c.name, c])), [columns])
  const nextId = useRef(Math.max(filters.length, 1))
  // Filtre zorunlu: başlangıçta en az bir (boş) satır açılır.
  const [rows, setRows] = useState<Row[]>(() => {
    if (filters.length > 0) return filters.map(rowFromFilter)
    const first = columns.find((c) => c.supported)
    return first ? [{ id: 0, column: first.name, op: 'eq', a: '', b: '', touched: false }] : []
  })
  const lastKey = useRef(JSON.stringify([filters, false]))

  const results = useMemo(() => rows.map((r) => evaluate(r, byName.get(r.column ?? ''))), [rows, byName])
  const hasErrors = results.some((r) => !r.filter)
  const noFilter = scopeError(results.flatMap((r) => (r.filter ? [r.filter] : [])))

  useEffect(() => {
    const valid = results.flatMap((r) => (r.filter ? [r.filter] : []))
    const key = JSON.stringify([valid, hasErrors])
    if (key === lastKey.current) return
    lastKey.current = key
    onChange(valid, hasErrors)
  }, [results, hasErrors, onChange])

  const patch = (id: number, p: Partial<Row>) => setRows((rs) => rs.map((r) => (r.id === id ? { ...r, ...p } : r)))
  const add = () => {
    const first = supported[0]
    if (!first) return
    setRows((rs) => [...rs, { id: nextId.current++, column: first.name, op: 'eq', a: '', b: '', touched: false }])
  }

  return (
    <div className="conn-stack">
      <div className="filter-hints">
        <p>Taşınacak kümeyi daraltan en az bir filtre gerekir; tüm satırları seçen koşullar kabul edilmez.</p>
        <p>Satırlar VE ile birleşir: yalnızca tüm koşulları sağlayan satırlar taşınır.</p>
      </div>
      {rows.length > 0 && (
        <ol className="filter-list" aria-label="Filtreler">
          {rows.map((row, i) => (
            <FilterRowView
              key={row.id}
              index={i}
              row={row}
              result={results[i]}
              supported={supported}
              column={byName.get(row.column ?? '')}
              onPatch={(p) => patch(row.id, { ...p, touched: true })}
              onRemove={() => setRows((rs) => rs.filter((r) => r.id !== row.id))}
            />
          ))}
        </ol>
      )}
      {noFilter && !hasErrors && <p className="field-error" role="alert">{noFilter}</p>}
      {hasErrors && <p className="field-hint">Eksik ya da hatalı filtre satırı var; tamamlayın ya da silin.</p>}
      <div>
        <Button onClick={add} disabled={supported.length === 0}>Filtre ekle</Button>
      </div>
    </div>
  )
}

interface RowViewProps {
  index: number
  row: Row
  result: RowResult
  supported: ColumnInfo[]
  column: ColumnInfo | undefined
  onPatch: (p: Partial<Row>) => void
  onRemove: () => void
}

function FilterRowView({ index, row, result, supported, column, onPatch, onRemove }: RowViewProps) {
  const opId = useId()
  const kind: Kind = column ? kindOf(column) : 'text'
  const ops = kind === 'text' ? TEXT_OPS : ORDERED_OPS
  const shape = shapeOf(row.op)
  const errA = row.touched ? result.errA : undefined
  const errB = row.touched ? result.errB : undefined
  const errT = result.errT
  const options = useMemo(() => supported.map((c) => ({ value: c.name, label: c.name, description: c.data_type })), [supported])

  const onColumn = (name: string) => {
    const next = supported.find((c) => c.name === name)
    if (!next) return
    const nk = kindOf(next)
    const opOk = (nk === 'text' ? TEXT_OPS : ORDERED_OPS).includes(row.op)
    // Tür değişince eski değerler anlamsızlaşır: temizle, işleç uygunsa koru.
    onPatch(nk === kind ? { column: name } : { column: name, op: opOk ? row.op : 'eq', a: '', b: '' })
  }
  const onOp = (op: FilterOp) => onPatch(shapeOf(op) === shape ? { op } : { op, a: '', b: '' })

  const valueHint = shape === 'list' ? 'Virgülle ayırın (en çok 1.000)' : kind === 'date' ? 'ISO-8601, saat dilimi olmadan' : row.op === 'like' ? '% ve _ joker karakterdir' : undefined
  const placeholder = shape === 'list' ? 'değer1, değer2' : kind === 'date' ? '2024-01-31' : undefined
  const mode = kind === 'number' ? ('decimal' as const) : undefined

  return (
    <li className="filter-row" role="group" aria-label={`Filtre ${index + 1}`}>
      <SearchableSelect label="Kolon" options={options} value={row.column} onChange={onColumn} />
      <Field id={opId} label="Koşul" error={errT}>
        <select id={opId} className="input" aria-invalid={errT ? true : undefined} aria-describedby={describedBy(opId, undefined, errT)} value={row.op} onChange={(e) => onOp(e.target.value as FilterOp)}>
          {ops.map((o) => <option key={o} value={o}>{OP_LABEL[o]}</option>)}
        </select>
      </Field>
      <div className="filter-values">
        {shape === 'one' && (
          <TextField label="Değer" hint={valueHint} error={errA} inputMode={mode} placeholder={placeholder} value={row.a} onChange={(e) => onPatch({ a: e.target.value })} onBlur={() => onPatch({})} />
        )}
        {shape === 'list' && (
          <TextField label="Değerler" hint={valueHint} error={errA} placeholder={placeholder} value={row.a} onChange={(e) => onPatch({ a: e.target.value })} onBlur={() => onPatch({})} />
        )}
        {shape === 'two' && (
          <>
            <TextField label="Alt sınır" hint={valueHint} error={errA} inputMode={mode} placeholder={placeholder} value={row.a} onChange={(e) => onPatch({ a: e.target.value })} onBlur={() => onPatch({})} />
            <TextField label="Üst sınır" error={errB} inputMode={mode} placeholder={placeholder} value={row.b} onChange={(e) => onPatch({ b: e.target.value })} onBlur={() => onPatch({})} />
          </>
        )}
      </div>
      <Button variant="ghost" className="filter-remove" aria-label={`Filtre ${index + 1} satırını sil`} onClick={onRemove}>Sil</Button>
    </li>
  )
}

