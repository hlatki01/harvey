import { useEffect, useMemo, useState } from 'react'
import { Mail, MessageCircle } from 'lucide-react'
import { useApi } from '@/lib/hooks'
import { relTime, safeHref } from '@/lib/format'
import { cn } from '@/lib/utils'
import { useToast } from '@/components/toast'
import { Button } from '@/components/ui/button'
import { SectionHead } from '@/components/ui/card'
import { StatusBadge, ToneBadge } from '@/components/ui/badge'
import { EmptyState, Muted, OfflineState, Skeleton } from '@/components/ui/states'
import { submitFeedback } from './pipeline/shared'
import { ProspectThread } from './campaigns/ProspectThread'
import { isWhatsApp, personName, type Campaign, type ProspectDetail, type Recipient } from './campaigns/types'

type Tab = 'email' | 'whatsapp'
const KEY = 'harvey-campaigns-tab'
const recall = (): Tab => { try { return localStorage.getItem(KEY) === 'whatsapp' ? 'whatsapp' : 'email' } catch { return 'email' } }
const remember = (t: Tab) => { try { localStorage.setItem(KEY, t) } catch { /* private mode */ } }

const PROGRESS: [string, string[], string][] = [
  ['Contacted', ['contacted'], 'bg-accent'],
  ['Replied', ['replied'], 'bg-active'],
  ['Interested', ['interested', 'meeting'], 'bg-good'],
  ['Waiting', ['new', 'queued'], 'bg-accent-soft'],
]

function Progress({ c, legend }: { c: Campaign; legend?: boolean }) {
  const rs = c.recipients || []
  if (!rs.length) return null
  const parts = PROGRESS.map(([label, st, cls]) => [label, rs.filter(r => st.includes(r.status || '')).length, cls] as [string, number, string])
  const closed = rs.length - parts.reduce((x, p) => x + p[1], 0)
  if (closed > 0) parts.push(['Closed out', closed, 'bg-text-3'])
  const shown = parts.filter(p => p[1])
  return (
    <div className="mt-2">
      <div role="img" aria-label="Where this campaign's people stand" className="flex h-1.5 overflow-hidden rounded-full bg-surface-2">
        {shown.map(p => <span key={p[0]} className={p[2]} style={{ width: (100 * p[1] / rs.length) + '%' }} title={`${p[0]}: ${p[1]}`} />)}
      </div>
      {legend && (
        <div className="mt-1.5 flex flex-wrap gap-x-4 text-[12px] text-text-2">
          {shown.map(p => <span key={p[0]} className="flex items-center gap-1.5"><i className={'size-2 rounded-full ' + p[2]} />{p[0]} <b className="text-text">{p[1]}</b></span>)}
        </div>
      )}
    </div>
  )
}

const Column = ({ className, children, label }: { className?: string; children: React.ReactNode; label: string }) => (
  <section aria-label={label} className={cn('flex min-h-0 flex-col overflow-hidden rounded-lg bg-surface shadow-card', className)}>{children}</section>
)
const Scroll = ({ children }: { children: React.ReactNode }) =>
  <div className="max-h-[60vh] min-h-0 flex-1 overflow-y-auto overscroll-contain min-[1101px]:max-h-none">{children}</div>

function CampaignItem({ c, selected, onSelect }: { c: Campaign; selected: boolean; onSelect: () => void }) {
  const n = (c.recipients || []).length
  return (
    <button type="button" onClick={onSelect} aria-current={selected || undefined}
      className={cn('block w-full rounded-md p-2.5 text-left hover:bg-surface-2',
        selected && 'bg-accent-soft shadow-[inset_0_0_0_1px_var(--accent-line)] hover:bg-accent-soft')}>
      <span className="flex items-center justify-between gap-2 text-[13px]">
        <b className="min-w-0 break-words font-semibold">{c.name || 'Untitled campaign'}</b>
        <span className="whitespace-nowrap text-[11px] text-text-3">{relTime(c.created_at)}</span>
      </span>
      <span className="mt-0.5 block text-[12px] text-text-3">{n} {n === 1 ? 'person' : 'people'} · {(c.sequence || []).length} steps</span>
      <Progress c={c} />
    </button>
  )
}

const Row = ({ k, children }: { k: string; children: React.ReactNode }) => children
  ? <div className="flex justify-between gap-2.5 py-[2px]"><span className="flex-none text-text-3">{k}</span><span className="break-words text-right">{children}</span></div>
  : null

// A person in the campaign; the selected one opens up with what Harvey knows.
function PersonItem({ c, r, d, selected, onSelect }: {
  c: Campaign; r: Recipient; d: ProspectDetail | null | undefined; selected: boolean; onSelect: () => void
}) {
  const steps = (c.sequence || []).length
  const contact = isWhatsApp(c) ? r.phone : r.email
  const co = selected && d ? d.company : null
  const site = co ? safeHref(co.website || (co.domain ? 'https://' + co.domain : '')) : ''
  return (
    <div className={cn('border-b border-line', selected && 'bg-accent-soft/40')}>
      <button type="button" onClick={onSelect} aria-current={selected || undefined} aria-expanded={selected}
        className="block w-full p-3 text-left hover:bg-surface-2">
        <span className="flex items-center justify-between gap-2 text-[13px]">
          <b className="min-w-0 break-words font-semibold">{personName(r)}</b>
          <StatusBadge status={r.status} />
        </span>
        {(r.first_name || r.last_name) && r.company && <span className="block truncate text-[12px] text-text-2">{r.title ? r.title + ' · ' : ''}{r.company}</span>}
        <span className="block truncate text-[12px] text-text-3">{contact}</span>
        <span className="mt-1 block text-[11px] text-text-3">
          {r.sent || 0}/{steps} sent{r.next_at ? ' · next ' + relTime(r.next_at) : ''}
          {r.sent_by_hand ? ' · sent by you' : ''}
        </span>
      </button>
      {selected && (
        <div className="px-3 pb-3 text-[12px]">
          {r.personalization_notes && <p className="mb-2 text-text-2">{r.personalization_notes}</p>}
          {d === undefined ? <Skeleton rows={2} /> : d && (
            <>
              <Row k="Business type">{d.lead?.industry}</Row>
              <Row k="Score">{d.lead?.score}</Row>
              <Row k="Came from">{d.lead?.source}</Row>
              {!isWhatsApp(c) && <Row k="Email check">{r.email_status}</Row>}
              {co && (
                <>
                  <Row k="Place">{co.location}</Row>
                  <Row k="Phone">{co.phone}</Row>
                  <Row k="Website">{site && <a className="text-accent underline" href={site} target="_blank" rel="noopener noreferrer">{co.website || co.domain}</a>}</Row>
                  {(d.signals || []).length > 0 && (
                    <div className="mt-1.5 flex flex-wrap gap-1">{d.signals!.map((s, i) => <ToneBadge key={i} tone="idle">{s}</ToneBadge>)}</div>
                  )}
                  {co.description && <p className="mt-1.5 text-text-2">{co.description}</p>}
                </>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}

function Sequence({ c }: { c: Campaign }) {
  const steps = c.sequence || []
  const wa = isWhatsApp(c)
  return (
    <details className="mt-2 text-[12px]">
      <summary className="cursor-pointer text-text-2">The {steps.length} {wa ? 'messages' : 'emails'} of this campaign</summary>
      <ol className="mt-2 list-none space-y-2.5 p-0">
        {steps.map((s, i) => (
          <li key={i}>
            <div className="text-text-3">
              {i === 0 ? (wa ? 'First message (each business gets its own, written for it)' : 'First email') : `Follow-up ${i}`}
              {s.delay_days ? ` · ${s.delay_days} days after the last` : ''}
            </div>
            {!wa && <div className="font-medium">{s.subject}</div>}
            <div className="whitespace-pre-wrap text-text-2">{s.body}</div>
          </li>
        ))}
      </ol>
    </details>
  )
}

export function CampaignsPage() {
  const { data, refetch } = useApi<Campaign[]>('/api/campaigns', { interval: 15000 })
  const toast = useToast()
  const [tab, setTab] = useState<Tab>(recall)
  const [selC, setSelC] = useState('')
  const [selP, setSelP] = useState('')

  const byTab = useMemo(() => ({
    email: (data || []).filter(c => !isWhatsApp(c)),
    whatsapp: (data || []).filter(isWhatsApp),
  }), [data])
  const list = byTab[tab]
  const campaign = list.find(c => c.id === selC) || list[0]
  const people = campaign?.recipients || []
  const person = people.find(r => r.id === selP) || people[0]
  const { data: detail, refetch: refetchDetail } = useApi<ProspectDetail>(
    campaign && person ? `/api/campaigns/${encodeURIComponent(campaign.id)}/prospects/${encodeURIComponent(person.id)}` : null,
    { interval: 15000 })

  // Land on the tab that has campaigns when the remembered one has none.
  useEffect(() => {
    if (data && !byTab[tab].length) {
      const other: Tab = tab === 'email' ? 'whatsapp' : 'email'
      if (byTab[other].length) setTab(other)
    }
  }, [data, byTab, tab])

  const head = <SectionHead title="Campaigns">Email and WhatsApp sequences Harvey has written, and where each person stands.</SectionHead>
  if (data === undefined) return <>{head}<Skeleton /></>
  if (!data) return <>{head}<OfflineState /></>

  const pick = (t: Tab) => { setTab(t); remember(t); setSelC(''); setSelP('') }
  const changed = () => { refetch(); refetchDetail() }
  const noun = tab === 'whatsapp' ? 'message' : 'email'

  return (
    <>
      {head}
      <div role="tablist" aria-label="Channel" className="mb-3 inline-flex gap-0.5 rounded-md bg-surface-2 p-0.5">
        {([['email', 'Email', Mail], ['whatsapp', 'WhatsApp', MessageCircle]] as const).map(([id, label, Icon]) => (
          <button key={id} type="button" role="tab" aria-selected={id === tab} onClick={() => pick(id)}
            className={cn('inline-flex items-center gap-1.5 rounded-sm px-3 py-1 text-[13px] font-medium text-text-2', id === tab && 'bg-surface text-text shadow-card')}>
            <Icon className="size-3.5" />{label} <span className="text-text-3">{byTab[id].length}</span>
          </button>
        ))}
      </div>
      {!campaign ? (
        <EmptyState icon={tab === 'whatsapp' ? <MessageCircle className="size-5" /> : <Mail className="size-5" />} title={`No ${tab === 'whatsapp' ? 'WhatsApp' : 'email'} campaigns yet`}>
          {tab === 'whatsapp'
            ? 'Every WhatsApp opening joins a campaign once Harvey has written its sequence.'
            : "The Writer agent hasn't drafted any sequences. It kicks in automatically once Harvey has scored prospects to write for."}
        </EmptyState>
      ) : (
        <div className="grid min-h-[480px] gap-4 min-[1101px]:h-[calc(100vh-250px)] min-[1101px]:grid-cols-[minmax(220px,280px)_minmax(260px,360px)_minmax(0,1fr)]">
          <Column label="Campaigns">
            <Scroll>
              <div className="p-1.5">
                {list.map(c => <CampaignItem key={c.id} c={c} selected={c.id === campaign.id} onSelect={() => { setSelC(c.id); setSelP('') }} />)}
              </div>
            </Scroll>
          </Column>
          <Column label="People">
            <div className="border-b border-line p-3">
              <div className="flex items-center justify-between gap-2">
                <h3 className="truncate text-[14px] font-semibold">{campaign.name || 'Untitled campaign'}</h3>
                <StatusBadge status={campaign.status} />
              </div>
              <Progress c={campaign} legend />
              <div className="mt-2 flex items-center gap-2">
                <Sequence c={campaign} />
              </div>
              <Button variant="ghost" size="sm" className="mt-1 -ml-2" onClick={() => submitFeedback(toast, 'campaign', campaign.id,
                `What should Harvey do differently in the ${noun}s it writes next? (e.g. "shorter; don't mention reviews")`)}>Feedback</Button>
            </div>
            <Scroll>
              {!people.length ? <Muted className="p-3">Nobody in this campaign can be reached.</Muted>
                : people.map(r => (
                  <PersonItem key={r.id} c={campaign} r={r} d={r.id === person?.id ? detail : undefined}
                    selected={r.id === person?.id} onSelect={() => setSelP(r.id)} />
                ))}
            </Scroll>
          </Column>
          <Column label="Messages and timeline">
            {person ? (
              <>
                <div className="border-b border-line p-3">
                  <h3 className="truncate text-[14px] font-semibold">{personName(person)}</h3>
                  <div className="truncate text-[12px] text-text-3">{isWhatsApp(campaign) ? person.phone : person.email}</div>
                </div>
                <Scroll><ProspectThread c={campaign} r={person} d={detail} onChanged={changed} /></Scroll>
              </>
            ) : <Muted className="p-4">Pick someone to see their messages.</Muted>}
          </Column>
        </div>
      )}
    </>
  )
}
