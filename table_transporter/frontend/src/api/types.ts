// docs/API.md ile birebir. Değişiklik önce API.md'de yapılır.

export type ErrorCode =
  | 'AUTH_INVALID'
  | 'AUTH_FORBIDDEN'
  | 'AUTH_THROTTLED'
  | 'LDAP_UNAVAILABLE'
  | 'SESSION_EXPIRED'
  | 'CSRF_INVALID'
  | 'VALIDATION'
  | 'DB_CONNECT_FAILED'
  | 'DB_DISCONNECTED'
  | 'DB_PERMISSION'
  | 'LIMIT_EXCEEDED'
  | 'TABLE_EXISTS'
  | 'TARGET_LOCKED'
  | 'TARGET_DENIED'
  | 'INCOMPATIBLE'
  | 'RULE_INVALID'
  | 'TRIGGER_PRESENT'
  | 'JOB_RUNNING'
  | 'NOT_FOUND'
  | 'ORA_ERROR'
  | 'INTERNAL'

export interface ApiError {
  error: ErrorCode
  detail: string
}

// --- Kimlik ---
export interface User {
  username: string
  display_name: string
  groups: string[]
  is_admin: boolean
}
export interface LoginRequest {
  username: string
  password: string
}
export interface SessionInfo {
  user: User
  csrf_token: string
}
export interface LogoutRequest {
  cancel_running?: boolean
}

// --- Oracle ---
export type Role = 'source' | 'target'

export interface TnsAlias {
  alias: string
  host: string
  port: number
  service_name: string
  target_allowed: boolean
}
export interface TnsAliasesResponse {
  aliases: TnsAlias[]
}

export interface ConnectRequest {
  role: Role
  alias: string
  user: string
  password: string
}
export interface ConnectionInfo {
  role: Role
  alias: string
  db_version: string
  user: string
  mode: 'thin' | 'thick'
  direct_path: boolean
  max_ident_len: number
}
export interface DbStatus {
  source: ConnectionInfo | null
  target: ConnectionInfo | null
}
export interface SchemasResponse {
  schemas: string[]
}

export interface TableSummary {
  schema: string
  name: string
}
export interface TablesResponse {
  items: TableSummary[]
  total: number
  limit: number
  offset: number
}

export interface ColumnInfo {
  position: number
  name: string
  data_type: string
  char_length: number
  char_used: 'B' | 'C' | null
  data_length: number
  precision: number | null
  scale: number | null
  nullable: boolean
  virtual: boolean
  identity: boolean
  hidden: boolean
  default: string | null
  is_pk_or_unique: boolean
  supported: boolean
  unsupported_reason: string | null
}
export interface ColumnsResponse {
  columns: ColumnInfo[]
}

// --- Filtre ve kurallar ---
export type FilterScalar = string | number
export type FilterOp =
  | 'eq'
  | 'ne'
  | 'lt'
  | 'le'
  | 'gt'
  | 'ge'
  | 'like'
  | 'in'
  | 'between'
  | 'is_null'
  | 'is_not_null'

export interface Filter {
  column: string
  op: FilterOp
  value?: FilterScalar
  values?: FilterScalar[]
}

export type RuleType =
  | 'keep'
  | 'exclude'
  | 'null'
  | 'constant'
  | 'mask'
  | 'hmac'
  | 'num_token'
  | 'text_token'
  | 'date_token'
  | 'date_shift'
  | 'random'

export type Rule =
  | { column: string; type: 'keep'; params: Record<string, never> }
  | { column: string; type: 'exclude'; params: Record<string, never> }
  | { column: string; type: 'null'; params: Record<string, never> }
  | { column: string; type: 'constant'; params: { value: string } }
  | { column: string; type: 'mask'; params: { show_last: number } }
  | { column: string; type: 'hmac'; params: Record<string, never> }
  | { column: string; type: 'num_token'; params: Record<string, never> }
  | { column: string; type: 'text_token'; params: Record<string, never> }
  | { column: string; type: 'date_token'; params: Record<string, never> }
  | { column: string; type: 'date_shift'; params: { anchor: string; max_days: number } }
  | { column: string; type: 'random'; params: Record<string, never> }

// --- Taşıma ---
export interface TableRef {
  schema: string
  table: string
}

export interface CountRequest {
  source: TableRef
  filters: Filter[]
  full_table?: boolean
}
export interface CountResponse {
  count: number | null
  limit: number
  over_limit: boolean
  timed_out: boolean
}

export interface PreviewRequest {
  source: TableRef
  filters: Filter[]
  full_table?: boolean
  rules: Rule[]
}
export type PreviewValue = string | number | null
export interface PreviewColumn {
  column: string
  type: RuleType
  before: PreviewValue[]
  after: PreviewValue[]
  null_before: number
  null_after: number
}
export interface PreviewResponse {
  columns: PreviewColumn[]
}

export type CompatLevel = 'ok' | 'warn' | 'error'
export interface CompatRequest {
  source: TableRef
  target: TableRef
  rules: Rule[]
}
export interface CompatItem {
  column: string
  level: CompatLevel
  message: string
}
export interface CompatResponse {
  status: CompatLevel
  target_is_table: boolean
  has_enabled_trigger: boolean
  items: CompatItem[]
}

export type TransferMode = 'create' | 'append'
export interface TransferRequest {
  source: TableRef
  target: TableRef & { mode: TransferMode }
  rules: Rule[]
  filters: Filter[]
  full_table?: boolean
  confirm_target: string
  confirm_unmasked_live: boolean
}
export interface TransferCreated {
  job_id: string
}

export type JobStatus =
  | 'queued'
  | 'running'
  | 'cancelling'
  | 'done'
  | 'failed'
  | 'partial'
  | 'cancelled'

export interface JobColumn {
  column_name: string
  rule_type: RuleType
  params: Record<string, unknown>
  source_type: string
  position: number
}

export interface Job {
  id: string
  owner: string
  status: JobStatus
  source_alias: string
  source_schema: string
  source_table: string
  target_alias: string
  target_schema: string
  target_table: string
  mode: TransferMode
  filters: Filter[]
  total_rows: number | null
  rows_read: number
  rows_written: number
  rows_per_sec: number | null
  eta_sec: number | null
  error_code: ErrorCode | null
  error_detail: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  columns?: JobColumn[] // yalnız GET /transfers/{id}
}
export interface JobsResponse {
  items: Job[]
}

// --- SSE olayları ---
export interface ProgressEvent {
  job_id: string
  status: 'running'
  rows_read: number
  rows_written: number
  total_rows: number | null
  rows_per_sec: number | null
  eta_sec: number | null
}
export interface CancellingEvent {
  job_id: string
  status: 'cancelling'
}
export interface DoneEvent {
  job_id: string
  status: 'done'
  rows_written: number
  finished_at: string
  target: { schema: string; table: string }
}
export interface FailedEvent {
  job_id: string
  status: 'failed' | 'partial'
  error: ErrorCode
  detail: string
  rows_written: number
}
export interface CancelledEvent {
  job_id: string
  status: 'cancelled'
  rows_written: number
}
export type SseEventName = 'progress' | 'cancelling' | 'done' | 'failed' | 'cancelled'
export interface SseEventMap {
  progress: ProgressEvent
  cancelling: CancellingEvent
  done: DoneEvent
  failed: FailedEvent
  cancelled: CancelledEvent
}

// --- Temizlik ---
export type TableStatus = 'active' | 'expired_present' | 'deleted_verified' | 'unverifiable'
export interface TrackedTable {
  id: number
  job_id: string
  owner: string
  alias: string
  schema: string
  table: string
  object_id: number
  object_created: string
  verified_visible_at: string | null
  table_status: TableStatus
  expires_at: string
  last_checked_at: string | null
}
export interface CleanupResponse {
  items: TrackedTable[]
}

// --- Değer çözme (num_token / text_token / date_token) ---
export type RevealRule = 'num_token' | 'text_token' | 'date_token'
export interface RevealRequest {
  rule: RevealRule
  values: string[] // 1-100 değer, her biri en çok 4000 karakter
}
export interface RevealItem {
  value: string
  original: string | null
  error: string | null
}
export interface RevealResponse {
  rule: string
  items: RevealItem[]
}
