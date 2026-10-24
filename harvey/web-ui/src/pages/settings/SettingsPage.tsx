import { useState } from 'react'
import { SectionHead } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import { IntegrationsPane } from './IntegrationsPane'
import { UsersPane } from './UsersPane'

const KEY = 'harvey-settings-pane'
type Pane = 'integrations' | 'users'

function initialPane(): Pane {
  try { return localStorage.getItem(KEY) === 'users' ? 'users' : 'integrations' } catch { return 'integrations' }
}

export function SettingsPage() {
  const [pane, setPane] = useState<Pane>(initialPane)
  function show(p: Pane) {
    setPane(p)
    try { localStorage.setItem(KEY, p) } catch { /* private mode */ }
  }
  return (
    <>
      <SectionHead title="Settings">
        Credentials are stored locally in <code className="font-mono text-[12px]">.env</code> and never sent anywhere except the services themselves.
      </SectionHead>
      <div role="group" aria-label="Settings" className="mb-5 inline-flex rounded-md bg-surface-2 p-0.5">
        {(['integrations', 'users'] as const).map(p => (
          <button
            key={p}
            type="button"
            aria-pressed={pane === p}
            onClick={() => show(p)}
            className={cn('rounded-sm px-3 py-1 text-[13px] font-medium capitalize', pane === p ? 'bg-surface text-text shadow-card' : 'text-text-2 hover:text-text')}
          >{p}</button>
        ))}
      </div>
      {pane === 'users' ? <UsersPane /> : <IntegrationsPane />}
    </>
  )
}
