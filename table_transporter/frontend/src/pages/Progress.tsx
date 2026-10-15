import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { cancelJob, getJob } from '../api/endpoints.ts'
import { ApiError } from '../api/client.ts'
import { subscribeJob } from '../api/sse.ts'
import type { JobEvent } from '../api/sse.ts'
import type { Job } from '../api/types.ts'
import { JobStatusBadge, STATUS_TEXT, errorText, fmtEta, fmtNum, jobDuration } from '../components/jobs/JobStatusBadge.tsx'
import { Banner, Button, Dialog, ProgressBar, Spinner } from '../components/ui/index.ts'
import { useWizard } from '../state/wizard.tsx'
import '../components/wizard/wizard.css'
import '../components/jobs/jobs.css'

function applyEvent(job: Job, e: JobEvent): Job {
  switch (e.name) {
    case 'progress':
      return { ...job, status: 'running', rows_read: e.data.rows_read, rows_written: e.data.rows_written, total_rows: e.data.total_rows, rows_per_sec: e.data.rows_per_sec, eta_sec: e.data.eta_sec }
    case 'cancelling':
      return { ...job, status: 'cancelling' }
    case 'done':
      return { ...job, status: 'done', rows_written: e.data.rows_written, finished_at: e.data.finished_at || job.finished_at, target_schema: e.data.target.schema, target_table: e.data.target.table, eta_sec: null, rows_per_sec: null }
    case 'failed':
      return { ...job, status: e.data.status, rows_written: e.data.rows_written, error_code: e.data.error, error_detail: e.data.detail, eta_sec: null, rows_per_sec: null }
    case 'cancelled':
      return { ...job, status: 'cancelled', rows_written: e.data.rows_written, eta_sec: null, rows_per_sec: null }
  }
}

const ACTIVE = new Set(['queued', 'running', 'cancelling'])

function summaryText(job: Job): string {
  if (job.status === 'running' || job.status === 'queued') {
    const total = job.total_rows != null ? ` / ${fmtNum(job.total_rows)}` : ''
    return `${STATUS_TEXT[job.status]}: ${fmtNum(job.rows_written)}${total} satır yazıldı.`
  }
  return `${STATUS_TEXT[job.status]}.`
}

export function Progress() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const { reset } = useWizard()
  const [job, setJob] = useState<Job | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [cancelBusy, setCancelBusy] = useState(false)
  const [cancelError, setCancelError] = useState<string | null>(null)
  const [announce, setAnnounce] = useState('')

  useEffect(() => {
    let alive = true
    let unsub: (() => void) | null = null
    setJob(null)
    setLoadError(null)
    getJob(id)
      .then((j) => {
        if (!alive) return
        setJob(j)
        if (!ACTIVE.has(j.status)) return
        unsub = subscribeJob(id, (e) => { if (alive) setJob((cur) => (cur ? applyEvent(cur, e) : cur)) })
      })
      .catch((e) => { if (alive) setLoadError(e instanceof ApiError && e.error === 'NOT_FOUND' ? 'Bu iş bulunamadı.' : 'İş bilgisi alınamadı. Sayfayı yenileyin.') })
    return () => { alive = false; unsub?.() }
  }, [id])

  // Ekran okuyucu özeti en çok 5 sn'de bir güncellenir; durum değişimi hemen duyurulur.
  const text = job ? summaryText(job) : ''
  const latest = useRef(text)
  latest.current = text
  const status = job?.status
  useEffect(() => { setAnnounce(latest.current) }, [status])
  useEffect(() => {
    const t = setInterval(() => setAnnounce(latest.current), 5000)
    return () => clearInterval(t)
  }, [])

  const doCancel = async () => {
    setCancelBusy(true)
    setCancelError(null)
    try {
      const j = await cancelJob(id)
      setJob((cur) => (cur ? { ...cur, status: j.status } : cur))
    } catch (e) {
      setCancelError(e instanceof ApiError && e.detail ? e.detail : 'İptal edilemedi. Yeniden deneyin.')
    } finally {
      setCancelBusy(false)
      setConfirmOpen(false)
    }
  }

  const startNew = () => { reset(); navigate('/connect') }

  if (loadError) return (<div className="page"><h1 className="page-title">İlerleme</h1><Banner tone="error">{loadError}</Banner></div>)
  if (!job) return (<div className="page"><h1 className="page-title">İlerleme</h1><Spinner label="İş bilgisi yükleniyor" /></div>)

  const pct = job.total_rows ? Math.min(100, (job.rows_written / job.total_rows) * 100) : null
  const active = ACTIVE.has(job.status)
  const target = `${job.target_schema}.${job.target_table}`

  return (
    <div className="page" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
      <div className="job-head">
        <h1 className="page-title" style={{ margin: 0 }}>İlerleme</h1>
        <JobStatusBadge status={job.status} />
      </div>
      <p className="sr-only" aria-live="polite" role="status">{announce}</p>

      <div className="panel">
        <div className="job-route">
          <span><strong>{job.source_alias}</strong> {job.source_schema}.{job.source_table}</span>
          <span className="arrow" aria-label="hedef">→</span>
          <span><strong>{job.target_alias}</strong> {target}</span>
        </div>

        {active && (
          <>
            <ProgressBar value={pct} label="Taşıma ilerlemesi" />
            <dl className="job-stats">
              <div><dt>Yazılan satır</dt><dd>{fmtNum(job.rows_written)}{job.total_rows != null ? ` / ${fmtNum(job.total_rows)}` : ''}</dd></div>
              <div><dt>Hız</dt><dd>{job.rows_per_sec != null ? `${fmtNum(Math.round(job.rows_per_sec))} satır/sn` : '-'}</dd></div>
              <div><dt>Kalan süre</dt><dd>{job.eta_sec != null ? fmtEta(job.eta_sec) : '-'}</dd></div>
            </dl>
            <p className="job-note">İşiniz arka planda sürer; bu sayfadan ayrılabilir, <Link className="row-link" to="/history">Geçmiş</Link>'ten izleyebilirsiniz.</p>
            <div>
              <Button variant="danger" onClick={() => setConfirmOpen(true)} disabled={job.status === 'cancelling'}>İptal et</Button>
            </div>
            {cancelError && <Banner tone="error">{cancelError}</Banner>}
          </>
        )}
      </div>

      {job.status === 'done' && (
        <section className="panel job-result" aria-labelledby="job-done">
          <h2 id="job-done">Taşıma tamamlandı</h2>
          <dl className="job-stats">
            <div><dt>Hedef tablo</dt><dd>{target}</dd></div>
            <div><dt>Yazılan satır</dt><dd>{fmtNum(job.rows_written)}</dd></div>
            <div><dt>Süre</dt><dd>{jobDuration(job)}</dd></div>
          </dl>
          {job.mode === 'create' && (
            <p className="job-note">Yeni tablo 7 gün sonra temizlik listesine girer. <Link className="row-link" to="/cleanup">Temizlik listesi</Link></p>
          )}
          <div className="job-actions"><Button variant="primary" onClick={startNew}>Yeni taşıma başlat</Button></div>
        </section>
      )}

      {(job.status === 'failed' || job.status === 'partial') && (
        <div className="job-result">
          <Banner tone="error" title={job.status === 'partial' ? 'Taşıma kısmen tamamlandı' : 'Taşıma başarısız oldu'}>
            <p>{errorText(job.error_code)}{job.error_detail ? ` ${job.error_detail}` : ''}</p>
            {job.status === 'partial' && <p>Tablo kısmen oluştu ve temizlik listesine eklendi.</p>}
            <p>Yazılan satır: {fmtNum(job.rows_written)}</p>
          </Banner>
          <div className="job-actions" style={{ marginTop: 'var(--space-3)' }}>
            <Button variant="primary" onClick={startNew}>Yeni taşıma başlat</Button>
            {job.status === 'partial' && <Link className="row-link" to="/cleanup">Temizlik listesi</Link>}
          </div>
        </div>
      )}

      {job.status === 'cancelled' && (
        <div className="job-result">
          <Banner tone="info" title="Taşıma iptal edildi">
            {job.mode === 'append' ? 'Ekleme modunda hiçbir satır eklenmedi.' : 'Kısmi tablo oluştuysa temizlik listesinde görünür.'}
          </Banner>
          <div className="job-actions" style={{ marginTop: 'var(--space-3)' }}>
            <Button variant="primary" onClick={startNew}>Yeni taşıma başlat</Button>
          </div>
        </div>
      )}

      <Dialog
        open={confirmOpen}
        title="Taşıma iptal edilsin mi?"
        onClose={() => setConfirmOpen(false)}
        actions={
          <>
            <Button onClick={() => setConfirmOpen(false)}>Vazgeç</Button>
            <Button variant="danger" onClick={doCancel} loading={cancelBusy}>Taşımayı iptal et</Button>
          </>
        }
      >
        <p>Ekleme modunda hiçbir satır eklenmez; yeni tablo modunda kısmi tablo temizlik listesine girer.</p>
      </Dialog>
    </div>
  )
}
