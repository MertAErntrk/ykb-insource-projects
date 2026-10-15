import { useEffect, useId, useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { getJob, listJobs } from '../api/endpoints.ts'
import type { Job, JobColumn, JobStatus } from '../api/types.ts'
import { JobStatusBadge, STATUS_TEXT, fmtDateTime, fmtNum, jobDuration, modeText } from '../components/jobs/JobStatusBadge.tsx'
import { Badge, Banner, Button, DataTable, Dialog } from '../components/ui/index.ts'
import type { Column } from '../components/ui/index.ts'
import { RULE_LABELS, isReversible, standardRuleForType } from '../components/wizard/ruleSuggest.ts'
import { useAuth } from '../state/auth.tsx'
import '../components/wizard/wizard.css'
import '../components/jobs/jobs.css'

const STATUSES = Object.keys(STATUS_TEXT) as JobStatus[]

/** İşin anonimleştirilen kolonları pencerede: kolon, kural, standart olup olmadığı, geri çevrilebilirlik. keep/exclude gösterilmez. */
function RulesCell({ job }: { job: Job }) {
  const [open, setOpen] = useState(false)
  const [cols, setCols] = useState<JobColumn[] | null>(null)
  const [failed, setFailed] = useState(false)

  const show = () => {
    setOpen(true)
    if (!cols) {
      setFailed(false)
      getJob(job.id)
        .then((j) => setCols((j.columns ?? []).filter((c) => c.rule_type !== 'keep' && c.rule_type !== 'exclude')))
        .catch(() => setFailed(true))
    }
  }

  // Pencere portalda olsa da React olayları bu kapsayıcıya kabarır; satır tıklamasına ulaşmasın.
  return (
    <div onClick={(e) => e.stopPropagation()}>
      <Button size="sm" variant="ghost" aria-haspopup="dialog" aria-expanded={open} aria-label={`Kurallar: ${job.source_table}`} onClick={show}>
        Kurallar
      </Button>
      <Dialog
        open={open}
        title={`${job.source_schema}.${job.source_table} kuralları`}
        onClose={() => setOpen(false)}
        actions={<Button onClick={() => setOpen(false)}>Kapat</Button>}
      >
        {failed && <p className="field-error">Kurallar alınamadı.</p>}
        {!failed && !cols && <p>Yükleniyor…</p>}
        {cols && cols.length === 0 && <p>Anonimleştirilen kolon yok.</p>}
        {cols && cols.length > 0 && (
          <table className="std-table">
            <caption className="sr-only">{job.source_table} kuralları</caption>
            <thead><tr><th scope="col">Kolon</th><th scope="col">Kural</th><th scope="col">Tür</th><th scope="col">Geri çevrilebilir</th></tr></thead>
            <tbody>
              {cols.map((c) => (
                <tr key={c.column_name}>
                  <td>{c.column_name}</td>
                  <td>{RULE_LABELS[c.rule_type]}</td>
                  <td>{standardRuleForType(c.source_type) === c.rule_type ? 'Standart' : 'Standart dışı'}</td>
                  <td>{isReversible(c.rule_type) ? 'Evet' : 'Hayır'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Dialog>
    </div>
  )
}

export function History() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const isAdmin = user?.is_admin === true
  const [all, setAll] = useState(false)
  const [filter, setFilter] = useState<JobStatus | ''>('')
  const [jobs, setJobs] = useState<Job[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const filterId = useId()

  useEffect(() => {
    let alive = true
    setLoading(true)
    setError(false)
    listJobs(!(isAdmin && all))
      .then((r) => { if (alive) setJobs(r.items) })
      .catch(() => { if (alive) setError(true) })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [isAdmin, all])

  const rows = filter ? jobs.filter((j) => j.status === filter) : jobs
  const columns: Column<Job>[] = [
    { key: 'status', header: 'Durum', render: (j) => <JobStatusBadge status={j.status} /> },
    {
      key: 'source', header: 'Kaynak',
      render: (j) => (
        <>
          <Link className="row-link" to={`/jobs/${j.id}`} onClick={(e) => e.stopPropagation()}>
            {j.source_alias} · {j.source_schema}.{j.source_table}
          </Link>
          {j.filters.length === 0 && <> <Badge tone="info">Tam tablo</Badge></>}
        </>
      ),
    },
    { key: 'target', header: 'Hedef', render: (j) => `${j.target_alias} · ${j.target_schema}.${j.target_table}` },
    { key: 'mode', header: 'Mod', render: (j) => modeText(j.mode) },
    { key: 'rows', header: 'Satır', align: 'right', render: (j) => fmtNum(j.rows_written) },
    { key: 'start', header: 'Başlangıç', render: (j) => fmtDateTime(j.started_at ?? j.created_at) },
    { key: 'dur', header: 'Süre', render: (j) => jobDuration(j) },
    { key: 'rules', header: 'Kurallar', render: (j) => <RulesCell job={j} /> },
  ]
  if (isAdmin && all) columns.splice(1, 0, { key: 'owner', header: 'Sahip', render: (j) => j.owner })

  return (
    <div className="page" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
      <h1 className="page-title" style={{ margin: 0 }}>Geçmiş</h1>
      <div className="list-toolbar">
        <label htmlFor={filterId}>
          Durum
          <select id={filterId} value={filter} onChange={(e) => setFilter(e.target.value as JobStatus | '')}>
            <option value="">Tümü</option>
            {STATUSES.map((s) => <option key={s} value={s}>{STATUS_TEXT[s]}</option>)}
          </select>
        </label>
        {isAdmin && (
          <label>
            <input type="checkbox" className="check" checked={all} onChange={(e) => setAll(e.target.checked)} />
            Tüm işler
          </label>
        )}
      </div>
      {error && <Banner tone="error">Taşıma geçmişi alınamadı. Sayfayı yenileyin.</Banner>}
      <DataTable
        caption="Taşıma geçmişi"
        columns={columns}
        rows={rows}
        rowKey={(j) => j.id}
        loading={loading}
        maxHeight="none"
        emptyText="Henüz taşıma yok. Yeni taşıma başlattığınızda burada görünür."
        onRowClick={(j) => navigate(`/jobs/${j.id}`)}
      />
    </div>
  )
}
