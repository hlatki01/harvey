import { createContext, useCallback, useContext, useState } from 'react'

export type TabId =
  | 'today' | 'outbox' | 'conversations'
  | 'signals' | 'discover' | 'companies' | 'prospects' | 'campaigns'
  | 'activity' | 'usage' | 'controls' | 'settings' | 'help'

export const TAB_IDS: TabId[] = [
  'today', 'outbox', 'conversations', 'signals', 'discover', 'companies', 'prospects',
  'campaigns', 'activity', 'usage', 'controls', 'settings', 'help',
]
/** Only admins see these (operators work the queue, admins configure it). */
export const ADMIN_TABS: TabId[] = ['signals', 'discover', 'controls', 'settings']

const KEY = 'harvey-tab'

function recall(): TabId {
  try {
    const v = localStorage.getItem(KEY) as TabId | null
    return v && TAB_IDS.includes(v) ? v : 'today'
  } catch { return 'today' }
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
  const [tab, setTab] = useState<TabId>(recall)
  const [params, setParams] = useState<Record<string, string>>({})
  const goTab = useCallback((t: TabId, p: Record<string, string> = {}) => {
    try { localStorage.setItem(KEY, t) } catch { /* private mode */ }
    setParams(p)
    setTab(t)
  }, [])
  const clearParams = useCallback(() => setParams({}), [])
  return <NavCtx.Provider value={{ tab, goTab, params, clearParams }}>{children}</NavCtx.Provider>
}
