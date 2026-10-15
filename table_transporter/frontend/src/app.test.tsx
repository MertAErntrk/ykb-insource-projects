import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { resetMock } from './api/mock.ts'
import { SearchableSelect } from './components/ui/index.ts'
import { AppRoutes } from './router.tsx'
import { AuthProvider } from './state/auth.tsx'
import { WIZARD_STORAGE_KEY, WizardProvider, serializeWizard, initialWizardState, useWizard } from './state/wizard.tsx'

afterEach(() => {
  cleanup()
  vi.unstubAllEnvs()
})

describe('SearchableSelect', () => {
  const options = [{ value: 'DEMO', label: 'DEMO' }, { value: 'CRM', label: 'CRM' }, { value: 'HR', label: 'HR' }]
  function Harness({ onPick }: { onPick: (v: string) => void }) {
    const [v, setV] = useState<string | null>(null)
    return <SearchableSelect label="Şema" options={options} value={v} onChange={(x) => { setV(x); onPick(x) }} />
  }

  it('ok tuşları ve Enter ile seçer', async () => {
    const onPick = vi.fn()
    const user = userEvent.setup()
    render(<Harness onPick={onPick} />)
    const box = screen.getByRole('combobox', { name: 'Şema' })
    await user.click(box)
    expect(box).toHaveAttribute('aria-expanded', 'true')
    await user.keyboard('{ArrowDown}{ArrowDown}{Enter}')
    expect(onPick).toHaveBeenCalledWith('HR')
    expect(box).toHaveValue('HR')
    expect(box).toHaveAttribute('aria-expanded', 'false')
  })

  it('Esc listeyi seçim yapmadan kapatır; yazınca süzer', async () => {
    const onPick = vi.fn()
    const user = userEvent.setup()
    render(<Harness onPick={onPick} />)
    const box = screen.getByRole('combobox', { name: 'Şema' })
    await user.type(box, 'cr')
    expect(screen.getAllByRole('option')).toHaveLength(1)
    await user.keyboard('{Escape}')
    expect(box).toHaveAttribute('aria-expanded', 'false')
    expect(onPick).not.toHaveBeenCalled()
  })
})

describe('Giriş sayfası (mock)', () => {
  beforeEach(() => {
    vi.stubEnv('VITE_API_MOCK', '1')
    sessionStorage.clear()
    resetMock()
  })
  const renderLogin = () =>
    render(
      <MemoryRouter initialEntries={['/login']}>
        <WizardProvider>
          <AuthProvider>
            <AppRoutes />
          </AuthProvider>
        </WizardProvider>
      </MemoryRouter>,
    )

  it('başarılı girişte /connect sayfasına geçer', async () => {
    const user = userEvent.setup()
    renderLogin()
    await user.type(screen.getByLabelText('Kullanıcı adı'), 'mert')
    await user.type(screen.getByLabelText('Şifre'), 'Gizli123')
    await user.click(screen.getByRole('button', { name: 'Giriş' }))
    expect(await screen.findByRole('heading', { name: 'Bağlantılar' })).toBeInTheDocument()
  })

  it.each([
    ['mert', 'hatali', /Kullanıcı adı veya şifre hatalı/],
    ['yetkisiz', 'x', /yetkiniz yok/],
    ['kilitli', 'x', /5 dakika/],
    ['ldapyok', 'x', /sunucusuna ulaşılamadı/],
  ])('hatalı girişte Türkçe mesaj gösterir (%s)', async (name, pw, msg) => {
    const user = userEvent.setup()
    renderLogin()
    await user.type(screen.getByLabelText('Kullanıcı adı'), name)
    await user.type(screen.getByLabelText('Şifre'), pw)
    await user.click(screen.getByRole('button', { name: 'Giriş' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(msg)
    expect(screen.queryByRole('heading', { name: 'Bağlantılar' })).not.toBeInTheDocument()
  })

  it('boş alanlarda istek atmadan alan hatası gösterir', async () => {
    const user = userEvent.setup()
    renderLogin()
    await user.click(screen.getByRole('button', { name: 'Giriş' }))
    expect(await screen.findByText('Kullanıcı adınızı girin.')).toBeInTheDocument()
    expect(screen.getByText('Şifrenizi girin.')).toBeInTheDocument()
  })
})

describe('Sihirbaz durumu', () => {
  function Harness() {
    const w = useWizard()
    return (
      <button
        onClick={() =>
          w.setConnection('source', {
            alias: 'DEMO_TEST',
            user: 'ALI',
            password: 'GIZLI-SIFRE-123',
            info: { role: 'source', alias: 'DEMO_TEST', db_version: '19', user: 'ALI', mode: 'thin', direct_path: true, max_ident_len: 128 },
          } as never)
        }
      >
        bağlan
      </button>
    )
  }

  it('sessionStorage a şifre yazmaz', async () => {
    sessionStorage.clear()
    const user = userEvent.setup()
    render(<WizardProvider><Harness /></WizardProvider>)
    await user.click(screen.getByRole('button', { name: 'bağlan' }))
    const stored = sessionStorage.getItem(WIZARD_STORAGE_KEY) ?? ''
    expect(stored).toContain('DEMO_TEST')
    expect(stored).not.toContain('GIZLI-SIFRE-123')
    expect(stored.toLowerCase()).not.toContain('password')
  })

  it('serializeWizard iç içe password anahtarlarını da atar', () => {
    const s = serializeWizard({ ...initialWizardState, filters: [{ column: 'A', op: 'eq', value: 'x', password: 'p' } as never] })
    expect(s).not.toContain('"p"')
  })
})
