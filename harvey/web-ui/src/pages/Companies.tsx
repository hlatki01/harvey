import { useState } from 'react'
import { ArrowLeft, Pencil, Plus, Trash2 } from 'lucide-react'
import { useApi } from '@/lib/hooks'
import { api } from '@/lib/api'
import { useToast } from '@/components/toast'
import { safeHref, sourceLabel } from '@/lib/format'
import { Button } from '@/components/ui/button'
import { Card, SectionHead } from '@/components/ui/card'
import { StatusBadge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/states'
import { Avatar, EmailTag, TH, TD, type Prospect } from './pipeline/shared'
import { AddCompany } from './companies/AddCompany'
import { CompanyTable, type Company } from './companies/CompanyTable'
import { ContactDialog } from './companies/ContactDialog'


function Drill({ company, onBack }: { company: Company; onBack: () => void }) {
  const toast = useToast()
  const { data, refetch } = useApi<Prospect[]>('/api/companies/' + encodeURIComponent(String(company.id)) + '/contacts')
  const [editing, setEditing] = useState<Prospect | null | undefined>(undefined)   // null: adding one
  const [confirming, setConfirming] = useState('')

  async function remove(p: Prospect) {
    const r = await api<{ success?: boolean; message?: string }>('/api/prospects/' + encodeURIComponent(String(p.id)), { method: 'DELETE' })
    setConfirming('')
    if (r && r.success) { toast('Contact deleted.', 'success'); refetch() }
    else toast((r && r.message) || 'Could not delete it.', 'error')
  }

  return (
    <>
      <Button variant="ghost" size="sm" className="mb-3" onClick={onBack}><ArrowLeft className="size-3.5" />All companies</Button>
      <Card flush>
        <div className="flex items-center justify-between gap-3 px-4 py-3">
          <div className="min-w-0">
            <h3 className="truncate font-semibold">{company.name}</h3>
            <div className="text-[13px] text-text-2">{[company.industry, company.location].filter(Boolean).join(' · ')}</div>
          </div>
          <Button size="sm" onClick={() => setEditing(null)}><Plus className="size-3.5" aria-hidden />Add a contact</Button>
        </div>
        {data === undefined ? <div className="p-4"><Skeleton rows={2} /></div>
          : !data || !data.length ? <p className="px-4 pb-6 pt-2 text-center text-text-2">No contacts at this company yet.</p>
          : (
            <div className="overflow-x-auto">
              <table className="w-full text-[13px]">
                <thead><tr>{['Name', 'Email', 'Phone', 'LinkedIn', 'Status', 'Source', ''].map(h => <th key={h || 'actions'} className={TH}>{h}</th>)}</tr></thead>
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
                      <td className={TD + ' text-text-2'}>{p.source === 'manual' ? 'Added by hand' : sourceLabel(p.source)}</td>
                      <td className={TD + ' whitespace-nowrap text-right'}>
                        {confirming === p.id ? (
                          <span className="inline-flex items-center gap-1">
                            <span className="text-[12px] text-text-2">Delete?</span>
                            <Button size="sm" variant="ghost" className="text-bad" onClick={() => remove(p)}>Delete</Button>
                            <Button size="sm" variant="ghost" onClick={() => setConfirming('')}>Keep</Button>
                          </span>
                        ) : (
                          <span className="inline-flex gap-0.5">
                            <Button size="sm" variant="ghost" aria-label={`Edit ${contactName(p)}`} title="Edit" onClick={() => setEditing(p)}><Pencil className="size-3.5" /></Button>
                            <Button size="sm" variant="ghost" aria-label={`Delete ${contactName(p)}`} title="Delete" onClick={() => setConfirming(p.id)}><Trash2 className="size-3.5" /></Button>
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </Card>
      {editing !== undefined && (
        <ContactDialog companyId={String(company.id)} contact={editing}
          onClose={() => setEditing(undefined)} onSaved={() => { setEditing(undefined); refetch() }} />
      )}
    </>
  )
}

const contactName = (p: Prospect) => [p.first_name, p.last_name].filter(Boolean).join(' ') || p.email || p.phone || 'contact'

export function CompaniesPage() {
  const [drill, setDrill] = useState<Company | null>(null)
  const [adding, setAdding] = useState(false)
  const [version, setVersion] = useState(0)   // bumped when a company is added, to reload the table

  return (
    <>
      <SectionHead title="Companies">Organizations Roadie Crew has researched. Click a row to see its contacts.</SectionHead>
      {drill ? <Drill company={drill} onBack={() => setDrill(null)} /> : <>
        {adding
          ? <AddCompany onClose={() => setAdding(false)} onAdded={() => setVersion(v => v + 1)} />
          : <Button size="sm" className="mb-4" onClick={() => setAdding(true)}><Plus className="size-3.5" aria-hidden />Add a company</Button>}
        <CompanyTable onOpen={setDrill} version={version} />
      </>}
    </>
  )
}
