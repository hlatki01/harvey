import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ToastProvider } from '@/components/toast'

const calls: [string, RequestInit | undefined][] = []
vi.mock('@/lib/api', () => ({
  JSON_HEADERS: {},
  api: async (path: string, opts?: RequestInit) => { calls.push([path, opts]); return { success: true, email_status: 'verified' } },
}))

import { ContactDialog } from './ContactDialog'

afterEach(() => { calls.length = 0 })
const body = (i: number) => JSON.parse(String(calls[i][1]?.body))

describe('contact dialog', () => {
  it('adds a contact to the company', async () => {
    const saved = vi.fn()
    render(<ToastProvider><ContactDialog companyId="c1" onClose={() => {}} onSaved={saved} /></ToastProvider>)
    const user = userEvent.setup()
    await user.type(screen.getByLabelText('First name'), 'Ana')
    await user.type(screen.getByLabelText('Email'), 'ana@bello.com.br')
    await user.click(screen.getByRole('button', { name: 'Add contact' }))
    await waitFor(() => expect(saved).toHaveBeenCalled())
    expect(calls[0][0]).toBe('/api/companies/c1/contacts')
    expect(calls[0][1]?.method).toBe('POST')
    expect(body(0)).toMatchObject({ first_name: 'Ana', email: 'ana@bello.com.br' })
  })

  it('edits only what changed, so an untouched email is not checked again', async () => {
    const saved = vi.fn()
    render(<ToastProvider><ContactDialog companyId="c1" onClose={() => {}} onSaved={saved}
      contact={{ id: 'p1', first_name: 'Ana', email: 'ana@bello.com.br', title: 'Dona' }} /></ToastProvider>)
    const user = userEvent.setup()
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
    await user.clear(screen.getByLabelText('Role'))
    await user.type(screen.getByLabelText('Role'), 'Sócia')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(saved).toHaveBeenCalled())
    expect(calls[0][0]).toBe('/api/prospects/p1')
    expect(calls[0][1]?.method).toBe('PATCH')
    expect(body(0)).toEqual({ title: 'Sócia' })
  })
})
