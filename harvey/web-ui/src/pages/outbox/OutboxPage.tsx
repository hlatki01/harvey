import { useEffect, useRef, useState } from 'react'
import { AlertTriangle, Check } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { SectionHead } from '@/components/ui/card'
import { Banner, EmptyState, OfflineState, Skeleton } from '@/components/ui/states'
import { useToast } from '@/components/toast'
import { api, post } from '@/lib/api'
import { failMsg } from '@/lib/format'
import { useApi, useInvalidate } from '@/lib/hooks'
import { Pane, Rail } from './Desk'
import { OutboxTables } from './Tables'
import type { Edit, OutboxData } from './types'

const EMPTY: never[] = []

// Approving mail is a queue of one-at-a-time judgements: one email fills the
// pane, the rest wait in the rail. No polling: a refetch under someone
// mid-decision would lose their place, so it runs only after an action.
export function OutboxPage() {
  const { data, refetch } = useApi<OutboxData>('/api/outbox')
  const invalidate = useInvalidate()
  const toast = useToast()
  const [idx, setIdx] = useState(0)
  const [edit, setEdit] = useState<Edit | null>(null)
  const [busy, setBusy] = useState(false)

  const ok = data && !data.error ? data : null
  const pending = ok?.pending ?? EMPTY
  // Keep the desk on the draft being edited even if the list shifted.
  const editAt = edit ? pending.findIndex(p => p.id === edit.id) : -1
  const stale = !!edit && !!ok && editAt < 0
  const i = editAt >= 0 ? editAt : Math.min(idx, Math.max(0, pending.length - 1))
  const editing = stale ? null : edit
  const cur = pending[i]

  useEffect(() => { if (stale) setEdit(null) }, [stale])

  const after = () => { void refetch(); void invalidate('/api/outbox', '/api/today') }

  // While a draft is being edited, nothing else may act on its stored version.
  const guarded = () => {
    if (!editing) return false
    toast('Save or cancel your edit first.', 'error')
    return true
  }
  const go = (n: number) => { if (n >= 0 && n < pending.length && !guarded()) setIdx(n) }
  const act = async (id: string, action: 'approve' | 'reject') => {
    if (guarded() || busy) return
    setBusy(true)
    const r = await api<{ success?: boolean }>('/api/outbox/' + encodeURIComponent(id) + '/' + action, { method: 'POST' })
    if (r?.success) toast(action === 'approve' ? 'Approved — will send on schedule.' : 'Rejected.', 'success')
    else toast(failMsg(r, 'Action failed.'), 'error')
    setBusy(false)
    after()
  }
  const approveAll = async () => {
    if (guarded() || busy) return
    setBusy(true)
    const r = await api<{ success?: boolean; approved?: number }>('/api/outbox/approve-all', { method: 'POST' })
    if (r?.success) toast('Approved ' + r.approved + ' email(s).', 'success')
    else toast(failMsg(r, 'Approve-all failed.'), 'error')
    setBusy(false)
    after()
  }
  const toggleSending = async (action: 'pause' | 'resume') => {
    const r = await api<{ success?: boolean }>('/api/sending/' + action, { method: 'POST' })
    if (r?.success) toast(action === 'pause' ? 'Sending paused.' : 'Sending resumed.', 'success')
    else toast(failMsg(r, action === 'pause' ? 'Could not pause sending.' : 'Could not resume sending.'), 'error')
    after()
  }
  const save = async () => {
    if (!editing) return
    const subject = editing.subject.trim(), body = editing.body.trim()
    if (!subject || !body) { toast('Subject and body are both required.', 'error'); return }
    setBusy(true)
    const r = await post<{ success?: boolean; message?: string }>('/api/outbox/' + encodeURIComponent(editing.id) + '/edit', { subject, body })
    setBusy(false)
    if (r?.success) {
      setEdit(null)
      toast('Saved. Still waiting for your approval.', 'success')
      after()
    } else toast((r && r.message) || 'Save failed.', 'error')
  }

  // Keyboard review, read through a ref so the one listener always sees fresh state.
  const keys = useRef<(e: KeyboardEvent) => void>(() => {})
  keys.current = e => {
    if (e.metaKey || e.ctrlKey || e.altKey) return
    const t = e.target as HTMLElement | null
    const typing = !!t && (/^(input|textarea|select)$/i.test(t.tagName) || t.isContentEditable)
    const k = e.key.toLowerCase()
    // Mid-edit, A would approve the stored draft, not what's typed: only
    // Escape (cancel) works until the edit is saved or cancelled.
    if (editing) {
      if (e.key === 'Escape') { e.preventDefault(); setEdit(null) }
      else if (!typing && 'jkare'.includes(k) && k.length === 1) { e.preventDefault(); guarded() }
      return
    }
    if (typing) return
    if (k === 'j') { e.preventDefault(); go(i + 1) }
    else if (k === 'k') { e.preventDefault(); go(i - 1) }
    else if (cur && k === 'a') { e.preventDefault(); void act(cur.id, 'approve') }
    else if (cur && k === 'r') { e.preventDefault(); void act(cur.id, 'reject') }
    else if (cur && k === 'e') { e.preventDefault(); setEdit({ id: cur.id, subject: cur.subject || '', body: cur.body || '' }) }
  }
  useEffect(() => {
    const h = (e: KeyboardEvent) => keys.current(e)
    document.addEventListener('keydown', h)
    return () => document.removeEventListener('keydown', h)
  }, [])

  const paused = ok?.paused
  const anyMail = pending.length || ok?.approved?.length || ok?.sent?.length
  const actions = ok && (
    <>
      {pending.length > 0 && !paused && <Button variant="primary" size="sm" busy={busy} onClick={approveAll}>Approve all {pending.length}</Button>}
      {paused
        ? <Button size="sm" onClick={() => toggleSending('resume')}>Resume sending</Button>
        : <Button size="sm" onClick={() => toggleSending('pause')}>Pause all sending</Button>}
    </>
  )

  let body: React.ReactNode
  if (data === undefined) body = <Skeleton rows={4} />
  else if (data === null) body = <OfflineState />
  else if (data.error) {
    // Never let a failed read pass for "Nothing to review".
    body = <EmptyState icon={<AlertTriangle className="size-5" />} title="Couldn't read the Outbox">{data.error}</EmptyState>
  } else if (!cur) {
    body = <Card><EmptyState icon={<Check className="size-5" />} title="Nothing to review">
      Every draft Harvey writes lands here first. Approve one and it sends on schedule.</EmptyState></Card>
  } else {
    body = (
      <div className="grid grid-cols-[280px_minmax(0,1fr)] items-start gap-4 max-lg:grid-cols-1">
        <Rail items={pending} index={i} onGo={go} />
        <Pane cur={cur} index={i} total={pending.length} footer={ok?.footer || ''} edit={editing} busy={busy}
          onAct={a => act(cur.id, a)}
          onEdit={() => setEdit({ id: cur.id, subject: cur.subject || '', body: cur.body || '' })}
          onChange={setEdit} onSave={save} onCancel={() => setEdit(null)} />
      </div>
    )
  }

  return (
    <div>
      <SectionHead title="Outbox" actions={actions}>
        Every outgoing email stops here. Read one, decide, move on — <kbd>A</kbd> approve, <kbd>R</kbd> reject, <kbd>E</kbd> edit, <kbd>J</kbd>/<kbd>K</kbd> to move.
      </SectionHead>
      {/* The kill switch gets a banner only when it's actually on. */}
      <div className="mb-4 space-y-2">
        {paused && <Banner><div><b>Sending is paused</b><p>{paused}. Approved mail stays queued until you resume.</p></div></Banner>}
        {ok?.compliance_notice && anyMail ? (
          <Banner tone="wait"><div><b>Add your postal address</b><p>{ok.compliance_notice}</p></div></Banner>
        ) : null}
      </div>
      {body}
      {ok && <OutboxTables approved={ok.approved} sent={ok.sent} failed={ok.failed} />}
    </div>
  )
}
