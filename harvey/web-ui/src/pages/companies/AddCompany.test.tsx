import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { NavProvider } from '@/app/nav'
import { ToastProvider } from '@/components/toast'

const calls: [string, unknown][] = []
const replies: Record<string, unknown> = {
  '/api/companies/lookup': { results: [{ name: 'Bello Faro', phone: '(41) 99899-8698', category: 'Pet store', external_id: 'google_places:1', known_id: '' }] },
  '/api/companies/add': { success: true, company_id: 'c1', new: true, can_open: true, checks: [
    { key: 'mobile', ok: true, text: 'Mobile number 5541998998698.', warning: false },
    { key: 'site', ok: false, text: 'Already has a website (bellofaro.com.br): not who we usually sell to.', warning: true },
  ] },
  '/api/companies/c1/whatsapp': { success: true, outbox_id: 'o1' },
}
vi.mock('@/lib/api', () => ({
  post: async (path: string, body?: unknown) => { calls.push([path, body]); return replies[path] },
}))

import { AddCompany } from './AddCompany'

afterEach(() => { calls.length = 0 })

describe('add a company', () => {
  it('searches, adds the picked result, shows the next steps and writes the opening', async () => {
    const onAdded = vi.fn()
    render(<NavProvider><ToastProvider><AddCompany onClose={() => {}} onAdded={onAdded} /></ToastProvider></NavProvider>)
    const user = userEvent.setup()
    await user.type(screen.getByRole('textbox'), 'Bello Faro, Curitiba')
    await user.click(screen.getByRole('button', { name: /search/i }))
    await user.click(await screen.findByRole('button', { name: 'Add' }))
    expect(calls[1]).toEqual(['/api/companies/add', { business: expect.objectContaining({ external_id: 'google_places:1' }) }])
    expect(await screen.findByText(/Already has a website/)).toBeInTheDocument()
    expect(screen.getByLabelText('Warning')).toBeInTheDocument()
    expect(onAdded).toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: /write the whatsapp opening/i }))
    await waitFor(() => expect(screen.getByRole('button', { name: /review it in the outbox/i })).toBeInTheDocument())
    expect(calls[2][0]).toBe('/api/companies/c1/whatsapp')
  })
})
