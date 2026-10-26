import { formatDate, relTime } from '@/lib/format'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { StatusBadge, ToneBadge } from '@/components/ui/badge'
import { Muted, Skeleton } from '@/components/ui/states'
import { isWhatsApp, type Campaign, type Msg, type ProspectDetail, type Recipient } from './types'
import { useHandSend } from './useHandSend'

const Title = ({ children }: { children: React.ReactNode }) =>
  <div className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-text-3">{children}</div>

function label(m: Msg): string {
  if (m.who === 'lead') return 'They wrote'
  if (m.who === 'team') return 'You wrote'
  if (m.kind === 'reply') return 'Harvey answered'
  return m.step === 1 ? 'First message' : `Follow-up ${(m.step || 2) - 1}`
}

// One message: theirs on the left, ours on the right; not sent yet is dashed.
function Bubble({ m, wa }: { m: Msg; wa: boolean }) {
  const ours = m.who !== 'lead'
  const pending = ours && m.status !== 'sent' && m.status !== 'received'
  const when = m.status === 'sent' ? m.sent_at || m.ts : pending ? m.send_at || m.ts : m.ts
  return (
    <li className={cn('flex flex-col gap-1', ours ? 'items-end' : 'items-start')}>
      <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-text-3">
        <span>{label(m)}</span>
        {pending && <StatusBadge status={m.status} />}
        <span title={formatDate(when)}>{pending && m.status !== 'failed' ? 'due ' : ''}{relTime(when)}</span>
      </div>
      <div className={cn('max-w-[92%] whitespace-pre-wrap break-words rounded-lg px-3 py-2 text-[13px]',
        ours ? 'bg-accent-soft' : 'bg-surface-2',
        pending && 'border border-dashed border-line bg-transparent')}>
        {!wa && m.subject && m.kind !== 'reply' && <div className="mb-1 font-medium">{m.subject}</div>}
        {m.body}
      </div>
      {m.error && <div className="max-w-[92%] text-[11px] text-bad">{m.error}</div>}
    </li>
  )
}

/** Email the user sends by hand: shown only when Harvey can't send itself. */
function HandSend({ c, r, onChanged }: { c: Campaign; r: Recipient; onChanged: () => void }) {
  const { markSent, openInGmail } = useHandSend(onChanged)
  const steps = c.sequence || []
  if (r.sent_by_hand) {
    return (
      <div className="mb-4 space-y-2 rounded-md border border-line p-3 text-[13px]">
        <label className="inline-flex cursor-pointer items-center gap-1.5">
          <input type="checkbox" checked onChange={() => markSent(r, false)} /> Sent by you
        </label>
        <p className="text-[12px] text-text-2">Harvey won't send follow-ups to people you emailed yourself. Send them from here:</p>
        <div className="flex flex-wrap gap-2">
          {steps.slice(1).map((s, i) => (
            <Button key={i} size="sm" variant="ghost" onClick={() => openInGmail(c, s, r)}>Follow-up {i + 1} in Gmail</Button>
          ))}
        </div>
      </div>
    )
  }
  if (r.can_send_by_hand && steps[0]) {
    return (
      <div className="mb-4 flex flex-wrap items-center gap-2 rounded-md border border-line p-3 text-[13px]">
        {r.email_status === 'risky' && <ToneBadge tone="waiting">Catch-all, may bounce</ToneBadge>}
        <Button size="sm" onClick={() => openInGmail(c, steps[0], r)}>Open first email in Gmail</Button>
        <label className="inline-flex cursor-pointer items-center gap-1.5">
          <input type="checkbox" checked={false} onChange={() => markSent(r, true)} /> Mark as sent
        </label>
      </div>
    )
  }
  if (r.can_mark_sent) {
    return (
      <label className="mb-4 inline-flex cursor-pointer items-center gap-1.5 text-[12px] text-text-2">
        <input type="checkbox" checked={false} onChange={() => markSent(r, true)} /> I already emailed {r.email} myself
      </label>
    )
  }
  return null
}

export function ProspectThread({ c, r, d, onChanged }: {
  c: Campaign; r: Recipient; d: ProspectDetail | null | undefined; onChanged: () => void
}) {
  const wa = isWhatsApp(c)
  return (
    <div className="space-y-6 p-4">
      <section>
        <Title>Messages</Title>
        {!wa && <HandSend c={c} r={r} onChanged={onChanged} />}
        {d === undefined ? <Skeleton rows={3} />
          : !d ? <Muted>Could not load the messages.</Muted>
          : !(d.messages || []).length ? <Muted>Nothing written for this person yet.</Muted>
          : <ul className="m-0 list-none space-y-3 p-0">{d.messages!.map((m, i) => <Bubble key={i} m={m} wa={wa} />)}</ul>}
      </section>
      <section>
        <Title>Timeline</Title>
        {d === undefined ? <Skeleton rows={2} />
          : !d || !(d.history || []).length ? <Muted>Nothing yet.</Muted>
          : (
            <ol className="m-0 list-none border-l border-line p-0 pl-4 text-[12px]">
              {d.history!.map((e, i) => (
                <li key={i} className="relative pb-3 break-words">
                  <span aria-hidden className="absolute -left-[21px] top-1 size-2.5 rounded-full border-2 border-surface bg-accent" />
                  <span className="block text-text-3" title={formatDate(e.ts)}>{relTime(e.ts)}</span>
                  {e.text}
                </li>
              ))}
            </ol>
          )}
      </section>
    </div>
  )
}
