import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, SESSION_EXPIRED_EVENT, request, setCsrfToken } from './client.ts'

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

beforeEach(() => {
  vi.stubEnv('VITE_API_MOCK', '')
})
afterEach(() => {
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
  setCsrfToken(null)
})

describe('api client', () => {
  it('{error, detail} gövdesini ApiError olarak fırlatır', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json(401, { error: 'AUTH_INVALID', detail: 'Hatalı şifre' })))
    const err = await request('POST', '/auth/login', { body: {} }).catch((e) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err).toMatchObject({ error: 'AUTH_INVALID', detail: 'Hatalı şifre', status: 401 })
  })

  it('JSON olmayan hata gövdesini INTERNAL yapar', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('<html>502</html>', { status: 502 })))
    await expect(request('GET', '/db/status')).rejects.toMatchObject({ error: 'INTERNAL', status: 502 })
  })

  it('ağ hatasında Türkçe INTERNAL hatası verir', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    await expect(request('GET', '/db/status')).rejects.toMatchObject({ error: 'INTERNAL', status: 0 })
  })

  it('değiştiren isteklere X-CSRF-Token ekler, GET isteğine eklemez; çerez same-origin gider', async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(json(200, {})))
    vi.stubGlobal('fetch', fetchMock)
    setCsrfToken('csrf-abc')
    await request('POST', '/transfers/count', { body: { a: 1 } })
    await request('GET', '/tns/aliases', { query: { q: 'demo' } })
    const [postUrl, postInit] = fetchMock.mock.calls[0]
    const [getUrl, getInit] = fetchMock.mock.calls[1]
    expect(postUrl).toBe('/api/transfers/count')
    expect(postInit.headers['X-CSRF-Token']).toBe('csrf-abc')
    expect(postInit.credentials).toBe('same-origin')
    expect(getUrl).toBe('/api/tns/aliases?q=demo')
    expect(getInit.headers['X-CSRF-Token']).toBeUndefined()
  })

  it('SESSION_EXPIRED gelince oturum-sonu olayı yayınlar; 204 undefined döner', async () => {
    const onExpired = vi.fn()
    window.addEventListener(SESSION_EXPIRED_EVENT, onExpired)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json(401, { error: 'SESSION_EXPIRED', detail: 'Oturum bitti' })))
    await expect(request('GET', '/db/status')).rejects.toMatchObject({ error: 'SESSION_EXPIRED' })
    expect(onExpired).toHaveBeenCalledTimes(1)
    window.removeEventListener(SESSION_EXPIRED_EVENT, onExpired)

    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 204 })))
    await expect(request('DELETE', '/db/connect/source')).resolves.toBeUndefined()
  })
})
