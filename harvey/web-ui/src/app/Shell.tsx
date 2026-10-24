import { Suspense, useEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import {
  Activity, BarChart3, Building2, HelpCircle, Home, Inbox, MessageSquare, Power, Radio,
  RefreshCw, Search, Send, SlidersHorizontal, Users,
} from 'lucide-react'
import { api } from '@/lib/api'
import { authFetch } from '@/lib/auth'
import { useApi, useLockState } from '@/lib/hooks'
import { cycleTheme, useThemeMode } from '@/lib/theme'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/states'
import { ADMIN_TABS, useNav, type TabId } from './nav'
import { Notifications } from './Notifications'
import { PAGES } from './pages'

interface Item { id: TabId; label: string; icon: React.ComponentType<{ className?: string }>; count?: (t: TodayData) => number }
interface TodayData {
  items?: { tone?: string }[]
  stats?: { outbox_pending?: number; signals_proposed?: number; needs_human?: number }
}

const GROUPS: { label: string; items: Item[] }[] = [
  { label: 'Work', items: [
    { id: 'today', label: 'Today', icon: Home, count: t => (t.items || []).filter(i => i.tone !== 'good').length },
    { id: 'outbox', label: 'Outbox', icon: Inbox, count: t => t.stats?.outbox_pending || 0 },
    { id: 'conversations', label: 'Conversations', icon: MessageSquare, count: t => t.stats?.needs_human || 0 },
  ] },
  { label: 'Pipeline', items: [
    { id: 'signals', label: 'Signals', icon: Radio, count: t => t.stats?.signals_proposed || 0 },
    { id: 'discover', label: 'Discover', icon: Search },
    { id: 'companies', label: 'Companies', icon: Building2 },
    { id: 'prospects', label: 'Contacts', icon: Users },
    { id: 'campaigns', label: 'Campaigns', icon: Send },
  ] },
  { label: 'System', items: [
    { id: 'activity', label: 'Activity', icon: Activity },
    { id: 'usage', label: 'Usage', icon: BarChart3 },
    { id: 'controls', label: 'Controls', icon: Power },
    { id: 'settings', label: 'Settings', icon: SlidersHorizontal },
    { id: 'help', label: 'Help', icon: HelpCircle },
  ] },
]

export function Shell() {
  const { tab, goTab } = useNav()
  const { locked } = useLockState()
  const qc = useQueryClient()
  // Nav counts stay live wherever you are, so "something needs me" is visible
  // from any tab. Today shares this query.
  const { data: today } = useApi<TodayData>('/api/today', { interval: 15000 })
  const { data: status } = useApi<{ running?: boolean }>('/api/harvey/status', { interval: 8000 })
  const { data: me } = useApi<{ role?: string; owner?: boolean; name?: string; email?: string }>('/api/auth/me')
  const theme = useThemeMode()
  const admin = !me || me.role === 'admin'

  useEffect(() => {
    if (me && me.role !== 'admin' && ADMIN_TABS.includes(tab)) goTab('today')
  }, [me, tab, goTab])

  const Page = PAGES[tab]
  const dot = locked ? 'bg-bad' : status === null ? 'bg-bad' : status?.running ? 'bg-good' : 'bg-text-3'
  const statusText = locked ? 'Locked' : status === null ? 'Offline' : status === undefined ? 'Checking…' : status.running ? 'Harvey is running' : 'Harvey is stopped'

  return (
    <div className="flex min-h-dvh">
      <aside className="sticky top-0 flex h-dvh w-[236px] shrink-0 flex-col border-r border-line bg-surface px-3 py-4">
        <div className="px-2 pb-4">
          <h1 className="font-serif text-2xl font-semibold leading-none">Harvey</h1>
          <div className="mt-1 text-[11px] uppercase tracking-widest text-text-3">Always Be Closing</div>
        </div>

        <nav aria-label="Sections" className="flex-1 space-y-5 overflow-y-auto">
          {GROUPS.map(g => (
            <div key={g.label}>
              <div className="px-2 pb-1 text-[11px] font-medium uppercase tracking-wide text-text-3">{g.label}</div>
              {g.items.filter(i => admin || !ADMIN_TABS.includes(i.id)).map(i => {
                const n = today && i.count ? i.count(today) : 0
                const Icon = i.icon
                return (
                  <button key={i.id} onClick={() => goTab(i.id)} aria-current={tab === i.id ? 'page' : undefined}
                    className={cn('flex h-8 w-full items-center gap-2.5 rounded-sm px-2 text-left text-[13px]',
                      tab === i.id ? 'bg-accent-soft font-medium text-accent' : 'text-text-2 hover:bg-surface-2 hover:text-text')}>
                    <Icon className="size-4 shrink-0" aria-hidden />
                    <span className="flex-1">{i.label}</span>
                    {n > 0 && <span className="rounded-full bg-accent px-1.5 text-[11px] font-medium leading-4 text-accent-text">{n}</span>}
                  </button>
                )
              })}
            </div>
          ))}
        </nav>

        <div className="space-y-2 border-t border-line pt-3">
          {me && !me.owner && (
            <div className="flex items-center justify-between gap-2 px-2 text-[12px]">
              <span className="truncate text-text-2">{me.name || me.email}</span>
              <Button variant="ghost" size="sm" onClick={async () => { await authFetch('/api/auth/logout', { method: 'POST' }); window.location.reload() }}>Sign out</Button>
            </div>
          )}
          <div className="flex items-center gap-2 px-2 text-[12px] text-text-2">
            <span className={cn('size-2 rounded-full', dot, status?.running && !locked && 'animate-[pulse-dot_2s_ease-in-out_infinite]')} />
            {statusText}
          </div>
          <div className="flex items-center justify-between px-1">
            <Button variant="ghost" size="sm" onClick={cycleTheme} title="Appearance: follows your system until you pick one">{theme}</Button>
            <Button variant="ghost" size="sm" onClick={() => { qc.invalidateQueries(); void api('/api/today') }} title="Refresh this page">
              <RefreshCw className="size-3" aria-hidden />Refresh
            </Button>
          </div>
        </div>
      </aside>

      <main className="min-w-0 flex-1">
        <div className="mx-auto max-w-[1180px] px-8 py-8">
          <Suspense fallback={<Skeleton rows={4} />}>
            <Page key={tab} />
          </Suspense>
        </div>
      </main>
      <Notifications />
    </div>
  )
}
