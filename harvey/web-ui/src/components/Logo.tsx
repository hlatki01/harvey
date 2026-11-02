import { useId } from 'react'

const ROAD = 'M9 34C9 22 23 22 23 13S14 6 14 -2'

/** Roadie Crew mark: a winding road with its centre line. Solid colours from
 *  the theme tokens, so it follows light and dark. index.html carries the same
 *  drawing as the favicon; keep the two in step. */
export function Logo({ className }: { className?: string }) {
  const clip = useId() // the shell and the lock screen can both show it at once
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden="true">
      <defs><clipPath id={clip}><rect width="32" height="32" rx="8" /></clipPath></defs>
      <g clipPath={`url(#${clip})`}>
        <rect width="32" height="32" fill="var(--accent)" />
        <path d={ROAD} fill="none" stroke="var(--accent-text)" strokeWidth="7" />
        <path d={ROAD} fill="none" stroke="var(--accent)" strokeWidth="1.3" strokeLinecap="round" strokeDasharray="2.4 2.4" />
      </g>
    </svg>
  )
}
