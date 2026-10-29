import { Subhead } from '@/components/ui/card'
import { useApi } from '@/lib/hooks'
import { TableCard, TD, TH } from '../pipeline/shared'

interface CityRow {
  location: string; companies: number; prospects: number; contacted: number
  replied: number; positive: number; won: number; opted_out: number; reply_rate: number | null
}

const COLS: [keyof CityRow, string][] = [
  ['companies', 'Companies'], ['prospects', 'Contacts'], ['contacted', 'Contacted'],
  ['replied', 'Replied'], ['positive', 'Interested'], ['won', 'Won'], ['opted_out', 'Opted out'],
]

/** The pipeline per city, so markets can be compared side by side. */
export function Cities() {
  const { data } = useApi<CityRow[]>('/api/funnel/cities', { interval: 60000 })
  if (!Array.isArray(data) || !data.length) return null
  return (
    <>
      <Subhead hint="Reply rate = real replies ÷ contacted; automatic messages don't count">By city</Subhead>
      <TableCard>
        <table className="w-full text-[13px]">
          <thead>
            <tr className="border-b border-line">
              <th className={TH}>City</th>
              {COLS.map(([, label]) => <th key={label} className={TH + ' text-right'}>{label}</th>)}
              <th className={TH + ' text-right'}>Reply rate</th>
            </tr>
          </thead>
          <tbody>
            {data.map(r => (
              <tr key={r.location} className="border-b border-line last:border-0">
                <td className={TD}>{r.location || <span className="text-text-3">No city</span>}</td>
                {COLS.map(([k]) => <td key={k} className={TD + ' text-right tabular-nums'}>{r[k]}</td>)}
                <td className={TD + ' text-right tabular-nums'}>
                  {r.reply_rate == null ? <span className="text-text-3">—</span> : `${Math.round(r.reply_rate * 1000) / 10}%`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableCard>
    </>
  )
}
