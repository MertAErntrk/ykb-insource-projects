import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Job, JobColumn } from '../api/types.ts'
import { History } from './History.tsx'

vi.mock('../api/endpoints.ts', () => ({ listJobs: vi.fn(), getJob: vi.fn() }))
vi.mock('../state/auth.tsx', () => ({ useAuth: vi.fn() }))
import { getJob, listJobs } from '../api/endpoints.ts'
import { useAuth } from '../state/auth.tsx'

const mk = (id: string, status: Job['status']): Job => ({
  id, owner: 'u', status, source_alias: 'DEMOPROD', source_schema: 'DEMO', source_table: `T_${id}`,
  target_alias: 'DEMODEV', target_schema: 'DEMO', target_table: `H_${id}`, mode: 'create', filters: [],
  total_rows: 2000, rows_read: 2000, rows_written: 1234567, rows_per_sec: null, eta_sec: null, error_code: null, error_detail: null,
  created_at: '2026-10-15T10:00:00Z', started_at: '2026-10-15T10:00:00Z', finished_at: '2026-10-15T10:01:30Z',
})
const asUser = (is_admin: boolean) =>
  vi.mocked(useAuth).mockReturnValue({ status: 'authenticated', user: { username: 'u', display_name: 'U', groups: [], is_admin }, login: vi.fn(), logout: vi.fn() } as never)

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/history']}>
      <Routes>
        <Route path="/history" element={<History />} />
        <Route path="/jobs/:id" element={<p>iş sayfası</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => { vi.mocked(listJobs).mockResolvedValue({ items: [mk('a', 'done'), mk('b', 'failed')] }) })
afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('History', () => {
  it('satırları gösterir ve normal kullanıcıda Tüm işler anahtarı yoktur', async () => {
    asUser(false)
    renderPage()
    expect(await screen.findByText('DEMOPROD · DEMO.T_a')).toBeInTheDocument()
    expect(screen.getAllByText('1.234.567')).toHaveLength(2)
    expect(screen.queryByLabelText('Tüm işler')).toBeNull()
    expect(listJobs).toHaveBeenCalledWith(true)
  })

  it('admin anahtarı listJobs(false) çağırır', async () => {
    asUser(true)
    renderPage()
    await screen.findByText('DEMOPROD · DEMO.T_a')
    await userEvent.click(screen.getByLabelText('Tüm işler'))
    await waitFor(() => expect(listJobs).toHaveBeenLastCalledWith(false))
  })

  it('durum filtresi ve satır tıklama', async () => {
    asUser(false)
    renderPage()
    await screen.findByText('DEMOPROD · DEMO.T_a')
    await userEvent.selectOptions(screen.getByLabelText('Durum'), 'failed')
    expect(screen.queryByText('DEMOPROD · DEMO.T_a')).toBeNull()
    await userEvent.click(screen.getByText('DEMOPROD · DEMO.T_b'))
    expect(await screen.findByText('iş sayfası')).toBeInTheDocument()
  })

  it('Kurallar düğmesi iş kolonlarını açar; keep/exclude gösterilmez; filtresiz iş Tam tablo etiketi alır', async () => {
    asUser(false)
    vi.mocked(listJobs).mockResolvedValue({ items: [{ ...mk('a', 'done'), filters: [] }, { ...mk('b', 'done'), filters: [{ column: 'X', op: 'eq', value: 1 }] }] })
    const c = (column_name: string, rule_type: JobColumn['rule_type'], source_type: string): JobColumn => ({ column_name, rule_type, params: {}, source_type, position: 1 })
    vi.mocked(getJob).mockResolvedValue({ ...mk('a', 'done'), columns: [c('PARTY_ID', 'num_token', 'NUMBER'), c('AD', 'hmac', 'VARCHAR2'), c('NOT', 'keep', 'VARCHAR2'), c('X', 'exclude', 'DATE')] })
    renderPage()
    await screen.findByText('DEMOPROD · DEMO.T_a')
    expect(screen.getAllByText('Tam tablo')).toHaveLength(1)
    const btn = screen.getByRole('button', { name: 'Kurallar: T_a' })
    expect(btn).toHaveAttribute('aria-expanded', 'false')
    await userEvent.click(btn)
    expect(btn).toHaveAttribute('aria-expanded', 'true')
    expect(await screen.findByText('Sayı belirteci (geri çevrilebilir)')).toBeInTheDocument()
    expect(screen.getByText('Standart')).toBeInTheDocument()
    expect(screen.getByText('Standart dışı')).toBeInTheDocument()
    expect(screen.getByText('Özetle (HMAC)')).toBeInTheDocument()
    expect(screen.queryByText('NOT')).toBeNull()
    expect(screen.queryByText('Taşınmaz')).toBeNull()
    expect(getJob).toHaveBeenCalledWith('a')
    expect(screen.getByRole('dialog', { name: /T_a kuralları/ })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Kapat' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(btn).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText('iş sayfası')).toBeNull()
  })

  it('boş durum metni', async () => {
    asUser(false)
    vi.mocked(listJobs).mockResolvedValue({ items: [] })
    renderPage()
    expect(await screen.findByText(/Henüz taşıma yok/)).toBeInTheDocument()
  })
})
