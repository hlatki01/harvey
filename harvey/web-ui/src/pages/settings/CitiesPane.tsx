import { useState } from 'react'
import { api, post } from '@/lib/api'
import { formatDate } from '@/lib/format'
import { useApi } from '@/lib/hooks'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { OfflineState, Skeleton } from '@/components/ui/states'
import { useToast } from '@/components/toast'
import { Row, TextField, code } from './parts'

interface City { name: string; coordinate: string }
interface Added extends City { reason: string; added_at: string }
interface Yield { searched: number; searches: number; found: number; new: number; last_run_at: string }
interface Geo { cities: City[]; added: Added[]; yields: Record<string, Yield>; expand: boolean; per_week: number }

/** What a city's searches brought back last time: how much new ground is left there. */
function YieldLine({ y }: { y?: Yield }) {
  if (!y) return <span className="text-[12px] text-text-3">Not searched yet.</span>
  const share = y.found ? Math.round((100 * y.new) / y.found) : 0
  const tone = !y.found || share < 20 ? 'text-text-3' : 'text-good'
  return (
    <span className="text-[12px] text-text-2">
      {y.searched} of {y.searches} searches run · last time {y.found} businesses, <b className={'font-medium ' + tone}>{y.new} new ({share}%)</b>
      {!y.found || share < 20 ? ' · worn out' : ''}
    </span>
  )
}

export function CitiesPane() {
  const { data, refetch } = useApi<Geo>('/api/settings/geography')
  // Remounting after a save re-seeds the rows from what the file now holds.
  const [version, setVersion] = useState(0)
  if (data === undefined) return <Skeleton rows={3} />
  if (data === null) return <OfflineState />
  const reload = async () => { await refetch(); setVersion(v => v + 1) }
  return (
    <>
      <CitiesForm key={version} initial={data.cities} yields={data.yields} reload={reload} />
      <AddedCities geo={data} reload={reload} />
    </>
  )
}

function AddedCities({ geo, reload }: { geo: Geo; reload: () => Promise<void> }) {
  const toast = useToast()
  async function remove(name: string) {
    const r = await api<{ success?: boolean; message?: string }>('/api/settings/geography/added/' + encodeURIComponent(name), { method: 'DELETE' })
    if (r && r.success) toast(`${name} taken out. Harvey won't search it or suggest it again.`, 'success')
    else toast((r && r.message) || 'Could not take it out.', 'error')
    await reload()
  }
  return (
    <Card className="mt-4">
      <Row
        title="Cities Harvey added"
        desc={geo.expand
          ? <>When the cities above run short of new businesses, Harvey adds the next one itself, at most {geo.per_week} a week, and tells you on WhatsApp. Take out any you don't want: it never comes back.</>
          : <>Off. Turn on <code className={code}>discovery.expand_cities</code> and Harvey adds the next city itself when these run short of new businesses.</>}
      >
        {geo.added.length ? (
          <div className="space-y-3">
            {geo.added.map(c => (
              <div key={c.name} className="flex items-start gap-3">
                <div className="min-w-0 flex-1">
                  <div className="text-[13px] font-medium">{c.name} <span className="font-normal text-text-3">· added {formatDate(c.added_at)}</span></div>
                  {c.reason && <div className="text-[12px] text-text-2">{c.reason}</div>}
                  <YieldLine y={geo.yields[c.name]} />
                </div>
                <Button variant="ghost" size="sm" aria-label={`Take out ${c.name}`} onClick={() => remove(c.name)}>Take out</Button>
              </div>
            ))}
          </div>
        ) : <p className="text-[13px] text-text-2">None yet.</p>}
      </Row>
    </Card>
  )
}

function CitiesForm({ initial, yields, reload }: { initial: City[]; yields: Record<string, Yield>; reload: () => Promise<void> }) {
  const toast = useToast()
  const [rows, setRows] = useState<City[]>(initial.length ? initial : [{ name: '', coordinate: '' }])
  const edit = (i: number, k: keyof City) => (v: string) => setRows(rs => rs.map((r, j) => (j === i ? { ...r, [k]: v } : r)))

  async function save() {
    const cities = rows.filter(r => r.name.trim())
    if (!cities.length) return toast('Keep at least one city.', 'error')
    const r = await post<{ success?: boolean; message?: string; harvey_running?: boolean }>('/api/settings/geography', { cities })
    if (r && r.success) toast('Cities saved. Harvey uses them from its next cycle.', 'success')
    else toast((r && r.message) || 'Save failed — is the dashboard still running?', 'error')
    await reload()
  }

  return (
    <Card>
      <Row
        title="Cities to search"
        desc={<>Discovery runs one search per industry × city. Coordinates (<code className={code}>lat,lng,radius_km</code>) are optional: left blank, the city is geocoded once with a 40 km radius. Saved to <code className={code}>icp.geography</code> in your config.</>}
      >
        <div className="space-y-3">
          {rows.map((r, i) => (
            <div key={i}>
              <div className="flex items-end gap-2">
                <TextField className="flex-1" label="City" value={r.name} onChange={edit(i, 'name')} placeholder="Joinville, SC" />
                <TextField className="flex-1" label="Coordinates (optional)" value={r.coordinate} onChange={edit(i, 'coordinate')} placeholder="-26.30,-48.85,40" />
                <Button variant="ghost" aria-label={`Remove ${r.name || 'city'}`} onClick={() => setRows(rs => rs.filter((_, j) => j !== i))}>Remove</Button>
              </div>
              {r.name.trim() && <div className="mt-1"><YieldLine y={yields[r.name.trim()]} /></div>}
            </div>
          ))}
          <div className="flex gap-2">
            <Button size="sm" onClick={() => setRows(rs => [...rs, { name: '', coordinate: '' }])}>Add city</Button>
            <Button variant="primary" size="sm" onClick={save}>Save cities</Button>
          </div>
        </div>
      </Row>
    </Card>
  )
}
