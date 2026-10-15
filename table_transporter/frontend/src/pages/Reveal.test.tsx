import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ToastProvider } from '../components/ui/index.ts'
import { Reveal } from './Reveal.tsx'

vi.mock('../api/endpoints.ts', () => ({ revealValues: vi.fn() }))
import { revealValues } from '../api/endpoints.ts'

const renderPage = () => render(<ToastProvider><Reveal /></ToastProvider>)
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('Reveal', () => {
  it('üç standart kuralı sunar; tarih kuralında biçim ipucu görünür', async () => {
    renderPage()
    const sel = screen.getByLabelText('Kural')
    expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual([
      'Sayı belirteci (geri çevrilebilir)', 'Metin belirteci (geri çevrilebilir)', 'Tarih belirteci (geri çevrilebilir)',
    ])
    expect(screen.queryByText(/GG\.AA\.YYYY/)).toBeNull()
    await userEvent.selectOptions(sel, 'date_token')
    expect(screen.getByText(/GG\.AA\.YYYY/)).toBeInTheDocument()
    expect(screen.getByText('Çözme işlemi kayıt altına alınır; değerler kaydedilmez.')).toBeInTheDocument()
  })

  it('101 değerde Çöz kapalıdır', () => {
    renderPage()
    const area = screen.getByLabelText('Anonim değerler')
    expect(screen.getByRole('button', { name: 'Çöz' })).toBeDisabled()
    fireEvent.change(area, { target: { value: Array.from({ length: 100 }, (_, i) => String(i + 1)).join('\n') } })
    expect(screen.getByText('100 / 100')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Çöz' })).toBeEnabled()
    fireEvent.change(area, { target: { value: Array.from({ length: 101 }, (_, i) => String(i + 1)).join('\n') } })
    expect(screen.getByRole('button', { name: 'Çöz' })).toBeDisabled()
    expect(screen.getByText('En çok 100 değer çözülebilir.')).toBeInTheDocument()
  })

  it('seçilen kural ve değerlerle çağırır; sonucu ve hata satırını gösterir', async () => {
    vi.mocked(revealValues).mockResolvedValue({
      rule: 'text_token',
      items: [{ value: 'Xyz', original: 'Ayşe', error: null }, { value: 'abc', original: null, error: 'Çözülemedi.' }],
    })
    renderPage()
    await userEvent.selectOptions(screen.getByLabelText('Kural'), 'text_token')
    fireEvent.change(screen.getByLabelText('Anonim değerler'), { target: { value: 'Xyz\nabc\n' } })
    await userEvent.click(screen.getByRole('button', { name: 'Çöz' }))
    expect(await screen.findByText('Ayşe')).toBeInTheDocument()
    expect(screen.getByText('Çözülemedi.')).toBeInTheDocument()
    expect(revealValues).toHaveBeenCalledWith({ rule: 'text_token', values: ['Xyz', 'abc'] })
    await waitFor(() => expect(screen.getByRole('button', { name: 'Gerçek değerleri kopyala' })).toBeEnabled())
  })
})
