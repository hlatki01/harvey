import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Search, X } from 'lucide-react'
import { Card, Subhead } from '@/components/ui/card'
import { useNav } from '@/app/nav'
import { cn } from '@/lib/utils'
import { StatusBadge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/field'
import { formatDate, parseTs } from '@/lib/format'
import { useApi } from '@/lib/hooks'
import type { OutboxItem } from './types'

type Group = 'approved' | 'sent' | 'failed'
interface Page { rows?: OutboxItem[]; total?: number; page?: number; per_page?: number }
interface Col { head: string; cell: (r: OutboxItem) => ReactNode; muted?: boolean }

// Why something didn't go out, in words; anything else is shown as Harvey wrote it.
const REASONS: [RegExp, string][] = [
  [/^stop_on_reply$/, 'They replied, so the rest of the sequence stopped.'],
  [/^team answered$/, 'The team wrote in the chat first, so this would have talked over them.'],
  [/^replaced by follow-up$/, 'Replaced by the follow-up set when the chat was handed back.'],
  [/^conversation closed$/, 'The conversation was closed.'],
  [/^already contacted$/, 'This person had already been contacted.'],
  [/^gate: /, 'Blocked by the safety check: '],
]

export function reason(error?: string) {
  if (!error) return ''
  for (const [re, text] of REASONS) if (re.test(error)) return text.endsWith(': ') ? text + error.replace(re, '') : text
  return error
}

// When it goes out, not just when it was first scheduled: an overdue row waits
// its turn, and one outside business hours waits for the next opening.
function sends(r: OutboxItem) {
  const overdue = !!r.send_at && parseTs(r.send_at).getTime() < Date.now()
  if (r.sends_from) return (overdue ? 'Overdue · from ' : 'From ') + formatDate(r.sends_from)
  return overdue ? 'Overdue · next in line' : formatDate(r.send_at)
}

const who = (r: OutboxItem) => r.company ? `${r.company} · ${r.recipient || r.to_email}` : r.recipient || r.to_email
const what = (r: OutboxItem) => r.kind === 'reply' ? 'Reply' : r.step && Number(r.step) > 1 ? `Follow-up ${Number(r.step) - 1}` : 'Opening'

function Detail({ r, onClose }: { r: OutboxItem; onClose: () => void }) {
  const closeBtn = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    closeBtn.current?.focus()
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  const row = (k: string, v: ReactNode) => v ? <div className="flex justify-between gap-3 py-[3px] text-[13px]"><span className="text-text-3">{k}</span><b className="break-words text-right font-medium">{v}</b></div> : null
  return (
    <div className="fixed inset-0 z-[1000] grid place-items-center bg-black/50 p-4" onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
      <div role="dialog" aria-modal="true" aria-label={'Message to ' + who(r)} className="max-h-[85vh] w-full max-w-[560px] overflow-y-auto rounded-lg bg-surface p-5 shadow-pop">
        <div className="mb-3 flex items-start gap-2">
          <div className="min-w-0 flex-1">
            <div className="truncate text-[15px] font-semibold">{r.company || r.recipient || r.to_email}</div>
            <div className="text-[12px] text-text-2">{r.company ? (r.recipient || r.to_email) + ' · ' : ''}{what(r)} · {r.channel === 'whatsapp' ? 'WhatsApp' : r.channel === 'instagram' ? 'Instagram' : 'Email'}</div>
          </div>
          <StatusBadge status={r.status} />
          <Button ref={closeBtn} variant="ghost" size="sm" aria-label="Close" onClick={onClose}><X className="size-4" /></Button>
        </div>
        {r.error && (
          <div className="mb-3 rounded-md bg-wait-bg px-3 py-2 text-[13px] shadow-[inset_0_0_0_1px_var(--color-wait-line)]">
            <b className="font-medium">Why it didn't send:</b> {reason(r.error)}
          </div>
        )}
        {row('Scheduled for', r.status === 'sent' ? '' : formatDate(r.send_at))}
        {row('Goes out', r.status === 'approved' ? sends(r) : '')}
        {row('Sent', r.sent_at ? formatDate(r.sent_at) : '')}
        {row('Last change', r.status === 'sent' ? '' : formatDate(r.updated_at))}
        {r.channel !== 'whatsapp' && r.channel !== 'instagram' && row('Subject', r.subject)}
        <div className="mt-3 whitespace-pre-wrap break-words rounded-md bg-surface-2 p-3 text-[13px] leading-relaxed">{r.body || <span className="text-text-3">No text.</span>}</div>
      </div>
    </div>
  )
}

function SearchBox({ value, onChange, label }: { value: string; onChange: (v: string) => void; label: string }) {
  return (
    <div className="relative mb-2 w-72">
      <Search className="pointer-events-none absolute left-2.5 top-2 size-4 text-text-3" aria-hidden />
      <Input type="search" aria-label={'Search ' + label} placeholder="Search number, company, text, reason" className="w-full pl-8"
        value={value} onChange={e => onChange(e.target.value)} />
    </div>
  )
}

function useSearch() {
  const [typed, setTyped] = useState('')
  const [q, setQ] = useState('')
  useEffect(() => {   // search as they type, without a request per key
    const t = setTimeout(() => setQ(typed.trim()), 300)
    return () => clearTimeout(t)
  }, [typed])
  return { typed, setTyped, q }
}

function Table({ title, group, cols, onOpen }: { title: string; group: Group; cols: Col[]; onOpen: (r: OutboxItem) => void }) {
  const { typed, setTyped, q } = useSearch()
  const [page, setPage] = useState(1)
  useEffect(() => setPage(1), [q])
  const { data } = useApi<Page>(`/api/outbox/list?group=${group}&q=${encodeURIComponent(q)}&page=${page}`, { interval: 15000 })
  const total = data?.total ?? 0
  const per = data?.per_page || 25
  const pages = Math.max(1, Math.ceil(total / per))
  if (!data) return null
  if (!total && !q) return <p className="py-6 text-center text-[13px] text-text-2">Nothing here.</p>
  const rows = data.rows || []
  return (
    <>
      <SearchBox value={typed} onChange={setTyped} label={title} />
      <Card flush className="overflow-x-auto">
        <table className="w-full whitespace-nowrap text-left text-[13px]">
          <thead>
            <tr className="border-b border-line text-[12px] text-text-3">
              {cols.map(c => <th key={c.head} className="px-3 py-2 font-medium">{c.head}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.id} tabIndex={0} title="See the message" className="cursor-pointer border-b border-line last:border-0 hover:bg-surface-2 focus-visible:bg-surface-2 focus-visible:outline-none"
                onClick={() => onOpen(r)} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onOpen(r) } }}>
                {cols.map(c => (
                  <td key={c.head} className={'max-w-[320px] truncate px-3 py-2' + (c.muted ? ' text-text-2' : '')}>{c.cell(r)}</td>
                ))}
              </tr>
            ))}
            {!rows.length && <tr><td colSpan={cols.length} className="px-3 py-4 text-center text-text-2">Nothing matches that search.</td></tr>}
          </tbody>
        </table>
      </Card>
      {pages > 1 && (
        <div className="mt-2 flex items-center justify-end gap-2 text-[12px] text-text-2">
          <span className="tabular-nums">{(page - 1) * per + 1}–{Math.min(page * per, total)} of {total}</span>
          <Button size="sm" variant="ghost" disabled={page <= 1} onClick={() => setPage(p => p - 1)}>Previous</Button>
          <Button size="sm" variant="ghost" disabled={page >= pages} onClick={() => setPage(p => p + 1)}>Next</Button>
        </div>
      )}
    </>
  )
}

interface FollowUp {
  conversation_id: string; follow_up_at: string; follow_ups: number
  company?: string | null; phone?: string | null; last: { sender: string; content: string }
}

// Planned, not written: Harvey writes each one from the chat when it comes due.
function FollowUps() {
  const { goTab } = useNav()
  const { typed, setTyped, q } = useSearch()
  const { data } = useApi<{ rows: FollowUp[]; total: number }>(`/api/outbox/follow-ups?q=${encodeURIComponent(q)}`, { interval: 15000 })
  if (!data) return null
  if (!data.total && !q) {
    return <p className="py-6 text-center text-[13px] text-text-2">No follow-ups planned. When a lead says they'll get back to you, Roadie Crew plans one here.</p>
  }
  const open = (r: FollowUp) => goTab('conversations', { conversation: r.conversation_id })
  return (
    <>
      <SearchBox value={typed} onChange={setTyped} label="follow-ups" />
      <p className="mb-2 text-[12px] text-text-3">Written from the conversation when it comes due, so it takes in anything said until then. Their reply cancels it.</p>
      <Card flush className="overflow-x-auto">
        <table className="w-full whitespace-nowrap text-left text-[13px]">
          <thead>
            <tr className="border-b border-line text-[12px] text-text-3">
              {['To', 'Message', 'Last in the chat', 'Goes out'].map(h => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}
            </tr>
          </thead>
          <tbody>
            {data.rows.map(r => (
              <tr key={r.conversation_id} tabIndex={0} title="Open the conversation" className="cursor-pointer border-b border-line last:border-0 hover:bg-surface-2 focus-visible:bg-surface-2 focus-visible:outline-none"
                onClick={() => open(r)} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(r) } }}>
                <td className="max-w-[320px] truncate px-3 py-2">{r.company ? `${r.company} · ${r.phone}` : r.phone}</td>
                <td className="px-3 py-2">{r.follow_ups ? 'Last follow-up' : 'Follow-up'}</td>
                <td className="max-w-[320px] truncate px-3 py-2 text-text-2">{r.last.sender === 'prospect' ? '' : 'You: '}{r.last.content}</td>
                <td className="px-3 py-2 text-text-2">{formatDate(r.follow_up_at)}</td>
              </tr>
            ))}
            {!data.rows.length && <tr><td colSpan={4} className="px-3 py-4 text-center text-text-2">Nothing matches that search.</td></tr>}
          </tbody>
        </table>
      </Card>
    </>
  )
}

type TabKey = 'approved' | 'follow_ups' | 'sent' | 'failed'
const TABS: [TabKey, string][] = [['approved', 'Scheduled'], ['follow_ups', 'Follow-ups'], ['sent', 'Recently sent'], ['failed', "Didn't send"]]

export function OutboxTables() {
  const [open, setOpen] = useState<OutboxItem | null>(null)
  const [tab, setTab] = useState<TabKey>('approved')
  const { data: counts } = useApi<Record<TabKey, number>>('/api/outbox/counts', { interval: 15000 })
  return (
    <>
      <Subhead>Queue and history</Subhead>
      <div role="tablist" aria-label="Outbox" className="mb-3 flex w-fit flex-wrap gap-1 rounded-md bg-surface-2 p-1 text-[13px]">
        {TABS.map(([key, label]) => (
          <button key={key} type="button" role="tab" aria-selected={tab === key} onClick={() => setTab(key)}
            className={cn('rounded-sm px-3 py-1', tab === key ? 'bg-surface font-medium shadow-sm' : 'text-text-2 hover:text-text')}>
            {label} <span className="tabular-nums text-text-3">{counts?.[key] ?? ''}</span>
          </button>
        ))}
      </div>
      {tab === 'approved' && <Table title="scheduled" group="approved" onOpen={setOpen} cols={[
        { head: 'To', cell: who },
        { head: 'Message', cell: what },
        { head: 'Text', muted: true, cell: r => r.body },
        { head: 'Sends', muted: true, cell: r => r.status === 'sending' ? <StatusBadge status={r.status} /> : sends(r) },
      ]} />}
      {tab === 'follow_ups' && <FollowUps />}
      {tab === 'sent' && <Table title="sent" group="sent" onOpen={setOpen} cols={[
        { head: 'To', cell: who },
        { head: 'Message', cell: what },
        { head: 'Text', muted: true, cell: r => r.body },
        { head: 'Sent', muted: true, cell: r => formatDate(r.sent_at) },
      ]} />}
      {tab === 'failed' && <Table title="didn't send" group="failed" onOpen={setOpen} cols={[
        { head: 'To', cell: who },
        { head: 'Status', cell: r => <StatusBadge status={r.status} /> },
        { head: 'Reason', muted: true, cell: r => reason(r.error) },
        { head: 'Updated', muted: true, cell: r => formatDate(r.updated_at) },
      ]} />}
      {open && <Detail r={open} onClose={() => setOpen(null)} />}
    </>
  )
}
