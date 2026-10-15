import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { CompatResponse, ConnectionInfo, Role } from '../api/types.ts'
import { WIZARD_STORAGE_KEY, WizardProvider } from '../state/wizard.tsx'
import { Target } from './Target.tsx'

vi.mock('../api/endpoints.ts', () => ({
  listSchemas: vi.fn(),
  listTables: vi.fn(),
  checkCompat: vi.fn(),
}))
import { checkCompat, listSchemas, listTables } from '../api/endpoints.ts'

const info = (role: Role, max = 128): ConnectionInfo => ({ role, alias: 'DEMODEV', db_version: '19.21.0.0', user: 'DEMO', mode: 'thin', direct_path: true, max_ident_len: max })
const empty = { items: [], total: 0, limit: 50, offset: 0 }
const compatOf = (over: Partial<CompatResponse>): CompatResponse => ({ status: 'ok', target_is_table: true, has_enabled_trigger: false, items: [], ...over })

function renderPage(opts: { max?: number; sourceTable?: string; targetSetting?: unknown } = {}) {
  sessionStorage.setItem(
    WIZARD_STORAGE_KEY,
    JSON.stringify({
      source: { alias: 'DEMODEV', user: 'DEMO', info: info('source') },
      target: { alias: 'DEMODEV', user: 'DEMO', info: info('target', opts.max) },
      table: { schema: 'DEMO', table: opts.sourceTable ?? 'MUSTERI' },
      rules: [{ column: 'AD', type: 'keep', params: {} }],
      targetSetting: opts.targetSetting ?? null,
    }),
  )
  return render(
    <MemoryRouter initialEntries={['/target']}>
      <WizardProvider>
        <Routes>
          <Route path="/target" element={<Target />} />
          <Route path="/summary" element={<p>özet sayfası</p>} />
        </Routes>
      </WizardProvider>
    </MemoryRouter>,
  )
}
const nameInput = () => screen.getByRole('textbox', { name: 'Tablo adı' }) as HTMLInputElement
const next = () => screen.getByRole('button', { name: 'İleri' })

beforeEach(() => {
  sessionStorage.clear()
  vi.mocked(listSchemas).mockResolvedValue({ schemas: ['DEMO', 'DIGER'] })
  vi.mocked(listTables).mockResolvedValue(empty)
})
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('Target', () => {
  it('varsayılan ad KAYNAK_ANON_tarih; ad boşsa İleri açılır ve /summary a gider', async () => {
    const user = userEvent.setup()
    renderPage()
    expect(nameInput().value).toMatch(/^MUSTERI_ANON_\d{8}$/)
    expect(screen.getByRole('textbox', { name: 'Şema' })).toHaveValue('DEMO')
    await waitFor(() => expect(next()).toBeEnabled())
    await user.click(next())
    expect(await screen.findByText('özet sayfası')).toBeInTheDocument()
    expect(JSON.parse(sessionStorage.getItem(WIZARD_STORAGE_KEY)!).targetSetting).toMatchObject({ mode: 'create', schema: 'DEMO', table: expect.stringMatching(/^MUSTERI_ANON_/) })
  })

  it('ad max_ident_len değerine kırpılır, tarih eki korunur', () => {
    renderPage({ max: 20, sourceTable: 'COK_UZUN_TABLO_ADI' })
    expect(nameInput().value).toHaveLength(20)
    expect(nameInput().value).toMatch(/^COK_UZ_ANON_\d{8}$/)
  })

  it('geçersiz ad hata gösterir ve İleri kapalıdır', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.clear(nameInput())
    await user.type(nameInput(), '1a-b')
    expect(await screen.findByText('Ad harfle başlamalı; yalnız A-Z, 0-9, _, $ ve # içerebilir.')).toBeInTheDocument()
    expect(nameInput().value).toBe('1A-B')
    expect(next()).toBeDisabled()
  })

  it('dolu ad uyarısı gösterir', async () => {
    vi.mocked(listTables).mockResolvedValue({ ...empty, items: [{ schema: 'DEMO', name: 'MUSTERI_X' }] })
    const user = userEvent.setup()
    renderPage()
    await user.clear(nameInput())
    await user.type(nameInput(), 'musteri_x')
    expect(await screen.findByText('Bu adla bir tablo var; başka bir ad seçin.')).toBeInTheDocument()
    expect(next()).toBeDisabled()
  })

  it('ekleme: compat hatası İleri yi kilitler, trigger mesajı görünür', async () => {
    vi.mocked(checkCompat).mockResolvedValue(compatOf({ status: 'error', has_enabled_trigger: true, items: [{ column: 'AD', level: 'error', message: 'Tip uyumsuz' }] }))
    renderPage({ targetSetting: { mode: 'append', schema: 'DIGER', table: 'HEDEF' } })
    expect(await screen.findByText('Hedef tabloda etkin trigger var; güvenlik nedeniyle bu tabloya eklenemez.')).toBeInTheDocument()
    expect(screen.getByText('Tip uyumsuz')).toBeInTheDocument()
    expect(screen.getByText(/Başka bir şemaya yazıyorsunuz/)).toBeInTheDocument()
    expect(next()).toBeDisabled()
    expect(checkCompat).toHaveBeenCalledWith({ source: { schema: 'DEMO', table: 'MUSTERI' }, target: { schema: 'DIGER', table: 'HEDEF' }, rules: [{ column: 'AD', type: 'keep', params: {} }] })
  })

  it('ekleme: uyumlu sonuçta İleri açılır', async () => {
    vi.mocked(checkCompat).mockResolvedValue(compatOf({ status: 'warn', items: [{ column: 'AD', level: 'warn', message: 'Hedef kolon daha dar' }] }))
    renderPage({ targetSetting: { mode: 'append', schema: 'DEMO', table: 'HEDEF' } })
    expect(await screen.findByText('Hedef kolon daha dar')).toBeInTheDocument()
    expect(screen.queryByText(/Başka bir şemaya yazıyorsunuz/)).toBeNull()
    await waitFor(() => expect(next()).toBeEnabled())
  })
})
