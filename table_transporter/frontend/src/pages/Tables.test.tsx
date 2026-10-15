import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ColumnInfo, ConnectionInfo, CountResponse, Filter, Role } from '../api/types.ts'
import { WIZARD_STORAGE_KEY, WizardProvider } from '../state/wizard.tsx'
import { Tables } from './Tables.tsx'

vi.mock('../api/endpoints.ts', () => ({
  listSchemas: vi.fn(),
  listTables: vi.fn(),
  listColumns: vi.fn(),
  countRows: vi.fn(),
}))
import { countRows, listColumns, listSchemas, listTables } from '../api/endpoints.ts'

const info = (role: Role): ConnectionInfo => ({ role, alias: 'DEMODEV', db_version: '19.21.0.0', user: 'DEMO', mode: 'thin', direct_path: true, max_ident_len: 128 })
const col = (name: string, data_type: string, position: number): ColumnInfo => ({
  position, name, data_type, char_length: 0, char_used: null, data_length: 22, precision: null, scale: null, nullable: true,
  virtual: false, identity: false, hidden: false, default: null, is_pk_or_unique: false, supported: true, unsupported_reason: null,
})
const ok: CountResponse = { count: 1234567, limit: 5000000, over_limit: false, timed_out: false }

const ONE_FILTER: Filter[] = [{ column: 'ID', op: 'eq', value: 5 }]
function seed(count: CountResponse | null, filters: Filter[] = ONE_FILTER) {
  sessionStorage.setItem(
    WIZARD_STORAGE_KEY,
    JSON.stringify({
      source: { alias: 'DEMODEV', user: 'DEMO', info: info('source') },
      target: { alias: 'DEMODEV', user: 'DEMO', info: info('target') },
      table: { schema: 'DEMO', table: 'HESAP' },
      filters,
      count,
    }),
  )
}
function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/table']}>
      <WizardProvider>
        <Routes>
          <Route path="/table" element={<Tables />} />
          <Route path="/columns" element={<p>kolonlar sayfası</p>} />
        </Routes>
      </WizardProvider>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  sessionStorage.clear()
  vi.mocked(listSchemas).mockResolvedValue({ schemas: ['DEMO'] })
  vi.mocked(listTables).mockResolvedValue({ items: [{ schema: 'DEMO', name: 'HESAP' }, { schema: 'DEMO', name: 'MUSTERI' }], total: 2, limit: 100, offset: 0 })
  vi.mocked(listColumns).mockResolvedValue({ columns: [col('ID', 'NUMBER', 1), col('BOLGE', 'VARCHAR2', 2)] })
})
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('Tables', () => {
  it('Tablo açılır listesi seçili tabloyu gösterir ve listeden seçim yapılır', async () => {
    seed(null)
    const user = userEvent.setup()
    renderPage()
    const combo = await screen.findByRole('combobox', { name: 'Tablo' })
    await waitFor(() => expect(combo).toHaveValue('HESAP'))
    await user.click(combo)
    await user.click(await screen.findByRole('option', { name: 'MUSTERI' }))
    expect(await screen.findByRole('heading', { name: 'Filtre ve satır sayısı: DEMO.MUSTERI' })).toBeInTheDocument()
    expect(listTables).toHaveBeenCalledWith('source', { schema: 'DEMO', q: undefined, limit: 100 })
    expect(screen.getByRole('button', { name: 'İleri' })).toBeDisabled()
  })

  it('şema seçilmeden Tablo alanı kapalıdır', async () => {
    sessionStorage.setItem(WIZARD_STORAGE_KEY, JSON.stringify({ source: { alias: 'DEMODEV', user: 'BASKA', info: info('source') }, filters: [], count: null }))
    renderPage()
    expect(await screen.findByRole('combobox', { name: 'Şema' })).toBeEnabled()
    expect(screen.getByRole('combobox', { name: 'Tablo' })).toBeDisabled()
  })

  it('100 tabloyu aşan sonuçta daraltma ipucu görünür', async () => {
    seed(null)
    vi.mocked(listTables).mockResolvedValue({ items: [{ schema: 'DEMO', name: 'HESAP' }], total: 101, limit: 100, offset: 0 })
    renderPage()
    expect(await screen.findByText('İlk 100 tablo gösteriliyor; daraltmak için adını yazın.')).toBeInTheDocument()
  })

  it('tablo yoksa boş metni gösterir', async () => {
    sessionStorage.setItem(WIZARD_STORAGE_KEY, JSON.stringify({ source: { alias: 'DEMODEV', user: 'DEMO', info: info('source') }, filters: [], count: null }))
    vi.mocked(listTables).mockResolvedValue({ items: [], total: 0, limit: 100, offset: 0 })
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('combobox', { name: 'Tablo' }))
    expect(await screen.findByText('Bu şemada erişebildiğiniz tablo yok.')).toBeInTheDocument()
  })

  it('filtresiz durumda bir boş satır açılır, sayım kapalıdır ve neden yazar', async () => {
    seed(null, [])
    renderPage()
    expect(await screen.findByRole('group', { name: 'Filtre 1' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Satır sayısını hesapla' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'İleri' })).toBeDisabled()
    expect(await screen.findByText('Taşınacak kümeyi belirlemek için en az bir filtre gerekli. Tablo 5 milyonun altındaysa yukarıdaki kutuyu işaretleyebilirsiniz.', { selector: 'p.field-error' })).toBeInTheDocument()
  })

  it('tüm filtre satırları silinince R0 metni görünür', async () => {
    seed(null)
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Filtre 1 satırını sil' }))
    expect((await screen.findAllByText('Taşınacak kümeyi belirlemek için en az bir filtre gerekli. Tablo 5 milyonun altındaysa yukarıdaki kutuyu işaretleyebilirsiniz.')).length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: 'Satır sayısını hesapla' })).toBeDisabled()
  })

  it('geçerli sayımla İleri açılır ve /columns sayfasına gider', async () => {
    seed(ok)
    const user = userEvent.setup()
    renderPage()
    expect(await screen.findByText('1.234.567 / 5.000.000 satır')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'İleri' }))
    expect(await screen.findByText('kolonlar sayfası')).toBeInTheDocument()
  })

  it('over_limit mesajını gösterir ve İleri kapalı kalır', async () => {
    seed(null)
    vi.mocked(countRows).mockResolvedValue({ count: 48200000, limit: 5000000, over_limit: true, timed_out: false })
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Satır sayısını hesapla' }))
    expect(await screen.findByText('Seçiminiz 48.200.000 satır, sınır 5.000.000. Lütfen 5 milyonu aşmayacak şekilde filtreyi daraltın.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'İleri' })).toBeDisabled()
    expect(screen.getByRole('progressbar')).toHaveAttribute('data-tone', 'danger')
  })

  it('kutu işaretlenince filtre gizlenir, doğrulama full_table ile sayar ve İleri açılır', async () => {
    seed(null, [])
    vi.mocked(countRows).mockResolvedValue({ count: 120000, limit: 5000000, over_limit: false, timed_out: false })
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('checkbox', { name: 'Tablo 5.000.000 satırın altında; tamamını taşıyacağım' }))
    expect(screen.queryByRole('group', { name: 'Filtre 1' })).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Tablonun satır sayısını doğrula' }))
    expect(await screen.findByText('Tablonun tamamı: 120.000 satır')).toBeInTheDocument()
    expect(countRows).toHaveBeenCalledWith({ source: { schema: 'DEMO', table: 'HESAP' }, filters: [], full_table: true }, expect.anything())
    expect(screen.getByRole('button', { name: 'İleri' })).toBeEnabled()
  })

  it('tam tablo 5 milyonu aşarsa uyarı çıkar, kutu devre dışı kalır ve filtre geri gelir', async () => {
    seed(null, [])
    vi.mocked(countRows).mockResolvedValue({ count: null, limit: 5000000, over_limit: true, timed_out: false })
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('checkbox', { name: /tamamını taşıyacağım/ }))
    await user.click(screen.getByRole('button', { name: 'Tablonun satır sayısını doğrula' }))
    expect(await screen.findByText('Bu tablo 5.000.000 satırın üzerinde; tamamı taşınamaz. Filtre girerek kümeyi daraltın.')).toBeInTheDocument()
    const box = screen.getByRole('checkbox', { name: /tamamını taşıyacağım/ })
    expect(box).toBeDisabled()
    expect(box).not.toBeChecked()
    expect(screen.getByText('Tablo 5 milyonun üzerinde; filtre gerekli.')).toBeInTheDocument()
    expect(await screen.findByRole('group', { name: 'Filtre 1' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'İleri' })).toBeDisabled()
    expect(screen.queryByText(/kutuyu işaretleyebilirsiniz/)).not.toBeInTheDocument()
  })

  it('tam tabloda zaman aşımı uyarısı kutuyu kapatmaz', async () => {
    seed(null, [])
    vi.mocked(countRows).mockResolvedValue({ count: null, limit: 5000000, over_limit: false, timed_out: true })
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('checkbox', { name: /tamamını taşıyacağım/ }))
    await user.click(screen.getByRole('button', { name: 'Tablonun satır sayısını doğrula' }))
    expect(await screen.findByText('Sayım zaman aşımına uğradı; tablo çok büyük olabilir. Filtre girerek deneyin.')).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: /tamamını taşıyacağım/ })).toBeEnabled()
  })

  it('filtre değişince sayım sıfırlanır', async () => {
    seed(ok)
    const user = userEvent.setup()
    renderPage()
    expect(await screen.findByText('1.234.567 / 5.000.000 satır')).toBeInTheDocument()
    await user.click(await screen.findByRole('button', { name: 'Filtre ekle' }))
    await waitFor(() => expect(screen.queryByText('1.234.567 / 5.000.000 satır')).toBeNull())
    expect(screen.getByRole('button', { name: 'İleri' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Satır sayısını hesapla' })).toBeDisabled() // eksik satır
  })

  it('sayım iptali AbortController sinyalini tetikler', async () => {
    seed(null)
    let signal: AbortSignal | undefined
    vi.mocked(countRows).mockImplementation((_b, s) => {
      signal = s
      return new Promise(() => {})
    })
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: 'Satır sayısını hesapla' }))
    expect(signal?.aborted).toBe(false)
    await user.click(screen.getByRole('button', { name: 'İptal' }))
    expect(signal?.aborted).toBe(true)
    expect(screen.queryByRole('button', { name: 'İptal' })).toBeNull()
  })
})
