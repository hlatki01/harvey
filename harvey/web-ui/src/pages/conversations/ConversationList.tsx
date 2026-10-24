import { initials, relTime, statusMeta } from '@/lib/format'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { convoName, type Convo } from './types'

export const PAGE = 10

export function Avatar({ c }: { c: Convo }) {
  return (
    <span aria-hidden className="grid size-8 flex-none place-items-center rounded-full bg-accent-soft text-[12px] font-semibold text-accent">
      {initials(c.first_name, c.last_name)}
    </span>
  )
}

// A line of the list: who, the last thing said, when, and where it stands.
function Item({ c, selected, onSelect }: { c: Convo; selected: boolean; onSelect: () => void }) {
  const last = (c.thread || []).slice(-1)[0]
  const snippet = c.draft ? 'Draft waiting for you' : last ? (last.sender === 'prospect' ? '' : 'You: ') + last.content : ''
  const dot = c.status === 'needs_human' || c.draft ? 'bg-wait' : c.status === 'closed' ? 'bg-text-3' : 'bg-good'
  return (
    <button type="button" onClick={onSelect} aria-current={selected || undefined}
      className={cn('flex w-full items-center gap-2.5 rounded-md p-2.5 text-left hover:bg-surface-2',
        selected && 'bg-accent-soft shadow-[inset_0_0_0_1px_var(--accent-line)] hover:bg-accent-soft')}>
      <Avatar c={c} />
      <span className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="flex justify-between gap-2 text-[13px]">
          <b className="truncate font-semibold">{convoName(c)}</b>
          <span className="whitespace-nowrap text-[11px] text-text-3">{relTime(c.updated_at)}</span>
        </span>
        <span className="truncate text-[12px] text-text-3">{snippet || c.company || ''}</span>
      </span>
      <span className={cn('size-2 flex-none rounded-full', dot)} title={statusMeta(c.status).label} />
    </button>
  )
}

export function ConversationList({ shown, page, sel, onSelect, onPage }: {
  shown: Convo[]; page: number; sel: string; onSelect: (id: string) => void; onPage: (delta: number) => void
}) {
  const pages = Math.ceil(shown.length / PAGE)
  const from = page * PAGE
  return (
    <aside className="flex min-h-0 flex-col overflow-hidden rounded-lg bg-surface shadow-card">
      <div className="max-h-60 min-h-0 flex-1 overflow-y-auto overscroll-contain p-1.5 min-[901px]:max-h-none">
        {shown.slice(from, from + PAGE).map(c => <Item key={c.id} c={c} selected={c.id === sel} onSelect={() => onSelect(c.id)} />)}
      </div>
      {pages > 1 && (
        <div className="flex items-center justify-between gap-2 border-t border-line px-2.5 py-2 text-[12px] text-text-3">
          <Button variant="ghost" size="sm" disabled={!page} onClick={() => onPage(-1)}>&lsaquo; Prev</Button>
          <span>{from + 1}–{Math.min(from + PAGE, shown.length)} of {shown.length}</span>
          <Button variant="ghost" size="sm" disabled={page >= pages - 1} onClick={() => onPage(1)}>Next &rsaquo;</Button>
        </div>
      )}
    </aside>
  )
}
