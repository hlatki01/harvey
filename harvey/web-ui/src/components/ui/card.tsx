import * as React from 'react'
import { cn } from '@/lib/utils'

export function Card({ className, flush, ...props }: React.HTMLAttributes<HTMLDivElement> & { flush?: boolean }) {
  return (
    <div
      className={cn('rounded-lg bg-surface shadow-card', flush ? 'overflow-hidden' : 'p-5', className)}
      {...props}
    />
  )
}

/** Small uppercase section label between groups of cards. */
export function Subhead({ className, children, hint }: { className?: string; children: React.ReactNode; hint?: React.ReactNode }) {
  return (
    <div className={cn('mb-2 mt-7 flex items-baseline gap-2 text-[12px] font-medium uppercase tracking-wide text-text-3 first:mt-0', className)}>
      {children}
      {hint && <span className="ml-auto text-[12px] font-normal normal-case tracking-normal">{hint}</span>}
    </div>
  )
}

export function SectionHead({ title, children, actions }: { title: string; children?: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <div className="mb-6 flex items-start justify-between gap-4">
      <div>
        <h2 className="text-[22px] font-semibold leading-tight tracking-tight">{title}</h2>
        {children && <p className="mt-1 max-w-[62ch] text-text-2">{children}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  )
}
