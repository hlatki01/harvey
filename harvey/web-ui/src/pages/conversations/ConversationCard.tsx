import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, ArrowRight, Clock, Info, Mail, PenLine } from 'lucide-react'
import { api, JSON_HEADERS } from '@/lib/api'
import { failMsg, formatDate, own, parseTs, relTime } from '@/lib/format'
import { useNav } from '@/app/nav'
import { useToast } from '@/components/toast'
import { Button } from '@/components/ui/button'
import { StatusBadge, ToneBadge } from '@/components/ui/badge'
import { Muted } from '@/components/ui/states'
import { Avatar } from './ConversationList'
import { bubble, ChatBubbles, Scroller, Spin } from './Chat'
import { Composer } from './Composer'
import { LeadModal } from './LeadModal'
import { useChat } from './useChat'
import { convoName, STAGE_LABELS, type Convo, type SentEmail, type ThreadMsg } from './types'

function DraftPanel({ c }: { c: Convo }) {
  const { goTab } = useNav()
  if (!c.draft) return null
  return (
    <div className="w-[min(78%,64ch)] self-end rounded-[14px] border-[1.5px] border-dashed border-accent bg-accent-soft px-3.5 pb-3 pt-2.5 max-sm:w-auto max-sm:max-w-[92%]">
      <div className="mb-1 flex items-center gap-1.5 text-[11px] font-semibold text-accent"><PenLine className="size-3" aria-hidden />Harvey drafted a reply · waiting for you</div>
      <div className="mb-2.5 whitespace-pre-wrap break-words text-[13px] leading-relaxed">{c.draft.body}</div>
      <Button variant="primary" size="sm" onClick={() => goTab('outbox')}>Review in Outbox<ArrowRight className="size-3.5" aria-hidden /></Button>
    </div>
  )
}

function EmailThread({ c }: { c: Convo }) {
  const first = c.first_name || 'Them'
  // An item with no time goes last instead of scrambling the order.
  const items = useMemo(() => {
    const all: { ts: string; email?: SentEmail; msg?: ThreadMsg }[] = [
      ...(c.emails || []).map(email => ({ ts: email.sent_at, email })),
      ...(c.thread || []).map(msg => ({ ts: msg.timestamp, msg })),
    ]
    return all.map(it => ({ ...it, key: +parseTs(it.ts) }))
      .sort((x, y) => (isNaN(x.key) ? Infinity : x.key) - (isNaN(y.key) ? Infinity : y.key) || 0)
  }, [c.emails, c.thread])
  return (
    <Scroller>
      {items.map((it, i) => {
        if (it.email) {
          const e = it.email
          return (
            <div key={i} className="w-[min(78%,64ch)] self-end overflow-hidden rounded-[14px] rounded-br-[4px] bg-surface shadow-[inset_0_0_0_1px_var(--line)] max-sm:w-auto max-sm:max-w-[92%]">
              <div className="flex items-center gap-2 border-b border-line bg-surface-2 px-3.5 py-2 text-[12px] text-text-2">
                <Mail className="size-3.5 flex-none text-accent" aria-hidden />
                <b className="font-semibold text-text">Email {String(e.step)}</b>
                <span className="min-w-0 truncate">{e.subject}</span>
                <span className="ml-auto whitespace-nowrap text-text-3">{relTime(e.sent_at)}</span>
              </div>
              <div className="whitespace-pre-wrap break-words px-3.5 py-2.5 text-[13px] leading-relaxed text-text-2">{e.body}</div>
            </div>
          )
        }
        const m = it.msg!, sent = m.sender === 'harvey' || m.sender === 'team'
        return (
          <div key={i} className={bubble(sent)}>
            <div className="mb-0.5 text-[11px] opacity-70">{m.sender === 'team' ? 'Equipe' : sent ? 'Harvey' : first} &middot; {relTime(m.timestamp)}</div>
            {m.content}
          </div>
        )
      })}
      {!items.length && <Muted>No messages in this thread yet.</Muted>}
      <DraftPanel c={c} />
    </Scroller>
  )
}

function WhatsAppThread({ c, chat }: { c: Convo; chat: ReturnType<typeof useChat> }) {
  const [shot, setShot] = useState('')
  useEffect(() => {
    if (!shot) return
    const k = (e: KeyboardEvent) => { if (e.key === 'Escape') setShot('') }
    document.addEventListener('keydown', k)
    return () => document.removeEventListener('keydown', k)
  }, [shot])
  const empty = !chat.msgs.length && !chat.pending.length && !c.draft
  return (
    <>
      <Scroller onTop={chat.loadOlder}>
        <ChatBubbles id={c.id} first={c.first_name || 'Them'} chat={chat} onImage={setShot} />
        <DraftPanel c={c} />
        {empty && (chat.loaded ? <Muted>No messages in this chat yet.</Muted> : <div className="self-center py-1 text-[12px] text-text-3"><Spin />Loading chat…</div>)}
      </Scroller>
      {shot && (
        <div className="fixed inset-0 z-[1000] grid cursor-zoom-out place-items-center bg-black/80 p-6" onClick={() => setShot('')}>
          <img src={shot} alt="" className="max-h-full max-w-full rounded-lg" />
        </div>
      )}
    </>
  )
}

// Harvey answers new replies only on open conversations: an escalated one
// waits for you to reply yourself, then close it or hand it back.
function Actions({ c, onStatus, onFollowUp, busy }: {
  c: Convo; onStatus: (s: 'open' | 'closed') => void; onFollowUp: (a: 'now' | 'cancel') => void; busy: string
}) {
  const wrap = 'mt-4 flex flex-none flex-wrap items-center gap-2 border-t border-line pt-3'
  const toast = useToast()
  const [preview, setPreview] = useState('')
  useEffect(() => setPreview(''), [c.id, c.follow_up_at])
  async function showPreview() {
    if (preview && preview !== 'loading') { setPreview(''); return }
    setPreview('loading')
    const data = await api<{ success?: boolean; text?: string; message?: string }>(
      '/api/conversations/' + encodeURIComponent(c.id) + '/follow-up/preview', { method: 'POST', headers: JSON_HEADERS, body: '{}' })
    if (data && data.success && data.text) setPreview(data.text)
    else { setPreview(''); toast(failMsg(data, 'Could not write a preview.'), 'error') }
  }
  if (c.status === 'needs_human') {
    return (
      <div className={wrap}>
        <span className="mr-auto inline-flex items-center gap-1.5 text-[12px] text-text-2">
          <AlertTriangle className="size-3.5" aria-hidden />
          {c.channel === 'whatsapp' ? 'Harvey handed this to you. Reply above, then:' : 'Harvey handed this to you. Reply from your inbox, then:'}
        </span>
        <Button size="sm" disabled={!!busy} onClick={() => onStatus('open')}>{busy === 'open' ? <><Spin />Handing back</> : 'Hand back to Harvey'}</Button>
        <Button size="sm" disabled={!!busy} onClick={() => onStatus('closed')}>Close</Button>
      </div>
    )
  }
  if (c.status === 'open') {
    return (
      <div className={wrap}>
        {c.follow_up_at && (
          <>
            <button type="button" onClick={showPreview} aria-expanded={!!preview} title="See what it would say"
              className="mr-auto inline-flex items-center gap-1.5 text-left text-[12px] text-text-2 hover:text-text hover:underline">
              <Clock className="size-3.5" aria-hidden />
              Next step: {c.follow_ups ? 'last follow-up' : 'follow-up'} {formatDate(c.follow_up_at)}, if they stay quiet
            </button>
            <Button size="sm" variant="ghost" disabled={!!busy} onClick={() => onFollowUp('now')}>Send now</Button>
            <Button size="sm" variant="ghost" disabled={!!busy} onClick={() => onFollowUp('cancel')}>Cancel follow-up</Button>
            {preview && (
              <div className="basis-full rounded-md bg-surface-2 p-3 text-[13px]">
                {preview === 'loading' ? <span className="inline-flex items-center gap-2 text-text-2"><Spin />Harvey is writing a preview…</span> : (
                  <>
                    <div className="whitespace-pre-wrap break-words leading-relaxed">{preview}</div>
                    <div className="mt-1.5 text-[11px] text-text-3">A preview: the real message is written from the chat when it goes out, so it may differ.</div>
                  </>
                )}
              </div>
            )}
          </>
        )}
        <Button size="sm" variant="ghost" className={c.follow_up_at ? '' : 'ml-auto'} disabled={!!busy} onClick={() => onStatus('closed')}>Close conversation</Button>
      </div>
    )
  }
  if (c.status === 'closed') return <div className={wrap}><Button size="sm" variant="ghost" className="ml-auto" disabled={!!busy} onClick={() => onStatus('open')}>Reopen</Button></div>
  return null
}

export function ConversationCard({ c, onChanged }: { c: Convo; onChanged: () => void }) {
  const toast = useToast()
  const [lead, setLead] = useState(false)
  const [busy, setBusy] = useState<'' | 'open' | 'closed' | 'follow-up'>('')
  const wa = c.channel === 'whatsapp'
  const chat = useChat(c.id, wa)
  const name = convoName(c)
  const stage = own(STAGE_LABELS, c.stage || '')

  async function followUp(action: 'now' | 'cancel') {
    if (busy) return
    setBusy('follow-up')
    const data = await api<{ success?: boolean; message?: string }>('/api/conversations/' + encodeURIComponent(c.id) + '/follow-up', {
      method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ action }),
    })
    setBusy('')
    if (data && data.success) toast(action === 'cancel' ? 'Follow-up cancelled.' : 'Harvey writes and sends it on its next cycle in business hours.', 'success')
    else toast(failMsg(data, 'Could not change the follow-up.'), 'error')
    onChanged()
  }

  // Handing back can take a few seconds: Harvey reads the chat and decides what comes next.
  async function setStatus(status: 'open' | 'closed') {
    if (busy) return
    setBusy(status)
    const data = await api<{ success?: boolean; follow_up_at?: string | null; next?: string; reason?: string }>('/api/conversations/' + encodeURIComponent(c.id) + '/status', {
      method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ status }),
    })
    setBusy('')
    const when = data?.follow_up_at ? formatDate(data.follow_up_at) : ''
    if (data && data.success) toast(status === 'closed' ? 'Closed. A new reply starts a new conversation.'
      : data.next === 'reply' ? 'Back with Harvey. It answered what was left open: the reply is in the Outbox or on its way.'
      : data.next === 'handoff' ? `Harvey thinks this one is still yours${data.reason ? ` (${data.reason})` : ''}.`
      : when ? `Back with Harvey. If they stay quiet, a follow-up goes out ${when}.` : 'Open again. Harvey will answer the next reply.', 'success')
    else toast(failMsg(data, 'Could not update that conversation.'), 'error')
    onChanged()
  }

  return (
    <div className="flex h-full flex-col p-5">
      <div className="mb-4 flex flex-none flex-wrap items-center gap-3">
        <button type="button" onClick={() => setLead(true)} aria-haspopup="dialog" title="Lead, company and contact history"
          className="flex min-w-0 flex-[1_1_200px] items-center gap-3 text-left">
          <Avatar c={c} />
          <div className="min-w-0">
            <h3 className="break-words text-[14px] font-semibold">{name} <span className="text-text-3"><Info className="inline size-3.5" aria-hidden /></span></h3>
            <div className="break-words text-[12px] text-text-3">{[c.company, c.prospect_email].filter(Boolean).join(' · ')} · {relTime(c.updated_at)}</div>
          </div>
        </button>
        <div className="ml-auto flex flex-wrap gap-1.5">
          {/* An escalation's intent badge says the same as its status: show one. */}
          {c.intent && !(c.status === 'needs_human' && c.intent === 'escalate') && <StatusBadge status={c.intent} />}
          {stage && <ToneBadge tone="idle">{stage}</ToneBadge>}
          <StatusBadge status={c.status} />
        </div>
      </div>
      {wa ? <WhatsAppThread c={c} chat={chat} /> : <EmailThread c={c} />}
      {wa && ['needs_human', 'open'].includes(c.status) && <Composer id={c.id} chat={chat} />}
      <Actions c={c} onStatus={setStatus} onFollowUp={followUp} busy={busy} />
      {lead && <LeadModal id={c.id} name={[c.first_name, c.last_name].filter(Boolean).join(' ') || 'Lead'} onClose={() => setLead(false)} />}
    </div>
  )
}

