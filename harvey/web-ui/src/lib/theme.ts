import { useSyncExternalStore } from 'react'

// Three states, not two: "auto" follows the OS and is the default, so the
// dashboard matches the rest of the machine until a theme is deliberately
// picked. Stored per-browser; nothing is sent anywhere.
export type ThemeMode = 'auto' | 'light' | 'dark'
export const THEMES: ThemeMode[] = ['auto', 'light', 'dark']
const KEY = 'harvey-theme'

function read(): ThemeMode {
  try {
    const v = localStorage.getItem(KEY) as ThemeMode | null
    return v && THEMES.includes(v) ? v : 'auto'
  } catch { return 'auto' }
}

let mode: ThemeMode = read()
const listeners = new Set<() => void>()

function apply(m: ThemeMode) {
  const root = document.documentElement
  if (m === 'auto') root.removeAttribute('data-theme')
  else root.setAttribute('data-theme', m)
}

export function initTheme() { apply(mode) }

export function cycleTheme() {
  mode = THEMES[(THEMES.indexOf(mode) + 1) % THEMES.length]
  try { localStorage.setItem(KEY, mode) } catch { /* private mode */ }
  apply(mode)
  listeners.forEach(fn => fn())
}

const subscribe = (fn: () => void) => {
  listeners.add(fn)
  const mq = window.matchMedia('(prefers-color-scheme: dark)')
  mq.addEventListener('change', fn)
  return () => { listeners.delete(fn); mq.removeEventListener('change', fn) }
}

export function useThemeMode(): ThemeMode {
  return useSyncExternalStore(subscribe, () => mode)
}

/** The theme actually showing: "auto" resolved against the OS. */
export function useResolvedTheme(): 'light' | 'dark' {
  const m = useThemeMode()
  const dark = useSyncExternalStore(subscribe, () => window.matchMedia('(prefers-color-scheme: dark)').matches)
  return m === 'auto' ? (dark ? 'dark' : 'light') : m
}
