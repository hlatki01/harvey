import { useState } from 'react'
import { api, post } from '@/lib/api'
import { useApi } from '@/lib/hooks'
import { failMsg, own, relTime, type Tone } from '@/lib/format'
import { Button } from '@/components/ui/button'
import { Card, Subhead } from '@/components/ui/card'
import { ToneBadge } from '@/components/ui/badge'
import { Field, Input, Select } from '@/components/ui/field'
import { Muted } from '@/components/ui/states'
import { useToast } from '@/components/toast'

interface User { id: string; name?: string; email: string; role: string; status: string; last_login?: string }
interface InviteResult { success?: boolean; email_sent?: boolean; message?: string; invite_url?: string }

const USER_STATUS: Record<string, [string, Tone]> = { active: ['Active', 'good'], invited: ['Invited', 'waiting'], disabled: ['Disabled', 'bad'] }

export function UsersPane() {
  const toast = useToast()
  const { data, refetch } = useApi<{ users: User[] }>('/api/users')
  const [note, setNote] = useState('')
  const [sending, setSending] = useState(false)
  const [form, setForm] = useState({ email: '', name: '', role: 'operator' })

  // The server says whether the email went out; if it didn't, the link is here to copy.
  function showInviteResult(r: InviteResult | null) {
    if (!r) return toast('Could not send the invite.', 'error')
    if (!r.success) return toast(failMsg(r, ''), 'error')
    toast(r.email_sent ? 'Invite sent.' : (r.message || 'Invite created.'), r.email_sent ? 'success' : 'error')
    setNote(!r.email_sent && r.invite_url ? `${r.message || ''} Link: ${r.invite_url}` : '')
  }

  async function invite(e: React.FormEvent) {
    e.preventDefault()
    setSending(true)
    const r = await post<InviteResult>('/api/users/invite', form)
    setSending(false)
    showInviteResult(r)
    if (r && r.success) { setForm({ email: '', name: '', role: 'operator' }); refetch() }
  }

  async function userAction(id: string, kind: 'role' | 'status' | 'resend' | 'delete', value?: string) {
    const base = '/api/users/' + encodeURIComponent(id)
    let r: { success?: boolean } | null
    if (kind === 'delete') {
      if (!confirm('Remove this person? They are signed out and lose access.')) { refetch(); return }
      r = await api(base, { method: 'DELETE' })
    } else if (kind === 'resend') {
      showInviteResult(await post<InviteResult>(base + '/resend')); refetch(); return
    } else {
      r = await post(base + '/' + kind, kind === 'role' ? { role: value } : { status: value })
    }
    if (!r || !r.success) toast(r ? failMsg(r, 'That did not work.') : 'That did not work.', 'error')
    refetch()
  }

  return (
    <div>
      <Subhead hint="They get an email with a link to create a password. Operators work conversations and approvals; admins also manage settings, spending and users.">
        Invite someone
      </Subhead>
      <Card>
        <form onSubmit={invite} className="flex flex-wrap items-end gap-3">
          <Field label="Email" htmlFor="inv-email" className="mb-0 min-w-48 flex-1">
            <Input id="inv-email" type="email" required autoComplete="off" value={form.email} onChange={e => setForm(f => ({ ...f, email: e.target.value }))} />
          </Field>
          <Field label="Name" htmlFor="inv-name" className="mb-0 min-w-40 flex-1">
            <Input id="inv-name" maxLength={80} autoComplete="off" value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} />
          </Field>
          <Field label="Role" htmlFor="inv-role" className="mb-0 w-36">
            <Select id="inv-role" value={form.role} onChange={e => setForm(f => ({ ...f, role: e.target.value }))}>
              <option value="operator">Operator</option>
              <option value="admin">Admin</option>
            </Select>
          </Field>
          <Button variant="primary" type="submit" busy={sending}>{sending ? 'Sending…' : 'Send invite'}</Button>
        </form>
        {note && <Muted className="mt-3 break-all">{note}</Muted>}
      </Card>

      <Subhead>People</Subhead>
      {data === undefined ? null : data === null ? (
        <Card><Muted>Could not load users.</Muted></Card>
      ) : !data.users.length ? (
        <Card><Muted>Nobody yet. Invite your first operator above.</Muted></Card>
      ) : (
        <Card flush>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-[13px]">
              <thead className="text-[12px] text-text-3">
                <tr><th className="px-4 py-2 font-medium">Person</th><th className="px-4 py-2 font-medium">Role</th><th className="px-4 py-2 font-medium">Status</th><th className="px-4 py-2 font-medium">Last sign-in</th><th className="px-4 py-2" /></tr>
              </thead>
              <tbody>
                {data.users.map(u => {
                  const st = own(USER_STATUS, u.status) || [u.status, 'idle' as Tone]
                  return (
                    <tr key={u.id} className="border-t border-line">
                      <td className="px-4 py-2.5">
                        <b>{u.name || u.email}</b>
                        {u.name && <div className="text-[12px] text-text-3">{u.email}</div>}
                      </td>
                      <td className="px-4 py-2.5">
                        <Select className="w-32" aria-label={`Role of ${u.email}`} value={u.role} onChange={e => userAction(u.id, 'role', e.target.value)}>
                          <option value="operator">Operator</option>
                          <option value="admin">Admin</option>
                        </Select>
                      </td>
                      <td className="px-4 py-2.5"><ToneBadge tone={st[1]}>{st[0]}</ToneBadge></td>
                      <td className="px-4 py-2.5">{u.last_login ? relTime(u.last_login) : '—'}</td>
                      <td className="px-4 py-2.5">
                        <div className="flex justify-end gap-2">
                          {u.status === 'invited' && <Button size="sm" onClick={() => userAction(u.id, 'resend')}>Resend</Button>}
                          {u.status === 'active' && <Button size="sm" onClick={() => userAction(u.id, 'status', 'disabled')}>Disable</Button>}
                          {u.status === 'disabled' && <Button size="sm" onClick={() => userAction(u.id, 'status', 'active')}>Enable</Button>}
                          <Button size="sm" variant="ghost" onClick={() => userAction(u.id, 'delete')}>Remove</Button>
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  )
}
