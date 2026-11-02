import { useEffect, useRef, useState } from 'react'
import { ArrowDown, ArrowUp, LayoutGrid, Pencil, Plus, Trash2, X } from 'lucide-react'
import { post } from '@/lib/api'
import { useApi } from '@/lib/hooks'
import { safeHref } from '@/lib/format'
import { Button } from '@/components/ui/button'
import { Card, SectionHead, Subhead } from '@/components/ui/card'
import { Field, Input, Textarea } from '@/components/ui/field'
import { EmptyState, OfflineState, Skeleton } from '@/components/ui/states'
import { useToast } from '@/components/toast'
import { TableCard, TH, TD } from './pipeline/shared'

interface Demo { label: string; url: string; keywords: string[]; companies?: number }
interface Data { demos: Demo[]; unmatched: { industry: string; n: number }[]; unmatched_total: number }

/** Kinds of business and the example site Harvey shows each one. A company
 *  gets the first category with a keyword in its industry, so order matters. */
export function CategoriesPage() {
  const toast = useToast()
  const { data, refetch } = useApi<Data>('/api/settings/demos')
  const [editing, setEditing] = useState<number | null | undefined>(undefined)   // null: adding one
  const [confirming, setConfirming] = useState(-1)
  const [saving, setSaving] = useState(false)

  const head = (
    <SectionHead title="Categories">
      Each kind of business and the example site Roadie Crew shows it. A company gets the <b>first</b> category with
      one of its keywords in the company's industry, so put specific ones (Odontologia) before generic ones (Clínica).
    </SectionHead>
  )
  if (data === undefined) return <>{head}<Skeleton rows={5} /></>
  if (!data) return <>{head}<OfflineState /></>
  const demos = data.demos

  async function save(next: Demo[], done: string) {
    setSaving(true)
    const r = await post<{ success?: boolean; message?: string }>('/api/settings/demos',
      { demos: next.map(({ label, url, keywords }) => ({ label, url, keywords })) })
    setSaving(false)
    if (r && r.success) { toast(`${done} Roadie Crew uses it from its next cycle.`, 'success'); refetch(); return true }
    toast((r && r.message) || 'Could not save it.', 'error')
    return false
  }
  const move = (i: number, by: number) => {
    const next = [...demos]
    ;[next[i], next[i + by]] = [next[i + by], next[i]]
    save(next, 'Order saved.')
  }

  return (
    <>
      {head}
      <Button size="sm" className="mb-4" onClick={() => setEditing(null)}><Plus className="size-3.5" aria-hidden />Add a category</Button>
      {!demos.length
        ? <EmptyState icon={<LayoutGrid className="size-5" />} title="No categories yet">Add one with its demo site, and Roadie Crew offers it to the businesses it fits.</EmptyState>
        : (
          <TableCard>
            <table className="w-full text-[13px]">
              <thead><tr>{['#', 'Category', 'Demo site', 'Keywords', 'Companies', ''].map(h => <th key={h || 'actions'} className={TH + (h === 'Companies' ? ' text-right' : '')}>{h}</th>)}</tr></thead>
              <tbody>
                {demos.map((d, i) => (
                  <tr key={d.label} className="border-t border-line align-top">
                    <td className={TD + ' tabular-nums text-text-3'}>{i + 1}</td>
                    <td className={TD + ' whitespace-nowrap font-medium'}>{d.label}</td>
                    <td className={TD + ' whitespace-nowrap'}>
                      {safeHref(d.url) && <a className="text-accent hover:underline" href={safeHref(d.url)} target="_blank" rel="noopener noreferrer">{d.url.replace(/^https:\/\//, '')}</a>}
                    </td>
                    <td className={TD}>
                      <div className="flex max-w-[420px] flex-wrap gap-1">
                        {d.keywords.slice(0, 10).map(k => <span key={k} className="rounded-xs bg-surface-2 px-1.5 py-px text-[12px]" title={k.startsWith(' ') ? 'Starts with a space: matches only as a separate word' : undefined}>{k.startsWith(' ') ? '␣' + k.trimStart() : k}</span>)}
                        {d.keywords.length > 10 && <span className="text-[12px] text-text-3">+{d.keywords.length - 10}</span>}
                      </div>
                    </td>
                    <td className={TD + ' text-right tabular-nums'}>{d.companies ?? 0}</td>
                    <td className={TD + ' whitespace-nowrap text-right'}>
                      {confirming === i ? (
                        <span className="inline-flex items-center gap-1">
                          <span className="text-[12px] text-text-2">Delete?</span>
                          <Button size="sm" variant="ghost" className="text-bad" disabled={saving}
                            onClick={async () => { setConfirming(-1); await save(demos.filter((_, j) => j !== i), `${d.label} deleted.`) }}>Delete</Button>
                          <Button size="sm" variant="ghost" onClick={() => setConfirming(-1)}>Keep</Button>
                        </span>
                      ) : (
                        <span className="inline-flex gap-0.5">
                          <Button size="sm" variant="ghost" aria-label={`Move ${d.label} up`} title="Move up" disabled={saving || i === 0} onClick={() => move(i, -1)}><ArrowUp className="size-3.5" /></Button>
                          <Button size="sm" variant="ghost" aria-label={`Move ${d.label} down`} title="Move down" disabled={saving || i === demos.length - 1} onClick={() => move(i, 1)}><ArrowDown className="size-3.5" /></Button>
                          <Button size="sm" variant="ghost" aria-label={`Edit ${d.label}`} title="Edit" onClick={() => setEditing(i)}><Pencil className="size-3.5" /></Button>
                          <Button size="sm" variant="ghost" aria-label={`Delete ${d.label}`} title="Delete" onClick={() => setConfirming(i)}><Trash2 className="size-3.5" /></Button>
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableCard>
        )}

      {data.unmatched_total > 0 && (
        <>
          <Subhead hint={data.unmatched_total}>Companies with no category</Subhead>
          <Card>
            <p className="mb-2 text-[13px] text-text-2">These industries match no keyword, so Roadie Crew has no example site to offer them. Add a keyword to a category, or a new category.</p>
            <div className="flex flex-wrap gap-1.5">
              {data.unmatched.map(u => <span key={u.industry} className="rounded-xs bg-surface-2 px-1.5 py-px text-[12px]">{u.industry} <span className="text-text-3">{u.n}</span></span>)}
            </div>
          </Card>
        </>
      )}

      {editing !== undefined && (
        <DemoDialog demo={editing === null ? null : demos[editing]} saving={saving} onClose={() => setEditing(undefined)}
          onSave={async d => {
            const next = editing === null ? [...demos, d] : demos.map((x, j) => (j === editing ? d : x))
            if (await save(next, editing === null ? `${d.label} added.` : 'Saved.')) setEditing(undefined)
          }} />
      )}
    </>
  )
}

function DemoDialog({ demo, saving, onClose, onSave }: {
  demo: Demo | null; saving: boolean; onClose: () => void; onSave: (d: Demo) => void
}) {
  const first = useRef<HTMLInputElement>(null)
  const [label, setLabel] = useState(demo?.label || '')
  const [url, setUrl] = useState(demo?.url || 'https://')
  // One keyword per line: a leading space is kept (" law" matches the word, not "lawn").
  const [words, setWords] = useState((demo?.keywords || []).join('\n'))
  useEffect(() => {
    first.current?.focus()
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  const keywords = words.split('\n').map(w => w.replace(/\s+$/, '')).filter(w => w.trim())

  return (
    <div className="fixed inset-0 z-[1000] grid place-items-center bg-black/50 p-4" onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
      <form role="dialog" aria-modal="true" aria-label={demo ? 'Edit category' : 'Add a category'}
        onSubmit={e => { e.preventDefault(); onSave({ label: label.trim(), url: url.trim(), keywords }) }}
        className="max-h-[90vh] w-full max-w-[520px] overflow-y-auto rounded-lg bg-surface p-5 shadow-pop">
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-[15px] font-semibold">{demo ? 'Edit category' : 'Add a category'}</h3>
          <Button type="button" variant="ghost" size="sm" aria-label="Close" onClick={onClose}><X className="size-4" /></Button>
        </div>
        <div className="space-y-3">
          <Field label="Name" htmlFor="demo-label"><Input id="demo-label" ref={first} value={label} onChange={e => setLabel(e.target.value)} placeholder="Pet shop" maxLength={60} /></Field>
          <Field label="Demo site" htmlFor="demo-url"><Input id="demo-url" value={url} onChange={e => setUrl(e.target.value)} placeholder="https://demo-petshop.guilhermeh.me" /></Field>
          <Field label="Keywords, one per line" htmlFor="demo-words"
            hint="Lowercase pieces of the industry, in Portuguese or English (pet, veterinár, pet store). Start one with a space to match it only as a separate word.">
            <Textarea id="demo-words" rows={7} value={words} onChange={e => setWords(e.target.value)} placeholder={'pet\nveterinár'} />
          </Field>
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={saving || !label.trim() || !keywords.length}>{saving ? 'Saving…' : demo ? 'Save' : 'Add category'}</Button>
        </div>
      </form>
    </div>
  )
}
