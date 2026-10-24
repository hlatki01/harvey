import { NumberTicker } from '@/components/ui/number-ticker'
import { cn } from '@/lib/utils'

export interface TodayStats {
  prospects?: number; outbox_pending?: number; outbox_approved?: number; needs_human?: number
  live_conversations?: number | null; open_conversations?: number; email_provider?: string
  companies?: number; unprofiled?: number; signals_confirmed?: number
  whatsapp?: { enabled?: boolean; sent_today?: number; cap?: number; chats?: number; connected?: boolean | null }
}
export interface Trend { days?: unknown[]; found?: number[]; sent?: number[]; sent_whatsapp?: number[]; replies?: number[] }

const sum = (a?: number[]) => (a || []).reduce((x, y) => x + (Number(y) || 0), 0)

/** Columns of a tiny bar chart; the last day is "today". */
function SparkBars({ values, title }: { values?: number[]; title: string }) {
  const vals = (values || []).map(v => Number(v) || 0)
  if (!vals.length) return null
  const max = Math.max(...vals, 1)
  return (
    <span className="flex h-7 items-end gap-px" role="img" aria-label={title}>
      {vals.map((v, i) => (
        <i key={i} title={String(v)} className={cn('w-1 rounded-t-xs', v ? 'bg-accent' : 'bg-surface-3')}
          style={{ height: Math.max(2, Math.round(v / max * 28)) }} />
      ))}
    </span>
  )
}

function Kpi({ label, value, spark, note }: { label: string; value: number | null; spark?: React.ReactNode; note: React.ReactNode }) {
  return (
    <div className="rounded-lg bg-surface p-4 shadow-card">
      <div className="text-[12px] text-text-3">{label}</div>
      <div className="my-1 flex items-end justify-between gap-2">
        {value === null
          ? <span className="text-[28px] font-semibold leading-none text-text-3">&mdash;</span>
          : <NumberTicker value={value} className={cn('text-[28px] font-semibold leading-none tracking-normal', value ? 'text-text dark:text-text' : 'text-text-3 dark:text-text-3')} />}
        {spark}
      </div>
      <span className="text-[12px] text-text-2">{note}</span>
    </div>
  )
}

export function Kpis({ stats, trend }: { stats: TodayStats; trend: Trend | null }) {
  const t = trend || {}
  const wa = stats.whatsapp || {}
  const sentWa = sum(t.sent_whatsapp), sentAll = sum(t.sent), sentEmail = sentAll - sentWa
  const waiting = (stats.outbox_pending || 0) + (stats.needs_human || 0)
  const waitNote = [
    stats.outbox_pending ? stats.outbox_pending + ' to approve' : '',
    stats.needs_human ? stats.needs_human + ' handed to you' : '',
  ].filter(Boolean).join(' · ') || 'nothing to approve'
  // Instantly sends from its own servers, so the Outbox never sees them.
  const instantly = stats.email_provider === 'instantly' && !wa.enabled
  return (
    <div className="mb-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <Kpi label="Contacts found" value={stats.prospects || 0}
        spark={<SparkBars values={t.found} title="Contacts found per day, last 14 days" />}
        note={<><b>+{sum((t.found || []).slice(-7))}</b> this week</>} />
      {instantly
        ? <Kpi label="Emails sent" value={null} note="Instantly tracks sends" />
        : <Kpi label={wa.enabled ? 'Messages sent' : 'Emails sent'} value={sentAll}
            spark={<SparkBars values={t.sent} title="Messages sent per day, last 14 days" />}
            note={<>{wa.enabled ? <><b>{sentWa}</b> WhatsApp · <b>{sentEmail}</b> email</> : 'last 14 days'} · <b>{stats.outbox_approved || 0}</b> scheduled</>} />}
      <Kpi label="Conversations" value={stats.live_conversations != null ? stats.live_conversations : (stats.open_conversations || 0)}
        spark={<SparkBars values={t.replies} title="New conversations per day, last 14 days" />}
        note={<><b>{sum(t.replies)}</b> new in 14 days</>} />
      <Kpi label="Waiting on you" value={waiting} note={waitNote} />
    </div>
  )
}

export function Facts({ stats }: { stats: TodayStats }) {
  const wa = stats.whatsapp || {}
  return (
    <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-text-2">
      {wa.enabled && <>
        <span><b>{wa.sent_today || 0}/{wa.cap || 0}</b> WhatsApp sent today</span>
        <span><b>{wa.chats || 0}</b> WhatsApp chats</span>
        <span className={wa.connected ? 'text-good' : 'text-text-3'}>
          WhatsApp {wa.connected ? 'connected' : wa.connected === false ? 'offline' : 'unchecked'}
        </span>
      </>}
      <span><b>{stats.companies || 0}</b> companies</span>
      <span><b>{stats.unprofiled || 0}</b> sites not read yet</span>
      <span><b>{stats.signals_confirmed || 0}</b> signals on</span>
    </div>
  )
}
