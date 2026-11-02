import { useEffect, useRef, useState } from 'react'
import { ArrowDown, ArrowUp, Pencil, Search, Trash2, X } from 'lucide-react'
import { api, JSON_HEADERS } from '@/lib/api'
import { useApi } from '@/lib/hooks'
import { relTime, safeHref, sourceLabel } from '@/lib/format'
import { Button } from '@/components/ui/button'
import { Input, Field } from '@/components/ui/field'
import { Skeleton, OfflineState } from '@/components/ui/states'
import { useToast } from '@/components/toast'
import { TableCard, TH, TD } from '../pipeline/shared'

export interface Company {
  id: string; name?: string; domain?: string; website?: string; industry?: string; phone?: string
  location?: string; contact_count?: number; source?: string; created_at?: string
}
interface Page { rows?: Company[]; total?: number; page?: number; per_page?: number }
type SortKey = 'name' | 'industry' | 'location' | 'contacts' | 'source' | 'created_at'

const COLS: [string, SortKey | null, string][] = [
  ['Company', 'name', ''], ['Industry', 'industry', ''], ['Location', 'location', ''], ['Phone', null, ''],
  ['Contacts', 'contacts', ' text-right'], ['Source', 'source', ''], ['Added', 'created_at', ''], ['', null, ''],
]

/** All companies, a page at a time: search, sort by a column, edit or delete a row. */
export function CompanyTable({ onOpen, version }: { onOpen: (c: Company) => void; version: number }) {
  const toast = useToast()
  const [typed, setTyped] = useState('')
  const [q, setQ] = useState('')
  const [page, setPage] = useState(1)
  const [sort, setSort] = useState<SortKey>('created_at')
  const [dir, setDir] = useState<'asc' | 'desc'>('desc')
  const [editing, setEditing] = useState<Company | null>(null)
  const [confirming, setConfirming] = useState('')
  useEffect(() => {   // search as they type, without a request per key
    const t = setTimeout(() => { setQ(typed.trim()); setPage(1) }, 300)
    return () => clearTimeout(t)
  }, [typed])
  const { data, refetch } = useApi<Page>(
    `/api/companies?q=${encodeURIComponent(q)}&page=${page}&sort=${sort}&dir=${dir}&v=${version}`, { interval: 15000 })

  function sortBy(key: SortKey) {
    if (key === sort) setDir(d => (d === 'asc' ? 'desc' : 'asc'))
    else { setSort(key); setDir(key === 'created_at' || key === 'contacts' ? 'desc' : 'asc') }
    setPage(1)
  }

  async function remove(c: Company) {
    const r = await api<{ success?: boolean; message?: string }>('/api/companies/' + encodeURIComponent(c.id), { method: 'DELETE' })
    setConfirming('')
    if (r && r.success) { toast(`${c.name} deleted. Discovery won't add it back.`, 'success'); refetch() }
    else toast((r && r.message) || 'Could not delete it.', 'error')
  }

  if (data === undefined) return <Skeleton />
  if (!data) return <OfflineState />
  const total = data.total ?? 0
  const per = data.per_page || 25
  const pages = Math.max(1, Math.ceil(total / per))
  const rows = data.rows || []

  return (
    <>
      <div className="relative mb-2 w-80 max-w-full">
        <Search className="pointer-events-none absolute left-2.5 top-2 size-4 text-text-3" aria-hidden />
        <Input type="search" aria-label="Search companies" placeholder="Search name, site, industry, place, phone"
          className="w-full pl-8" value={typed} onChange={e => setTyped(e.target.value)} />
      </div>
      <TableCard>
        <table className="w-full whitespace-nowrap text-[13px]">
          <thead>
            <tr>
              {COLS.map(([label, key, align]) => (
                <th key={label || 'actions'} className={TH + align}
                  aria-sort={key && key === sort ? (dir === 'asc' ? 'ascending' : 'descending') : undefined}>
                  {key ? (
                    <button type="button" onClick={() => sortBy(key)} className="inline-flex items-center gap-1 hover:text-text">
                      {label}
                      {key === sort && (dir === 'asc' ? <ArrowUp className="size-3" aria-hidden /> : <ArrowDown className="size-3" aria-hidden />)}
                    </button>
                  ) : label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map(c => {
              const website = c.website || (c.domain ? 'https://' + c.domain : '')
              return (
                <tr key={c.id} className="cursor-pointer border-t border-line hover:bg-surface-2" tabIndex={0}
                  onClick={() => onOpen(c)} onKeyDown={e => { if (e.key === 'Enter' && e.target === e.currentTarget) onOpen(c) }}>
                  <td className={TD + ' max-w-[380px]'}>
                    <div className="truncate font-medium" title={c.name}>{c.name}</div>
                    <div className="truncate text-[12px] text-text-3">
                      {safeHref(website)
                        ? <a className="text-accent hover:underline" href={safeHref(website)} target="_blank" rel="noopener noreferrer" onClick={e => e.stopPropagation()}>{c.domain || website.replace(/^https?:\/\//i, '')}</a>
                        : c.domain}
                    </div>
                  </td>
                  <td className={TD}>{c.industry && <span className="rounded-xs bg-surface-2 px-1.5 py-px text-[12px]">{c.industry}</span>}</td>
                  <td className={TD}>{c.location}</td>
                  <td className={TD + ' tabular-nums text-text-2'}>{c.phone}</td>
                  <td className={TD + ' text-right tabular-nums'}>{c.contact_count || 0}</td>
                  <td className={TD + ' text-text-2'}>{sourceLabel(String(c.source || '').replace(/^discover:/, ''))}</td>
                  <td className={TD + ' text-text-2'}>{relTime(c.created_at)}</td>
                  <td className={TD + ' text-right'} onClick={e => e.stopPropagation()}>
                    {confirming === c.id ? (
                      <span className="inline-flex items-center gap-1">
                        <span className="text-[12px] text-text-2">Delete?</span>
                        <Button size="sm" variant="ghost" className="text-bad" onClick={() => remove(c)}>Delete</Button>
                        <Button size="sm" variant="ghost" onClick={() => setConfirming('')}>Keep</Button>
                      </span>
                    ) : (
                      <span className="inline-flex gap-0.5">
                        <Button size="sm" variant="ghost" aria-label={`Edit ${c.name}`} title="Edit" onClick={() => setEditing(c)}><Pencil className="size-3.5" /></Button>
                        <Button size="sm" variant="ghost" aria-label={`Delete ${c.name}`} title="Delete" onClick={() => setConfirming(c.id)}><Trash2 className="size-3.5" /></Button>
                      </span>
                    )}
                  </td>
                </tr>
              )
            })}
            {!rows.length && <tr><td colSpan={COLS.length} className="px-3 py-6 text-center text-text-2">{q ? 'Nothing matches that search.' : 'No companies yet.'}</td></tr>}
          </tbody>
        </table>
      </TableCard>
      <div className="mt-2 flex items-center justify-between gap-2 text-[12px] text-text-2">
        <span className="tabular-nums">{total ? `${(page - 1) * per + 1}–${Math.min(page * per, total)} of ${total}` : '0 companies'}</span>
        {pages > 1 && (
          <span className="flex items-center gap-1">
            <Button size="sm" variant="ghost" disabled={page <= 1} onClick={() => setPage(p => p - 1)}>Previous</Button>
            <span className="tabular-nums">Page {page} of {pages}</span>
            <Button size="sm" variant="ghost" disabled={page >= pages} onClick={() => setPage(p => p + 1)}>Next</Button>
          </span>
        )}
      </div>
      {editing && <EditCompany c={editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); refetch() }} />}
    </>
  )
}

const FIELDS: [keyof Company, string][] = [
  ['name', 'Name'], ['industry', 'Industry'], ['location', 'Location'], ['phone', 'Phone'], ['website', 'Website'],
]

function EditCompany({ c, onClose, onSaved }: { c: Company; onClose: () => void; onSaved: () => void }) {
  const toast = useToast()
  const first = useRef<HTMLInputElement>(null)
  const [form, setForm] = useState(() => Object.fromEntries(FIELDS.map(([k]) => [k, String(c[k] ?? '')])) as Record<string, string>)
  const [saving, setSaving] = useState(false)
  useEffect(() => {
    first.current?.focus()
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  async function save(e: React.FormEvent) {
    e.preventDefault()
    setSaving(true)
    const r = await api<{ success?: boolean; message?: string }>('/api/companies/' + encodeURIComponent(c.id),
      { method: 'PATCH', headers: JSON_HEADERS, body: JSON.stringify(form) })
    setSaving(false)
    if (r && r.success) { toast('Saved.', 'success'); onSaved() }
    else toast((r && r.message) || 'Could not save it.', 'error')
  }

  return (
    <div className="fixed inset-0 z-[1000] grid place-items-center bg-black/50 p-4" onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
      <form role="dialog" aria-modal="true" aria-label={'Edit ' + c.name} onSubmit={save}
        className="w-full max-w-[480px] rounded-lg bg-surface p-5 shadow-pop">
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-[15px] font-semibold">Edit company</h3>
          <Button type="button" variant="ghost" size="sm" aria-label="Close" onClick={onClose}><X className="size-4" /></Button>
        </div>
        <div className="space-y-3">
          {FIELDS.map(([k, label], i) => (
            <Field key={k} label={label} htmlFor={'edit-' + k}>
              <Input id={'edit-' + k} ref={i === 0 ? first : undefined} value={form[k]} autoComplete="off"
                placeholder={k === 'website' ? 'https://…' : undefined}
                onChange={e => setForm(f => ({ ...f, [k]: e.target.value }))} />
            </Field>
          ))}
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={saving || !form.name.trim()}>{saving ? 'Saving…' : 'Save'}</Button>
        </div>
      </form>
    </div>
  )
}
