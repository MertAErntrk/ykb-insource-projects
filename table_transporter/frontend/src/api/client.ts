import { mockRequest } from './mock.ts'
import type { ApiError as ApiErrorBody, ErrorCode } from './types.ts'

/** SESSION_EXPIRED alındığında window üzerinde yayınlanır; AuthProvider dinler. */
export const SESSION_EXPIRED_EVENT = 'tt:session-expired'

let csrfToken: string | null = null
export function setCsrfToken(token: string | null) {
  csrfToken = token
}

export const isMock = () => import.meta.env.VITE_API_MOCK === '1'

export class ApiError extends Error {
  error: ErrorCode
  detail: string
  status: number
  constructor(error: ErrorCode, detail: string, status: number) {
    super(detail)
    this.name = 'ApiError'
    this.error = error
    this.detail = detail
    this.status = status
  }
}

export type Query = Record<string, string | number | boolean | undefined | null>
export interface RequestOptions {
  query?: Query
  body?: unknown
  signal?: AbortSignal
}

function notify(err: unknown) {
  if (err instanceof ApiError && err.error === 'SESSION_EXPIRED') {
    window.dispatchEvent(new CustomEvent(SESSION_EXPIRED_EVENT))
  }
}

async function toApiError(res: Response): Promise<ApiError> {
  try {
    const body = (await res.json()) as Partial<ApiErrorBody>
    if (typeof body.error === 'string' && typeof body.detail === 'string') {
      return new ApiError(body.error, body.detail, res.status)
    }
  } catch {
    // gövde JSON değil
  }
  return new ApiError('INTERNAL', 'Beklenmeyen bir hata oluştu. Biraz sonra yeniden deneyin.', res.status)
}

function buildUrl(path: string, query?: Query) {
  const params = new URLSearchParams()
  for (const [k, v] of Object.entries(query ?? {})) {
    if (v !== undefined && v !== null && v !== '') params.set(k, String(v))
  }
  const qs = params.toString()
  return `/api${path}${qs ? `?${qs}` : ''}`
}

async function send(method: string, path: string, opts: RequestOptions): Promise<Response> {
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (opts.body !== undefined) headers['Content-Type'] = 'application/json'
  if (method !== 'GET' && method !== 'HEAD' && csrfToken) headers['X-CSRF-Token'] = csrfToken
  let res: Response
  try {
    res = await fetch(buildUrl(path, opts.query), {
      method,
      headers,
      credentials: 'same-origin',
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      signal: opts.signal,
    })
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') throw e
    throw new ApiError('INTERNAL', 'Sunucuya ulaşılamadı. Bağlantınızı kontrol edip yeniden deneyin.', 0)
  }
  if (!res.ok) {
    const err = await toApiError(res)
    notify(err)
    throw err
  }
  return res
}

export async function request<T>(method: string, path: string, opts: RequestOptions = {}): Promise<T> {
  if (isMock()) {
    try {
      return (await mockRequest(method, path, opts.query ?? {}, opts.body)) as T
    } catch (e) {
      notify(e)
      throw e
    }
  }
  const res = await send(method, path, opts)
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

export async function requestText(method: string, path: string, opts: RequestOptions = {}): Promise<string> {
  if (isMock()) return String(await request<unknown>(method, path, opts))
  return (await send(method, path, opts)).text()
}
