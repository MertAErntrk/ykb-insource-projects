import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/client.ts'
import type { ConnectionInfo, Role } from '../api/types.ts'
import { WIZARD_STORAGE_KEY, WizardProvider } from '../state/wizard.tsx'
import { Summary } from './Summary.tsx'

vi.mock('../api/endpoints.ts', () => ({
  listAliases: vi.fn(),
  startTransfer: vi.fn(),
}))
import { listAliases, startTransfer } from '../api/endpoints.ts'

const info = (role: Role): ConnectionInfo => ({ role, alias: 'DEMODEV', db_version: '19.21.0.0', user: 'DEMO', mode: 'thin', direct_path: true, max_ident_len: 128 })
const RULES = [
  { column: 'AD', type: 'hmac', params: {} },
  { column: 'BOLGE', type: 'keep', params: {} },
  { column: 'NOTLAR', type: 'exclude', params: {} },
]
const HMAC_ONLY = [RULES[0]]
const TARGET_TEXT = 'DEMO.MUSTERI_ANON_20261015'

function renderPage(rules: unknown[] = RULES, extra: Record<string, unknown> = {}) {
  sessionStorage.setItem(
    WIZARD_STORAGE_KEY,
    JSON.stringify({
      source: { alias: 'DEMOPROD', user: 'DEMO', info: info('source') },
      target: { alias: 'DEMODEV', user: 'DEMO', info: info('target') },
      table: { schema: 'DEMO', table: 'MUSTERI' },
      filters: [{ column: 'BOLGE', op: 'eq', value: 'IST' }],
      count: { count: 1234567, limit: 5000000, over_limit: false, timed_out: false },
      rules,
      targetSetting: { mode: 'create', schema: 'DEMO', table: 'MUSTERI_ANON_20261015' },
      ...extra,
    }),
  )
  return render(
    <MemoryRouter initialEntries={['/summary']}>
      <WizardProvider>
        <Routes>
          <Route path="/summary" element={<Summary />} />
          <Route path="/jobs/:id" element={<p>iş sayfası</p>} />
          <Route path="/table" element={<p>tablo sayfası</p>} />
        </Routes>
      </WizardProvider>
    </MemoryRouter>,
  )
}
const startBtn = () => screen.getByRole('button', { name: 'Taşımayı başlat' })
const confirmInput = () => screen.getByRole('textbox', { name: 'Onaylamak için hedefi yazın' })
const CHECK = 'Canlı ortamdan anonimleştirilmemiş kolonlar taşındığını biliyorum ve onaylıyorum'

beforeEach(() => {
  sessionStorage.clear()
  vi.mocked(listAliases).mockResolvedValue({ aliases: [{ alias: 'DEMOPROD', host: 'h', port: 1521, service_name: 's', target_allowed: false }] })
})
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('Summary', () => {
  it('özet kartlarını insan diliyle gösterir', async () => {
    renderPage()
    expect(await screen.findByText('1.234.567')).toBeInTheDocument()
    expect(screen.getByText('BOLGE = IST')).toBeInTheDocument()
    expect(screen.getByText('Özetle (HMAC)')).toBeInTheDocument()
    expect(screen.getByText('BOLGE', { selector: 'strong' })).toBeInTheDocument()
    expect(screen.getByText('NOTLAR', { selector: 'strong' })).toBeInTheDocument()
  })

  it('tam tabloda Kapsam satırı ve full_table gövdesi', async () => {
    vi.mocked(startTransfer).mockResolvedValue({ job_id: 'j3' })
    const user = userEvent.setup()
    renderPage(HMAC_ONLY, { filters: [], fullTable: true, count: { count: 120000, limit: 5000000, over_limit: false, timed_out: false } })
    expect(await screen.findByText('Tablonun tamamı (120.000 satır)')).toBeInTheDocument()
    await user.type(confirmInput(), TARGET_TEXT)
    await user.click(startBtn())
    await screen.findByText('iş sayfası')
    expect(vi.mocked(startTransfer).mock.calls[0][0]).toMatchObject({ filters: [], full_table: true })
  })

  it('onay metni eşleşmeden başlat kapalıdır', async () => {
    const user = userEvent.setup()
    renderPage(HMAC_ONLY)
    expect(startBtn()).toBeDisabled()
    await user.type(confirmInput(), 'DEMO.MUSTERI')
    expect(startBtn()).toBeDisabled()
    await user.clear(confirmInput())
    await user.type(confirmInput(), TARGET_TEXT)
    expect(startBtn()).toBeEnabled()
  })

  it('canlı kaynak ve keep varsa onay kutusu zorunludur; gövde alanları doğrudur', async () => {
    vi.mocked(startTransfer).mockResolvedValue({ job_id: 'j1' })
    const user = userEvent.setup()
    renderPage()
    const box = await screen.findByRole('checkbox', { name: CHECK })
    await user.type(confirmInput(), TARGET_TEXT)
    expect(startBtn()).toBeDisabled()
    await user.click(box)
    await user.click(startBtn())
    expect(await screen.findByText('iş sayfası')).toBeInTheDocument()
    expect(startTransfer).toHaveBeenCalledWith({
      source: { schema: 'DEMO', table: 'MUSTERI' },
      target: { schema: 'DEMO', table: 'MUSTERI_ANON_20261015', mode: 'create' },
      rules: RULES,
      filters: [{ column: 'BOLGE', op: 'eq', value: 'IST' }],
      full_table: false,
      confirm_target: TARGET_TEXT,
      confirm_unmasked_live: true,
    })
  })

  it('keep yoksa onay kutusu çıkmaz ve confirm_unmasked_live false gider', async () => {
    vi.mocked(startTransfer).mockResolvedValue({ job_id: 'j2' })
    const user = userEvent.setup()
    renderPage(HMAC_ONLY)
    await waitFor(() => expect(listAliases).toHaveBeenCalled())
    expect(screen.queryByRole('checkbox')).toBeNull()
    await user.type(confirmInput(), TARGET_TEXT)
    await user.click(startBtn())
    await screen.findByText('iş sayfası')
    expect(vi.mocked(startTransfer).mock.calls[0][0].confirm_unmasked_live).toBe(false)
  })

  it('LIMIT_EXCEEDED Türkçe mesaj ve Tablo adımına dön bağlantısı gösterir', async () => {
    vi.mocked(startTransfer).mockRejectedValue(new ApiError('LIMIT_EXCEEDED', 'limit', 422))
    const user = userEvent.setup()
    renderPage(HMAC_ONLY)
    await user.type(confirmInput(), TARGET_TEXT)
    await user.click(startBtn())
    expect(await screen.findByText(/Satır sayısı sınırı aşıldı/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Tablo adımına dön' }))
    expect(await screen.findByText('tablo sayfası')).toBeInTheDocument()
  })
})
