import { useState } from 'react'
import { post } from '@/lib/api'
import { useApi } from '@/lib/hooks'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/field'
import { OfflineState, Skeleton } from '@/components/ui/states'
import { useToast } from '@/components/toast'
import { Row, TextField, code } from './parts'

const DAYS = [['mon', 'Monday'], ['tue', 'Tuesday'], ['wed', 'Wednesday'], ['thu', 'Thursday'], ['fri', 'Friday'], ['sat', 'Saturday'], ['sun', 'Sunday']] as const
type Day = typeof DAYS[number][0]
interface Hours { open: boolean; start: string; end: string }
interface Data { days: Record<Day, string>; timezone: string; holidays_country: string }

export function HoursPane() {
  const { data, refetch } = useApi<Data>('/api/settings/business-hours')
  const [version, setVersion] = useState(0)
  if (data === undefined) return <Skeleton rows={3} />
  if (data === null) return <OfflineState />
  return <HoursForm key={version} data={data} reload={async () => { await refetch(); setVersion(v => v + 1) }} />
}

function HoursForm({ data, reload }: { data: Data; reload: () => Promise<void> }) {
  const toast = useToast()
  // Nothing set yet means "any time": start the form on the usual week.
  const unset = Object.values(data.days).every(s => !s)
  const [rows, setRows] = useState<Record<Day, Hours>>(() => Object.fromEntries(DAYS.map(([d]) => {
    const span = unset ? (d === 'sat' ? '09:00-13:00' : d === 'sun' ? '' : '09:00-18:00') : data.days[d]
    const [start, end] = (span || '09:00-18:00').split('-')
    return [d, { open: !!span, start, end }]
  })) as Record<Day, Hours>)
  const [country, setCountry] = useState(data.holidays_country)
  const edit = (d: Day, patch: Partial<Hours>) => setRows(rs => ({ ...rs, [d]: { ...rs[d], ...patch } }))

  async function save() {
    const days = Object.fromEntries(DAYS.map(([d]) => [d, rows[d].open ? `${rows[d].start}-${rows[d].end}` : '']))
    const r = await post<{ success?: boolean; message?: string; harvey_running?: boolean }>('/api/settings/business-hours', { days, holidays_country: country })
    if (r && r.success) toast(r.harvey_running ? 'Hours saved. Restart Harvey to use them.' : 'Hours saved.', 'success')
    else toast((r && r.message) || 'Save failed — is the dashboard still running?', 'error')
    await reload()
  }

  return (
    <Card>
      <Row
        title="Business hours"
        desc={<>Harvey sends email and WhatsApp only inside these hours ({data.timezone}). Anything due outside them waits for the next opening. Your own sends from the dashboard are not held. {unset && <>Not saved yet: Harvey currently sends at any time. </>}Saved to <code className={code}>usage.business_hours</code> in your config.</>}
      >
        <div className="space-y-2">
          {DAYS.map(([d, label]) => (
            <div key={d} className="flex items-center gap-2">
              <label className="flex w-32 items-center gap-2 text-[13px]">
                <input type="checkbox" checked={rows[d].open} onChange={e => edit(d, { open: e.target.checked })} />
                {label}
              </label>
              <Input type="time" aria-label={`${label} opens`} className="w-28" disabled={!rows[d].open} value={rows[d].start} onChange={e => edit(d, { start: e.target.value })} />
              <span className="text-text-2">–</span>
              <Input type="time" aria-label={`${label} closes`} className="w-28" disabled={!rows[d].open} value={rows[d].end} onChange={e => edit(d, { end: e.target.value })} />
            </div>
          ))}
          <TextField className="w-60 pt-2" label="Closed on national holidays of (country code)" value={country} onChange={setCountry} placeholder="BR (blank: no holidays)" />
          <Button variant="primary" size="sm" onClick={save}>Save hours</Button>
        </div>
      </Row>
    </Card>
  )
}
