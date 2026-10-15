import type { ColumnInfo, Rule } from '../../api/types.ts'
import type { DateShiftSetting } from './ruleSuggest.ts'
import { SHOW_LAST_MAX } from './ruleSuggest.ts'

interface RuleParamsProps {
  column: ColumnInfo
  rule: Rule
  error: string | null
  dateSetting: DateShiftSetting
  onChange: (rule: Rule) => void
}

/** Kural türüne göre satır içi parametre alanı. Hata aria-describedby ile bağlanır. */
export function RuleParams({ column, rule, error, dateSetting, onChange }: RuleParamsProps) {
  const errId = `param-err-${column.name}`
  const common = {
    'aria-invalid': error ? true : undefined,
    'aria-describedby': error ? errId : undefined,
  } as const
  let body = null
  switch (rule.type) {
    case 'mask': {
      const n = rule.params.show_last
      body = (
        <label className="param-field">
          <span>Açık kalan son karakter</span>
          <input
            {...common}
            className="input param-input"
            type="number"
            inputMode="numeric"
            min={0}
            max={SHOW_LAST_MAX}
            aria-label={`Açık kalan son karakter sayısı, ${column.name}`}
            value={Number.isNaN(n) ? '' : n}
            onChange={(e) => onChange({ ...rule, params: { show_last: e.target.value === '' ? NaN : Number(e.target.value) } })}
          />
        </label>
      )
      break
    }
    case 'constant':
      body = (
        <label className="param-field">
          <span>Değer</span>
          <input
            {...common}
            className="input param-input param-wide"
            type="text"
            autoComplete="off"
            aria-label={`Sabit değer, ${column.name}`}
            value={rule.params.value}
            onChange={(e) => onChange({ ...rule, params: { value: e.target.value } })}
          />
        </label>
      )
      break
    case 'date_shift':
      body = <span className="param-note">±{dateSetting.max_days} gün, kaynak kolon {dateSetting.anchor || '(seçilmedi)'} (tablo ayarı)</span>
      break
    case 'hmac':
      body = <span className="param-note">64 karakter hex; yeni tabloda kolon VARCHAR2(64) olur.</span>
      break
    default:
      body = <span className="param-none" aria-hidden="true">-</span>
  }
  return (
    <div>
      {body}
      {error && <p className="field-error" id={errId}>{error}</p>}
    </div>
  )
}
