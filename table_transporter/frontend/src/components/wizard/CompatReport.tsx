import type { CompatLevel, CompatResponse } from '../../api/types.ts'
import { Badge } from '../ui/index.ts'
import type { Tone } from '../ui/index.ts'
import './summary.css'

const LEVEL_TONE: Record<CompatLevel, Tone> = { ok: 'success', warn: 'warn', error: 'danger' }
const LEVEL_LABEL: Record<CompatLevel, string> = { ok: 'Uyumlu', warn: 'Uyarı', error: 'Hata' }

/** Uyumluluk sonucu İleri adımını kilitliyor mu? */
export function compatBlocked(c: CompatResponse): boolean {
  return c.status === 'error' || c.has_enabled_trigger || !c.target_is_table || c.items.some((i) => i.level === 'error')
}

/** Genel durum + kolon bazlı satırlar. */
export function CompatReport({ result }: { result: CompatResponse }) {
  return (
    <div className="conn-stack">
      <div className="compat-head">
        <strong>Genel durum</strong>
        <Badge tone={LEVEL_TONE[result.status]}>{LEVEL_LABEL[result.status]}</Badge>
      </div>
      {result.items.length > 0 && (
        <ul className="compat-list" aria-label="Kolon uyumluluğu">
          {result.items.map((i) => (
            <li key={`${i.column}-${i.message}`} className="compat-item">
              <Badge tone={LEVEL_TONE[i.level]}>{LEVEL_LABEL[i.level]}</Badge>
              <code>{i.column}</code>
              <span>{i.message}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
