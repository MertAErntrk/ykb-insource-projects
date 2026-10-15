import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ColumnInfo } from '../api/types.ts'
import { ToastProvider } from '../components/ui/index.ts'
import { WIZARD_STORAGE_KEY, WizardProvider, useWizard } from '../state/wizard.tsx'
import { Columns } from './Columns.tsx'

vi.mock('../api/endpoints.ts', () => ({
  listColumns: vi.fn(),
  listAliases: vi.fn(),
  listJobs: vi.fn(),
  getJob: vi.fn(),
  previewRules: vi.fn(),
}))
import * as api from '../api/endpoints.ts'

function col(over: Partial<ColumnInfo> & { name: string }): ColumnInfo {
  return {
    position: 1, data_type: 'VARCHAR2', char_length: 50, char_used: 'C', data_length: 200, precision: null, scale: null,
    nullable: true, virtual: false, identity: false, hidden: false, default: null, is_pk_or_unique: false,
    supported: true, unsupported_reason: null, ...over,
  }
}

const COLUMNS: ColumnInfo[] = [
  col({ name: 'PARTY_ID', data_type: 'NUMBER', precision: 12, scale: 0, nullable: false, is_pk_or_unique: true }),
  col({ name: 'AD' }),
  col({ name: 'ADET', data_type: 'NUMBER', precision: 5, scale: 0 }),
  col({ name: 'DOGUM_TARIHI', data_type: 'DATE' }),
  col({ name: 'KAYIT_TARIHI', data_type: 'DATE' }),
  col({ name: 'NOTLAR', data_type: 'CLOB', supported: false, unsupported_reason: 'CLOB kolonları desteklenmiyor.' }),
  col({ name: 'DURUM', char_length: 10, nullable: false }),
]

function Probe() {
  const { rules } = useWizard()
  return <pre data-testid="rules">{JSON.stringify(rules)}</pre>
}

function renderPage() {
  sessionStorage.setItem(
    WIZARD_STORAGE_KEY,
    JSON.stringify({ table: { schema: 'DEMO', table: 'MUSTERI' }, source: { alias: 'DEMO_TEST', user: 'u', info: {} } }),
  )
  return render(
    <MemoryRouter>
      <ToastProvider>
        <WizardProvider>
          <Columns />
          <Probe />
        </WizardProvider>
      </ToastProvider>
    </MemoryRouter>,
  )
}

type User = ReturnType<typeof userEvent.setup>
const rulesOf = () => JSON.parse(screen.getByTestId('rules').textContent ?? '[]') as Array<{ column: string; type: string; params: Record<string, unknown> }>
const typeOf = (name: string) => rulesOf().find((x) => x.column === name)?.type
const NUM_TOKEN = 'Sayı belirteci (geri çevrilebilir)'
const next = () => screen.getByRole('button', { name: 'İleri' })
const toggleAnon = (user: User) => user.click(screen.getByRole('checkbox', { name: 'Anonimleştirme yapacağım' }))
const ruleSelect = (n: number) => screen.getByRole('combobox', { name: `Kural (satır ${n})` })

async function openPanel(user: User) {
  await screen.findByRole('checkbox', { name: 'Taşı: AD' })
  await toggleAnon(user)
}
async function addRow(user: User, n: number, column: string) {
  await user.click(screen.getByRole('button', { name: '+ Kolon ekle' }))
  await user.click(screen.getByRole('combobox', { name: `Kolon (satır ${n})` }))
  await user.click(screen.getByRole('option', { name: new RegExp(`^${column} `) }))
}

beforeEach(() => {
  sessionStorage.clear()
  vi.mocked(api.listColumns).mockResolvedValue({ columns: COLUMNS })
  vi.mocked(api.listAliases).mockResolvedValue({ aliases: [] })
  vi.mocked(api.listJobs).mockResolvedValue({ items: [] })
})
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('Columns', () => {
  it('varsayılan kapalı: kural düzenleyicisi yok, desteklenenler keep, desteklenmeyen exclude', async () => {
    renderPage()
    await screen.findByRole('checkbox', { name: 'Taşı: AD' })
    expect(screen.getByRole('checkbox', { name: 'Anonimleştirme yapacağım' })).not.toBeChecked()
    expect(screen.getByText('Kapalıyken seçili kolonlar olduğu gibi taşınır.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '+ Kolon ekle' })).not.toBeInTheDocument()
    await waitFor(() => expect(rulesOf()).toHaveLength(COLUMNS.length))
    for (const r of rulesOf()) expect(r.type).toBe(r.column === 'NOTLAR' ? 'exclude' : 'keep')
  })

  it('veri tipi ve PK/NOT NULL işaretleri gösterilir; desteklenmeyen kolon pasif ve nedeni yazılı', async () => {
    renderPage()
    await screen.findByRole('checkbox', { name: 'Taşı: AD' })
    expect(screen.getByText('NUMBER(12)')).toBeInTheDocument()
    expect(screen.getAllByText('VARCHAR2(50 CHAR)').length).toBeGreaterThan(0)
    expect(screen.getByText('PK/UNIQUE')).toBeInTheDocument()
    const notlar = screen.getByRole('checkbox', { name: 'Taşı: NOTLAR' })
    expect(notlar).toBeDisabled()
    expect(notlar).not.toBeChecked()
    expect(screen.getByText('CLOB kolonları desteklenmiyor.')).toBeInTheDocument()
  })

  it('taşınmayan kolon exclude olur; tümünü kaldır ve seç', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByRole('checkbox', { name: 'Taşı: AD' })
    await user.click(screen.getByRole('checkbox', { name: 'Taşı: AD' }))
    await waitFor(() => expect(typeOf('AD')).toBe('exclude'))
    expect(typeOf('ADET')).toBe('keep')
    await user.click(screen.getByRole('button', { name: 'Tümünü seç' }))
    await waitFor(() => expect(typeOf('AD')).toBe('keep'))
    await user.click(screen.getByRole('button', { name: 'Tümünü kaldır' }))
    await waitFor(() => expect(rulesOf().every((r) => r.type === 'exclude')).toBe(true))
    expect(next()).toBeDisabled()
  })

  it('kutu açılınca bilgi notu görünür', async () => {
    const user = userEvent.setup()
    renderPage()
    await openPanel(user)
    expect(screen.getByRole('table', { name: 'Standart kurallar' })).toBeInTheDocument()
    expect(screen.getByText('Metin belirteci')).toBeInTheDocument()
    expect(screen.getByText(/Tek yönlü \(isteğe bağlı\):/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '+ Kolon ekle' })).toBeEnabled()
  })

  it('+ ile satır eklenir; NUMBER PK kolonda mask yok, sayı belirteci var; tip rozeti görünür', async () => {
    const user = userEvent.setup()
    renderPage()
    await openPanel(user)
    await addRow(user, 1, 'PARTY_ID')
    const sel = ruleSelect(1)
    expect(within(sel).getByRole('option', { name: NUM_TOKEN })).toBeEnabled()
    expect(within(sel).getByRole('option', { name: 'Özetle (HMAC)' })).toBeInTheDocument()
    for (const label of ['Maskele', 'Rastgele', 'Sabit değer', 'Boş (NULL)', 'Olduğu gibi', 'Taşınmaz']) {
      expect(within(sel).queryByRole('option', { name: label })).not.toBeInTheDocument()
    }
    expect(screen.getAllByText('NUMBER(12)').length).toBeGreaterThan(1)
    await user.selectOptions(sel, 'num_token')
    await waitFor(() => expect(typeOf('PARTY_ID')).toBe('num_token'))
  })

  it('kullanılan kolon diğer satırların listesinden düşer; satır kaldırılınca kural keep e döner', async () => {
    const user = userEvent.setup()
    renderPage()
    await openPanel(user)
    await addRow(user, 1, 'AD')
    await user.selectOptions(ruleSelect(1), 'hmac')
    await waitFor(() => expect(typeOf('AD')).toBe('hmac'))
    await user.click(screen.getByRole('button', { name: '+ Kolon ekle' }))
    await user.click(screen.getByRole('combobox', { name: 'Kolon (satır 2)' }))
    expect(screen.queryByRole('option', { name: /^AD / })).not.toBeInTheDocument()
    expect(screen.queryByRole('option', { name: /^NOTLAR / })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Satırı kaldır (satır 2)' }))
    await user.click(screen.getByRole('button', { name: 'Satırı kaldır (satır 1)' }))
    await waitFor(() => expect(typeOf('AD')).toBe('keep'))
  })

  it('tamamlanmamış satır İleri yi kapatır ve nedeni gösterir; tamamlanınca açılır', async () => {
    const user = userEvent.setup()
    renderPage()
    await openPanel(user)
    expect(next()).toBeEnabled()
    await user.click(screen.getByRole('button', { name: '+ Kolon ekle' }))
    expect(next()).toBeDisabled()
    expect(screen.getByText(/İleri için:.*Kolon seçin\./)).toBeInTheDocument()
    await user.click(screen.getByRole('combobox', { name: 'Kolon (satır 1)' }))
    await user.click(screen.getByRole('option', { name: /^AD / }))
    // Kolon seçilince standart kural otomatik gelir.
    await waitFor(() => expect(typeOf('AD')).toBe('text_token'))
    expect(screen.getByText('Standart')).toBeInTheDocument()
    expect(next()).toBeEnabled()
    await user.selectOptions(ruleSelect(1), 'hmac')
    expect(screen.getByText('Standart dışı · geri çevrilemez')).toBeInTheDocument()
  })

  it('NOT NULL kolonda boş sabit değer İleri yi kilitler', async () => {
    const user = userEvent.setup()
    renderPage()
    await openPanel(user)
    await addRow(user, 1, 'DURUM')
    await user.selectOptions(ruleSelect(1), 'constant')
    expect(next()).toBeDisabled()
    const input = screen.getByRole('textbox', { name: 'Sabit değer, DURUM' })
    expect(input).toHaveAttribute('aria-invalid', 'true')
    await user.type(input, 'A')
    await waitFor(() => expect(next()).toBeEnabled())
  })

  it('Önerileri ekle standart kuralları ekler; kapatınca kurallar keep e döner, açınca geri gelir', async () => {
    const user = userEvent.setup()
    renderPage()
    await openPanel(user)
    expect(screen.queryByLabelText('En çok gün')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Önerileri ekle \(/ }))
    await waitFor(() => expect(typeOf('AD')).toBe('text_token'))
    expect(typeOf('PARTY_ID')).toBe('num_token')
    expect(typeOf('DOGUM_TARIHI')).toBe('date_token')
    expect(typeOf('KAYIT_TARIHI')).toBe('keep')
    expect(screen.queryByLabelText('En çok gün')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Önerileri ekle \(0\)/ })).toBeDisabled()
    await toggleAnon(user)
    await waitFor(() => expect(typeOf('AD')).toBe('keep'))
    expect(rulesOf().some((r) => ['text_token', 'num_token', 'date_token'].includes(r.type))).toBe(false)
    await toggleAnon(user)
    await waitFor(() => expect(typeOf('AD')).toBe('text_token'))
  })

  it('önizleme yalnız kural satırı varken görünür; önce/sonra değerlerini gösterir', async () => {
    const user = userEvent.setup()
    vi.mocked(api.previewRules).mockResolvedValue({
      columns: [
        { column: 'AD', type: 'hmac', before: ['Ayşe', 'Ali'], after: ['a1b2', 'c3d4'], null_before: 0, null_after: 0 },
        { column: 'NOTLAR', type: 'exclude', before: ['x'], after: [], null_before: 1, null_after: 0 },
      ],
    })
    renderPage()
    await openPanel(user)
    expect(screen.queryByRole('button', { name: 'Önizle' })).not.toBeInTheDocument()
    await addRow(user, 1, 'AD')
    await user.selectOptions(ruleSelect(1), 'hmac')
    await user.click(screen.getByRole('button', { name: 'Önizle' }))
    expect(await screen.findByText('Ayşe')).toBeInTheDocument()
    expect(screen.getByText('a1b2')).toBeInTheDocument()
    expect(screen.getByText('hedefe yazılmaz')).toBeInTheDocument()
    expect(api.previewRules).toHaveBeenCalledWith(expect.objectContaining({ source: { schema: 'DEMO', table: 'MUSTERI' } }))
  })
})
