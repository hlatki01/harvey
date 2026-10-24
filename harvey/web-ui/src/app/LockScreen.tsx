import { useEffect, useState } from 'react'
import { authFetch, authToken, clearInvite, onPublicHost, pendingInvite, setAuthToken } from '@/lib/auth'
import { JSON_HEADERS, lock } from '@/lib/api'
import { useLockState } from '@/lib/hooks'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Field, Input } from '@/components/ui/field'
import { BorderBeam } from '@/components/ui/border-beam'

type Mode = 'token' | 'login' | 'invite'

/** Shown whenever the API refuses this tab (no token, a wrong one, or none on
 *  the server), and for sign-in / invite acceptance on the public address. */
export function LockScreen({ forceInvite }: { forceInvite: boolean }) {
  const { reason: raw } = useLockState()
  const publicHost = onPublicHost()
  const invite = publicHost && (forceInvite || !!pendingInvite())
  const mode: Mode = invite ? 'invite' : publicHost ? 'login' : 'token'
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [token, setToken] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [name, setName] = useState('')

  // An invite link: look it up, then let the person choose a password.
  useEffect(() => {
    if (mode !== 'invite') return
    ;(async () => {
      const r = await authFetch('/api/auth/invite-info', {
        method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ token: pendingInvite() }),
      })
      const body = await r.json().catch(() => ({}))
      if (!r.ok) {
        clearInvite()
        setMessage(body.message || 'This invite is invalid or has expired.')
        return
      }
      setEmail(body.email || '')
      setName(body.name || '')
    })()
  }, [mode])

  const localReason = raw === 'refused'
    ? (authToken()
        ? 'The saved token was not accepted (the dashboard may have a new one).'
        : "This tab doesn't have the dashboard token yet.")
    : raw

  async function post(path: string, body: unknown) {
    setBusy(true)
    try {
      const r = await authFetch(path, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(body) })
      return { ok: r.ok, body: await r.json().catch(() => ({})) }
    } finally { setBusy(false) }
  }

  async function signIn(e: React.FormEvent) {
    e.preventDefault()
    const r = await post('/api/auth/login', { email, password })
    if (r.ok) { window.location.reload(); return }
    setMessage(r.body.message || 'Could not sign in.')
    setPassword('')
  }

  async function acceptInvite(e: React.FormEvent) {
    e.preventDefault()
    const r = await post('/api/auth/accept', { token: pendingInvite(), password, name })
    if (r.ok) { clearInvite(); window.location.reload(); return }
    setMessage(r.body.message || 'Could not create the account.')
  }

  function unlock(e: React.FormEvent) {
    e.preventDefault()
    const value = token.trim()
    setToken('')
    if (!setAuthToken(value)) {
      setMessage("That doesn't look like a dashboard token. Copy it again from the link or data/dashboard-token.")
      return
    }
    lock.clear()
    window.location.reload()
  }

  const title = mode === 'invite' ? 'Create your account' : mode === 'login' ? 'Sign in' : 'Dashboard locked'
  const shown = message || (publicHost ? '' : localReason)

  return (
    <div className="fixed inset-0 z-[1000] flex items-center justify-center bg-bg p-6">
      <Card className="relative w-full max-w-[500px] overflow-hidden">
        <BorderBeam size={120} duration={8} colorFrom="var(--accent)" colorTo="var(--accent-line)" />
        <h2 className="text-lg font-semibold">{title}</h2>
        {shown && <p role="alert" className="mb-3 mt-1 text-text-2">{shown}</p>}

        {mode === 'token' && (
          <>
            <p className="mb-3 text-text-2">
              Open the link that <code className="font-mono">harvey dashboard</code> printed in your terminal, or run{' '}
              <code className="font-mono">harvey dashboard --url</code> to print it again. You can also paste the token
              from <code className="font-mono">data/dashboard-token</code>:
            </p>
            <form onSubmit={unlock} className="flex items-end gap-2">
              <Field label="Dashboard token" htmlFor="lock-token" className="mb-0 flex-1">
                <Input id="lock-token" type="password" autoComplete="off" spellCheck={false}
                  value={token} onChange={e => setToken(e.target.value)} />
              </Field>
              <Button variant="primary" type="submit">Unlock</Button>
            </form>
          </>
        )}

        {mode === 'login' && (
          <form onSubmit={signIn} className="mt-2">
            <Field label="Email" htmlFor="login-email">
              <Input id="login-email" type="email" autoComplete="username" required value={email} onChange={e => setEmail(e.target.value)} />
            </Field>
            <Field label="Password" htmlFor="login-password">
              <Input id="login-password" type="password" autoComplete="current-password" required value={password} onChange={e => setPassword(e.target.value)} />
            </Field>
            <Button variant="primary" type="submit" busy={busy}>Sign in</Button>
          </form>
        )}

        {mode === 'invite' && (
          <form onSubmit={acceptInvite} className="mt-2">
            <Field label="Email" htmlFor="invite-email"><Input id="invite-email" type="email" disabled value={email} readOnly /></Field>
            <Field label="Your name" htmlFor="invite-name">
              <Input id="invite-name" autoComplete="name" maxLength={80} value={name} onChange={e => setName(e.target.value)} />
            </Field>
            <Field label="Choose a password" hint="(at least 10 characters)" htmlFor="invite-password">
              <Input id="invite-password" type="password" autoComplete="new-password" minLength={10} required value={password} onChange={e => setPassword(e.target.value)} />
            </Field>
            <Button variant="primary" type="submit" busy={busy}>Create account</Button>
          </form>
        )}
      </Card>
    </div>
  )
}
