import { describe, expect, it } from 'vitest'
import type { ColumnInfo, Filter } from '../../api/types.ts'
import { FULL_TABLE_FILTER_MESSAGE, NO_FILTER_MESSAGE, TAUTOLOGY_MESSAGE, scopeError, tautologyReason } from './scopeRules.ts'

const col = (data_type: string, o: Partial<ColumnInfo> = {}): ColumnInfo => ({
  position: 1, name: 'C', data_type, char_length: 0, char_used: null, data_length: 22, precision: null, scale: null, nullable: true,
  virtual: false, identity: false, hidden: false, default: null, is_pk_or_unique: false, supported: true, unsupported_reason: null, ...o,
})
const num = (p: number | null, s: number | null) => col('NUMBER', { precision: p, scale: s })
const f = (op: Filter['op'], value?: Filter['value'], values?: Filter['values']): Filter => ({ column: 'C', op, value, values })
const bad = (flt: Filter, c: ColumnInfo) => expect(tautologyReason(flt, c)).toBe(TAUTOLOGY_MESSAGE)
const good = (flt: Filter, c: ColumnInfo) => expect(tautologyReason(flt, c)).toBeNull()

describe('scopeError (R0)', () => {
  it('boş liste hata, dolu liste değil', () => {
    expect(scopeError([])).toBe(NO_FILTER_MESSAGE)
    expect(scopeError([f('eq', 1)])).toBeNull()
    expect(scopeError([], false)).toBe(NO_FILTER_MESSAGE)
  })

  it('tam tabloda filtre boşsa null, doluysa hata', () => {
    expect(scopeError([], true)).toBeNull()
    expect(scopeError([f('eq', 1)], true)).toBe(FULL_TABLE_FILTER_MESSAGE)
  })
})

describe('R1 is_not_null', () => {
  it('NOT NULL kolonda red, nullable kolonda kabul', () => {
    bad(f('is_not_null'), col('VARCHAR2', { nullable: false }))
    good(f('is_not_null'), col('VARCHAR2', { nullable: true }))
  })
})

describe('R2 like', () => {
  it.each(['%', '%%', '_%', '%_%'])('%s red', (p) => bad(f('like', p), col('VARCHAR2')))
  it.each(['_', 'A%', '%A', '_A_'])('%s kabul', (p) => good(f('like', p), col('VARCHAR2')))
})

describe('R3 NUMBER', () => {
  const c = num(5, 0)
  it('gt/ge alt sınır', () => {
    bad(f('gt', -100000), c)
    bad(f('ge', -100000), c)
    good(f('gt', -99999), c)
  })
  it('lt/le üst sınır', () => {
    bad(f('lt', 100000), c)
    bad(f('le', 100000), c)
    good(f('lt', 99999), c)
  })
  it('between', () => {
    bad(f('between', undefined, [-100000, 100000]), c)
    good(f('between', undefined, [-99999, 100000]), c)
    good(f('between', undefined, [-100000, 99999]), c)
  })
  it('ne: aralık dışı ya da fazla ondalık red', () => {
    bad(f('ne', 100000), c)
    bad(f('ne', 1.5), c)
    good(f('ne', 99999), c)
    bad(f('ne', 1.234), num(5, 2))
    good(f('ne', 1.23), num(5, 2))
  })
  it('scale null 0 sayılır, ölçek M değerini değiştirir', () => {
    bad(f('gt', -100000), num(5, null))
    good(f('gt', -999), num(5, 2))
    bad(f('gt', -1000), num(5, 2))
  })
  it('precision null ise kural yok', () => {
    good(f('gt', -1e30), num(null, null))
  })
})

describe('R4 DATE/TIMESTAMP', () => {
  const d = col('DATE')
  it('gt/ge alt sınır', () => {
    bad(f('gt', '0001-01-01'), d)
    bad(f('ge', '0001-01-01T00:00:00'), d)
    good(f('gt', '0001-01-02'), d)
    good(f('gt', '0001-01-01T00:00:01'), d)
  })
  it('lt/le üst sınır', () => {
    bad(f('lt', '9999-12-31'), d)
    bad(f('le', '9999-12-31T00:00:00'), col('TIMESTAMP(6)'))
    good(f('lt', '9999-12-30'), d)
  })
  it('between', () => {
    bad(f('between', undefined, ['0001-01-01', '9999-12-31']), d)
    good(f('between', undefined, ['0001-01-02', '9999-12-31']), d)
    good(f('between', undefined, ['0001-01-01', '9999-12-30']), d)
  })
})
