import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ToastProvider } from '@/components/toast'

const paths: string[] = []
const calls: [string, RequestInit | undefined][] = []
const refetch = vi.fn()
vi.mock('@/lib/hooks', () => ({
  useApi: (path: string) => {
    if (path === '/api/companies/industries') return { data: [{ name: 'Pet store', n: 386 }, { name: 'Dentist', n: 148 }] }
    paths.push(path)
    return { data: { rows: [{ id: 'c1', name: 'Bello Faro', industry: 'Pet store', phone: '(41) 99899-8698' }], total: 60, page: 1, per_page: 25 }, refetch }
  },
}))
vi.mock('@/lib/api', () => ({
  JSON_HEADERS: {},
  api: async (path: string, opts?: RequestInit) => { calls.push([path, opts]); return { success: true } },
}))

import { CompanyTable } from './CompanyTable'

describe('company table', () => {
  it('sorts by a header, pages, edits and deletes after a confirm', async () => {
    const open = vi.fn()
    render(<ToastProvider><CompanyTable onOpen={open} version={0} /></ToastProvider>)
    const user = userEvent.setup()
    expect(screen.getByText('1–25 of 60')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Company' }))
    expect(paths.at(-1)).toContain('sort=name&dir=asc')
    await user.click(screen.getByRole('button', { name: 'Next' }))
    expect(paths.at(-1)).toContain('page=2')

    await user.click(screen.getByRole('button', { name: 'Edit Bello Faro' }))
    const industry = screen.getByLabelText('Industry')
    expect(industry).toHaveValue('Pet store')                 // the current one is picked in the list
    await user.selectOptions(industry, 'Dentist (148)')
    await user.selectOptions(industry, 'Other…')
    await user.type(screen.getByLabelText('New industry'), 'x')
    expect(screen.getByLabelText('New industry')).toHaveValue('x')
    await user.selectOptions(industry, 'Pet store (386)')
    const name = screen.getByLabelText('Name')
    await user.clear(name)
    await user.type(name, 'Bello Faro Pet')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(calls[0][0]).toBe('/api/companies/c1'))
    expect(calls[0][1]?.method).toBe('PATCH')
    expect(JSON.parse(String(calls[0][1]?.body))).toMatchObject({ name: 'Bello Faro Pet', industry: 'Pet store' })

    await user.click(screen.getByRole('button', { name: 'Delete Bello Faro' }))
    expect(calls).toHaveLength(1)                       // nothing deleted before the confirm
    await user.click(screen.getByRole('button', { name: 'Delete' }))
    await waitFor(() => expect(calls[1]).toEqual(['/api/companies/c1', { method: 'DELETE' }]))
    expect(open).not.toHaveBeenCalled()                 // the buttons don't open the row
  })
})
