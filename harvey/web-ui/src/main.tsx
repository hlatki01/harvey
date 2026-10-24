import '@/lib/boot' // first: moves #token out of the address bar before anything else runs
import { onPublicHost, pendingInvite } from '@/lib/auth'
import React from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import './index.css'
import { initTheme } from '@/lib/theme'
import { useLockState } from '@/lib/hooks'
import { ToastProvider } from '@/components/toast'
import { NavProvider } from '@/app/nav'
import { Shell } from '@/app/Shell'
import { LockScreen } from '@/app/LockScreen'

initTheme()

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, refetchOnWindowFocus: true } },
})

// An invite link on the public address shows the account form straight away.
const inviteFirst = onPublicHost() && !!pendingInvite()

function App() {
  const { locked } = useLockState()
  return (
    <>
      {!inviteFirst && <Shell />}
      {(locked || inviteFirst) && <LockScreen forceInvite={inviteFirst} />}
    </>
  )
}

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <NavProvider>
          <App />
        </NavProvider>
      </ToastProvider>
    </QueryClientProvider>
  </React.StrictMode>,
)
