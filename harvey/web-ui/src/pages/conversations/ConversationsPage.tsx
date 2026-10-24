import { useEffect, useMemo, useRef, useState } from 'react'
import { Check, MessageSquare } from 'lucide-react'
import { useApi, useInvalidate } from '@/lib/hooks'
import { useNav } from '@/app/nav'
import { Card, SectionHead } from '@/components/ui/card'
import { EmptyState, OfflineState, Skeleton } from '@/components/ui/states'
import { cn } from '@/lib/utils'
import { ConversationCard } from './ConversationCard'
import { ConversationList, PAGE } from './ConversationList'
import type { Convo } from './types'

type FilterId = 'needs' | 'open' | 'closed' | 'all'
const FILTERS: [FilterId, string, (c: Convo) => boolean][] = [
  ['needs', 'Needs you', c => !!c.draft || c.status === 'needs_human'],
  ['open', 'Open', c => c.status !== 'closed'],
  ['closed', 'Closed', c => c.status === 'closed'],
  ['all', 'All', () => true],
]
const KEY = 'harvey-convo'
const recall = () => { try { return localStorage.getItem(KEY) || '' } catch { return '' } }
const remember = (v: string) => { try { localStorage.setItem(KEY, v) } catch { /* private mode */ } }

// Handed-to-you first, then drafts waiting; otherwise newest first (as served).
const rank = (c: Convo) => (c.status === 'needs_human' ? 0 : c.draft ? 1 : 2)

export function ConversationsPage() {
  const { params, clearParams } = useNav()
  const invalidate = useInvalidate()
  const { data, refetch } = useApi<Convo[]>('/api/conversations', { interval: 4000 })
  const [filter, setFilter] = useState<FilterId>('needs')
  const [sel, setSel] = useState(recall)
  const [page, setPage] = useState(0)
  const jump = useRef(!!sel)   // land on the page that holds the selected conversation
  const autoFilter = useRef(true)

  // Deep link from a notification.
  useEffect(() => {
    if (!params.conversation) return
    setFilter('all'); setSel(params.conversation); jump.current = true; autoFilter.current = false
    clearParams()
  }, [params.conversation, clearParams])

  // Open on "Needs you" only when something needs you.
  useEffect(() => {
    if (!data || !autoFilter.current) return
    autoFilter.current = false
    if (filter === 'needs' && !data.some(FILTERS[0][2])) setFilter('open')
  }, [data, filter])

  const shown = useMemo(() => {
    const f = FILTERS.find(x => x[0] === filter) || FILTERS[3]
    return (data || []).filter(f[2]).map((c, i) => [c, i] as const)
      .sort((a, b) => rank(a[0]) - rank(b[0]) || a[1] - b[1]).map(x => x[0])
  }, [data, filter])

  useEffect(() => {
    if (!shown.length) return
    const at = shown.findIndex(c => c.id === sel)
    if (at < 0) { setSel(shown[0].id); setPage(0) }
    else if (jump.current) { setPage(Math.floor(at / PAGE)); jump.current = false }
  }, [shown, sel])

  if (data === undefined) return <><Head /><Skeleton rows={5} /></>
  if (!data) return <><Head /><OfflineState /></>
  if (!data.length) {
    return (
      <><Head />
        <EmptyState icon={<MessageSquare className="size-5" />} title="No conversations yet">
          No prospects have replied so far. When they do, the Handler agent classifies each reply and responds — every thread shows up here.
        </EmptyState></>
    )
  }

  const pages = Math.max(1, Math.ceil(shown.length / PAGE))
  const selected = shown.find(c => c.id === sel)

  return (
    <>
      <Head />
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div role="group" aria-label="Filter conversations" className="inline-flex gap-0.5 rounded-md bg-surface-2 p-0.5">
          {FILTERS.map(([id, label, test]) => (
            <button key={id} type="button" aria-pressed={id === filter}
              onClick={() => { setFilter(id); setPage(0) }}
              className={cn('rounded-sm px-3 py-1 text-[13px] font-medium text-text-2', id === filter && 'bg-surface text-text shadow-card')}>
              {label} <span className="font-medium text-text-3">{data.filter(test).length}</span>
            </button>
          ))}
        </div>
      </div>
      {!shown.length ? (
        <Card><EmptyState icon={<Check className="size-5" />} title="Nothing here">No conversation matches this filter.</EmptyState></Card>
      ) : (
        <div className="grid min-h-[480px] gap-4 min-[901px]:h-[calc(100vh-250px)] min-[901px]:grid-cols-[minmax(260px,340px)_minmax(0,1fr)]">
          <ConversationList shown={shown} page={Math.min(page, pages - 1)} sel={selected ? sel : shown[0].id}
            onSelect={id => { setSel(id); remember(id) }} onPage={d => setPage(Math.max(0, Math.min(page + d, pages - 1)))} />
          <section className="h-[78vh] min-h-0 overflow-hidden rounded-lg bg-surface shadow-card min-[901px]:h-auto">
            {(selected || shown[0]) && (
              <ConversationCard key={(selected || shown[0]).id} c={selected || shown[0]}
                onChanged={() => { autoFilter.current = true; refetch(); invalidate('/api/today') }} />
            )}
          </section>
        </div>
      )}
    </>
  )
}

function Head() {
  return <SectionHead title="Conversations">Every reply, and how Harvey handled it.</SectionHead>
}
