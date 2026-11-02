import { useState } from 'react'
import { post } from '@/lib/api'
import { useApi } from '@/lib/hooks'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { OfflineState, Skeleton } from '@/components/ui/states'
import { useToast } from '@/components/toast'
import { Row, TextField, code } from './parts'

interface City { name: string; coordinate: string }

export function CitiesPane() {
  const { data, refetch } = useApi<{ cities: City[] }>('/api/settings/geography')
  // Remounting after a save re-seeds the rows from what the file now holds.
  const [version, setVersion] = useState(0)
  if (data === undefined) return <Skeleton rows={3} />
  if (data === null) return <OfflineState />
  return <CitiesForm key={version} initial={data.cities} reload={async () => { await refetch(); setVersion(v => v + 1) }} />
}

function CitiesForm({ initial, reload }: { initial: City[]; reload: () => Promise<void> }) {
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
            <div key={i} className="flex items-end gap-2">
              <TextField className="flex-1" label="City" value={r.name} onChange={edit(i, 'name')} placeholder="Joinville, SC" />
              <TextField className="flex-1" label="Coordinates (optional)" value={r.coordinate} onChange={edit(i, 'coordinate')} placeholder="-26.30,-48.85,40" />
              <Button variant="ghost" aria-label={`Remove ${r.name || 'city'}`} onClick={() => setRows(rs => rs.filter((_, j) => j !== i))}>Remove</Button>
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
