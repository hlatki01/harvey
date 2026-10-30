import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Search, X } from 'lucide-react'
import { Card, Subhead } from '@/components/ui/card'
import { StatusBadge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/field'
import { formatDate } from '@/lib/format'
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

const who = (r: OutboxItem) => r.company ? `${r.company} · ${r.to_email}` : r.to_email
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
            <div className="truncate text-[15px] font-semibold">{r.company || r.to_email}</div>
            <div className="text-[12px] text-text-2">{r.company ? r.to_email + ' · ' : ''}{what(r)} · {r.channel === 'whatsapp' ? 'WhatsApp' : 'Email'}</div>
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
        {row('Sent', r.sent_at ? formatDate(r.sent_at) : '')}
        {row('Last change', r.status === 'sent' ? '' : formatDate(r.updated_at))}
        {r.channel !== 'whatsapp' && row('Subject', r.subject)}
        <div className="mt-3 whitespace-pre-wrap break-words rounded-md bg-surface-2 p-3 text-[13px] leading-relaxed">{r.body || <span className="text-text-3">No text.</span>}</div>
      </div>
    </div>
  )
}

function Table({ title, group, cols, onOpen }: { title: string; group: Group; cols: Col[]; onOpen: (r: OutboxItem) => void }) {
  const [typed, setTyped] = useState('')
  const [q, setQ] = useState('')
  const [page, setPage] = useState(1)
  useEffect(() => {   // search as they type, without a request per key
    const t = setTimeout(() => { setQ(typed.trim()); setPage(1) }, 300)
    return () => clearTimeout(t)
  }, [typed])
  const { data } = useApi<Page>(`/api/outbox/list?group=${group}&q=${encodeURIComponent(q)}&page=${page}`, { interval: 15000 })
  const total = data?.total ?? 0
  const per = data?.per_page || 25
  const pages = Math.max(1, Math.ceil(total / per))
  if (!data || (!total && !q)) return null
  const rows = data.rows || []
  return (
    <>
      <Subhead hint={total}>{title}</Subhead>
      <div className="relative mb-2 w-72">
        <Search className="pointer-events-none absolute left-2.5 top-2 size-4 text-text-3" aria-hidden />
        <Input type="search" aria-label={'Search ' + title} placeholder="Search number, company, text, reason" className="w-full pl-8"
          value={typed} onChange={e => setTyped(e.target.value)} />
      </div>
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

export function OutboxTables() {
  const [open, setOpen] = useState<OutboxItem | null>(null)
  return (
    <>
      <Table title="Approved & scheduled" group="approved" onOpen={setOpen} cols={[
        { head: 'To', cell: who },
        { head: 'Message', cell: what },
        { head: 'Text', muted: true, cell: r => r.body },
        { head: 'Sends', muted: true, cell: r => r.status === 'sending' ? <StatusBadge status={r.status} /> : formatDate(r.send_at) },
      ]} />
      <Table title="Recently sent" group="sent" onOpen={setOpen} cols={[
        { head: 'To', cell: who },
        { head: 'Message', cell: what },
        { head: 'Text', muted: true, cell: r => r.body },
        { head: 'Sent', muted: true, cell: r => formatDate(r.sent_at) },
      ]} />
      <Table title="Didn't send" group="failed" onOpen={setOpen} cols={[
        { head: 'To', cell: who },
        { head: 'Status', cell: r => <StatusBadge status={r.status} /> },
        { head: 'Reason', muted: true, cell: r => reason(r.error) },
        { head: 'Updated', muted: true, cell: r => formatDate(r.updated_at) },
      ]} />
      {open && <Detail r={open} onClose={() => setOpen(null)} />}
    </>
  )
}
