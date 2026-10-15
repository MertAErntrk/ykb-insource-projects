import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ConnectionInfo, Role } from '../api/types.ts'
import { WIZARD_STORAGE_KEY, WizardProvider } from '../state/wizard.tsx'
import { Connect } from './Connect.tsx'

vi.mock('../api/endpoints.ts', () => ({
  listAliases: vi.fn(),
  dbStatus: vi.fn(),
  connectDb: vi.fn(),
  disconnectDb: vi.fn(),
}))
import { connectDb, dbStatus, listAliases } from '../api/endpoints.ts'

const info = (role: Role, alias: string): ConnectionInfo => ({ role, alias, db_version: '19.21.0.0', user: 'DEMO', mode: 'thin', direct_path: true, max_ident_len: 128 })

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/connect']}>
      <WizardProvider>
        <Routes>
          <Route path="/connect" element={<Connect />} />
          <Route path="/table" element={<p>tablo sayfası</p>} />
        </Routes>
      </WizardProvider>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  sessionStorage.clear()
  vi.mocked(listAliases).mockResolvedValue({
    aliases: [
      { alias: 'DEMODEV', host: 'dev.local', port: 1521, service_name: 'DEMODEV', target_allowed: true },
      { alias: 'DEMOLIVE', host: 'live.local', port: 1521, service_name: 'DEMOLIVE', target_allowed: false },
    ],
  })
  vi.mocked(dbStatus).mockResolvedValue({ source: null, target: null })
})
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('Connect', () => {
  it('hedef listesinde canlı veritabanı yok, kaynakta rozetle var', async () => {
    const user = userEvent.setup()
    renderPage()
    const target = await screen.findByRole('region', { name: 'Hedef' })
    await user.click(within(target).getByRole('combobox', { name: 'Veritabanı' }))
    expect(await screen.findByRole('option', { name: /DEMODEV/ })).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: /DEMOLIVE/ })).toBeNull()
    await user.keyboard('{Escape}')

    const source = screen.getByRole('region', { name: 'Kaynak' })
    await user.click(within(source).getByRole('combobox', { name: 'Veritabanı' }))
    expect(await screen.findByRole('option', { name: /DEMOLIVE.*Canlı · yalnız kaynak/ })).toBeInTheDocument()
  })

  it('bağlanınca sürüm görünür ve sessionStorage şifre içermez', async () => {
    vi.mocked(connectDb).mockResolvedValue(info('source', 'DEMODEV'))
    const user = userEvent.setup()
    renderPage()
    const source = await screen.findByRole('region', { name: 'Kaynak' })
    await user.click(within(source).getByRole('combobox', { name: 'Veritabanı' }))
    await user.click(await screen.findByRole('option', { name: /DEMODEV/ }))
    await user.type(within(source).getByLabelText('Kullanıcı adı'), 'DEMO')
    await user.type(within(source).getByLabelText('Şifre'), 'gizli-sifre-123')
    await user.click(within(source).getByRole('button', { name: 'Bağlan' }))

    expect(await within(source).findByText('19.21.0.0')).toBeInTheDocument()
    expect(within(source).getByText('Açık')).toBeInTheDocument()
    expect(connectDb).toHaveBeenCalledWith({ role: 'source', alias: 'DEMODEV', user: 'DEMO', password: 'gizli-sifre-123' })
    await waitFor(() => expect(sessionStorage.getItem(WIZARD_STORAGE_KEY)).toContain('19.21.0.0'))
    expect(JSON.stringify({ ...sessionStorage })).not.toContain('gizli-sifre-123')
  })

  it('iki taraf bağlanmadan İleri kapalı; ikisi de bağlıysa açılır ve aynı alias bandı çıkar', async () => {
    vi.mocked(dbStatus).mockResolvedValue({ source: info('source', 'DEMODEV'), target: null })
    const user = userEvent.setup()
    renderPage()
    expect(await screen.findByText('19.21.0.0')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'İleri' })).toBeDisabled()
    cleanup()

    vi.mocked(dbStatus).mockResolvedValue({ source: info('source', 'DEMODEV'), target: info('target', 'DEMODEV') })
    renderPage()
    expect(await screen.findByText(/aynı veritabanı \(DEMODEV\)/)).toBeInTheDocument()
    const next = screen.getByRole('button', { name: 'İleri' })
    expect(next).toBeEnabled()
    await user.click(next)
    expect(await screen.findByText('tablo sayfası')).toBeInTheDocument()
  })
})
