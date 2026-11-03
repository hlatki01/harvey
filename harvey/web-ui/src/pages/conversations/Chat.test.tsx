import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { Chat } from './useChat'

const api = vi.fn(async (..._args: unknown[]) => ({ success: true }))
vi.mock('@/lib/api', () => ({ api: (...a: unknown[]) => api(...a), JSON_HEADERS: {} }))
vi.mock('@/components/toast', () => ({ useToast: () => vi.fn() }))

import { ChatBubbles } from './Chat'

const chat = (refresh = vi.fn()) => ({
  msgs: [{ id: 'M1', ts: 1, from_me: true, who: 'harvey', text: 'oi', status: 'read',
           reactions: [{ emoji: '👍', from_me: false }, { emoji: '🙏', from_me: true }] }],
  more: false, loadingOlder: false, pending: [], urls: {}, refresh, loadMedia: vi.fn(),
}) as unknown as Chat

describe('chat bubbles', () => {
  it('show the receipt and both sides\' reactions', () => {
    render(<ChatBubbles id="c1" first="Ana" chat={chat()} onImage={() => {}} />)
    expect(screen.getByRole('img', { name: 'Read' })).toBeInTheDocument()
    expect(screen.getByTitle('Their reaction')).toHaveTextContent('👍')
    expect(screen.getByTitle('Our reaction')).toHaveTextContent('🙏')
  })

  it('send a reaction, and picking ours again takes it back', async () => {
    const refresh = vi.fn()
    render(<ChatBubbles id="c1" first="Ana" chat={chat(refresh)} onImage={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: 'Add a reaction' }))
    fireEvent.click(screen.getByRole('button', { name: 'React ❤️' }))
    await waitFor(() => expect(refresh).toHaveBeenCalled())
    expect(JSON.parse((api.mock.calls[0][1] as RequestInit).body as string)).toEqual({ message_id: 'M1', emoji: '❤️' })

    fireEvent.click(screen.getByRole('button', { name: 'Add a reaction' }))
    fireEvent.click(screen.getByRole('button', { name: 'React 🙏' }))
    await waitFor(() => expect(api).toHaveBeenCalledTimes(2))
    expect(JSON.parse((api.mock.calls[1][1] as RequestInit).body as string).emoji).toBe('')
  })
})
