import { useState } from 'react'
import { ArrowLeft, Plus } from 'lucide-react'
import { useApi } from '@/lib/hooks'
import { safeHref, sourceLabel } from '@/lib/format'
import { Button } from '@/components/ui/button'
import { Card, SectionHead } from '@/components/ui/card'
import { StatusBadge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/states'
import { Avatar, EmailTag, TH, TD, type Prospect } from './pipeline/shared'
import { AddCompany } from './companies/AddCompany'
import { CompanyTable, type Company } from './companies/CompanyTable'


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
  const [adding, setAdding] = useState(false)
  const [version, setVersion] = useState(0)   // bumped when a company is added, to reload the table

  return (
    <>
      <SectionHead title="Companies">Organizations Harvey has researched. Click a row to see its contacts.</SectionHead>
      {drill ? <Drill company={drill} onBack={() => setDrill(null)} /> : <>
        {adding
          ? <AddCompany onClose={() => setAdding(false)} onAdded={() => setVersion(v => v + 1)} />
          : <Button size="sm" className="mb-4" onClick={() => setAdding(true)}><Plus className="size-3.5" aria-hidden />Add a company</Button>}
        <CompanyTable onOpen={setDrill} version={version} />
      </>}
    </>
  )
}
