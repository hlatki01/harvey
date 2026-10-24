import { useEffect, useRef, useState } from 'react'
import { Archive, Bell, BellOff, Trash2, Undo2 } from 'lucide-react'
import { api } from '@/lib/api'
import { useApi, useInvalidate } from '@/lib/hooks'
import { relTime } from '@/lib/format'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { useNav, type TabId } from './nav'

// What needs your attention, one line per thing. Opening one goes straight to it.
// Mute stops a thing from alerting you; archive files it away; delete drops it
// (it comes back only if it happens again).

type View = 'inbox' | 'archived' | 'muted'
const VIEWS: [View, string][] = [['inbox', 'Inbox'], ['archived', 'Archived'], ['muted', 'Muted']]
const EMPTY: Record<View, string> = {
  inbox: 'Nothing needs your attention.', archived: 'Nothing archived.', muted: 'Nothing muted.',
}
const DOT: Record<string, string> = { good: 'bg-good', bad: 'bg-bad', warn: 'bg-wait' }

interface Notif { id: string; title: string; detail?: string; tone?: string; status: string; updated_at?: string; tab?: string; target?: string }

export function Notifications() {
  const [open, setOpen] = useState(false)
  const [view, setView] = useState<View>('inbox')
  const { data, refetch } = useApi<{ items: Notif[]; unread: number }>('/api/notifications?view=' + view, { interval: 10000 })
  const invalidate = useInvalidate()
  const { goTab } = useNav()
  const root = useRef<HTMLDivElement>(null)
  const items = data?.items ?? []
  const unread = data?.unread ?? 0

  useEffect(() => {
    if (!open) return
    const click = (e: MouseEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false) }
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('click', click)
    document.addEventListener('keydown', key)
    return () => { document.removeEventListener('click', click); document.removeEventListener('keydown', key) }
  }, [open])

  const done = () => { invalidate('/api/notifications'); refetch() }
  const act = async (id: string, action: string) => {
    await api('/api/notifications/' + encodeURIComponent(id) + '/' + action, { method: 'POST' }); done()
  }
  const del = async (id: string) => {
    await api('/api/notifications/' + encodeURIComponent(id), { method: 'DELETE' }); done()
  }
  const readAll = async () => { await api('/api/notifications/read-all', { method: 'POST' }); done() }
  const openOne = (n: Notif) => {
    if (n.status === 'new') api('/api/notifications/' + encodeURIComponent(n.id) + '/read', { method: 'POST' })
    setOpen(false)
    goTab((n.tab as TabId) || 'today', n.tab === 'conversations' && n.target ? { conversation: n.target } : {})
    done()
  }

  return (
    <div ref={root} className="fixed right-5 top-4 z-[900]">
      <button
        aria-label={'Notifications' + (unread ? ', ' + unread + ' unread' : '')}
        aria-haspopup="true" aria-expanded={open}
        onClick={() => setOpen(o => !o)}
        className="relative grid size-9 place-items-center rounded-full bg-surface text-text-2 shadow-card hover:text-text"
      >
        <Bell className="size-4" />
        {unread > 0 && (
          <span className="absolute -right-1 -top-1 min-w-4 rounded-full bg-accent px-1 text-center text-[10px] font-semibold leading-4 text-accent-text">
            {unread > 99 ? '99+' : unread}
          </span>
        )}
      </button>

      {open && (
        <div role="dialog" aria-label="Notifications" className="absolute right-0 mt-2 w-[360px] max-w-[calc(100vw-2.5rem)] rounded-lg bg-surface shadow-pop">
          <div className="flex items-center justify-between px-3.5 pt-3">
            <b>Notifications</b>
            {unread > 0 && <Button variant="ghost" size="sm" onClick={readAll}>Mark all read</Button>}
          </div>
          <div className="flex gap-1 px-3 pb-1 pt-2">
            {VIEWS.map(([v, label]) => (
              <button key={v} onClick={() => setView(v)}
                className={cn('rounded-sm px-2 py-1 text-[12px]', v === view ? 'bg-surface-3 font-medium' : 'text-text-2 hover:bg-surface-2')}>
                {label}
              </button>
            ))}
          </div>
          <div className="max-h-[60vh] overflow-y-auto">
            {items.length === 0 && <p className="px-4 py-6 text-center text-text-3">{EMPTY[view]}</p>}
            {items.map(n => {
              const muted = n.status === 'muted', arch = n.status === 'archived'
              return (
                <div key={n.id} className={cn('flex items-start gap-1 border-t border-line px-2 py-2', n.status === 'new' && 'bg-accent-soft')}>
                  <button className="flex min-w-0 flex-1 items-start gap-2 rounded-sm p-1 text-left hover:bg-surface-2" onClick={() => openOne(n)}>
                    <span className={cn('mt-1.5 size-2 shrink-0 rounded-full', DOT[n.tone || 'warn'] ?? 'bg-wait')} />
                    <span className="min-w-0">
                      <b className="block truncate">{n.title}</b>
                      {n.detail && <span className="block text-text-2">{n.detail}</span>}
                      <em className="text-[11px] not-italic text-text-3">{relTime(n.updated_at)}</em>
                    </span>
                  </button>
                  <span className="flex shrink-0 gap-0.5">
                    <IconBtn label={muted ? 'Unmute' : 'Mute'} title={muted ? 'Unmute' : 'Mute: stop alerting me about this'}
                      onClick={() => act(n.id, muted ? 'unmute' : 'mute')}>{muted ? <Bell className="size-4" /> : <BellOff className="size-4" />}</IconBtn>
                    <IconBtn label={arch ? 'Unarchive' : 'Archive'} title={arch ? 'Move back to inbox' : 'Archive'}
                      onClick={() => act(n.id, arch ? 'unarchive' : 'archive')}>{arch ? <Undo2 className="size-4" /> : <Archive className="size-4" />}</IconBtn>
                    <IconBtn label="Delete" title="Delete" onClick={() => del(n.id)}><Trash2 className="size-4" /></IconBtn>
                  </span>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

function IconBtn({ label, title, onClick, children }: { label: string; title: string; onClick: () => void; children: React.ReactNode }) {
  return (
    <button aria-label={label} title={title} onClick={onClick} className="grid size-7 place-items-center rounded-sm text-text-3 hover:bg-surface-2 hover:text-text">
      {children}
    </button>
  )
}
