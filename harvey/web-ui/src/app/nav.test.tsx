import { describe, expect, it } from 'vitest'
import { act, render } from '@testing-library/react'
import { NavProvider, useNav } from './nav'

function Probe({ seen }: { seen: { nav?: ReturnType<typeof useNav> } }) {
  seen.nav = useNav()
  return null
}

describe('nav', () => {
  it('opens the conversation an alert links to, then clears the link', () => {
    history.replaceState(null, '', '/?conversation=7d66ef3b76ba')
    const seen: { nav?: ReturnType<typeof useNav> } = {}
    render(<NavProvider><Probe seen={seen} /></NavProvider>)
    expect(seen.nav!.tab).toBe('conversations')
    expect(seen.nav!.params).toEqual({ conversation: '7d66ef3b76ba' })
    act(() => seen.nav!.clearParams())
    expect(window.location.search).toBe('')
  })

  it('ignores a link that is not a conversation id', () => {
    history.replaceState(null, '', '/?conversation=<script>')
    const seen: { nav?: ReturnType<typeof useNav> } = {}
    render(<NavProvider><Probe seen={seen} /></NavProvider>)
    expect(seen.nav!.params).toEqual({})
  })
})
