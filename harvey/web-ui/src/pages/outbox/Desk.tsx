import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Input, Textarea } from '@/components/ui/field'
import { formatDate, initials, own, safeHref } from '@/lib/format'
import { cn } from '@/lib/utils'
import type { DeskContext, Edit, OutboxItem } from './types'

const CONTACT_SOURCES: Record<string, string> = {
  known_company_backfill: 'team page on their site',
  company_website: 'team page on their site',
  scraped_mailto: 'address published on their site',
  linkedin_search: 'LinkedIn search',
  hunter_domain_search: 'Hunter domain search',
  job_board: 'job board',
  web_search: 'web search',
}

const Kbd = ({ children }: { children: string }) => (
  <kbd className="ml-1 rounded-xs bg-black/10 px-1 font-mono text-[10px]">{children}</kbd>
)

// What the draft is built on, so every claim can be checked against it.
function Context({ ctx }: { ctx?: DeskContext | null }) {
  if (!ctx) return null
  const href = safeHref(ctx.website)
  const joined = (xs: unknown[]) => xs.filter(Boolean).join(' · ')
  const rows: [string, React.ReactNode][] = [
    ['Company', ctx.company],
    ['Website', href && <a className="text-accent underline" href={href} target="_blank" rel="noopener noreferrer">{String(ctx.website).replace(/^https?:\/\//i, '')} ↗</a>],
    ['About', ctx.description],
    ['Contact', joined([ctx.contact, ctx.contact_title])],
    ['Found via', ctx.contact_source && (own(CONTACT_SOURCES, ctx.contact_source) ?? ctx.contact_source)],
    ['Email', joined([ctx.email_status, ctx.score ? 'score ' + ctx.score : ''])],
    ['Signals', ctx.signals && Object.keys(ctx.signals).length > 0 && Object.entries(ctx.signals).map(([k, v]) => (
      <div key={k}>{k}: <b>{String(v)}</b></div>
    ))],
    ['Campaign', ctx.campaign && <>{ctx.campaign} <span className="text-text-3">(how they were found, not a fact about them)</span></>],
    ['Writer notes', ctx.notes],
  ]
  return (
    <dl className="my-4 grid grid-cols-[88px_minmax(0,1fr)] gap-x-3 gap-y-1 rounded-md bg-surface-2 p-3 text-[12px] max-sm:grid-cols-1">
      {rows.filter(([, v]) => v).map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-text-3">{k}</dt>
          <dd className="[overflow-wrap:anywhere]">{v}</dd>
        </div>
      ))}
    </dl>
  )
}

export function Rail({ items, index, onGo }: { items: OutboxItem[]; index: number; onGo: (n: number) => void }) {
  return (
    <Card flush className="max-h-[70vh] overflow-y-auto p-1 max-lg:max-h-60">
      {items.map((it, n) => {
        const who = it.context?.contact || it.to_email
        const [a, b] = String(who || '').split(/[\s@.]+/)
        return (
          <button key={it.id} type="button" onClick={() => onGo(n)} aria-current={n === index || undefined}
            className={cn('flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left hover:bg-surface-2', n === index && 'bg-accent-soft')}>
            <span className={cn('grid size-7 shrink-0 place-items-center rounded-full bg-surface-3 text-[11px] font-medium', n === index && 'bg-accent text-accent-text')}>{initials(a, b)}</span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[13px] font-medium">{who}</span>
              <span className="block truncate text-[12px] text-text-3">{it.subject || '(no subject)'}</span>
            </span>
          </button>
        )
      })}
    </Card>
  )
}

export function Pane({ cur, index, total, footer, edit, busy, onAct, onEdit, onChange, onSave, onCancel }: {
  cur: OutboxItem; index: number; total: number; footer: string
  edit: Edit | null; busy: boolean
  onAct: (a: 'approve' | 'reject') => void
  onEdit: () => void
  onChange: (e: Edit) => void
  onSave: () => void
  onCancel: () => void
}) {
  // Cold email leaves with the footer underneath; show it as it will go out.
  const added = cur.kind === 'sequence' && cur.channel !== 'whatsapp' && !(cur.body || '').trimEnd().endsWith(footer) ? footer : ''
  return (
    <Card className="min-w-0 p-6">
      <div className="flex flex-wrap items-center gap-2 text-[13px] text-text-2 [overflow-wrap:anywhere]">
        <span>To <b className="font-medium text-text">{cur.to_email}</b></span>
        <span className="rounded-xs bg-surface-2 px-1.5 text-[11px]">{cur.kind === 'reply' ? 'Reply' : 'Email ' + cur.step}</span>
        <span className="text-text-3">sends {formatDate(cur.send_at)}</span>
      </div>
      <Context ctx={cur.context} />
      {edit ? (
        <div className="space-y-3">
          <Input aria-label="Subject" value={edit.subject} onChange={e => onChange({ ...edit, subject: e.target.value })}
            readOnly={cur.kind === 'reply'} title={cur.kind === 'reply' ? 'A reply keeps its subject so it stays in the thread' : undefined} />
          <Textarea aria-label="Body" autoFocus rows={14} className="max-w-[66ch] leading-relaxed" value={edit.body}
            onChange={e => onChange({ ...edit, body: e.target.value })} />
        </div>
      ) : (
        <>
          <h3 className="mb-3 mt-5 text-[20px] font-semibold leading-snug tracking-tight">{cur.subject || '(no subject)'}</h3>
          <div className="max-w-[66ch] whitespace-pre-wrap text-[14px] leading-relaxed [overflow-wrap:anywhere]">{cur.body}</div>
        </>
      )}
      {added && (
        <div title="Added when it sends (compliance in harvey.yaml)"
          className="mt-4 max-w-[66ch] whitespace-pre-wrap border-t border-dashed border-line pt-3 text-[12px] text-text-3">{added}</div>
      )}
      <div className="mt-6 flex flex-wrap items-center gap-2">
        {edit ? (
          <>
            <Button variant="primary" onClick={onSave} busy={busy}>Save</Button>
            <Button onClick={onCancel}>Cancel</Button>
          </>
        ) : (
          <>
            <Button variant="primary" busy={busy} onClick={() => onAct('approve')}>Approve<Kbd>A</Kbd></Button>
            <Button onClick={onEdit}>Edit<Kbd>E</Kbd></Button>
            <Button busy={busy} onClick={() => onAct('reject')}>Reject<Kbd>R</Kbd></Button>
          </>
        )}
        <span className="ml-auto text-[12px] text-text-3">{index + 1} of {total}</span>
      </div>
    </Card>
  )
}
