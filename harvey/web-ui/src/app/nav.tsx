import { createContext, useCallback, useContext, useState } from 'react'

export type TabId =
  | 'today' | 'outbox' | 'conversations'
  | 'signals' | 'discover' | 'companies' | 'prospects' | 'campaigns' | 'categories'
  | 'activity' | 'usage' | 'controls' | 'settings' | 'help'

export const TAB_IDS: TabId[] = [
  'today', 'outbox', 'conversations', 'signals', 'discover', 'companies', 'prospects',
  'campaigns', 'categories', 'activity', 'usage', 'controls', 'settings', 'help',
]
/** Only admins see these (operators work the queue, admins configure it). */
export const ADMIN_TABS: TabId[] = ['signals', 'discover', 'categories', 'controls', 'settings']

const KEY = 'harvey-tab'

function recall(): TabId {
  try {
    const v = localStorage.getItem(KEY) as TabId | null
    return v && TAB_IDS.includes(v) ? v : 'today'
  } catch { return 'today' }
}

/** ?conversation=<id>, the link in a WhatsApp alert. It stays in the address
 *  bar until the page uses it, so it survives the sign-in reload. */
function linked(): Record<string, string> {
  try {
    const id = new URLSearchParams(window.location.search).get('conversation') || ''
    return /^[A-Za-z0-9_-]{1,64}$/.test(id) ? { conversation: id } : {}
  } catch { return {} }
}

interface Nav {
  tab: TabId
  /** Jump to a tab; `params` carries a deep link (e.g. {conversation: id}). */
  goTab: (tab: TabId, params?: Record<string, string>) => void
  /** Deep-link params of the last goTab, consumed once by the page. */
  params: Record<string, string>
  clearParams: () => void
}

const NavCtx = createContext<Nav>({ tab: 'today', goTab: () => {}, params: {}, clearParams: () => {} })
export const useNav = () => useContext(NavCtx)

export function NavProvider({ children }: { children: React.ReactNode }) {
  const [params, setParams] = useState<Record<string, string>>(linked)
  const [tab, setTab] = useState<TabId>(() => (params.conversation ? 'conversations' : recall()))
  const goTab = useCallback((t: TabId, p: Record<string, string> = {}) => {
    try { localStorage.setItem(KEY, t) } catch { /* private mode */ }
    setParams(p)
    setTab(t)
  }, [])
  const clearParams = useCallback(() => {
    setParams({})
    if (window.location.search) history.replaceState(null, '', window.location.pathname)
  }, [])
  return <NavCtx.Provider value={{ tab, goTab, params, clearParams }}>{children}</NavCtx.Provider>
}
