import { useState } from 'react'
import { AlertTriangle, ArrowRight, Check, Flag } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, SectionHead, Subhead } from '@/components/ui/card'
import { EmptyState, Muted, OfflineState, Skeleton } from '@/components/ui/states'
import { post } from '@/lib/api'
import { own, relTime } from '@/lib/format'
import { useApi } from '@/lib/hooks'
import { useNav, type TabId } from '@/app/nav'
import { cn } from '@/lib/utils'
import { Facts, Kpis, type TodayStats, type Trend } from './today/Kpis'
import { LiveStatus } from './today/LiveStatus'
import { Funnel, Runs, Setup } from './today/Side'
import { TimelineItem, type ActivityRow } from './today/timeline'

interface Item { tone?: string; title?: string; detail?: string; action?: string; tab?: string }
interface TodayData { error?: string; items?: Item[]; stats?: TodayStats }
interface Lesson { id: string; text?: string }

const TONE: Record<string, string> = { bad: 'bg-bad-bg text-bad', warn: 'bg-wait-bg text-wait', good: 'bg-good-bg text-good' }
const GLYPH = { bad: AlertTriangle, warn: Flag, good: Check }

function Lessons() {
  const { data, refetch } = useApi<{ lessons?: Lesson[] }>('/api/lessons', { interval: 15000 })
  const [busy, setBusy] = useState('')
  const rows = data?.lessons || []
  if (!rows.length) return null
  const decide = async (id: string, decision: 'approve' | 'reject') => {
    setBusy(id)
    await post('/api/lessons/' + id + '/' + decision)
    await refetch()
    setBusy('')
  }
  return (
    <>
      <Subhead>Theo learned from your corrections</Subhead>
      <div className="space-y-2">
        {rows.map(l => (
          <div key={l.id} className="flex items-center gap-3 rounded-lg bg-surface p-3 shadow-card">
            <div className="flex-1 font-medium">{l.text}</div>
            <Button size="sm" busy={busy === l.id} onClick={() => decide(l.id, 'approve')}>Approve</Button>
            <Button size="sm" busy={busy === l.id} onClick={() => decide(l.id, 'reject')}>Reject</Button>
          </div>
        ))}
      </div>
    </>
  )
}

function Queue({ data }: { data: TodayData | null | undefined }) {
  const { goTab } = useNav()
  if (data === undefined) return <Skeleton rows={2} />
  if (!data) return <OfflineState />
  // A failed check must never read as "Nothing needs you".
  if (data.error) {
    return <EmptyState icon={<AlertTriangle className="size-5" />} title="Couldn't check what needs you">
      The dashboard hit an error reading Harvey's data: {data.error}
    </EmptyState>
  }
  const items = data.items || []
  if (!items.length) {
    return (
      <div className="flex items-start gap-3 rounded-lg bg-surface p-4 shadow-card">
        <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-good-bg text-good"><Check className="size-4" aria-hidden /></span>
        <div>
          <div className="font-medium">Nothing needs you</div>
          <div className="text-text-2">Harvey has everything it needs. Anything that requires a decision — an email to approve, a signal to confirm — shows up here.</div>
        </div>
      </div>
    )
  }
  return (
    <div className="space-y-2">
      {items.map((it, i) => {
        const tone = it.tone || 'warn'
        const Icon = own(GLYPH, tone) ?? Flag
        return (
          <div key={i} className="flex items-center gap-3 rounded-lg bg-surface p-3 shadow-card">
            <span className={cn('flex size-8 shrink-0 items-center justify-center rounded-full', own(TONE, tone) ?? TONE.warn)}><Icon className="size-4" aria-hidden /></span>
            <div className="min-w-0 flex-1">
              <div className="font-medium">{it.title}</div>
              <div className="text-text-2">{it.detail}</div>
            </div>
            <Button size="sm" onClick={() => goTab(it.tab as TabId)}>{it.action}<ArrowRight className="size-3.5" aria-hidden /></Button>
          </div>
        )
      })}
    </div>
  )
}

function RecentActivity() {
  const { data } = useApi<ActivityRow[]>('/api/activity', { interval: 15000 })
  const { goTab } = useNav()
  if (!Array.isArray(data)) return <Subhead hint={<button className="text-accent underline" onClick={() => goTab('activity')}>See all</button>}>Recent activity</Subhead>
  // Heartbeats are noise here; the Activity tab keeps them.
  const shown = data.filter(a => a.agent !== 'main').slice(0, 8)
  return (
    <>
      <Subhead hint={<button className="text-accent underline" onClick={() => goTab('activity')}>See all</button>}>Recent activity</Subhead>
      <Card>
        {shown.length
          ? shown.map((a, i) => <TimelineItem key={i} a={a} time={relTime(a.created_at)} />)
          : <Muted>Nothing yet. Every action Harvey takes shows up here.</Muted>}
      </Card>
    </>
  )
}

export function TodayPage() {
  // Same key as the shell's poll, so this shares its cache.
  const { data } = useApi<TodayData>('/api/today', { interval: 15000 })
  const trend = useApi<Trend>('/api/trend?days=14&tz=' + -new Date().getTimezoneOffset(), { interval: 15000 }).data
  const stats = data && !data.error ? data.stats || {} : null
  return (
    <div>
      <SectionHead title="Today">What's waiting on you, and how the pipeline is moving.</SectionHead>
      {stats && <Kpis stats={stats} trend={trend && Array.isArray(trend.days) ? trend : null} />}
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div>
          <Subhead>Needs you</Subhead>
          <Queue data={data} />
          <Lessons />
          <Subhead>Pipeline right now</Subhead>
          <Card><LiveStatus /></Card>
          <RecentActivity />
        </div>
        <aside>
          <Subhead>Contacts by stage</Subhead>
          <Card><Funnel /></Card>
          {stats && <Facts stats={stats} />}
          <Runs />
          <Setup />
        </aside>
      </div>
    </div>
  )
}
