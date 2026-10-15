import type { ColumnInfo, Rule, RuleType } from '../../api/types.ts'
import { Badge, Button, SearchableSelect } from '../ui/index.ts'
import type { SelectOption } from '../ui/index.ts'
import { RuleParams } from './RuleParams.tsx'
import { RULE_DESCRIPTIONS, RULE_LABELS, RULE_TYPES, defaultRule, formatType, isReversible, ruleBlockReason, ruleParamError, standardRule } from './ruleSuggest.ts'
import type { DateShiftSetting } from './ruleSuggest.ts'

/** Anonimleştirme satırı: kolon ve kural seçimi bağımsız doldurulur; ikisi de varsa tamamdır. */
export interface AnonRow {
  id: string
  column: string
  rule: Rule | null
}

/** Satırın eksik/tutarsız olma nedeni (Türkçe); tamamsa null. Parametre hataları ayrıca ruleParamError ile bulunur. */
export function rowIssue(row: AnonRow, byName: Map<string, ColumnInfo>, moved: Set<string>): string | null {
  if (!row.column) return 'Kolon seçin.'
  const col = byName.get(row.column)
  if (!col) return 'Kolon bu tabloda yok; satırı kaldırın.'
  if (!moved.has(row.column)) return 'Bu kolon taşınmayacak; satırı kaldırın ya da kolonu "Taşı" ile işaretleyin.'
  if (!row.rule) return 'Kural seçin.'
  if (ruleBlockReason(col, row.rule.type) !== null) return 'Bu kural kolona uygun değil; başka kural seçin.'
  return null
}

/** Satırın kolonuna uygun kurallar: keep/exclude hariç, ruleBlockReason geçenler; standart kural ilk sırada. */
export function allowedRuleTypes(col: ColumnInfo): RuleType[] {
  const std = standardRule(col)
  const list = RULE_TYPES.filter((t) => t !== 'keep' && t !== 'exclude' && ruleBlockReason(col, t) === null)
  return std && list.includes(std) ? [std, ...list.filter((t) => t !== std)] : list
}

interface AnonRuleRowsProps {
  columns: ColumnInfo[]
  moved: Set<string>
  rows: AnonRow[]
  dateSetting: DateShiftSetting
  onChange: (row: AnonRow) => void
  onRemove: (id: string) => void
}

/** Kolon seçimi (taşınan, desteklenen, başka satırda kullanılmamış) + tipe uygun kural + parametreler + kaldır. */
export function AnonRuleRows({ columns, moved, rows, dateSetting, onChange, onRemove }: AnonRuleRowsProps) {
  const byName = new Map(columns.map((c) => [c.name, c]))
  return (
    <ul className="anon-rows" aria-label="Anonimleştirme kuralları">
      {rows.map((row, i) => {
        const n = i + 1
        const col = byName.get(row.column)
        const used = new Set(rows.filter((r) => r.id !== row.id).map((r) => r.column))
        const options: SelectOption[] = columns
          .filter((c) => c.supported && !used.has(c.name) && (moved.has(c.name) || c.name === row.column))
          .map((c) => ({ value: c.name, label: `${c.name} — ${formatType(c)}` }))
        const issue = row.column ? rowIssue(row, byName, moved) : null
        // Eksik seçim yer tutucu ile anlatılır; yalnız tutarsızlık hata olarak bağlanır.
        const colError = issue && row.column && !(issue === 'Kural seçin.') ? issue : null
        const ruleId = `anon-rule-${row.id}`
        const allowed = col ? allowedRuleTypes(col) : []
        const std = col ? standardRule(col) : null
        const isStd = !!row.rule && row.rule.type === std
        const offStd = !!row.rule && !isReversible(row.rule.type) && !isStd
        const onColumn = (name: string) => {
          const next = byName.get(name)
          const std = next ? standardRule(next) : null
          if (next && std) return onChange({ ...row, column: name, rule: defaultRule(next, std, dateSetting) })
          const keepRule = next && row.rule && ruleBlockReason(next, row.rule.type) === null && row.rule.type !== 'keep' && row.rule.type !== 'exclude'
          onChange({ ...row, column: name, rule: keepRule && row.rule ? defaultRule(next, row.rule.type, dateSetting) : null })
        }
        return (
          <li key={row.id} className="anon-row" data-row-id={row.id}>
            <div className="anon-cell anon-col">
              <SearchableSelect
                label={`Kolon (satır ${n})`}
                options={options}
                value={row.column || null}
                onChange={onColumn}
                placeholder="Kolon seçin"
                error={colError}
                emptyText="Uygun kolon kalmadı."
              />
              {col && <span className="anon-type"><Badge tone="info">{formatType(col)}</Badge></span>}
            </div>
            <div className="anon-cell anon-kind">
              <label htmlFor={ruleId} className="anon-label">Kural{isStd && <> <Badge tone="success">Standart</Badge></>}</label>
              <select
                id={ruleId}
                className="input"
                aria-label={`Kural (satır ${n})`}
                aria-describedby={row.rule ? `${ruleId}-desc` : undefined}
                value={row.rule?.type ?? ''}
                disabled={!col}
                onChange={(e) => {
                  if (col && e.target.value) onChange({ ...row, rule: defaultRule(col, e.target.value as RuleType, dateSetting) })
                }}
              >
                <option value="" disabled>{col ? 'Kural seçin' : 'Önce kolon seçin'}</option>
                {allowed.map((t) => <option key={t} value={t}>{RULE_LABELS[t]}</option>)}
              </select>
              {row.rule && <p className="rule-desc" id={`${ruleId}-desc`}>{RULE_DESCRIPTIONS[row.rule.type]}</p>}
              {offStd && <p className="anon-note">Standart dışı · geri çevrilemez</p>}
            </div>
            <div className="anon-cell anon-params">
              {col && row.rule && (
                <RuleParams column={col} rule={row.rule} error={ruleParamError(col, row.rule)} dateSetting={dateSetting} onChange={(rule) => onChange({ ...row, rule })} />
              )}
            </div>
            <div className="anon-cell anon-remove">
              <Button size="sm" variant="ghost" aria-label={`Satırı kaldır (satır ${n})`} onClick={() => onRemove(row.id)}>Kaldır</Button>
            </div>
          </li>
        )
      })}
    </ul>
  )
}
