import type { ReactNode } from 'react'

// --- Spinner / Skeleton ---
export function Spinner({ label }: { label?: string }) {
  return label ? (
    <span role="status"><span className="spinner" aria-hidden="true" /><span className="sr-only">{label}</span></span>
  ) : (
    <span className="spinner" aria-hidden="true" />
  )
}

export function Skeleton({ width = '100%' }: { width?: string }) {
  return <span className="skeleton" style={{ width }} aria-hidden="true" />
}

// --- Badge / ProgressBar / Banner ---
export type Tone = 'neutral' | 'info' | 'success' | 'warn' | 'danger'

export function Badge({ tone = 'neutral', children }: { tone?: Tone; children: ReactNode }) {
  return <span className="badge" data-tone={tone}>{children}</span>
}

/** value: 0-100; null ise belirsiz (indeterminate). */
export function ProgressBar({ value, label, tone = 'brand' }: { value: number | null; label: string; tone?: 'brand' | 'danger' }) {
  const v = value === null ? undefined : Math.max(0, Math.min(100, value))
  return (
    <div className="progress" role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={v} data-indeterminate={v === undefined} data-tone={tone}>
      <div className="progress-bar" style={v === undefined ? undefined : { width: `${v}%` }} />
    </div>
  )
}

export interface BannerProps {
  tone?: 'info' | 'warn' | 'error'
  title?: string
  children?: ReactNode
  action?: ReactNode
}

export function Banner({ tone = 'info', title, children, action }: BannerProps) {
  return (
    <div className="banner" data-tone={tone} role={tone === 'info' ? 'status' : 'alert'}>
      <div className="banner-body">
        {title && <p className="banner-title">{title}</p>}
        {children && <div>{children}</div>}
      </div>
      {action}
    </div>
  )
}

// --- Stepper ---
export interface StepItem {
  id: string
  label: string
}

export function Stepper({ steps, current }: { steps: StepItem[]; current: string }) {
  const idx = steps.findIndex((s) => s.id === current)
  return (
    <ol className="stepper" aria-label="Taşıma adımları">
      {steps.map((s, i) => (
        <li key={s.id} className="step" data-state={i < idx ? 'done' : i === idx ? 'current' : 'todo'} aria-current={i === idx ? 'step' : undefined}>
          <span className="step-index" aria-hidden="true">{i + 1}</span>
          <span className="step-label">{s.label}{i < idx && <span className="sr-only"> (tamamlandı)</span>}</span>
        </li>
      ))}
    </ol>
  )
}

// --- DataTable ---
export interface Column<T> {
  key: string
  header: string
  render: (row: T) => ReactNode
  align?: 'left' | 'right'
}

export interface DataTableProps<T> {
  columns: Column<T>[]
  rows: T[]
  rowKey: (row: T) => string
  caption: string
  emptyText?: string
  loading?: boolean
  maxHeight?: string
  onRowClick?: (row: T) => void
}

export function DataTable<T>({ columns, rows, rowKey, caption, emptyText = 'Gösterilecek kayıt yok.', loading = false, maxHeight = '420px', onRowClick }: DataTableProps<T>) {
  const cell = (c: Column<T>) => (c.align === 'right' ? 'align-right' : undefined)
  return (
    <div className="table-scroll" style={{ maxHeight }}>
      <table className="table" aria-busy={loading || undefined}>
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>{columns.map((c) => <th key={c.key} scope="col" className={cell(c)}>{c.header}</th>)}</tr>
        </thead>
        <tbody>
          {loading
            ? Array.from({ length: 5 }, (_, i) => (
                <tr key={i}>{columns.map((c) => <td key={c.key}><Skeleton width="70%" /></td>)}</tr>
              ))
            : rows.map((row) => (
                <tr key={rowKey(row)} data-clickable={onRowClick ? true : undefined} onClick={onRowClick ? () => onRowClick(row) : undefined}>
                  {columns.map((c) => <td key={c.key} className={cell(c)}>{c.render(row)}</td>)}
                </tr>
              ))}
        </tbody>
      </table>
      {!loading && rows.length === 0 && <p className="table-empty">{emptyText}</p>}
    </div>
  )
}
