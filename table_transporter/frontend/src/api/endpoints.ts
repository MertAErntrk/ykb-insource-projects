import { request, requestText } from './client.ts'
import type {
  CleanupResponse, ColumnsResponse, CompatRequest, CompatResponse, ConnectRequest, ConnectionInfo, CountRequest, CountResponse,
  DbStatus, Job, JobsResponse, LoginRequest, LogoutRequest, PreviewRequest, PreviewResponse, RevealRequest, RevealResponse, Role, SchemasResponse, SessionInfo,
  TableStatus, TablesResponse, TnsAliasesResponse, TransferCreated, TransferRequest, TrackedTable,
} from './types.ts'

const seg = encodeURIComponent

// --- Kimlik ---
export const login = (body: LoginRequest) => request<SessionInfo>('POST', '/auth/login', { body })
export const logout = (body: LogoutRequest = {}) => request<void>('POST', '/auth/logout', { body })
export const me = () => request<SessionInfo>('GET', '/auth/me')

// --- Oracle ---
export const health = () => request<{ status: 'ok' }>('GET', '/health')
export const listAliases = (q?: string) => request<TnsAliasesResponse>('GET', '/tns/aliases', { query: { q } })
export const connectDb = (body: ConnectRequest) => request<ConnectionInfo>('POST', '/db/connect', { body })
export const disconnectDb = (role: Role) => request<void>('DELETE', `/db/connect/${role}`)
export const dbStatus = () => request<DbStatus>('GET', '/db/status')
export const listSchemas = (role: Role, q?: string) => request<SchemasResponse>('GET', `/db/${role}/schemas`, { query: { q } })
export const listTables = (role: Role, p: { schema: string; q?: string; limit?: number; offset?: number }) =>
  request<TablesResponse>('GET', `/db/${role}/tables`, { query: p })
export const listColumns = (role: Role, schema: string, table: string) =>
  request<ColumnsResponse>('GET', `/db/${role}/tables/${seg(schema)}/${seg(table)}/columns`)

// --- Taşıma ---
export const countRows = (body: CountRequest, signal?: AbortSignal) => request<CountResponse>('POST', '/transfers/count', { body, signal })
export const previewRules = (body: PreviewRequest) => request<PreviewResponse>('POST', '/transfers/preview', { body })
export const checkCompat = (body: CompatRequest) => request<CompatResponse>('POST', '/transfers/compat', { body })
export const startTransfer = (body: TransferRequest) => request<TransferCreated>('POST', '/transfers', { body })
export const listJobs = (mine = true) => request<JobsResponse>('GET', '/transfers', { query: { mine } })
export const getJob = (id: string) => request<Job>('GET', `/transfers/${seg(id)}`)
export const cancelJob = (id: string) => request<Job>('POST', `/transfers/${seg(id)}/cancel`)

export const revealValues = (body: RevealRequest) => request<RevealResponse>('POST', '/anon/reveal', { body })

// --- Temizlik ---
export const listCleanup = (status?: TableStatus) => request<CleanupResponse>('GET', '/cleanup', { query: { status } })
/** SQL metnini döner (indirme için Blob'a çevrilir). */
export const getDropScript = (jobIds: string[]) => requestText('GET', '/cleanup/drop-script', { query: { job_ids: jobIds.join(',') } })
export const checkTracked = (jobId: string) => request<TrackedTable>('POST', `/cleanup/check/${seg(jobId)}`)
