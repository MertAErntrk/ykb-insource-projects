import type { PreviewColumn, PreviewResponse, PreviewValue } from '../../api/types.ts'
import { Banner, Skeleton } from '../ui/index.ts'
import { RULE_LABELS } from './ruleSuggest.ts'

interface PreviewPanelProps {
  data: PreviewResponse | null
  loading: boolean
  /** RULE_INVALID vb. sunucu hatası ayrıntısı */
  error: string | null
}

const show = (v: PreviewValue | undefined) => (v === null || v === undefined ? 'NULL' : String(v))
const n = (x: number) => x.toLocaleString('tr-TR')

function PreviewTable({ col }: { col: PreviewColumn }) {
  const excluded = col.type === 'exclude'
  const rows = Math.max(col.before.length, excluded ? 0 : col.after.length)
  return (
    <section className="preview-col" aria-label={`Önizleme: ${col.column}`}>
      <h3>
        <code>{col.column}</code> <span className="preview-rule">{RULE_LABELS[col.type]}</span>
      </h3>
      <table className="table preview-table">
        <caption className="sr-only">{col.column} için ilk 5 satır, önce ve sonra</caption>
        <thead>
          <tr><th scope="col">Önce</th><th scope="col">Sonra</th></tr>
        </thead>
        <tbody>
          {Array.from({ length: rows }, (_, i) => (
            <tr key={i}>
              <td className={col.before[i] === null ? 'is-null' : undefined}>{show(col.before[i])}</td>
              <td className={!excluded && col.after[i] === null ? 'is-null' : undefined}>
                {excluded ? (i === 0 ? 'hedefe yazılmaz' : '') : show(col.after[i])}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="preview-nulls">
        NULL sayısı: önce {n(col.null_before)}{excluded ? '' : `, sonra ${n(col.null_after)}`}
      </p>
    </section>
  )
}

/** Önizleme sonucu: kural uygulanmış kolonlar için ilk 5 satır önce/sonra. */
export function PreviewPanel({ data, loading, error }: PreviewPanelProps) {
  if (loading) {
    return (
      <div className="preview" role="status" aria-label="Önizleme hazırlanıyor">
        <Skeleton width="40%" /><Skeleton width="80%" /><Skeleton width="60%" />
      </div>
    )
  }
  if (error) return <Banner tone="error" title="Önizleme oluşturulamadı">{error}</Banner>
  if (!data) return null
  if (data.columns.length === 0) return <Banner>Önizlenecek kural uygulanmış kolon yok.</Banner>
  return (
    <div className="preview">
      <p className="preview-note">Kaynaktan okunan ilk 5 satır; önizleme hiçbir şey yazmaz.</p>
      <div className="preview-grid">
        {data.columns.map((c) => <PreviewTable key={c.column} col={c} />)}
      </div>
    </div>
  )
}
