import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { JobEvent } from '../api/sse.ts'
import type { Job } from '../api/types.ts'
import { WizardProvider } from '../state/wizard.tsx'
import { Progress } from './Progress.tsx'

vi.mock('../api/endpoints.ts', () => ({ getJob: vi.fn(), cancelJob: vi.fn() }))
vi.mock('../api/sse.ts', () => ({ subscribeJob: vi.fn() }))
import { cancelJob, getJob } from '../api/endpoints.ts'
import { subscribeJob } from '../api/sse.ts'

const JOB: Job = {
  id: 'j1', owner: 'u', status: 'running', source_alias: 'DEMOPROD', source_schema: 'DEMO', source_table: 'MUSTERI',
  target_alias: 'DEMODEV', target_schema: 'DEMO', target_table: 'MUSTERI_ANON', mode: 'create', filters: [],
  total_rows: 10000, rows_read: 0, rows_written: 0, rows_per_sec: null, eta_sec: null, error_code: null, error_detail: null,
  created_at: '2026-10-15T10:00:00Z', started_at: '2026-10-15T10:00:00Z', finished_at: null,
}
let emit: (e: JobEvent) => void
const unsub = vi.fn()

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/jobs/j1']}>
      <WizardProvider>
        <Routes>
          <Route path="/jobs/:id" element={<Progress />} />
          <Route path="/connect" element={<p>bağlantı sayfası</p>} />
        </Routes>
      </WizardProvider>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.mocked(getJob).mockResolvedValue(JOB)
  vi.mocked(subscribeJob).mockImplementation((_id, cb) => { emit = (e) => act(() => cb(e)); return unsub })
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('Progress', () => {
  it('progress olayında çubuk ve metin güncellenir', async () => {
    renderPage()
    await screen.findByRole('progressbar')
    emit({ name: 'progress', data: { job_id: 'j1', status: 'running', rows_read: 2500, rows_written: 2500, total_rows: 10000, rows_per_sec: 1250, eta_sec: 120 } })
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '25')
    expect(screen.getByText('2.500 / 10.000')).toBeInTheDocument()
    expect(screen.getByText('1.250 satır/sn')).toBeInTheDocument()
    expect(screen.getByText('~2 dk')).toBeInTheDocument()
    expect(screen.getByText(/arka planda sürer/)).toBeInTheDocument()
  })

  it('done olayında başarı paneli ve temizlik notu', async () => {
    renderPage()
    await screen.findByRole('progressbar')
    emit({ name: 'done', data: { job_id: 'j1', status: 'done', rows_written: 10000, finished_at: '2026-10-15T10:02:05Z', target: { schema: 'DEMO', table: 'MUSTERI_ANON' } } })
    expect(screen.getByRole('heading', { name: 'Taşıma tamamlandı' })).toBeInTheDocument()
    expect(screen.getByText('2 dk 5 sn')).toBeInTheDocument()
    expect(screen.getByText(/7 gün sonra temizlik listesine girer/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Yeni taşıma başlat' }))
    expect(await screen.findByText('bağlantı sayfası')).toBeInTheDocument()
  })

  it('iptal Dialog onaylanınca cancelJob çağrılır', async () => {
    vi.mocked(cancelJob).mockResolvedValue({ ...JOB, status: 'cancelling' })
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'İptal et' }))
    expect(screen.getByText(/Ekleme modunda hiçbir satır eklenmez/)).toBeInTheDocument()
    expect(cancelJob).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Taşımayı iptal et' }))
    await waitFor(() => expect(cancelJob).toHaveBeenCalledWith('j1'))
    expect(await screen.findAllByText('İptal ediliyor')).not.toHaveLength(0)
  })

  it('partial mesajı gösterilir', async () => {
    renderPage()
    await screen.findByRole('progressbar')
    emit({ name: 'failed', data: { job_id: 'j1', status: 'partial', error: 'ORA_ERROR', detail: 'ORA-01653', rows_written: 500 } })
    expect(screen.getByText('Tablo kısmen oluştu ve temizlik listesine eklendi.')).toBeInTheDocument()
    expect(screen.getByText(/ORA-01653/)).toBeInTheDocument()
  })

  it('unmount olunca abonelik kapanır', async () => {
    const { unmount } = renderPage()
    await screen.findByRole('progressbar')
    unmount()
    expect(unsub).toHaveBeenCalled()
  })
})
