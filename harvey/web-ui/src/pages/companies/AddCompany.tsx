import { useState } from 'react'
import { Check, AlertTriangle, X, Search } from 'lucide-react'
import { post } from '@/lib/api'
import { safeHref } from '@/lib/format'
import { useNav } from '@/app/nav'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/field'
import { useToast } from '@/components/toast'

interface Found {
  name: string; phone?: string; address?: string; category?: string; website?: string
  rating?: number | null; review_count?: number | null; external_id: string; source_url?: string; known_id?: string
}
interface CheckRow { key: string; ok: boolean; text: string; warning: boolean }
interface Added { company_id: string; new: boolean; checks: CheckRow[]; can_open: boolean; name: string }

/** Look a business up on Google Maps, save the right one, and show what
 *  Harvey can do next with it. */
export function AddCompany({ onClose, onAdded }: { onClose: () => void; onAdded: () => void }) {
  const toast = useToast()
  const { goTab } = useNav()
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState('')
  const [found, setFound] = useState<Found[] | null>(null)
  const [added, setAdded] = useState<Added | null>(null)
  const [queued, setQueued] = useState(false)

  async function search(e: React.FormEvent) {
    e.preventDefault()
    if (!q.trim() || busy) return
    setBusy('search'); setAdded(null); setQueued(false)
    const r = await post<{ results?: Found[]; success?: boolean; message?: string }>('/api/companies/lookup', { q })
    setBusy('')
    if (r && r.results) setFound(r.results)
    else toast((r && r.message) || 'Search failed — is the dashboard still running?', 'error')
  }

  async function add(b: Found) {
    setBusy('add:' + b.external_id)
    const r = await post<Omit<Added, 'name'> & { success?: boolean; message?: string }>('/api/companies/add', { business: b })
    setBusy('')
    if (r && r.success) { setAdded({ ...r, name: b.name }); setFound(null); onAdded() }
    else toast((r && r.message) || 'Could not add it.', 'error')
  }

  async function open() {
    if (!added) return
    setBusy('open')
    const r = await post<{ success?: boolean; message?: string }>(`/api/companies/${encodeURIComponent(added.company_id)}/whatsapp`)
    setBusy('')
    if (r && r.success) { setQueued(true); toast('First message written. It waits in the Outbox for your approval.', 'success') }
    else toast((r && r.message) || 'Could not write the first message.', 'error')
  }

  return (
    <Card className="mb-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h3 className="text-[14px] font-semibold">Add a company</h3>
        <Button variant="ghost" size="sm" aria-label="Close" onClick={onClose}><X className="size-4" /></Button>
      </div>
      <form onSubmit={search} className="flex gap-2">
        <Input aria-label="Business name and city, or a Google Maps link" className="flex-1" value={q} onChange={e => setQ(e.target.value)}
          placeholder="Bello Faro, Curitiba — or paste a Google Maps link" autoFocus />
        <Button type="submit" variant="primary" disabled={!q.trim() || !!busy}><Search className="size-3.5" aria-hidden />{busy === 'search' ? 'Searching…' : 'Search'}</Button>
      </form>
      <p className="mt-1 text-[12px] text-text-3">One Google Maps search, inside the free monthly allowance.</p>

      {found && (
        found.length === 0
          ? <p className="mt-4 text-[13px] text-text-2">Nothing on Google Maps for that. Try the name as it appears there, with the city.</p>
          : (
            <ul className="mt-4 divide-y divide-line">
              {found.map(b => (
                <li key={b.external_id} className="flex items-center gap-3 py-2.5">
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-medium">{b.name}</div>
                    <div className="truncate text-[12px] text-text-2">
                      {[b.category, b.address].filter(Boolean).join(' · ')}
                    </div>
                    <div className="text-[12px] text-text-3">
                      {[b.phone || 'no phone', b.website ? 'has a website' : 'no website',
                        b.rating != null ? `★ ${b.rating} (${b.review_count ?? 0})` : ''].filter(Boolean).join(' · ')}
                      {safeHref(b.source_url) && <> · <a className="text-accent hover:underline" href={safeHref(b.source_url)} target="_blank" rel="noopener noreferrer">Maps</a></>}
                    </div>
                  </div>
                  <Button size="sm" variant={b.known_id ? 'ghost' : 'primary'} disabled={!!busy} onClick={() => add(b)}>
                    {busy === 'add:' + b.external_id ? 'Checking…' : b.known_id ? 'Already added · check' : 'Add'}
                  </Button>
                </li>
              ))}
            </ul>
          )
      )}

      {added && (
        <div className="mt-4 rounded-md bg-surface-2 p-3">
          <div className="mb-2 text-[13px] font-medium">{added.name}{added.new ? ' was added.' : ' was already in your list.'} Next steps:</div>
          <ul className="space-y-1.5 text-[13px]">
            {added.checks.map(c => (
              <li key={c.key} className="flex items-start gap-2">
                {c.ok ? <Check className="mt-0.5 size-4 shrink-0 text-good" aria-label="OK" />
                  : c.warning ? <AlertTriangle className="mt-0.5 size-4 shrink-0 text-wait" aria-label="Warning" />
                  : <X className="mt-0.5 size-4 shrink-0 text-bad" aria-label="Blocks" />}
                <span>{c.text}</span>
              </li>
            ))}
          </ul>
          <div className="mt-3 flex flex-wrap gap-2">
            {queued
              ? <Button size="sm" variant="primary" onClick={() => goTab('outbox')}>Review it in the Outbox</Button>
              : added.can_open
                ? <Button size="sm" variant="primary" disabled={!!busy} onClick={open}>{busy === 'open' ? 'Writing…' : 'Write the WhatsApp opening'}</Button>
                : <span className="text-[13px] text-text-2">Roadie Crew can't open a WhatsApp chat with this business.</span>}
            <Button size="sm" variant="ghost" onClick={() => { setAdded(null); setQ(''); setQueued(false) }}>Add another</Button>
          </div>
        </div>
      )}
    </Card>
  )
}
