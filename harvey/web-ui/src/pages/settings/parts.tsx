import { useId, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Field, Input } from '@/components/ui/field'
import { ToneBadge } from '@/components/ui/badge'
import type { Tone } from '@/lib/format'

/** Secrets never come back from the server: the placeholder says whether one is saved. */
export function SecretInput({ label, value, onChange, isSet, placeholder, status, extra, autoComplete = 'off' }: {
  label: React.ReactNode
  value: string
  onChange: (v: string) => void
  isSet: boolean
  placeholder: string
  status?: React.ReactNode
  extra?: React.ReactNode
  autoComplete?: string
}) {
  const id = useId()
  const [shown, setShown] = useState(false)
  return (
    <Field label={<>{label}{status}</>} htmlFor={id}>
      <div className="flex gap-2">
        <Input
          id={id}
          type={shown ? 'text' : 'password'}
          value={value}
          onChange={e => onChange(e.target.value)}
          placeholder={isSet ? 'Saved — enter new value to change' : placeholder}
          autoComplete={autoComplete}
        />
        <Button onClick={() => setShown(s => !s)} aria-pressed={shown}>{shown ? 'Hide' : 'Show'}</Button>
        {extra}
      </div>
    </Field>
  )
}

export function TextField({ label, value, onChange, placeholder, className }: {
  label: string; value: string; onChange: (v: string) => void; placeholder?: string; className?: string
}) {
  const id = useId()
  return (
    <Field label={label} htmlFor={id} className={className}>
      <Input id={id} value={value} onChange={e => onChange(e.target.value)} placeholder={placeholder} autoComplete="off" />
    </Field>
  )
}

export function StatusChip({ tone, children }: { tone: Tone; children: React.ReactNode }) {
  return <ToneBadge tone={tone} className="ml-2 align-middle">{children}</ToneBadge>
}

export function Row({ title, desc, children }: { title: React.ReactNode; desc: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="grid gap-4 border-b border-line py-5 first:pt-0 last:border-0 last:pb-0 md:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
      <div>
        <h3 className="text-[14px] font-semibold">{title}</h3>
        <p className="mt-1 text-[13px] text-text-2">{desc}</p>
      </div>
      <div>{children}</div>
    </div>
  )
}

export const code = 'font-mono text-[12px]'
