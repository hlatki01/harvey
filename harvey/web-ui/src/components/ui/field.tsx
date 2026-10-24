import * as React from 'react'
import { cn } from '@/lib/utils'

const control =
  'w-full rounded-sm bg-surface px-2.5 text-[13px] text-text shadow-card placeholder:text-text-3 focus-visible:outline-accent disabled:opacity-60'

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => <input ref={ref} className={cn(control, 'h-8', className)} {...props} />,
)
Input.displayName = 'Input'

export const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, ...props }, ref) => <textarea ref={ref} className={cn(control, 'min-h-20 py-2', className)} {...props} />,
)
Textarea.displayName = 'Textarea'

export const Select = React.forwardRef<HTMLSelectElement, React.SelectHTMLAttributes<HTMLSelectElement>>(
  ({ className, ...props }, ref) => <select ref={ref} className={cn(control, 'h-8', className)} {...props} />,
)
Select.displayName = 'Select'

/** Label + control (+ optional hint) stacked. Pass the control as children. */
export function Field({ label, htmlFor, hint, children, className }: {
  label: React.ReactNode; htmlFor?: string; hint?: React.ReactNode; children: React.ReactNode; className?: string
}) {
  return (
    <div className={cn('mb-3 flex flex-col gap-1', className)}>
      <label htmlFor={htmlFor} className="text-[12px] font-medium text-text-2">
        {label}
        {hint && <span className="ml-1.5 font-normal text-text-3">{hint}</span>}
      </label>
      {children}
    </div>
  )
}
