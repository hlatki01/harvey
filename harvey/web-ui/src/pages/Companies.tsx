import { useState } from 'react'
import { Building2, ArrowLeft } from 'lucide-react'
import { useApi } from '@/lib/hooks'
import { relTime, safeHref, sourceLabel } from '@/lib/format'
import { Button } from '@/components/ui/button'
import { Card, SectionHead } from '@/components/ui/card'
import { StatusBadge } from '@/components/ui/badge'
import { EmptyState, OfflineState, Skeleton } from '@/components/ui/states'
import { Avatar, EmailTag, TableCard, TH, TD, type Prospect } from './pipeline/shared'

interface Company {
  id: string | number; name?: string; domain?: string; website?: string; industry?: string
  location?: string; company_size?: string; contact_count?: number; source?: string; created_at?: string
}

function Drill({ company, onBack }: { company: Company; onBack: () => void }) {
  const { data } = useApi<Prospect[]>('/api/companies/' + encodeURIComponent(String(company.id)) + '/contacts')
  return (
    <>
      <Button variant="ghost" size="sm" className="mb-3" onClick={onBack}><ArrowLeft className="size-3.5" />All companies</Button>
      <Card flush>
        <div className="flex items-baseline justify-between gap-3 px-4 py-3">
          <h3 className="font-semibold">{company.name}</h3>
          <span className="text-[13px] text-text-2">{[company.industry, company.location].filter(Boolean).join(' · ')}</span>
        </div>
        {data === undefined ? <div className="p-4"><Skeleton rows={2} /></div>
          : !data || !data.length ? <p className="px-4 pb-6 pt-2 text-center text-text-2">No contacts found at this company yet.</p>
          : (
            <div className="overflow-x-auto">
              <table className="w-full text-[13px]">
                <thead><tr>{['Name', 'Email', 'Phone', 'LinkedIn', 'Status', 'Source'].map(h => <th key={h} className={TH}>{h}</th>)}</tr></thead>
                <tbody>
                  {data.map(p => (
                    <tr key={p.id} className="border-t border-line">
                      <td className={TD}>
                        <div className="flex items-center gap-2">
                          <Avatar first={p.first_name} last={p.last_name} />
                          <div><div className="font-medium">{p.first_name} {p.last_name}</div><div className="text-[12px] text-text-3">{p.title}</div></div>
                        </div>
                      </td>
                      <td className={TD}>{p.email}<EmailTag p={p} /></td>
                      <td className={TD}>{p.phone}{p.phone_verified && <span className="ml-1 text-text-3" title="Phone verified">✓</span>}</td>
                      <td className={TD}>{safeHref(p.linkedin_url) && <a className="text-accent underline" href={safeHref(p.linkedin_url)} target="_blank" rel="noopener noreferrer">Profile</a>}</td>
                      <td className={TD}><StatusBadge status={p.status} /></td>
                      <td className={TD + ' text-text-2'}>{sourceLabel(p.source)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </Card>
    </>
  )
}

export function CompaniesPage() {
  const [drill, setDrill] = useState<Company | null>(null)
  // Not refreshed while drilled into one company, as before.
  const { data } = useApi<Company[]>('/api/companies', { interval: drill ? undefined : 15000 })

  const head = <SectionHead title="Companies">Organizations Harvey has researched. Click a row to see its contacts.</SectionHead>
  if (drill) return <>{head}<Drill company={drill} onBack={() => setDrill(null)} /></>
  if (data === undefined) return <>{head}<Skeleton /></>
  if (!data) return <>{head}<OfflineState /></>
  if (!data.length) {
    return <>{head}<EmptyState icon={<Building2 className="size-5" />} title="No companies yet">
      Confirm your <b>Signals</b>, then find businesses on the <b>Discover</b> tab. OpenStreetMap is free and needs no account.
    </EmptyState></>
  }
  return (
    <>
      {head}
      <TableCard>
        <table className="w-full whitespace-nowrap text-[13px]">
          <thead><tr>{['Company', 'Industry', 'Location', 'Size', 'Contacts', 'Source', 'Added'].map(h => <th key={h} className={TH + (h === 'Contacts' ? ' text-right' : '')}>{h}</th>)}</tr></thead>
          <tbody>
            {data.map(c => {
              const website = c.website || (c.domain ? 'https://' + c.domain : '')
              return (
                <tr key={c.id} className="cursor-pointer border-t border-line hover:bg-surface-2" tabIndex={0}
                  onClick={() => setDrill(c)} onKeyDown={e => { if (e.key === 'Enter') setDrill(c) }}>
                  <td className={TD}>
                    <div className="font-medium">{c.name}</div>
                    <div className="text-[12px] text-text-3">
                      {safeHref(website)
                        ? <a className="text-accent hover:underline" href={safeHref(website)} target="_blank" rel="noopener noreferrer" onClick={e => e.stopPropagation()}>{c.domain || website.replace(/^https?:\/\//i, '')}</a>
                        : c.domain}
                    </div>
                  </td>
                  <td className={TD}>{c.industry && <span className="rounded-xs bg-surface-2 px-1.5 py-px text-[12px]">{c.industry}</span>}</td>
                  <td className={TD}>{c.location}</td>
                  <td className={TD + ' text-text-2'}>{c.company_size}</td>
                  <td className={TD + ' text-right tabular-nums'}>{c.contact_count || 0}</td>
                  <td className={TD + ' text-text-2'}>{sourceLabel(c.source)}</td>
                  <td className={TD + ' text-text-2'}>{relTime(c.created_at)}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </TableCard>
      <p className="mt-2 text-[12px] text-text-3">{data.length} {data.length === 1 ? 'company' : 'companies'}</p>
    </>
  )
}
