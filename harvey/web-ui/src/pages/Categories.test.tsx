import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ToastProvider } from '@/components/toast'

const DATA = {
  demos: [
    { label: 'Odontologia', url: 'https://demo-odonto.test', keywords: ['dentist', 'odonto'], companies: 265 },
    { label: 'Clínica', url: 'https://demo-clinica.test', keywords: ['clinic'], companies: 114 },
  ],
  unmatched: [{ industry: 'Car wash', n: 12 }], unmatched_total: 12,
}
const posts: unknown[] = []
vi.mock('@/lib/hooks', () => ({ useApi: () => ({ data: DATA, refetch: vi.fn() }) }))
vi.mock('@/lib/api', () => ({ post: async (_: string, body: { demos: unknown[] }) => { posts.push(body.demos); return { success: true } } }))

import { CategoriesPage } from './Categories'

afterEach(() => { posts.length = 0 })
const labels = (i: number) => (posts[i] as { label: string }[]).map(d => d.label)
const page = () => render(<ToastProvider><CategoriesPage /></ToastProvider>)

describe('categories', () => {
  it('shows coverage and the industries no category catches', () => {
    page()
    expect(screen.getByText('265')).toBeInTheDocument()
    expect(screen.getByText('Car wash')).toBeInTheDocument()
  })

  it('reorders, adds and deletes, saving the whole list in order', async () => {
    page()
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Move Clínica up' }))
    await waitFor(() => expect(labels(0)).toEqual(['Clínica', 'Odontologia']))

    await user.click(screen.getByRole('button', { name: 'Add a category' }))
    await user.type(screen.getByLabelText('Name'), 'Lava-jato')
    await user.clear(screen.getByLabelText('Demo site'))
    await user.type(screen.getByLabelText('Demo site'), 'https://demo-lavajato.test')
    await user.type(screen.getByLabelText(/Keywords, one per line/), 'car wash{enter} lava')
    await user.click(screen.getByRole('button', { name: 'Add category' }))
    await waitFor(() => expect(labels(1)).toEqual(['Odontologia', 'Clínica', 'Lava-jato']))
    expect((posts[1] as { keywords: string[] }[])[2].keywords).toEqual(['car wash', ' lava'])   // the leading space is kept

    await user.click(screen.getByRole('button', { name: 'Delete Odontologia' }))
    await user.click(screen.getByRole('button', { name: 'Delete' }))
    await waitFor(() => expect(labels(2)).toEqual(['Clínica']))
  })
})
