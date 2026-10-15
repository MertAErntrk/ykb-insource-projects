import { getJob, } from './endpoints.ts'
import { isMock } from './client.ts'
import type { Job, SseEventMap, SseEventName } from './types.ts'

export type JobEvent = { [K in SseEventName]: { name: K; data: SseEventMap[K] } }[SseEventName]

const NAMES: SseEventName[] = ['progress', 'cancelling', 'done', 'failed', 'cancelled']
const TERMINAL = new Set(['done', 'failed', 'cancelled'])

/** Job kaydını SSE olayına çevirir (polling yedeği için). */
export function jobToEvent(j: Job): JobEvent | null {
  switch (j.status) {
    case 'queued':
    case 'running':
      return { name: 'progress', data: { job_id: j.id, status: 'running', rows_read: j.rows_read, rows_written: j.rows_written, total_rows: j.total_rows, rows_per_sec: j.rows_per_sec, eta_sec: j.eta_sec } }
    case 'cancelling':
      return { name: 'cancelling', data: { job_id: j.id, status: 'cancelling' } }
    case 'done':
      return { name: 'done', data: { job_id: j.id, status: 'done', rows_written: j.rows_written, finished_at: j.finished_at ?? '', target: { schema: j.target_schema, table: j.target_table } } }
    case 'cancelled':
      return { name: 'cancelled', data: { job_id: j.id, status: 'cancelled', rows_written: j.rows_written } }
    case 'failed':
    case 'partial':
      return { name: 'failed', data: { job_id: j.id, status: j.status, error: j.error_code ?? 'INTERNAL', detail: j.error_detail ?? '', rows_written: j.rows_written } }
  }
}

/**
 * İş olaylarına abone olur: EventSource; kopunca (ya da mock modunda) 2 sn'de bir GET /transfers/{id}.
 * Dönen fonksiyon aboneliği iptal eder. Terminal olaydan sonra akış kendiliğinden kapanır.
 */
export function subscribeJob(jobId: string, onEvent: (e: JobEvent) => void): () => void {
  let closed = false
  let es: EventSource | null = null
  let timer: ReturnType<typeof setInterval> | null = null

  const stop = () => {
    closed = true
    es?.close()
    if (timer) clearInterval(timer)
  }
  const poll = () => {
    if (timer) return
    const tick = async () => {
      try {
        const ev = jobToEvent(await getJob(jobId))
        if (closed || !ev) return
        onEvent(ev)
        if (TERMINAL.has(ev.name)) stop()
      } catch {
        // geçici hata: sonraki turda yeniden denenir
      }
    }
    void tick()
    timer = setInterval(() => void tick(), 2000)
  }

  if (isMock() || typeof EventSource === 'undefined') {
    poll()
    return stop
  }
  es = new EventSource(`/api/transfers/${encodeURIComponent(jobId)}/events`, { withCredentials: true })
  for (const name of NAMES) {
    es.addEventListener(name, (m) => {
      if (closed) return
      onEvent({ name, data: JSON.parse((m as MessageEvent<string>).data) } as JobEvent)
      if (TERMINAL.has(name)) stop()
    })
  }
  es.onerror = () => {
    if (closed) return
    es?.close()
    poll()
  }
  return stop
}
