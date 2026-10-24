import * as React from 'react'
import { Slot } from '@radix-ui/react-slot'
import { cva, type VariantProps } from 'class-variance-authority'
import { Loader2 } from 'lucide-react'
import { cn } from '@/lib/utils'

const buttonVariants = cva(
  'inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-sm font-medium transition-[background,box-shadow,transform] active:scale-[.98] disabled:pointer-events-none disabled:opacity-50',
  {
    variants: {
      variant: {
        primary: 'bg-accent text-accent-text hover:bg-accent-hover shadow-[inset_0_1px_0_rgb(255_255_255/.18),0_0_0_1px_rgb(0_0_0/.15)]',
        secondary: 'bg-surface text-text shadow-card hover:bg-surface-2',
        ghost: 'text-text-2 hover:bg-surface-2 hover:text-text',
        danger: 'bg-bad-bg text-bad shadow-[0_0_0_1px_var(--bad-line)] hover:brightness-95',
      },
      size: { sm: 'h-7 px-2.5 text-[12px]', md: 'h-8 px-3 text-[13px]' },
    },
    defaultVariants: { variant: 'secondary', size: 'md' },
  },
)

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean
  /** Shows a spinner and disables the button while an action is in flight. */
  busy?: boolean
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild, busy, disabled, children, type, ...props }, ref) => {
    const Comp = asChild ? Slot : 'button'
    return (
      <Comp
        ref={ref}
        type={asChild ? undefined : (type ?? 'button')}
        className={cn(buttonVariants({ variant, size }), className)}
        disabled={disabled || busy}
        aria-busy={busy || undefined}
        {...props}
      >
        {asChild ? children : (<>{busy && <Loader2 className="size-3.5 animate-spin" aria-hidden />}{children}</>)}
      </Comp>
    )
  },
)
Button.displayName = 'Button'
