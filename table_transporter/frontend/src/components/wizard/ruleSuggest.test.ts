import { describe, expect, it } from 'vitest'
import type { ColumnInfo } from '../../api/types.ts'
import { formatType, ruleBlockReason, standardRule, suggestRule } from './ruleSuggest.ts'

function col(over: Partial<ColumnInfo> & { name: string }): ColumnInfo {
  return {
    position: 1, data_type: 'VARCHAR2', char_length: 50, char_used: 'C', data_length: 200, precision: null, scale: null,
    nullable: true, virtual: false, identity: false, hidden: false, default: null, is_pk_or_unique: false,
    supported: true, unsupported_reason: null, ...over,
  }
}

describe('suggestRule', () => {
  it('TCKN -> num_token, ADET -> null (parça tam eşleşmesi)', () => {
    expect(suggestRule(col({ name: 'TCKN', data_type: 'NUMBER', precision: 11, scale: 0 }))?.type).toBe('num_token')
    expect(suggestRule(col({ name: 'ADET', data_type: 'NUMBER', precision: 5, scale: 0 }))).toBeNull()
  })

  it('metin kolonlarında öneri standart text_token; standardı olmayan tipte ad kalıbı kuralı', () => {
    expect(suggestRule(col({ name: 'MUSTERI_ADI' }))?.type).toBe('text_token')
    expect(suggestRule(col({ name: 'TELEFON_NO' }))?.type).toBe('text_token')
    expect(suggestRule(col({ name: 'ADRES' }))?.type).toBe('text_token')
    expect(suggestRule(col({ name: 'ADRES', data_type: 'FLOAT' }))?.type).toBe('random')
  })

  it('DOGUM_TARIHI DATE -> date_token; TIMESTAMP WITH TIME ZONE -> date_shift', () => {
    expect(suggestRule(col({ name: 'DOGUM_TARIHI', data_type: 'DATE' }))?.type).toBe('date_token')
    expect(suggestRule(col({ name: 'DOGUM_TARIHI', data_type: 'TIMESTAMP(6) WITH TIME ZONE' }))?.type).toBe('date_shift')
  })

  it('IBAN text_token; PK kolonda da text_token (birebir)', () => {
    expect(suggestRule(col({ name: 'IBAN' }))?.type).toBe('text_token')
    expect(suggestRule(col({ name: 'HESAP_NO', is_pk_or_unique: true }))?.type).toBe('text_token')
  })

  it('iki parçalı kalıplar bitişik olmalı', () => {
    expect(suggestRule(col({ name: 'PARTY_ID', data_type: 'NUMBER', scale: 0 }))?.type).toBe('num_token')
    expect(suggestRule(col({ name: 'PARTY_TIP' }))).toBeNull()
  })
})

describe('ruleBlockReason / formatType', () => {
  it('tip kuralları: num_token yalnız NUMBER, text_token yalnız metin, date_token yalnız saat dilimsiz tarih; standart katalog', () => {
    const n = col({ name: 'TUTAR', data_type: 'NUMBER', precision: 12, scale: 2 })
    expect(ruleBlockReason(n, 'num_token')).toBeNull()
    expect(ruleBlockReason(col({ name: 'T' }), 'num_token')).not.toBeNull()
    expect(ruleBlockReason(n, 'text_token')).not.toBeNull()
    expect(ruleBlockReason(col({ name: 'D', data_type: 'DATE' }), 'date_token')).toBeNull()
    expect(ruleBlockReason(col({ name: 'D', data_type: 'TIMESTAMP(6) WITH LOCAL TIME ZONE' }), 'date_token')).not.toBeNull()
    expect(standardRule(n)).toBe('num_token')
    const negScale = col({ name: 'N2', data_type: 'NUMBER', precision: 10, scale: -2 })
    expect(ruleBlockReason(negScale, 'num_token')).not.toBeNull()
    expect(standardRule(negScale)).toBeNull()
    expect(standardRule(col({ name: 'T', data_type: 'NCHAR' }))).toBe('text_token')
    expect(standardRule(col({ name: 'D', data_type: 'TIMESTAMP(6)' }))).toBe('date_token')
    expect(standardRule(col({ name: 'F', data_type: 'BINARY_DOUBLE' }))).toBeNull()
    expect(ruleBlockReason(n, 'mask')).not.toBeNull()
    expect(formatType(n)).toBe('NUMBER(12,2)')
  })
  it('tip biçimi', () => {
    expect(formatType(col({ name: 'A', char_length: 80 }))).toBe('VARCHAR2(80 CHAR)')
  })
  it('zorunlu kolonda null izinsiz', () => {
    expect(ruleBlockReason(col({ name: 'A', nullable: false }), 'null')).not.toBeNull()
  })
})
