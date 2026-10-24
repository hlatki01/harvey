import { useState } from 'react'
import { Search, Users } from 'lucide-react'
import { useApi } from '@/lib/hooks'
import { relTime, sourceLabel } from '@/lib/format'
import { useToast } from '@/components/toast'
import { Button } from '@/components/ui/button'
import { SectionHead } from '@/components/ui/card'
import { StatusBadge } from '@/components/ui/badge'
import { Input } from '@/components/ui/field'
import { EmptyState, OfflineState, Skeleton } from '@/components/ui/states'
import { cn } from '@/lib/utils'
import { Avatar, CsvButton, EmailTag, Meter, TableCard, TH, TD, submitFeedback, type Prospect } from './pipeline/shared'

const KNOWN = ['new', 'queued', 'contacted', 'replied', 'interested', 'meeting']
const FILTERS: [string, string, string[] | null][] = [
  ['all', 'All', null],
  ['todo', 'To write', ['new', 'queued']],
  ['contacted', 'Contacted', ['contacted']],
  ['replied', 'Replied', ['replied', 'interested', 'meeting']],
  ['closed', 'Closed out', null],
]

function matches(p: Prospect, f: string) {
  if (f === 'all') return true
  if (f === 'closed') return !KNOWN.includes(p.status || '')
  const hit = FILTERS.find(x => x[0] === f)
  return !!(hit && hit[2] && hit[2].includes(p.status || ''))
}

export function ContactsPage() {
  const { data } = useApi<Prospect[]>('/api/prospects', { interval: 15000 })
  const toast = useToast()
  // Search and filter live in page state so the 15-second refresh keeps them.
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState('all')

  const head = (
    <SectionHead title="Contacts" actions={<>
      <CsvButton path="/api/export/prospects.csv" filename="prospects.csv">Export deliverable</CsvButton>
      <CsvButton variant="ghost" path="/api/export/prospects.csv?all=true" filename="prospects-all.csv">Export all</CsvButton>
    </>}>
      People Harvey has found, how sure it is of their address, and where each one stands.
    </SectionHead>
  )
  if (data === undefined) return <>{head}<Skeleton /></>
  if (!data) return <>{head}<OfflineState /></>
  if (!data.length) {
    return <>{head}<EmptyState icon={<Users className="size-5" />} title="No contacts yet">
      Harvey hasn't found any prospects. Once it's running, the Scout agent searches the web for people matching your ideal customer profile in <b>harvey.yaml</b>.
    </EmptyState></>
  }

  const q = query.trim().toLowerCase()
  const found = (p: Prospect) => !q || [p.first_name, p.last_name, p.company, p.email, p.title].join(' ').toLowerCase().includes(q)
  const rows = data.filter(p => matches(p, filter) && found(p))

  return (
    <>
      {head}
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <label className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-2 size-4 text-text-3" aria-hidden />
          <Input type="search" aria-label="Search contacts" placeholder="Search name, company, email" className="w-72 pl-8"
            value={query} onChange={e => setQuery(e.target.value)} />
        </label>
        <div role="group" aria-label="Filter by status" className="flex gap-1">
          {FILTERS.map(([key, label]) => (
            <button key={key} type="button" aria-pressed={key === filter} onClick={() => setFilter(key)}
              className={cn('h-7 rounded-sm px-2.5 text-[12px]', key === filter ? 'bg-accent-soft font-medium text-accent' : 'text-text-2 hover:bg-surface-2')}>
              {label} <span className="tabular-nums opacity-70">{data.filter(p => matches(p, key) && found(p)).length}</span>
            </button>
          ))}
        </div>
      </div>
      {!rows.length ? (
        <TableCard><EmptyState title="No matches" className="border-0">Nothing matches that search and filter.</EmptyState></TableCard>
      ) : (
        <TableCard>
          <table className="w-full whitespace-nowrap text-[13px]">
            <thead><tr>{['Name', 'Company', 'Email', 'Score', 'Status', 'Source', 'Added', ''].map((h, i) => <th key={i} className={TH}>{h}</th>)}</tr></thead>
            <tbody>
              {rows.map(p => (
                <tr key={p.id} className="border-t border-line">
                  <td className={TD}>
                    <div className="flex items-center gap-2">
                      <Avatar first={p.first_name} last={p.last_name} />
                      <div><div className="font-medium">{p.first_name} {p.last_name}</div><div className="text-[12px] text-text-3">{p.title}</div></div>
                    </div>
                  </td>
                  <td className={TD}>{p.company}</td>
                  <td className={TD}>
                    <div>{p.email}</div>
                    <div className="text-[12px] text-text-3">
                      <EmailTag p={p} />
                      {p.phone && <span className="ml-1">{p.phone}{p.phone_verified && <span title="Phone verified"> ✓</span>}</span>}
                    </div>
                  </td>
                  <td className={TD}>{p.score || p.score === 0 ? <Meter pct={p.score} /> : null}</td>
                  <td className={TD}><StatusBadge status={p.status} /></td>
                  <td className={TD + ' text-text-2'}>{sourceLabel(p.source)}</td>
                  <td className={TD + ' text-text-2'}>{relTime(p.created_at)}</td>
                  <td className={TD}>
                    <Button variant="ghost" size="sm" title="Leave feedback on this contact"
                      onClick={() => submitFeedback(toast, 'contact', p.id, 'Anything Harvey should know before writing to this person?')}>Feedback</Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableCard>
      )}
      {rows.length > 0 && <p className="mt-2 text-[12px] text-text-3">{rows.length} of {data.length} contacts</p>}
    </>
  )
}
