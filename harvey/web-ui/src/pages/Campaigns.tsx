import { useRef } from 'react'
import { Mail } from 'lucide-react'
import { useApi } from '@/lib/hooks'
import { lockIfRefused, JSON_HEADERS } from '@/lib/api'
import { authFetch } from '@/lib/auth'
import { relTime } from '@/lib/format'
import { useToast } from '@/components/toast'
import { Button } from '@/components/ui/button'
import { Card, SectionHead } from '@/components/ui/card'
import { StatusBadge, ToneBadge } from '@/components/ui/badge'
import { EmptyState, OfflineState, Skeleton } from '@/components/ui/states'
import { submitFeedback } from './pipeline/shared'

interface Recipient {
  id: string; email: string; phone?: string; first_name?: string; last_name?: string; company?: string; title?: string
  personalization_notes?: string; source?: string; status?: string; email_status?: string
  sent_by_hand?: boolean; can_send_by_hand?: boolean; can_mark_sent?: boolean
}
interface Step { step?: number | string; subject?: string; body?: string; delay_days?: number }
interface Campaign {
  id: string; name?: string; channel?: string; status?: string; created_at?: string; footer?: string; send_as?: string
  prospect_ids?: string[]; sequence?: Step[]; recipients?: Recipient[]
}

// Same values as the Sender's _render, so a hand-sent email reads the same.
function fillMergeVars(text: string | undefined, r: Recipient): string {
  const vals: Record<string, string> = {
    first_name: r.first_name || (r.source === 'company_inbox' ? 'there' : ''), last_name: r.last_name || '',
    company: r.company || '', title: r.title || '', personalization: r.personalization_notes || '',
  }
  return String(text || '').replace(/\{\{\s*(\w+)\s*\}\}/g, (m, k: string) => {
    k = k.toLowerCase()
    return Object.prototype.hasOwnProperty.call(vals, k) ? vals[k] : m
  })
}

const PROGRESS: [string, string[], string][] = [
  ['Contacted', ['contacted'], 'bg-accent'],
  ['Replied', ['replied'], 'bg-active'],
  ['Interested', ['interested', 'meeting'], 'bg-good'],
  ['Waiting', ['new', 'queued'], 'bg-accent-soft'],
]

function Progress({ c }: { c: Campaign }) {
  const rs = c.recipients || []
  if (!rs.length) return null
  const parts = PROGRESS.map(([label, st, cls]) => [label, rs.filter(r => st.includes(r.status || '')).length, cls] as [string, number, string])
  const closed = rs.length - parts.reduce((x, p) => x + p[1], 0)
  if (closed > 0) parts.push(['Closed out', closed, 'bg-text-3'])
  const shown = parts.filter(p => p[1])
  return (
    <div className="mt-3">
      <div role="img" aria-label="Where this campaign's people stand" className="flex h-2 overflow-hidden rounded-full bg-surface-2">
        {shown.map(p => <span key={p[0]} className={p[2]} style={{ width: (100 * p[1] / rs.length) + '%' }} title={`${p[0]}: ${p[1]}`} />)}
      </div>
      <div className="mt-1.5 flex flex-wrap gap-x-4 text-[12px] text-text-2">
        {shown.map(p => <span key={p[0]} className="flex items-center gap-1.5"><i className={'size-2 rounded-full ' + p[2]} />{p[0]} <b className="text-text">{p[1]}</b></span>)}
      </div>
    </div>
  )
}

export function CampaignsPage() {
  const { data, refetch } = useApi<Campaign[]>('/api/campaigns', { interval: 15000 })
  const toast = useToast()
  const gmailBusy = useRef(false)  // one compose window per click, never two

  /** True only when this call changed it: a status means they replied or
   *  opted out, 'already' means another click or tab got there first. */
  const markSent = async (r: Recipient, sent: boolean): Promise<boolean> => {
    let res: { success?: boolean; message?: string; sent?: boolean; status?: string; already?: boolean } | null = null
    try {
      const resp = await authFetch('/api/prospects/' + encodeURIComponent(r.id) + '/sent', {
        method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ sent }),
      })
      if (await lockIfRefused(resp)) return false
      res = await resp.json()
    } catch { /* falls through to the error toast */ }
    const ok = !!(res && res.success)
    if (!ok) toast((res && res.message) || 'Could not save that. Try again.', 'error')
    else if (!sent && res!.sent) toast('They already replied, so this stays marked as sent.')
    else toast(sent ? 'Marked as sent by you.' : 'Unmarked.', 'success')
    refetch()
    return ok && sent === !!res!.sent && !res!.status && !res!.already
  }

  // Opens a filled-in Gmail compose window. Nothing is sent from here.
  const openInGmail = async (c: Campaign, step: Step, r: Recipient) => {
    if (gmailBusy.current) return
    // Cold email carries the postal address and opt-out line, sent by hand too.
    const filled = fillMergeVars(step.body, r).trimEnd()
    const body = filled + (c.footer && !filled.endsWith(c.footer) ? '\n\n' + c.footer : '')
    const subject = fillMergeVars(step.subject, r)
    // authuser picks the Google account Harvey sends as, not just whichever one the browser is signed into.
    const url = 'https://mail.google.com/mail/?view=cm&fs=1' +
      (c.send_as ? '&authuser=' + encodeURIComponent(c.send_as) : '') +
      '&to=' + encodeURIComponent(r.email) + '&su=' + encodeURIComponent(subject) + '&body=' + encodeURIComponent(body)
    const left = /\{\{\s*\w+\s*\}\}/.test(subject + fillMergeVars(step.body, r))
    // No 'noopener' feature string: with it window.open always returns null, so a blocked
    // pop-up couldn't be told apart. The tab opens blank inside the click and gets the draft once recorded.
    const w = window.open('', '_blank')
    if (!w) {
      toast('Your browser blocked the Gmail tab. Allow pop-ups for this page and click again.', 'error')
      return
    }
    w.opener = null
    // Record the send BEFORE the user can press Send in Gmail, so Harvey can never email them as well.
    gmailBusy.current = true
    let claimed = !!r.sent_by_hand
    try { if (!claimed) claimed = await markSent(r, true) } finally { gmailBusy.current = false }
    if (!claimed) { w.close(); return }
    w.location.href = url
    if (left) toast('This draft still has a {{placeholder}} in it. Fix it in Gmail before sending.', 'error')
  }

  const head = <SectionHead title="Campaigns">Email and WhatsApp sequences Harvey has written, and how far each one has got.</SectionHead>
  if (data === undefined) return <>{head}<Skeleton /></>
  if (!data) return <>{head}<OfflineState /></>
  if (!data.length) {
    return <>{head}<EmptyState icon={<Mail className="size-5" />} title="No campaigns yet">
      The Writer agent hasn't drafted any sequences. It kicks in automatically once Harvey has scored prospects to write for.
    </EmptyState></>
  }

  const rcpt = 'flex flex-wrap items-center gap-2 text-[13px]'
  const tick = (r: Recipient, label: React.ReactNode, checked: boolean, cls = '') => (
    <label className={'inline-flex cursor-pointer items-center gap-1 ' + cls}>
      <input type="checkbox" checked={checked} onChange={e => markSent(r, e.target.checked)} /> {label}
    </label>
  )

  return (
    <>
      {head}
      <div className="space-y-4">
        {data.map(c => {
          const steps = c.sequence || []
          const pc = (c.prospect_ids || []).length
          const wa = c.channel === 'whatsapp'
          const noun = wa ? 'message' : 'email'
          return (
            <Card key={c.id} flush>
              <div className="p-4">
                <div className="flex items-center gap-2">
                  <h3 className="font-semibold">{c.name || 'Untitled Campaign'}</h3>
                  {wa && <ToneBadge tone="good">WhatsApp</ToneBadge>}
                  <Button variant="ghost" size="sm" onClick={() => submitFeedback(toast, 'campaign', c.id,
                    `What should Harvey do differently in the ${noun}s it writes next? (e.g. "shorter; don't mention reviews")`)}>Feedback</Button>
                </div>
                <div className="mt-1 flex flex-wrap items-center gap-3 text-[13px] text-text-2">
                  <StatusBadge status={c.status} />
                  <span>{pc} prospect{pc !== 1 ? 's' : ''}</span><span>{steps.length} {noun}s</span><span>Created {relTime(c.created_at)}</span>
                </div>
                <Progress c={c} />
              </div>
              <div className="border-t border-line px-4 pb-4">
                {!steps.length && <p className="pt-4 text-[13px] text-text-2">No {noun} steps in this campaign.</p>}
                {steps.map((step, s) => {
                  const actions = (c.recipients || []).map(r => {
                    // Only offered when Harvey can't send itself and this person can still be emailed (the server decides).
                    if (r.sent_by_hand) {
                      if (s === 0) return <div key={r.id} className={rcpt}><span className="font-medium">{r.email}</span>{tick(r, '✓ Sent by you', true)}</div>
                      // Follow-ups are the user's too: Harvey never emails someone they emailed.
                      return (
                        <div key={r.id} className={rcpt}>
                          <span className="font-medium">{r.email}</span>
                          <Button size="sm" onClick={() => openInGmail(c, step, r)}>Open in Gmail</Button>
                          <span className="text-[12px] text-text-2">Harvey won't send follow-ups to people you emailed yourself.</span>
                        </div>
                      )
                    }
                    if (s !== 0) return null
                    if (!r.can_send_by_hand) {
                      // Harvey sends here; this only records an email the user already sent.
                      return r.can_mark_sent
                        ? <div key={r.id} className={rcpt}>{tick(r, <>I already emailed {r.email} myself</>, false, 'text-[12px] text-text-2')}</div>
                        : null
                    }
                    return (
                      <div key={r.id} className={rcpt}>
                        <span className="font-medium">{r.email}</span>
                        {r.email_status === 'risky' && <ToneBadge tone="waiting">Catch-all, may bounce</ToneBadge>}
                        <Button size="sm" onClick={() => openInGmail(c, step, r)}>Open in Gmail</Button>
                        {tick(r, 'Mark as sent', false)}
                      </div>
                    )
                  }).filter(Boolean)
                  return (
                    <div key={s} className="mt-4 flex gap-3">
                      <span className="grid size-6 shrink-0 place-items-center rounded-full bg-accent-soft text-[12px] font-semibold text-accent">{String(step.step || '?')}</span>
                      <div className="min-w-0 flex-1">
                        <div className="text-[12px] text-text-3">
                          {s === 0 ? (wa ? 'First message (each business gets its own, written for it)' : 'First email') : 'Follow-up'}{step.delay_days ? ` · ${step.delay_days} days after the last` : ''}
                        </div>
                        {!wa && <div className="font-medium">{step.subject}</div>}
                        <div className="mt-1 whitespace-pre-wrap text-[13px] text-text-2">{step.body}</div>
                        {actions.length > 0 && <div className="mt-2 space-y-1.5">{actions}</div>}
                      </div>
                    </div>
                  )
                })}
              </div>
            </Card>
          )
        })}
      </div>
    </>
  )
}
