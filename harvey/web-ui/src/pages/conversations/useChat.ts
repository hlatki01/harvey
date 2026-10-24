import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '@/lib/api'
import { authFetch } from '@/lib/auth'
import { useInterval } from '@/lib/hooks'
import type { ChatMsg } from './types'

const CHAT_PAGE = 30
export interface Pending { text: string; file: string; at: number }
interface ChatState { msgs: ChatMsg[]; more: boolean; loadingOlder: boolean; loaded: boolean; pending: Pending[] }
const EMPTY: ChatState = { msgs: [], more: false, loadingOlder: false, loaded: false, pending: [] }
const sig = (m: ChatMsg[]) => m.map(x => x.id).join()

/** The real WhatsApp chat of one conversation: newest 30, older on demand,
 *  re-read every 4s, plus blob URLs for its media (revoked on unmount). */
export function useChat(id: string, enabled = true) {
  const [chat, setChat] = useState<ChatState>(EMPTY)
  const [urls, setUrls] = useState<Record<string, string>>({})
  const urlRef = useRef<Record<string, string>>({})
  const state = useRef(chat)
  state.current = chat
  const path = '/api/conversations/' + encodeURIComponent(id)

  const refresh = useCallback(async () => {
    const r = await api<{ messages?: ChatMsg[]; has_more?: boolean }>(path + '/chat?limit=' + CHAT_PAGE)
    if (!r || !r.messages) { setChat(s => (s.loaded ? s : { ...s, loaded: true })); return }
    const fresh = r.messages
    setChat(s => {
      const oldest = fresh.length ? fresh[0].ts : 0
      const keep = s.msgs.filter(m => m.ts < oldest)   // older pages already loaded stay
      const msgs = keep.concat(fresh)
      // A message that has arrived replaces its "Sending…" bubble.
      const pending = s.pending.length && msgs.some(m => m.from_me && m.ts >= s.pending[0].at - 5) ? s.pending.slice(1) : s.pending
      const same = s.loaded && sig(msgs) === sig(s.msgs) && pending === s.pending
      if (same) return s
      return { ...s, loaded: true, msgs, pending, more: keep.length ? s.more : !!r.has_more }
    })
  }, [path])

  const loadOlder = useCallback(async () => {
    const s = state.current
    if (s.loadingOlder || !s.more || !s.msgs.length) return
    setChat(x => ({ ...x, loadingOlder: true }))
    const r = await api<{ messages?: ChatMsg[]; has_more?: boolean }>(path + '/chat?limit=' + CHAT_PAGE + '&before=' + s.msgs[0].ts)
    setChat(x => r && r.messages
      ? { ...x, loadingOlder: false, msgs: r.messages!.concat(x.msgs), more: !!r.has_more }
      : { ...x, loadingOlder: false, more: false })
  }, [path])

  const tick = useCallback(() => { if (enabled) refresh() }, [enabled, refresh])
  useEffect(tick, [tick])
  useInterval(tick, 4000)

  const addPending = (p: Pending) => setChat(s => ({ ...s, pending: [...s.pending, p] }))
  const dropPending = () => setChat(s => ({ ...s, pending: s.pending.slice(1) }))

  /** Fetch one attachment as a blob URL; false when it could not be loaded. */
  const loadMedia = useCallback(async (mid: string): Promise<boolean> => {
    if (urlRef.current[mid]) return true
    try {
      const r = await authFetch(path + '/media/' + encodeURIComponent(mid))
      if (!r.ok) return false
      const u = URL.createObjectURL(await r.blob())
      urlRef.current[mid] = u
      setUrls({ ...urlRef.current })
      return true
    } catch { return false }
  }, [path])

  useEffect(() => () => { Object.values(urlRef.current).forEach(URL.revokeObjectURL) }, [])

  return { ...chat, urls, refresh, loadOlder, addPending, dropPending, loadMedia }
}
export type Chat = ReturnType<typeof useChat>
