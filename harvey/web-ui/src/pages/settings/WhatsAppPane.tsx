import { useEffect, useState } from 'react'
import { api, post } from '@/lib/api'
import { useApi } from '@/lib/hooks'
import { failMsg, own, relTime, type Tone } from '@/lib/format'
import { Button } from '@/components/ui/button'
import { Card, Subhead } from '@/components/ui/card'
import { ToneBadge } from '@/components/ui/badge'
import { Field, Input } from '@/components/ui/field'
import { Muted } from '@/components/ui/states'
import { useToast } from '@/components/toast'

interface Instance { name: string; status: string; number: string; profile_name: string; disconnected_at: string }
interface Result { success?: boolean; message?: string; qr?: string }

const STATUS: Record<string, [string, Tone]> = { open: ['Connected', 'good'], connecting: ['Waiting for scan', 'waiting'], close: ['Disconnected', 'bad'] }

export function WhatsAppPane() {
  const toast = useToast()
  const [qr, setQr] = useState<{ name: string; src: string } | null>(null)
  // While a QR code is up, watch for the phone to pair, then put it away.
  const { data, refetch } = useApi<{ active: string; instances: Instance[] }>('/api/settings/whatsapp/instances', { interval: qr ? 4000 : 0 })
  const [busy, setBusy] = useState('')
  const [name, setName] = useState('')

  useEffect(() => {
    if (qr && data?.instances.some(i => i.name === qr.name && i.status === 'open')) {
      setQr(null)
      toast(`${qr.name} connected.`, 'success')
    }
  }, [data, qr, toast])

  async function act(inst: string, kind: 'connect' | 'use' | 'restart' | 'logout' | 'delete' | 'create') {
    if (kind === 'delete' && !confirm(`Delete ${inst}? Its WhatsApp session is removed from Evolution.`)) return
    if (kind === 'use' && !confirm(`Send and read WhatsApp through ${inst} from now on?`)) return
    if (kind === 'logout' && !confirm(`Disconnect ${inst}? You will need to scan a QR code again.`)) return
    const base = '/api/settings/whatsapp/instances'
    const path = base + '/' + encodeURIComponent(inst)
    setBusy(inst + kind)
    const r: Result | null = kind === 'create' ? await post(base, { name: inst })
      : kind === 'delete' ? await api(path, { method: 'DELETE' })
      : await post(path + '/' + kind)
    setBusy('')
    if (!r || !r.success) { toast(r ? failMsg(r, 'That did not work.') : 'That did not work.', 'error'); return refetch() }
    if (r.qr) setQr({ name: inst, src: r.qr })
    else if (kind === 'connect') toast(`${inst} is already connected.`, 'success')
    else toast(r.message || 'Done.', 'success')
    if (kind === 'create') setName('')
    refetch()
  }

  return (
    <div>
      <Subhead hint="Every number on your Evolution server. Roadie Crew sends from the one marked in use.">WhatsApp numbers</Subhead>
      {qr && (
        <Card className="mb-4 flex flex-wrap items-center gap-5">
          <img src={qr.src} alt={`QR code for ${qr.name}`} className="h-56 w-56 rounded-md bg-white p-2" />
          <div className="max-w-sm text-[13px]">
            <b>Scan to connect {qr.name}</b>
            <p className="mt-1 text-text-2">On the phone: WhatsApp → Linked devices → Link a device. The code expires in about 40 seconds.</p>
            <div className="mt-3 flex gap-2">
              <Button size="sm" busy={busy === qr.name + 'connect'} onClick={() => act(qr.name, 'connect')}>New code</Button>
              <Button size="sm" variant="ghost" onClick={() => setQr(null)}>Close</Button>
            </div>
          </div>
        </Card>
      )}
      {data === undefined ? null : data === null ? (
        <Card><Muted>Could not reach Evolution. Check the API URL and key under Integrations.</Muted></Card>
      ) : !data.instances.length ? (
        <Card><Muted>No numbers yet. Add one below.</Muted></Card>
      ) : (
        <Card flush>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-[13px]">
              <thead className="text-[12px] text-text-3">
                <tr><th className="px-4 py-2 font-medium">Instance</th><th className="px-4 py-2 font-medium">Number</th><th className="px-4 py-2 font-medium">Status</th><th className="px-4 py-2" /></tr>
              </thead>
              <tbody>
                {data.instances.map(i => {
                  const st = own(STATUS, i.status) || [i.status, 'idle' as Tone]
                  const active = i.name === data.active
                  return (
                    <tr key={i.name} className="border-t border-line">
                      <td className="px-4 py-2.5">
                        <b>{i.name}</b>{active && <ToneBadge tone="good" className="ml-2">in use</ToneBadge>}
                        {i.profile_name && <div className="text-[12px] text-text-3">{i.profile_name}</div>}
                      </td>
                      <td className="px-4 py-2.5 font-mono text-[12px]">{i.number ? '+' + i.number : '—'}</td>
                      <td className="px-4 py-2.5">
                        <ToneBadge tone={st[1]}>{st[0]}</ToneBadge>
                        {i.status === 'close' && i.disconnected_at && <div className="text-[12px] text-text-3">{relTime(i.disconnected_at)}</div>}
                      </td>
                      <td className="px-4 py-2.5">
                        <div className="flex justify-end gap-2">
                          {i.status !== 'open' && <Button size="sm" variant="primary" busy={busy === i.name + 'connect'} onClick={() => act(i.name, 'connect')}>Connect</Button>}
                          {!active && <Button size="sm" busy={busy === i.name + 'use'} onClick={() => act(i.name, 'use')}>Use</Button>}
                          <Button size="sm" busy={busy === i.name + 'restart'} onClick={() => act(i.name, 'restart')}>Restart</Button>
                          {i.status === 'open' && <Button size="sm" busy={busy === i.name + 'logout'} onClick={() => act(i.name, 'logout')}>Disconnect</Button>}
                          {!active && <Button size="sm" variant="ghost" busy={busy === i.name + 'delete'} onClick={() => act(i.name, 'delete')}>Delete</Button>}
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

      <Subhead hint="Letters, numbers, - and _. Use a dedicated number: cold messages get numbers banned.">Add a number</Subhead>
      <Card>
        <form onSubmit={e => { e.preventDefault(); act(name.trim(), 'create') }} className="flex flex-wrap items-end gap-3">
          <Field label="Instance name" htmlFor="wa-new" className="mb-0 min-w-48 flex-1">
            <Input id="wa-new" required pattern="[A-Za-z0-9_\-]{1,40}" autoComplete="off" value={name} onChange={e => setName(e.target.value)} />
          </Field>
          <Button variant="primary" type="submit" busy={busy.endsWith('create')}>Create and show QR</Button>
        </form>
      </Card>
    </div>
  )
}
