import { useState } from 'react'
import { downloadFile, post } from '@/lib/api'
import { own, statusMeta, failMsg } from '@/lib/format'
import { ToneBadge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useToast } from '@/components/toast'

export interface Prospect {
  id: string; first_name?: string; last_name?: string; title?: string; company?: string
  email?: string; email_status?: string; email_verified?: boolean
  phone?: string; phone_verified?: boolean; linkedin_url?: string
  status?: string; source?: string; score?: number | null; created_at?: string
}

export const TH = 'px-3 py-2 text-left text-[12px] font-medium text-text-3 whitespace-nowrap'
export const TD = 'px-3 py-2.5 align-middle'

export function TableCard({ children }: { children: React.ReactNode }) {
  return <div className="overflow-x-auto rounded-lg bg-surface shadow-card">{children}</div>
}

export function Avatar({ first, last }: { first?: string; last?: string }) {
  const ch = (x?: string) => Array.from((x || '').trim())[0] || ''
  return (
    <span aria-hidden className="grid size-7 shrink-0 place-items-center rounded-full bg-accent-soft text-[11px] font-semibold text-accent">
      {(ch(first) + ch(last) || '?').toUpperCase()}
    </span>
  )
}

/** Ten-segment 0-100 meter, as the legacy dashboard drew it. */
export function Meter({ pct }: { pct: number }) {
  const v = Math.max(0, Math.min(100, Number(pct) || 0))
  const on = Math.round(v / 10)
  return (
    <span className="inline-flex items-center gap-2">
      <span role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={v} className="flex gap-px">
        {Array.from({ length: 10 }, (_, i) => (
          <i key={i} className={'h-3 w-1 rounded-[1px] ' + (i < on ? 'bg-accent' : 'bg-surface-3')} />
        ))}
      </span>
      <span className="text-[12px] tabular-nums text-text-2">{v}</span>
    </span>
  )
}

export function EmailTag({ p }: { p: Prospect }) {
  if (!p.email) return null
  // Fall back to the legacy boolean for rows predating email_status.
  const status = p.email_status || (p.email_verified ? 'verified' : 'guess')
  // shortcut: only the four email_status values the backend writes, not the whole status table.
  if (!own({ verified: 1, risky: 1, guess: 1, invalid: 1 }, status)) return null
  const m = statusMeta(status)
  return <ToneBadge tone={m.tone} className="ml-1"><span title={'Email ' + m.label.toLowerCase()}>{m.label}</span></ToneBadge>
}

/** CSV download button: authorised blob fetch, toast on failure. */
export function CsvButton({ path, filename, children, variant = 'secondary' }: {
  path: string; filename: string; children: React.ReactNode; variant?: 'secondary' | 'ghost'
}) {
  const toast = useToast()
  const [busy, setBusy] = useState(false)
  const go = async () => {
    setBusy(true)
    try {
      if (!(await downloadFile(path, filename))) toast('Export failed.', 'error')
    } catch {
      toast('Export failed — is the dashboard still running?', 'error')
    } finally {
      setBusy(false)
    }
  }
  return <Button size="sm" variant={variant} disabled={busy} onClick={go}>{children}</Button>
}

/** Free-text feedback Harvey folds into what it writes next. */
export async function submitFeedback(
  toast: (m: string, t?: 'success' | 'error') => void,
  entityType: 'contact' | 'campaign', entityId: string, promptText: string,
) {
  const comment = window.prompt(promptText)
  if (!comment) return
  const data = await post<{ success?: boolean; message?: string }>('/api/feedback', {
    entity_type: entityType, entity_id: entityId, comment,
  })
  if (data && data.success) {
    toast(entityType === 'campaign'
      ? 'Saved. Harvey follows it in the emails it writes from now on.'
      : "Saved. Harvey uses it if it writes this person's first email (Gmail, SMTP or Microsoft 365).", 'success')
  } else {
    toast((data && data.message) || failMsg(data, 'Could not save feedback.'), 'error')
  }
}
