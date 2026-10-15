import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { TableStatus, TrackedTable } from '../api/types.ts'
import { ToastProvider } from '../components/ui/index.ts'
import { Cleanup } from './Cleanup.tsx'

vi.mock('../api/endpoints.ts', () => ({ listCleanup: vi.fn(), getDropScript: vi.fn(), checkTracked: vi.fn() }))
vi.mock('../state/auth.tsx', () => ({ useAuth: vi.fn() }))
import { checkTracked, getDropScript, listCleanup } from '../api/endpoints.ts'
import { useAuth } from '../state/auth.tsx'

const mk = (id: number, table_status: TableStatus): TrackedTable => ({
  id, job_id: `job${id}`, owner: 'ayse', alias: 'DEMODEV', schema: 'DEMO', table: `T${id}`, object_id: id,
  object_created: '2026-10-01T10:00:00Z', verified_visible_at: null, table_status,
  expires_at: table_status === 'active' ? new Date(Date.now() + 3 * 86_400_000).toISOString() : '2026-10-08T10:00:00Z', last_checked_at: null,
})

function renderPage() {
  return render(<ToastProvider><Cleanup /></ToastProvider>)
}

beforeEach(() => {
  vi.mocked(useAuth).mockReturnValue({ status: 'authenticated', user: { username: 'u', display_name: 'U', groups: [], is_admin: false }, login: vi.fn(), logout: vi.fn() } as never)
  vi.mocked(listCleanup).mockResolvedValue({ items: [mk(1, 'active'), mk(2, 'expired_present'), mk(3, 'deleted_verified'), mk(4, 'expired_present')] })
  URL.createObjectURL = vi.fn(() => 'blob:x')
  URL.revokeObjectURL = vi.fn()
})
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('Cleanup', () => {
  it('açıklama ve kalan gün gösterilir', async () => {
    renderPage()
    expect(await screen.findByText(/Uygulama hiçbir tabloyu silmez/)).toBeInTheDocument()
    expect(screen.getByText('3 gün kaldı')).toBeInTheDocument()
    expect(screen.getAllByText('Süresi doldu')).toHaveLength(3)
    expect(screen.queryByRole('columnheader', { name: 'Sahip' })).toBeNull()
  })

  it('yalnız expired_present seçilebilir; indirme seçili job_ids ile çağrılır', async () => {
    vi.mocked(getDropScript).mockResolvedValue('DROP TABLE DEMO.T2;')
    renderPage()
    await screen.findByText('DEMODEV · DEMO.T1')
    expect(screen.getAllByRole('checkbox')).toHaveLength(2)
    expect(screen.getByRole('button', { name: /DROP script indir/ })).toBeDisabled()
    await userEvent.click(screen.getByLabelText('DEMO.T2 seç'))
    await userEvent.click(screen.getByRole('button', { name: /DROP script indir/ }))
    await waitFor(() => expect(getDropScript).toHaveBeenCalledWith(['job2']))
    expect(URL.createObjectURL).toHaveBeenCalled()
  })

  it('Şimdi kontrol et checkTracked çağırır ve sonucu bildirir', async () => {
    vi.mocked(checkTracked).mockResolvedValue({ ...mk(1, 'deleted_verified'), last_checked_at: '2026-10-15T10:00:00Z' })
    renderPage()
    await screen.findByText('DEMODEV · DEMO.T1')
    await userEvent.click(screen.getByRole('button', { name: /Şimdi kontrol et DEMO\.T1/ }))
    await waitFor(() => expect(checkTracked).toHaveBeenCalledWith('job1'))
    expect(await screen.findByText('DEMO.T1: Silindi.')).toBeInTheDocument()
  })

  it('durum filtresi', async () => {
    renderPage()
    await screen.findByText('DEMODEV · DEMO.T1')
    await userEvent.selectOptions(screen.getByLabelText('Durum'), 'deleted_verified')
    expect(screen.queryByText('DEMODEV · DEMO.T1')).toBeNull()
    expect(screen.getByText('DEMODEV · DEMO.T3')).toBeInTheDocument()
  })
})
