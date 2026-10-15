import type { ColumnInfo, Rule, RuleType } from '../../api/types.ts'

export const RULE_TYPES: RuleType[] = ['keep', 'exclude', 'null', 'constant', 'mask', 'hmac', 'num_token', 'text_token', 'date_token', 'date_shift', 'random']

export const RULE_LABELS: Record<RuleType, string> = {
  keep: 'Olduğu gibi',
  exclude: 'Taşınmaz',
  null: 'Boş (NULL)',
  constant: 'Sabit değer',
  mask: 'Maskele',
  hmac: 'Özetle (HMAC)',
  num_token: 'Sayı belirteci (geri çevrilebilir)',
  text_token: 'Metin belirteci (geri çevrilebilir)',
  date_token: 'Tarih belirteci (geri çevrilebilir)',
  date_shift: 'Tarih kaydır',
  random: 'Rastgele',
}

export const RULE_DESCRIPTIONS: Record<RuleType, string> = {
  keep: 'Değer değiştirilmeden hedefe yazılır.',
  exclude: 'Kolon hedef tabloya hiç yazılmaz.',
  null: 'Her satıra NULL yazılır.',
  constant: 'Her satıra aynı değer yazılır.',
  mask: 'Son birkaç karakter açık kalır, gerisi * olur.',
  hmac: 'Geri döndürülemez 64 karakterlik özet; aynı değer aynı özeti verir.',
  num_token: 'Rakamlar değişir; basamak sayısı, işaret ve ondalık korunur; join korunur. Değer çöz ekranından gerçek değere bakılabilir.',
  text_token: 'Harf ve rakamlar kendi türünde değişir; uzunluk, boşluk ve noktalama korunur; join korunur. Değer çöz ekranından gerçek değere bakılabilir.',
  date_token: 'Yıl korunur, gün ve saat değişir; saat yoksa gece yarısı kalır; join korunur. Değer çöz ekranından gerçek değere bakılabilir.',
  date_shift: 'Tarih, satır başına sabit bir gün sayısı kadar kaydırılır.',
  random: 'Tipe uygun rastgele değer üretilir.',
}

export interface DateShiftSetting {
  anchor: string
  max_days: number
}
export const DEFAULT_MAX_DAYS = 365
export const MAX_DAYS_LIMIT = 3650
export const SHOW_LAST_MAX = 10

const baseType = (c: ColumnInfo) => c.data_type.toUpperCase()
export const isTextType = (c: ColumnInfo) => /^(N?VARCHAR2|N?CHAR)$/.test(baseType(c))
export const isDateType = (c: ColumnInfo) => /^(DATE|TIMESTAMP)/.test(baseType(c))
export const isNumberType = (c: ColumnInfo) => /^(NUMBER|FLOAT)$/.test(baseType(c))
/** Saat dilimsiz DATE / TIMESTAMP. */
const isPlainDateType = (c: ColumnInfo) => isDateType(c) && !/WITH/.test(baseType(c))

/** Geri çevrilebilir (belirteç) kurallar. */
export const REVERSIBLE_RULES: RuleType[] = ['num_token', 'text_token', 'date_token']
export const isReversible = (t: RuleType) => REVERSIBLE_RULES.includes(t)

/** Kaynak veri tipi adından (ör. 'VARCHAR2', 'TIMESTAMP(6)') standart kural; yoksa null. */
export function standardRuleForType(dataType: string): RuleType | null {
  const t = dataType.toUpperCase()
  if (t === 'NUMBER') return 'num_token'
  if (/^(N?VARCHAR2|N?CHAR)$/.test(t)) return 'text_token'
  if (t === 'DATE' || /^TIMESTAMP(\(\d+\))?$/.test(t)) return 'date_token'
  return null
}

/** Kolonun standart kuralı (çıktı kaynak tip ve uzunluğunu aşmaz); desteklenmeyen ya da standardı olmayan kolonda null. */
export function standardRule(c: ColumnInfo): RuleType | null {
  if (!c.supported) return null
  const r = standardRuleForType(c.data_type)
  return r === 'num_token' && ruleBlockReason(c, r) ? null : r
}

/** VARCHAR2(80 CHAR), NUMBER(12,2) biçimi. */
export function formatType(c: ColumnInfo): string {
  const t = baseType(c)
  if (isTextType(c)) {
    const unit = c.char_used === 'C' ? ' CHAR' : c.char_used === 'B' ? ' BYTE' : ''
    return `${c.data_type}(${c.char_length || c.data_length}${unit})`
  }
  if (t === 'NUMBER') {
    if (c.precision === null) return 'NUMBER'
    return c.scale ? `NUMBER(${c.precision},${c.scale})` : `NUMBER(${c.precision})`
  }
  return c.data_type
}

/** Kuralın bu kolonda neden seçilemediği; izinliyse null. Son söz sunucuda. */
export function ruleBlockReason(c: ColumnInfo, type: RuleType): string | null {
  if (!c.supported) {
    return type === 'exclude' ? null : (c.unsupported_reason ?? 'Bu kolon tipi desteklenmiyor; yalnız taşınmaz seçilebilir.')
  }
  if (c.is_pk_or_unique && !['keep', 'exclude', 'num_token', 'text_token', 'date_token', 'hmac'].includes(type)) {
    return 'Anahtar kolonda kullanılamaz; eşsizlik bozulur.'
  }
  switch (type) {
    case 'null':
      return c.nullable ? null : 'Zorunlu kolona NULL yazılamaz.'
    case 'mask':
      return isTextType(c) ? null : 'Yalnız metin kolonlarında kullanılır.'
    case 'num_token':
      if (baseType(c) !== 'NUMBER') return 'Yalnız NUMBER kolonlarında kullanılır.'
      return c.scale !== null && c.scale < 0 ? 'Negatif ölçekli NUMBER kolonunda kullanılamaz.' : null
    case 'text_token':
      return isTextType(c) ? null : 'Yalnız metin kolonlarında kullanılır.'
    case 'date_token':
      return isPlainDateType(c) ? null : 'Yalnız DATE ve saat dilimsiz TIMESTAMP kolonlarında kullanılır.'
    case 'date_shift':
      return isDateType(c) ? null : 'Yalnız DATE ve TIMESTAMP kolonlarında kullanılır.'
    default:
      return null
  }
}

export function defaultRule(c: ColumnInfo, type: RuleType, date?: DateShiftSetting): Rule {
  const column = c.name
  switch (type) {
    case 'constant': return { column, type, params: { value: '' } }
    case 'mask': return { column, type, params: { show_last: 4 } }
    case 'date_shift': return { column, type, params: { anchor: date?.anchor ?? 'PARTY_ID', max_days: date?.max_days ?? DEFAULT_MAX_DAYS } }
    case 'keep': case 'exclude': case 'null': case 'hmac': case 'num_token': case 'text_token': case 'date_token': case 'random':
      return { column, type, params: {} }
  }
}

/** Parametre hatası (Türkçe); geçerliyse null. */
export function ruleParamError(c: ColumnInfo, rule: Rule): string | null {
  if (rule.type === 'constant') {
    const v = rule.params.value
    if (v === '') return c.nullable ? null : 'Zorunlu kolonda sabit değer boş olamaz.'
    if (isNumberType(c) && !/^-?\d+(\.\d+)?$/.test(v)) return 'Sayı girin (ör. 12 ya da 12.5).'
    if (isDateType(c) && !/^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?$/.test(v)) return 'Tarih girin (YYYY-AA-GG).'
    if (isTextType(c) && c.char_used === 'C' && c.char_length > 0 && v.length > c.char_length) return `En çok ${c.char_length} karakter girin.`
    return null
  }
  if (rule.type === 'mask') {
    const n = rule.params.show_last
    if (!Number.isInteger(n) || n < 0 || n > SHOW_LAST_MAX) return `Açık kalacak karakter sayısı 0 ile ${SHOW_LAST_MAX} arasında olmalı.`
    return null
  }
  if (rule.type === 'date_shift') {
    if (!rule.params.anchor) return 'Tarih kaydırma ayarında kaynak kolonu seçin.'
    const d = rule.params.max_days
    if (!Number.isInteger(d) || d < 1 || d > MAX_DAYS_LIMIT) return `Gün sayısı 1 ile ${MAX_DAYS_LIMIT} arasında olmalı.`
  }
  return null
}

const TR_MAP: Record<string, string> = { Ç: 'C', Ğ: 'G', İ: 'I', Ö: 'O', Ş: 'S', Ü: 'U' }
const normalize = (s: string) => s.toLocaleUpperCase('tr').replace(/[ÇĞİÖŞÜ]/g, (ch) => TR_MAP[ch] ?? ch)

// Sıra önemli: ilk eşleşen kalıp grubu kazanır. Kalıp, ad parçalarında bitişik ve tam parça eşleşmesi ister.
const PATTERNS: Array<{ parts: string[][]; make: (c: ColumnInfo, d?: DateShiftSetting) => Rule }> = [
  { parts: [['TCKN'], ['TC'], ['KIMLIK'], ['VKN'], ['PARTY', 'ID'], ['MUSTERI', 'NO'], ['MUSTERI', 'ID'], ['CUSTOMER', 'ID']], make: (c) => defaultRule(c, 'num_token') },
  { parts: [['IBAN'], ['KART'], ['CARD'], ['HESAP', 'NO']], make: (c) => defaultRule(c, 'mask') },
  { parts: [['AD'], ['ADI'], ['SOYAD'], ['SOYADI'], ['UNVAN'], ['NAME'], ['EMAIL'], ['EPOSTA'], ['MAIL']], make: (c) => defaultRule(c, 'hmac') },
  { parts: [['TEL'], ['TELEFON'], ['GSM'], ['CEP'], ['PHONE']], make: (c) => ({ column: c.name, type: 'mask', params: { show_last: 2 } }) },
  { parts: [['ADRES'], ['ADDRESS']], make: (c) => defaultRule(c, 'random') },
  { parts: [['DOGUM'], ['BIRTH']], make: (c, d) => defaultRule(c, 'date_shift', d) },
]

function hasSequence(tokens: string[], seq: string[]): boolean {
  for (let i = 0; i + seq.length <= tokens.length; i++) {
    if (seq.every((p, j) => tokens[i + j] === p)) return true
  }
  return false
}

/** Ad kalıbından kural önerisi; kolonun izinli kurallarına uymuyorsa null. Saf fonksiyon. */
export function suggestRule(column: ColumnInfo, date?: DateShiftSetting): Rule | null {
  const tokens = normalize(column.name).split('_').filter(Boolean)
  for (const p of PATTERNS) {
    if (!p.parts.some((seq) => hasSequence(tokens, seq))) continue
    const std = standardRule(column)
    const rule = std ? defaultRule(column, std) : p.make(column, date)
    return ruleBlockReason(column, rule.type) === null ? rule : null
  }
  return null
}

/** Varsayılan anchor: ilk PK/UNIQUE kolon, yoksa PARTY_ID, yoksa ilk kolon. */
export function defaultAnchor(columns: ColumnInfo[]): string {
  return columns.find((c) => c.is_pk_or_unique)?.name ?? columns.find((c) => c.name === 'PARTY_ID')?.name ?? columns[0]?.name ?? ''
}
