import { useEffect, useRef, useState } from 'react'
import { X } from 'lucide-react'
import { api, JSON_HEADERS } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Field, Input } from '@/components/ui/field'
import { useToast } from '@/components/toast'
import type { Prospect } from '../pipeline/shared'

const FIELDS: [keyof Prospect & string, string, string][] = [
  ['first_name', 'First name', ''], ['last_name', 'Last name', ''], ['title', 'Role', 'Owner, manager…'],
  ['email', 'Email', 'name@business.com'], ['phone', 'Phone', '(41) 99999-9999'], ['linkedin_url', 'LinkedIn', 'https://linkedin.com/in/…'],
]
const GRADE: Record<string, string> = {
  verified: 'Email verified: it can get cold email.',
  risky: 'Email accepted, but the domain takes any address: sent only if you allow risky ones.',
  guess: "Email couldn't be verified, so Roadie Crew won't send cold email to it.",
  invalid: 'That email address bounces: Roadie Crew won\'t use it.',
}

/** Add a person at a company, or edit one. Emails are graded like any
 *  address Harvey finds, so only a deliverable one gets cold email. */
export function ContactDialog({ companyId, contact, onClose, onSaved }: {
  companyId: string; contact?: Prospect | null; onClose: () => void; onSaved: () => void
}) {
  const toast = useToast()
  const first = useRef<HTMLInputElement>(null)
  const [form, setForm] = useState(() => Object.fromEntries(FIELDS.map(([k]) => [k, String(contact?.[k] ?? '')])) as Record<string, string>)
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
    // Only what changed: an untouched email isn't graded (and charged) again.
    const body = contact ? Object.fromEntries(Object.entries(form).filter(([k, v]) => v !== String(contact[k as keyof Prospect] ?? ''))) : form
    const r = await api<{ success?: boolean; message?: string; email_status?: string }>(
      contact ? '/api/prospects/' + encodeURIComponent(String(contact.id)) : `/api/companies/${encodeURIComponent(companyId)}/contacts`,
      { method: contact ? 'PATCH' : 'POST', headers: JSON_HEADERS, body: JSON.stringify(body) })
    setSaving(false)
    if (r && r.success) {
      toast([contact ? 'Saved.' : 'Contact added.', GRADE[r.email_status || '']].filter(Boolean).join(' '),
        'success')
      onSaved()
    } else toast((r && r.message) || 'Could not save it.', 'error')
  }

  const unchanged = !!contact && FIELDS.every(([k]) => form[k] === String(contact[k] ?? ''))
  return (
    <div className="fixed inset-0 z-[1000] grid place-items-center bg-black/50 p-4" onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
      <form role="dialog" aria-modal="true" aria-label={contact ? 'Edit contact' : 'Add a contact'} onSubmit={save}
        className="max-h-[90vh] w-full max-w-[520px] overflow-y-auto rounded-lg bg-surface p-5 shadow-pop">
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-[15px] font-semibold">{contact ? 'Edit contact' : 'Add a contact'}</h3>
          <Button type="button" variant="ghost" size="sm" aria-label="Close" onClick={onClose}><X className="size-4" /></Button>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          {FIELDS.map(([k, label, hint], i) => (
            <Field key={k} label={label} htmlFor={'contact-' + k} className={i >= 3 ? 'sm:col-span-2' : ''}>
              <Input id={'contact-' + k} ref={i === 0 ? first : undefined} value={form[k]} autoComplete="off" placeholder={hint}
                type={k === 'email' ? 'email' : 'text'} onChange={e => setForm(f => ({ ...f, [k]: e.target.value }))} />
            </Field>
          ))}
        </div>
        <p className="mt-3 text-[12px] text-text-3">A new email is checked with your verifier before Roadie Crew ever sends to it.</p>
        <div className="mt-4 flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={saving || unchanged}>{saving ? 'Saving…' : contact ? 'Save' : 'Add contact'}</Button>
        </div>
      </form>
    </div>
  )
}
