import type { ColumnInfo, Filter, FilterScalar } from '../../api/types.ts'

// Taşıma kapsamı kuralları R0-R4; backend aynısını zorlar, birebir tutulmalı.
export const NO_FILTER_MESSAGE = 'Taşınacak kümeyi belirlemek için en az bir filtre gerekli.'
export const FULL_TABLE_FILTER_MESSAGE = 'Tablonun tamamı seçiliyken filtre verilemez.'
export const TAUTOLOGY_MESSAGE = 'Bu koşul tüm satırları seçer; kümeyi daraltan bir koşul girin.'

const DATE_MIN = '0001-01-01T00:00:00'
const DATE_MAX = '9999-12-31T00:00:00'

/** Tarihi karşılaştırılabilir 'YYYY-MM-DDTHH:MM:SS[.f]' biçimine getirir. */
function normDate(v: FilterScalar): string {
  const t = String(v).trim().replace(' ', 'T')
  if (!t.includes('T')) return `${t}T00:00:00`
  return /T\d{2}:\d{2}$/.test(t) ? `${t}:00` : t
}

function decimals(v: FilterScalar): number {
  const s = String(v)
  if (/e/i.test(s)) return 0
  return s.split('.')[1]?.length ?? 0
}

function numberTautology(f: Filter, p: number, s: number): boolean {
  const M = 10 ** (p - s)
  const num = (v: FilterScalar | undefined) => Number(v)
  switch (f.op) {
    case 'gt':
    case 'ge':
      return f.value !== undefined && num(f.value) <= -M
    case 'lt':
    case 'le':
      return f.value !== undefined && num(f.value) >= M
    case 'between':
      return !!f.values && f.values.length === 2 && num(f.values[0]) <= -M && num(f.values[1]) >= M
    case 'ne':
      return f.value !== undefined && (Math.abs(num(f.value)) >= M || decimals(f.value) > s)
    default:
      return false
  }
}

function dateTautology(f: Filter): boolean {
  switch (f.op) {
    case 'gt':
    case 'ge':
      return f.value !== undefined && normDate(f.value) <= DATE_MIN
    case 'lt':
    case 'le':
      return f.value !== undefined && normDate(f.value) >= DATE_MAX
    case 'between':
      return !!f.values && f.values.length === 2 && normDate(f.values[0]) <= DATE_MIN && normDate(f.values[1]) >= DATE_MAX
    default:
      return false
  }
}

/** Filtre tüm satırları seçiyorsa hata metni, değilse null (R1-R4). */
export function tautologyReason(filter: Filter, column: ColumnInfo | undefined): string | null {
  if (!column) return null
  const type = column.data_type.toUpperCase()
  let bad = false
  if (filter.op === 'is_not_null') bad = !column.nullable
  else if (filter.op === 'like') {
    const v = typeof filter.value === 'string' ? filter.value : ''
    bad = /^[%_]+$/.test(v) && v.includes('%')
  } else if (type === 'NUMBER' && column.precision !== null) {
    bad = numberTautology(filter, column.precision, column.scale ?? 0)
  } else if (type === 'DATE' || type.startsWith('TIMESTAMP')) {
    bad = dateTautology(filter)
  }
  return bad ? TAUTOLOGY_MESSAGE : null
}

/** Filtre listesi kapsamı belirlemiyorsa (boş) hata metni (R0), değilse null. */
export function scopeError(filters: Filter[], fullTable = false): string | null {
  if (fullTable) return filters.length === 0 ? null : FULL_TABLE_FILTER_MESSAGE
  return filters.length === 0 ? NO_FILTER_MESSAGE : null
}
