// VITE_API_MOCK=1 iken tüm uçları bellekte yanıtlar (backend olmadan geliştirme/test).
// Giriş: parola "hatali" -> AUTH_INVALID; kullanıcı "yetkisiz" -> AUTH_FORBIDDEN, "kilitli" -> AUTH_THROTTLED, "ldapyok" -> LDAP_UNAVAILABLE.
import { ApiError } from './client.ts'
import type {
  ColumnInfo, CompatItem, ConnectRequest, DbStatus, ErrorCode, Job, Rule, RuleType, TnsAlias, TrackedTable, TransferRequest, User,
} from './types.ts'

const fail = (code: ErrorCode, detail: string, status = 400): never => {
  throw new ApiError(code, detail, status)
}
const delay = (ms = 120) => new Promise((r) => setTimeout(r, ms))
const now = () => new Date().toISOString().slice(0, 19) + 'Z'
const ROW_LIMIT = 5_000_000

const ALIASES: TnsAlias[] = [
  { alias: 'DEMO_DEV', host: 'db-test.example.invalid', port: 1453, service_name: 'DEMO_DEV', target_allowed: true },
  { alias: 'DEMO_TEST', host: 'db-test.example.invalid', port: 1453, service_name: 'DEMO_TEST', target_allowed: true },
  { alias: 'DEMOLIVE', host: 'db-live-scan.example.invalid', port: 1453, service_name: 'DEMOLIVE', target_allowed: false },
  { alias: 'TESTDB_DENEME', host: 'db-deneme.example.invalid', port: 1521, service_name: 'srv_demo1db', target_allowed: true },
]

const TABLES: Record<string, [string, number | null][]> = {
  DEMO: [['MUSTERI', 1_200_000], ['HESAP_HAREKET', 48_200_000], ['ADRES', 350_000], ['KART_BASVURU', null]],
  CRM: [['KAMPANYA', 12_400], ['ILETISIM_TERCIH', 2_100_000]],
  HR: [['PERSONEL', 8_300]],
  TEST: [['MUSTERI_ANON_20261001', 1_000], ['MUSTERI_TRG', 500]],
}

// Tam tablo sayımı (filtresiz); listedeki tahminlerden bağımsız, sözleşme örnekleri.
const FULL_ROWS: Record<string, number> = { MUSTERI: 120_000, ADRES: 80_000, KART_BASVURU: 45_000, HESAP_HAREKET: 48_200_000 }

const col = (position: number, name: string, data_type: string, o: Partial<ColumnInfo> = {}): ColumnInfo => ({
  position, name, data_type, char_length: 0, char_used: null, data_length: 22, precision: null, scale: null, nullable: true,
  virtual: false, identity: false, hidden: false, default: null, is_pk_or_unique: false, supported: true, unsupported_reason: null, ...o,
})
const varchar = (p: number, name: string, n: number) => col(p, name, 'VARCHAR2', { char_length: n, char_used: 'C', data_length: n * 4 })
const COLUMNS: ColumnInfo[] = [
  col(1, 'PARTY_ID', 'NUMBER', { precision: 12, scale: 0, nullable: false, is_pk_or_unique: true }),
  varchar(2, 'TCKN', 11), varchar(3, 'AD', 80), varchar(4, 'SOYAD', 80), varchar(5, 'EMAIL', 120), varchar(6, 'IBAN', 34),
  col(7, 'DOGUM_TARIHI', 'DATE', { data_length: 7 }),
  col(8, 'NOTLAR', 'CLOB', { data_length: 4000, supported: false, unsupported_reason: 'CLOB kolonlar taşınamaz; yalnızca hariç tutulabilir.' }),
]
const SAMPLES: Record<string, (string | number | null)[]> = {
  PARTY_ID: [100234, 100235, 100236, 100241, 100250],
  TCKN: ['12345678901', '23456789012', '34567890123', null, '45678901234'],
  AD: ['Ayşe', 'Mehmet', 'Zeynep', 'Mustafa', 'Elif'],
  SOYAD: ['Yılmaz', 'Kaya', 'Demir', 'Çelik', 'Şahin'],
  EMAIL: ['ayse.y@example.com', 'mehmet@example.com', 'zeynep.d@example.com', 'm.celik@example.com', 'elif@example.com'],
  IBAN: ['TR330006100519786457841326', 'TR120006200000112345678901', 'TR560001000000001234567890', 'TR090001500158007654321098', 'TR640001200000001122334455'],
  DOGUM_TARIHI: ['1985-03-12', '1990-11-02', '1978-07-30', '2001-01-15', '1996-09-23'],
}

const hex = (s: string) => {
  let h = 5381
  for (const c of s) h = ((h * 33) ^ c.charCodeAt(0)) >>> 0
  return h.toString(16).padStart(8, '0').repeat(8)
}
// Önizleme için basit ama tersinir sahte belirteçler (gerçek algoritma Feistel, sunucuda).
const mod = (n: number, m: number) => ((n % m) + m) % m
const num = (s: string, dir: 1 | -1) => s.replace(/\d/g, (d, i) => String(mod(Number(d) + dir * (3 + i * 7), 10)))
const CLASSES = ['ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz', '0123456789', 'ÇĞİÖŞÜ', 'çğıöşü']
const text = (s: string, dir: 1 | -1) =>
  [...s].map((ch, i) => {
    const cls = CLASSES.find((c) => c.includes(ch))
    return cls ? cls[mod(cls.indexOf(ch) + dir * (3 + i * 7), cls.length)] : ch
  }).join('')
const DATE_RE = /^(?:(\d{4})-(\d{2})-(\d{2})|(\d{2})([./])(\d{2})\5(\d{4}))(?:[ T](\d{2}):(\d{2}):(\d{2})(\.\d+)?)?$/
const p2 = (n: number) => String(n).padStart(2, '0')
function dateToken(s: string, dir: 1 | -1): string | null {
  const m = DATE_RE.exec(s.trim())
  if (!m) return null
  const iso = !!m[1]
  const y = Number(iso ? m[1] : m[7])
  const mo = Number(iso ? m[2] : m[6])
  const d = Number(iso ? m[3] : m[4])
  if (new Date(Date.UTC(y, mo - 1, d)).getUTCMonth() !== mo - 1) return null
  const days = (Date.UTC(y + 1, 0, 1) - Date.UTC(y, 0, 1)) / 86_400_000
  const doy = Math.round((Date.UTC(y, mo - 1, d) - Date.UTC(y, 0, 1)) / 86_400_000)
  const out = new Date(Date.UTC(y, 0, 1) + mod(doy + dir * (37 + (y % 50)), days) * 86_400_000)
  const om = p2(out.getUTCMonth() + 1)
  const od = p2(out.getUTCDate())
  const ds = iso ? `${y}-${om}-${od}` : `${od}${m[5]}${om}${m[5]}${y}`
  if (m[8] === undefined) return ds
  const sec = Number(m[8]) * 3600 + Number(m[9]) * 60 + Number(m[10])
  const ns = sec === 0 ? 0 : 1 + mod(sec - 1 + dir * 12345, 86399)
  return `${ds} ${p2(Math.floor(ns / 3600))}:${p2(Math.floor(ns / 60) % 60)}:${p2(ns % 60)}${m[11] ?? ''}`
}

function transform(rule: Rule, v: string | number | null): string | number | null {
  if (rule.type === 'keep') return v
  if (rule.type === 'null') return null
  if (v === null) return null
  const s = String(v)
  switch (rule.type) {
    case 'constant': return rule.params.value
    case 'mask': return '*'.repeat(Math.max(0, s.length - rule.params.show_last)) + s.slice(-rule.params.show_last)
    case 'hmac': return hex(s)
    case 'num_token': return num(s, 1)
    case 'text_token': return text(s, 1)
    case 'date_token': return dateToken(s, 1) ?? s
    case 'date_shift': return new Date(Date.parse(s) + 86_400_000 * (1 + (hex(s).charCodeAt(0) % rule.params.max_days))).toISOString().slice(0, 10)
    case 'random': return typeof v === 'number' ? Math.floor(Math.random() * 10 ** s.length) : hex(s + Math.random()).slice(0, s.length)
    default: return v
  }
}

// --- Durum ---
let user: User | null = null
let conns: DbStatus = { source: null, target: null }
interface MockJob { job: Job; startMs: number; rate: number; cancelMs?: number; frozen: boolean }
const jobs = new Map<string, MockJob>()
const tracked: TrackedTable[] = []

const baseJob = (id: string, over: Partial<Job>): Job => ({
  id, owner: 'mert', status: 'done', source_alias: 'DEMO_TEST', source_schema: 'DEMO', source_table: 'MUSTERI', target_alias: 'DEMO_DEV',
  target_schema: 'TEST', target_table: 'MUSTERI_ANON_20261001', mode: 'create', filters: [], total_rows: 1_000, rows_read: 1_000, rows_written: 1_000,
  rows_per_sec: null, eta_sec: null, error_code: null, error_detail: null, created_at: '2026-10-01T09:00:00Z', started_at: '2026-10-01T09:00:01Z',
  finished_at: '2026-10-01T09:00:09Z', ...over,
})
const jobCols = (rules: Rule[]) => rules.map((r, i) => ({ column_name: r.column, rule_type: r.type as RuleType, params: r.params as Record<string, unknown>, source_type: COLUMNS.find((c) => c.name === r.column)?.data_type ?? 'VARCHAR2', position: i + 1 }))

export function resetMock() {
  user = null
  conns = { source: null, target: null }
  jobs.clear()
  tracked.length = 0
  jobs.set('11111111-aaaa-4aaa-8aaa-000000000001', { job: baseJob('11111111-aaaa-4aaa-8aaa-000000000001', { columns: jobCols([{ column: 'PARTY_ID', type: 'num_token', params: {} }, { column: 'AD', type: 'text_token', params: {} }, { column: 'DOGUM_TARIHI', type: 'date_token', params: {} }, { column: 'TCKN', type: 'hmac', params: {} }, { column: 'IBAN', type: 'mask', params: { show_last: 4 } }, { column: 'EMAIL', type: 'keep', params: {} }]) }), startMs: 0, rate: 0, frozen: true })
  jobs.set('11111111-aaaa-4aaa-8aaa-000000000002', {
    job: baseJob('11111111-aaaa-4aaa-8aaa-000000000002', { status: 'failed', target_table: 'ADRES_ANON', rows_written: 120_000, rows_read: 120_000, total_rows: 350_000, error_code: 'ORA_ERROR', error_detail: 'ORA-12899: Değer kolon için çok büyük (SOYAD).', created_at: '2026-10-02T11:00:00Z', columns: jobCols([{ column: 'PARTY_ID', type: 'num_token', params: {} }, { column: 'SOYAD', type: 'text_token', params: {} }]) }),
    startMs: 0, rate: 0, frozen: true,
  })
  tracked.push({ id: 1, job_id: '11111111-aaaa-4aaa-8aaa-000000000001', owner: 'mert', alias: 'DEMO_DEV', schema: 'TEST', table: 'MUSTERI_ANON_20261001', object_id: 123456, object_created: '2026-10-01T09:00:05Z', verified_visible_at: '2026-10-01T09:05:00Z', table_status: 'expired_present', expires_at: '2026-10-08T09:00:00Z', last_checked_at: null })
}
resetMock()
try {
  const u = sessionStorage.getItem('tt_mock_user')
  if (u) user = JSON.parse(u) as User
} catch {
  // sessionStorage yok
}

function view(m: MockJob): Job {
  if (m.frozen) return m.job
  const total = m.job.total_rows ?? 0
  const sec = (Date.now() - m.startMs) / 1000
  const stopAt = m.cancelMs ? (m.cancelMs - m.startMs) / 1000 : sec
  const read = Math.min(total, Math.floor(Math.min(sec, stopAt) * m.rate))
  const base = { ...m.job, rows_read: read, rows_written: read }
  if (m.cancelMs) return { ...base, status: Date.now() - m.cancelMs < 1000 ? 'cancelling' : 'cancelled', rows_per_sec: null, eta_sec: null }
  if (read >= total) return { ...base, status: 'done', rows_per_sec: null, eta_sec: null, finished_at: now() }
  return { ...base, status: 'running', rows_per_sec: m.rate, eta_sec: Math.ceil((total - read) / m.rate) }
}

function needUser(): User {
  return user ?? fail('SESSION_EXPIRED', 'Oturumunuz sona erdi. Yeniden giriş yapın.', 401)
}

export async function mockRequest(method: string, path: string, query: Record<string, unknown>, body: unknown): Promise<unknown> {
  await delay()
  const b = (body ?? {}) as Record<string, unknown>
  let m: RegExpMatchArray | null

  if (path === '/health') return { status: 'ok' }
  if (method === 'POST' && path === '/auth/login') {
    const name = String(b.username ?? '')
    const pw = String(b.password ?? '')
    if (!/^[A-Za-z0-9._-]{1,64}$/.test(name)) fail('VALIDATION', 'body.username', 422)
    if (!pw.trim() || pw === 'hatali') fail('AUTH_INVALID', 'Kullanıcı adı veya şifre hatalı.', 401)
    if (name === 'yetkisiz') fail('AUTH_FORBIDDEN', 'Bu uygulamayı kullanma yetkiniz yok.', 403)
    if (name === 'kilitli') fail('AUTH_THROTTLED', 'Çok fazla başarısız deneme.', 429)
    if (name === 'ldapyok') fail('LDAP_UNAVAILABLE', 'Kimlik doğrulama sunucusuna ulaşılamadı.', 503)
    user = { username: name, display_name: name.charAt(0).toUpperCase() + name.slice(1), groups: ['CN=TT_USERS'], is_admin: false }
    try { sessionStorage.setItem('tt_mock_user', JSON.stringify(user)) } catch { /* yok say */ }
    return { user, csrf_token: 'mock-csrf-token' }
  }
  if (method === 'GET' && path === '/auth/me') return { user: needUser(), csrf_token: 'mock-csrf-token' }

  needUser()
  if (method === 'POST' && path === '/auth/logout') {
    if (!b.cancel_running && [...jobs.values()].some((j) => ['running', 'cancelling'].includes(view(j).status))) fail('JOB_RUNNING', 'Devam eden bir taşıma işiniz var. İş iptal edilip çıkış yapılsın mı?', 409)
    user = null
    conns = { source: null, target: null }
    try { sessionStorage.removeItem('tt_mock_user') } catch { /* yok say */ }
    return undefined
  }

  if (method === 'GET' && path === '/tns/aliases') {
    const q = String(query.q ?? '').toLowerCase()
    return { aliases: ALIASES.filter((a) => a.alias.toLowerCase().includes(q)) }
  }
  if (method === 'POST' && path === '/db/connect') {
    const r = b as unknown as ConnectRequest
    const a = ALIASES.find((x) => x.alias === r.alias) ?? fail('NOT_FOUND', 'Bilinmeyen alias.', 404)
    if (r.role === 'target' && !a.target_allowed) fail('TARGET_DENIED', 'Bu veritabanı hedef olarak kullanılamaz.', 403)
    if (!r.user || !r.password || r.password === 'hatali') fail('DB_CONNECT_FAILED', 'Bağlantı kurulamadı: kullanıcı adı veya şifre hatalı (ORA-01017).')
    const info = { role: r.role, alias: r.alias, db_version: '19.0.0.0', user: r.user.toUpperCase(), mode: 'thin' as const, direct_path: true, max_ident_len: 128 }
    conns = { ...conns, [r.role]: info }
    return info
  }
  if ((m = path.match(/^\/db\/connect\/(source|target)$/)) && method === 'DELETE') {
    conns = { ...conns, [m[1]]: null }
    return undefined
  }
  if (method === 'GET' && path === '/db/status') return conns

  if ((m = path.match(/^\/db\/(source|target)\/(schemas|tables)$/))) {
    if (!conns[m[1] as 'source' | 'target']) fail('DB_DISCONNECTED', 'Önce veritabanına bağlanın.', 409)
    const q = String(query.q ?? '').toLowerCase()
    if (m[2] === 'schemas') return { schemas: Object.keys(TABLES).filter((s) => s.toLowerCase().includes(q)) }
    const schema = String(query.schema ?? '')
    if (!schema) fail('VALIDATION', 'query.schema', 422)
    const all = (TABLES[schema] ?? []).filter(([n]) => n.toLowerCase().includes(q))
    const limit = Number(query.limit ?? 50)
    const offset = Number(query.offset ?? 0)
    const items = all.slice(offset, offset + limit).map(([name]) => ({ schema, name }))
    return { items, total: items.length >= limit ? offset + items.length + 1 : offset + items.length, limit, offset }
  }
  if ((m = path.match(/^\/db\/(source|target)\/tables\/[^/]+\/[^/]+\/columns$/))) return { columns: COLUMNS }

  if (method === 'POST' && ['/transfers/count', '/transfers/preview', '/transfers'].includes(path)) {
    const nFilters = ((b.filters as unknown[]) ?? []).length
    if (b.full_table && nFilters) fail('VALIDATION', 'Tablonun tamamı seçiliyken filtre verilemez.', 422)
    if (!b.full_table && !nFilters && path !== '/transfers/preview') fail('VALIDATION', 'Taşınacak kümeyi belirlemek için en az bir filtre gerekli.', 422)
  }
  if (method === 'POST' && path === '/transfers/count') {
    const src = b.source as { schema: string; table: string }
    const full = FULL_ROWS[src.table] ?? (TABLES[src.schema] ?? []).find(([t]) => t === src.table)?.[1] ?? 250_000
    const n = b.full_table ? full : Math.floor(full / 10 ** Math.min(((b.filters as unknown[]) ?? []).length, 3))
    if (n > ROW_LIMIT) return { count: null, limit: ROW_LIMIT, over_limit: true, timed_out: false }
    return { count: n, limit: ROW_LIMIT, over_limit: false, timed_out: false }
  }
  if (method === 'POST' && path === '/transfers/preview') {
    const rules = (b.rules as Rule[]) ?? []
    return {
      columns: rules.filter((r) => r.type !== 'exclude' && SAMPLES[r.column]).map((r) => {
        const before = SAMPLES[r.column]
        const after = before.map((v) => transform(r, v))
        return { column: r.column, type: r.type, before, after, null_before: before.filter((v) => v === null).length, null_after: after.filter((v) => v === null).length }
      }),
    }
  }
  if (method === 'POST' && path === '/transfers/compat') {
    const t = b.target as { schema: string; table: string }
    if (t.schema !== 'TEST' || !['MUSTERI_ANON_20261001', 'MUSTERI_TRG'].includes(t.table)) fail('NOT_FOUND', 'Hedef tablo bulunamadı.', 404)
    const trigger = t.table === 'MUSTERI_TRG'
    const items: CompatItem[] = [{ column: 'AD', level: 'warn', message: 'Hedef kolon daha kısa (50 < 80).' }]
    if (trigger) items.push({ column: '*', level: 'error', message: 'Hedef tabloda etkin trigger var.' })
    return { status: trigger ? 'error' : 'warn', target_is_table: true, has_enabled_trigger: trigger, items }
  }
  if (method === 'POST' && path === '/transfers') {
    const r = b as unknown as TransferRequest
    const full = FULL_ROWS[r.source.table] ?? (TABLES[r.source.schema] ?? []).find(([t]) => t === r.source.table)?.[1] ?? 250_000
    const total = r.full_table ? full : Math.floor(full / 10 ** Math.min(r.filters.length, 3))
    const { source: srcConn, target: tgtConn } = conns
    if (!srcConn || !tgtConn) return fail('DB_DISCONNECTED', 'Kaynak ve hedef bağlantıları gerekli.', 409)
    if (r.confirm_target !== `${r.target.schema}.${r.target.table}`) fail('VALIDATION', 'body.confirm_target', 422)
    if (total > ROW_LIMIT) fail('LIMIT_EXCEEDED', r.full_table ? 'Tablo 5.000.000 satırın üzerinde; tamamı taşınamaz. Filtre girerek kümeyi daraltın.' : 'Seçiminiz 5.000.000 satırı aşıyor; lütfen filtreyi daraltın.', 422)
    if (r.target.mode === 'create' && (TABLES[r.target.schema] ?? []).some(([t]) => t === r.target.table)) fail('TABLE_EXISTS', 'Hedefte aynı adlı bir tablo zaten var.', 409)
    const id = crypto.randomUUID()
    jobs.set(id, {
      job: { ...baseJob(id, { status: 'running', source_alias: srcConn.alias, source_schema: r.source.schema, source_table: r.source.table, target_alias: tgtConn.alias, target_schema: r.target.schema, target_table: r.target.table, mode: r.target.mode, filters: r.full_table ? [] : r.filters, total_rows: total, created_at: now(), started_at: now(), finished_at: null }), columns: jobCols(r.rules) },
      startMs: Date.now(), rate: 40_000, frozen: false,
    })
    tracked.push({ id: tracked.length + 1, job_id: id, owner: user!.username, alias: tgtConn.alias, schema: r.target.schema, table: r.target.table, object_id: 200000 + tracked.length, object_created: now(), verified_visible_at: null, table_status: 'active', expires_at: new Date(Date.now() + 7 * 86_400_000).toISOString().slice(0, 19) + 'Z', last_checked_at: null })
    return { job_id: id }
  }
  if (method === 'GET' && path === '/transfers') return { items: [...jobs.values()].map((j) => ({ ...view(j), columns: undefined })).sort((a, c) => c.created_at.localeCompare(a.created_at)) }
  if (method === 'POST' && path === '/anon/reveal') {
    const rule = String(b.rule)
    const values = Array.isArray(b.values) ? (b.values as unknown[]).map((v) => (rule === 'text_token' ? String(v) : String(v).trim())) : []
    if (!['num_token', 'text_token', 'date_token'].includes(rule)) fail('VALIDATION', 'body.rule', 422)
    if (!values.length || values.length > 100 || values.some((v) => v.length > 4000)) fail('VALIDATION', 'body.values', 422)
    return {
      rule,
      items: values.map((v) => {
        if (rule === 'text_token') return { value: v, original: text(v, -1), error: null }
        if (rule === 'num_token') return /^-?\d+(\.\d+)?$/.test(v) ? { value: v, original: num(v, -1), error: null } : { value: v, original: null, error: 'Geçerli bir sayı değil.' }
        const o = dateToken(v, -1)
        return o ? { value: v, original: o, error: null } : { value: v, original: null, error: 'Geçerli bir tarih değil.' }
      }),
    }
  }
  if ((m = path.match(/^\/transfers\/([^/]+)(\/cancel)?$/))) {
    const j = jobs.get(m[1]) ?? fail('NOT_FOUND', 'İş bulunamadı.', 404)
    if (m[2]) {
      if (!j.frozen && !j.cancelMs && view(j).status === 'running') j.cancelMs = Date.now()
    }
    return view(j)
  }

  if (method === 'GET' && path === '/cleanup') return { items: tracked.filter((t) => !query.status || t.table_status === query.status) }
  if (method === 'GET' && path === '/cleanup/drop-script') {
    const due = tracked.filter((t) => t.table_status === 'expired_present')
    if (!due.length) fail('NOT_FOUND', 'Silinmeye uygun tablo yok.', 404)
    return `-- VeriKöprü: süresi dolmuş tabloları silme betiği\n${due.map((t) => `DROP TABLE "${t.schema}"."${t.table}";`).join('\n')}\n`
  }
  if ((m = path.match(/^\/cleanup\/check\/(.+)$/)) && method === 'POST') {
    const t = tracked.find((x) => x.job_id === m![1]) ?? fail('NOT_FOUND', 'Kayıt bulunamadı.', 404)
    t.last_checked_at = now()
    return t
  }
  return fail('NOT_FOUND', 'Bilinmeyen yol.', 404)
}
