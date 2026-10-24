import { useEffect, useRef, useState } from 'react'
import { X } from 'lucide-react'
import { api } from '@/lib/api'
import { failMsg, relTime, safeHref } from '@/lib/format'
import { Button } from '@/components/ui/button'
import { ToneBadge } from '@/components/ui/badge'
import { Muted } from '@/components/ui/states'
import { Spin } from './Chat'
import type { LeadInfo } from './types'

function Row({ k, children }: { k: string; children: React.ReactNode }) {
  if (!children) return null
  return <div className="flex justify-between gap-2.5 py-[3px] text-[13px]"><span className="flex-none text-text-3">{k}</span><b className="break-words text-right font-medium">{children}</b></div>
}
const Title = ({ children }: { children: React.ReactNode }) => <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-text-3">{children}</div>

function Link({ u }: { u?: string }) {
  const href = safeHref(u)
  return href ? <a className="text-accent underline" href={href} target="_blank" rel="noopener noreferrer">{u}</a> : <>{u || ''}</>
}

function Panel({ d }: { d: LeadInfo }) {
  const l = d.lead || {}, co = d.company, cv = d.conversation || {}
  const person = [l.first_name, l.last_name].filter(Boolean).join(' ')
  return (
    <>
      <div className="mb-3 grid gap-4 [grid-template-columns:repeat(auto-fit,minmax(240px,1fr))]">
        <div>
          <Title>Lead</Title>
          <Row k="Name">{person}</Row><Row k="Phone">{l.phone}</Row><Row k="Email">{l.email}</Row>
          <Row k="Title">{l.title}</Row><Row k="Status">{l.pstatus}</Row><Row k="Came from">{l.source}</Row>
          <Row k="Business type">{l.industry}</Row><Row k="First seen">{l.pcreated ? relTime(l.pcreated) : ''}</Row>
          <Row k="Stage">{cv.stage}</Row><Row k="Intent">{cv.intent}</Row>
        </div>
        <div>
          <Title>Company</Title>
          {co ? (
            <>
              <Row k="Company">{co.name}</Row><Row k="Website"><Link u={co.website || co.domain} /></Row>
              <Row k="Type">{co.industry}</Row><Row k="Place">{co.location}</Row>
              <Row k="Phone">{co.phone}</Row><Row k="Found via">{co.source}</Row>
              {d.signals && d.signals.length > 0 && (
                <Row k="Signals"><span className="flex flex-wrap justify-end gap-1">{d.signals.map((s, i) => <ToneBadge key={i} tone="idle">{s}</ToneBadge>)}</span></Row>
              )}
              {co.description && <p className="mt-1.5 text-[12px] text-text-2">{co.description}</p>}
            </>
          ) : <Muted>Not from a discovered business: this person wrote to us first.</Muted>}
        </div>
      </div>
      <Title>Contact history</Title>
      <ul className="m-0 list-none p-0 text-[12px]">
        {(d.history || []).map((e, i) => (
          <li key={i} className="break-words border-t border-line py-1.5"><span className="inline-block min-w-[76px] text-text-3">{relTime(e.ts)}</span>{e.text}</li>
        ))}
        {!(d.history || []).length && <li className="text-text-2">Nothing yet.</li>}
      </ul>
    </>
  )
}

/** The lead opens in a modal: Esc, the close button or a click outside closes it. */
export function LeadModal({ id, name, onClose }: { id: string; name: string; onClose: () => void }) {
  const [d, setD] = useState<LeadInfo | { error: string } | undefined>()
  const closeBtn = useRef<HTMLButtonElement>(null)
  const closeRef = useRef(onClose)
  closeRef.current = onClose

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null
    closeBtn.current?.focus()
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') closeRef.current() }
    document.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('keydown', onKey); if (opener && document.contains(opener)) opener.focus() }
  }, [])

  useEffect(() => {
    let live = true
    api<LeadInfo>('/api/conversations/' + encodeURIComponent(id) + '/lead').then(r => {
      if (live) setD(r && r.lead ? r : { error: failMsg(r, 'Could not load this lead.') })
    })
    return () => { live = false }
  }, [id])

  return (
    <div className="fixed inset-0 z-[1000] grid place-items-center bg-black/50 p-4" onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
      <div role="dialog" aria-modal="true" aria-label={name} className="max-h-[85vh] w-full max-w-[640px] overflow-y-auto rounded-lg bg-surface p-5 shadow-pop">
        <div className="mb-3 flex items-center justify-between gap-2">
          <h3 className="text-[15px] font-semibold">{name}</h3>
          <Button ref={closeBtn} variant="ghost" size="sm" aria-label="Close" onClick={onClose}><X className="size-4" /></Button>
        </div>
        {!d ? <div className="py-1 text-center text-[12px] text-text-3"><Spin />Loading…</div>
          : 'error' in d ? <Muted>{d.error}</Muted> : <Panel d={d} />}
      </div>
    </div>
  )
}
