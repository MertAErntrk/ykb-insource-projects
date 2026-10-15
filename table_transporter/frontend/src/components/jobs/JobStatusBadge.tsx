import { Badge } from '../ui/index.ts'
import type { Tone } from '../ui/index.ts'
import type { ErrorCode, Job, JobStatus } from '../../api/types.ts'
import './jobs.css'

export const STATUS_TEXT: Record<JobStatus, string> = {
  queued: 'Sırada', running: 'Sürüyor', cancelling: 'İptal ediliyor', done: 'Tamamlandı', failed: 'Başarısız', partial: 'Kısmen', cancelled: 'İptal edildi',
}
const STATUS_TONE: Record<JobStatus, Tone> = {
  queued: 'neutral', running: 'info', cancelling: 'warn', done: 'success', failed: 'danger', partial: 'warn', cancelled: 'neutral',
}

export function JobStatusBadge({ status }: { status: JobStatus }) {
  return <span className="job-badge" data-status={status}><Badge tone={STATUS_TONE[status]}>{STATUS_TEXT[status]}</Badge></span>
}

export const ERROR_TEXT: Partial<Record<ErrorCode, string>> = {
  DB_DISCONNECTED: 'Veritabanı bağlantısı koptu.',
  DB_PERMISSION: 'Veritabanı izni yetersiz.',
  TARGET_DENIED: 'Hedefe yazma izni yok.',
  TARGET_LOCKED: 'Hedef başka bir taşıma tarafından kilitli.',
  TABLE_EXISTS: 'Hedef tablo zaten var.',
  INCOMPATIBLE: 'Kaynak ve hedef tablo uyumsuz.',
  RULE_INVALID: 'Bir kural geçersiz.',
  TRIGGER_PRESENT: 'Hedef tabloda etkin trigger var.',
  LIMIT_EXCEEDED: 'Satır sayısı sınırı aşıldı.',
  ORA_ERROR: 'Oracle hatası oluştu.',
  INTERNAL: 'Beklenmeyen bir hata oluştu.',
}
export const errorText = (code: ErrorCode | null) => (code && ERROR_TEXT[code]) || 'Taşıma tamamlanamadı.'

export const fmtNum = (n: number) => n.toLocaleString('tr-TR')
export const fmtDateTime = (iso: string | null) => (iso ? new Date(iso).toLocaleString('tr-TR', { dateStyle: 'short', timeStyle: 'short' }) : '-')

/** Saniyeyi "2 dk 5 sn" biçimine çevirir. */
export function fmtSeconds(sec: number): string {
  const s = Math.max(0, Math.round(sec))
  if (s < 60) return `${fmtNum(s)} sn`
  const m = Math.floor(s / 60)
  if (m < 60) return s % 60 ? `${fmtNum(m)} dk ${s % 60} sn` : `${fmtNum(m)} dk`
  const h = Math.floor(m / 60)
  return m % 60 ? `${fmtNum(h)} sa ${m % 60} dk` : `${fmtNum(h)} sa`
}
/** Kalan süre tahmini: "~2 dk". */
export function fmtEta(sec: number): string {
  if (sec < 60) return '~1 dk'
  const m = Math.round(sec / 60)
  return m < 60 ? `~${fmtNum(m)} dk` : `~${fmtNum(Math.round(m / 6) / 10)} sa`
}
export function jobDuration(j: Pick<Job, 'started_at' | 'finished_at'>): string {
  if (!j.started_at || !j.finished_at) return '-'
  return fmtSeconds((Date.parse(j.finished_at) - Date.parse(j.started_at)) / 1000)
}
export const modeText = (m: Job['mode']) => (m === 'create' ? 'Yeni tablo' : 'Ekleme')
