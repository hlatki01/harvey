import { useState } from 'react'
import { post } from '@/lib/api'
import { useApi } from '@/lib/hooks'
import { failMsg } from '@/lib/format'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Card, Subhead } from '@/components/ui/card'
import { OfflineState, Skeleton } from '@/components/ui/states'
import { ToneBadge } from '@/components/ui/badge'
import { useToast } from '@/components/toast'
import { Row, SecretInput, StatusChip, TextField, code } from './parts'

interface SettingsData {
  provider?: string
  gmail_client_id?: string; gmail_client_secret_set?: boolean; gmail_authorized?: boolean
  smtp_host?: string; smtp_port?: string | number; smtp_username?: string; smtp_password_set?: boolean
  imap_host?: string; imap_port?: string | number; imap_username?: string; imap_password_set?: boolean
  m365_client_id?: string; m365_tenant_id?: string; m365_authorized?: boolean
  instantly_api_key_set?: boolean
  reoon_api_key_set?: boolean; zerobounce_api_key_set?: boolean; hunter_api_key_set?: boolean
  linkedin_email?: string; linkedin_password_set?: boolean
  cloudflare_account_id?: string; cloudflare_api_token_set?: boolean
}

type Form = Record<string, string> & { imapSame: string }
type Test = { state: 'pending' | 'success' | 'error'; text: string } | null

const s = (v: unknown) => (v == null ? '' : String(v))

function initial(d: SettingsData): Form {
  return {
    gmailId: s(d.gmail_client_id), gmailSecret: '',
    smtpHost: s(d.smtp_host), smtpPort: s(d.smtp_port), smtpUser: s(d.smtp_username), smtpPass: '',
    imapHost: s(d.imap_host), imapPort: s(d.imap_port), imapUser: s(d.imap_username), imapPass: '',
    imapSame: d.imap_password_set ? '' : '1',
    m365Client: s(d.m365_client_id), m365Tenant: s(d.m365_tenant_id),
    instantlyKey: '', reoon: '', zerobounce: '', hunter: '',
    liEmail: s(d.linkedin_email), liPass: '',
    cfAccount: s(d.cloudflare_account_id), cfToken: '',
  }
}

export function IntegrationsPane() {
  const { data, refetch } = useApi<SettingsData>('/api/settings')
  // Remounting the form after a save re-seeds the fields and clears typed secrets.
  const [version, setVersion] = useState(0)
  if (data === undefined) return <Skeleton rows={4} />
  if (data === null) return <OfflineState />
  return (
    <IntegrationsForm
      key={version}
      data={data}
      reload={async () => { await refetch(); setVersion(v => v + 1) }}
    />
  )
}

function IntegrationsForm({ data, reload }: { data: SettingsData; reload: () => Promise<void> }) {
  const toast = useToast()
  const [f, setF] = useState<Form>(() => initial(data))
  const [test, setTest] = useState<Test>(null)
  const set = (k: keyof Form & string) => (v: string) => setF(p => ({ ...p, [k]: v }))

  async function save(payload: Record<string, string>, okMsg: string, reloadAfter = true) {
    const r = await post<{ success?: boolean; message?: string }>('/api/settings/env', payload)
    if (r && r.success) toast(okMsg, 'success')
    else toast((r && r.message) || 'Save failed — is the dashboard still running?', 'error')
    if (reloadAfter) await reload()
  }
  const need = (msg: string) => toast(msg, 'error')

  function saveGmail() {
    const p: Record<string, string> = { GMAIL_CLIENT_ID: f.gmailId.trim() }
    if (f.gmailSecret) p.GMAIL_CLIENT_SECRET = f.gmailSecret
    save(p, 'Gmail OAuth saved. Now run "harvey gmail auth" in your terminal.')
  }

  function saveSmtp() {
    const p: Record<string, string> = {
      SMTP_HOST: f.smtpHost.trim(), SMTP_PORT: f.smtpPort.trim(), SMTP_USERNAME: f.smtpUser.trim(),
      IMAP_HOST: f.imapHost.trim(), IMAP_PORT: f.imapPort.trim(), IMAP_USERNAME: f.imapUser.trim(),
    }
    if (f.smtpPass) p.SMTP_PASSWORD = f.smtpPass
    // A typed IMAP password wins; otherwise the checkbox clears it back to the
    // SMTP one, and an unticked empty box keeps whatever IMAP password is saved.
    if (f.imapPass) p.IMAP_PASSWORD = f.imapPass
    else if (f.imapSame) p.IMAP_PASSWORD = ''
    save(p, 'SMTP settings saved.')
  }

  function saveMicrosoft() {
    const client = f.m365Client.trim()
    if (!client) return need('Paste the Application (client) ID first.')
    save({ M365_CLIENT_ID: client, M365_TENANT_ID: f.m365Tenant.trim() }, 'Saved. Now run "harvey microsoft auth" in your terminal.')
  }

  function saveVerifiers() {
    const p: Record<string, string> = {}
    if (f.reoon) p.REOON_API_KEY = f.reoon
    if (f.zerobounce) p.ZEROBOUNCE_API_KEY = f.zerobounce
    if (f.hunter) p.HUNTER_API_KEY = f.hunter
    if (!Object.keys(p).length) return need('Enter at least one key first.')
    save(p, 'Verification keys saved.')
  }

  // Secrets are only sent when typed: an empty box means "keep the saved one".
  function saveInstantly() {
    const key = f.instantlyKey.trim()
    if (!key) return need('Enter a new key first. The saved one is kept.')
    save({ INSTANTLY_API_KEY: key }, 'Instantly API key saved.')
  }

  function saveLinkedIn() {
    const p: Record<string, string> = { LINKEDIN_EMAIL: f.liEmail.trim() }
    if (f.liPass) p.LINKEDIN_PASSWORD = f.liPass
    save(p, 'LinkedIn credentials saved.')
  }

  function saveCloudflare() {
    const p: Record<string, string> = { CLOUDFLARE_ACCOUNT_ID: f.cfAccount.trim() }
    const t = f.cfToken.trim()
    if (t) p.CLOUDFLARE_API_TOKEN = t
    save(p, 'Cloudflare credentials saved.')
  }

  async function testInstantly() {
    setTest({ state: 'pending', text: 'Testing…' })
    const r = await post<{ success?: boolean }>('/api/settings/test-instantly', { api_key: f.instantlyKey.trim() })
    if (!r) setTest({ state: 'error', text: 'Could not reach the dashboard server.' })
    else setTest({ state: r.success ? 'success' : 'error', text: failMsg(r, '') })
  }

  const gmail = data.gmail_authorized
    ? <StatusChip tone="good">authorized</StatusChip>
    : data.gmail_client_secret_set
      ? <StatusChip tone="waiting">run: harvey gmail auth</StatusChip>
      : <StatusChip tone="idle">not set up</StatusChip>
  const m365 = data.m365_authorized
    ? <StatusChip tone="good">authorized</StatusChip>
    : data.m365_client_id
      ? <StatusChip tone="waiting">run: harvey microsoft auth</StatusChip>
      : <StatusChip tone="idle">not set up</StatusChip>
  const setChip = (ok?: boolean) => <StatusChip tone={ok ? 'good' : 'idle'}>{ok ? 'set' : 'not set'}</StatusChip>

  return (
    <div>
      <Subhead hint={<>Active: <b>{data.provider || 'instantly'}</b> &middot; set <code className={code}>channels.email.provider</code> in <code className={code}>harvey.yaml</code> to gmail, microsoft, smtp or instantly</>}>
        Email provider
      </Subhead>
      <Card>
        <Row
          title={<>Gmail / Google Workspace <ToneBadge tone="active" className="ml-1">Recommended</ToneBadge>{gmail}</>}
          desc={<>Use a mailbox on a dedicated secondary domain. Create an OAuth client (type "Desktop app") in Google Cloud Console → Gmail API, paste the id and secret, save, then run <code className={code}>harvey gmail auth</code>.</>}
        >
          <TextField label="Client ID" value={f.gmailId} onChange={set('gmailId')} placeholder="xxxx.apps.googleusercontent.com" />
          <SecretInput label="Client secret" value={f.gmailSecret} onChange={set('gmailSecret')} isSet={!!data.gmail_client_secret_set} placeholder="Enter client secret" />
          <Button variant="primary" size="sm" onClick={saveGmail}>Save Gmail</Button>
        </Row>

        <Row
          title="SMTP + IMAP mailbox"
          desc="Any real mailbox (AgentMail, Fastmail, a Workspace app password). IMAP defaults to the SMTP host and login if left blank."
        >
          <div className="flex gap-3">
            <TextField className="flex-1" label="SMTP host" value={f.smtpHost} onChange={set('smtpHost')} placeholder="smtp.example.com" />
            <TextField className="w-28" label="Port" value={f.smtpPort} onChange={set('smtpPort')} placeholder="587" />
          </div>
          <TextField label="Username" value={f.smtpUser} onChange={set('smtpUser')} placeholder="you@example.com" />
          <SecretInput label="Password" value={f.smtpPass} onChange={set('smtpPass')} isSet={!!data.smtp_password_set} placeholder="Enter password / app password" />
          <details className="mb-3">
            <summary className="mb-3 cursor-pointer text-[13px] text-text-2">Reading replies from a different IMAP server or login</summary>
            <div className="flex gap-3">
              <TextField className="flex-1" label="IMAP host" value={f.imapHost} onChange={set('imapHost')} placeholder="Same as SMTP host" />
              <TextField className="w-28" label="Port" value={f.imapPort} onChange={set('imapPort')} placeholder="993" />
            </div>
            <TextField label="IMAP username" value={f.imapUser} onChange={set('imapUser')} placeholder="Same as SMTP username" />
            <SecretInput
              label="IMAP password"
              value={f.imapPass}
              // Typing a password unticks "same as SMTP".
              onChange={v => setF(p => ({ ...p, imapPass: v, imapSame: v ? '' : p.imapSame }))}
              isSet={!!data.imap_password_set}
              placeholder="Same as SMTP password"
            />
            <label className="flex items-center gap-2 text-[13px]">
              <input type="checkbox" checked={!!f.imapSame} onChange={e => set('imapSame')(e.target.checked ? '1' : '')} />
              Use the SMTP password for IMAP
            </label>
          </details>
          <Button variant="primary" size="sm" onClick={saveSmtp}>Save SMTP</Button>
        </Row>

        <Row
          title={<>Microsoft 365 / Outlook{m365}</>}
          desc={<>Microsoft turned off password sign-in for IMAP, so Outlook mailboxes connect with OAuth. In <a className="text-accent underline" href="https://entra.microsoft.com" target="_blank" rel="noopener noreferrer">Entra</a> → App registrations, register an app for your organization only and add the redirect URI <code className={code}>http://localhost:8766</code> as "Public client/native". Paste both IDs here, save, then run <code className={code}>harvey microsoft auth</code>. No client secret is needed.</>}
        >
          <TextField label="Application (client) ID" value={f.m365Client} onChange={set('m365Client')} placeholder="00000000-0000-0000-0000-000000000000" />
          <TextField label="Directory (tenant) ID" value={f.m365Tenant} onChange={set('m365Tenant')} placeholder="00000000-0000-0000-0000-000000000000" />
          <Button variant="primary" size="sm" onClick={saveMicrosoft}>Save Microsoft 365</Button>
        </Row>

        <Row
          title={<>Instantly <ToneBadge tone="idle" className="ml-1">Legacy</ToneBadge></>}
          desc={<>Get your API key from <a className="text-accent underline" href="https://app.instantly.ai/app/settings/integrations" target="_blank" rel="noopener noreferrer">Instantly Settings → Integrations</a> (Growth plan or higher for API).</>}
        >
          <SecretInput
            label="API key"
            value={f.instantlyKey}
            onChange={set('instantlyKey')}
            isSet={!!data.instantly_api_key_set}
            placeholder="Enter your Instantly API key"
            extra={<Button onClick={testInstantly}>Test</Button>}
          />
          {test && (
            <div
              role="status"
              className={cn('mb-3 rounded-md border px-3 py-2 text-[13px]',
                test.state === 'success' ? 'border-good-line bg-good-bg text-good'
                  : test.state === 'error' ? 'border-bad-line bg-bad-bg text-bad'
                    : 'border-line bg-surface-2 text-text-2')}
            >{test.text}</div>
          )}
          <Button variant="primary" size="sm" onClick={saveInstantly}>Save Instantly</Button>
        </Row>
      </Card>

      <Subhead hint="Strongly recommended">Email verification</Subhead>
      <Card>
        <Row
          title="Verifiers"
          desc={<>Add at least one so found emails get verified. Without any, addresses stay <ToneBadge tone="idle">guess</ToneBadge> and are never sent. Reoon has the best free tier (600 a month).</>}
        >
          <SecretInput label="Reoon API key" status={setChip(data.reoon_api_key_set)} value={f.reoon} onChange={set('reoon')} isSet={!!data.reoon_api_key_set} placeholder="600 free/mo — reoon.com/email-verifier" />
          <SecretInput label="ZeroBounce API key" status={setChip(data.zerobounce_api_key_set)} value={f.zerobounce} onChange={set('zerobounce')} isSet={!!data.zerobounce_api_key_set} placeholder="100 free/mo — best for M365/Workspace catch-alls" />
          <SecretInput label="Hunter API key" status={setChip(data.hunter_api_key_set)} value={f.hunter} onChange={set('hunter')} isSet={!!data.hunter_api_key_set} placeholder="50 free/mo + email-pattern lookup" />
          <Button variant="primary" size="sm" onClick={saveVerifiers}>Save verification keys</Button>
        </Row>
      </Card>

      <Subhead>Optional</Subhead>
      <Card>
        <Row title="LinkedIn" desc="For automated LinkedIn prospecting. Harvey logs in and searches like a human.">
          <TextField label="Email or username" value={f.liEmail} onChange={set('liEmail')} placeholder="your@email.com" />
          <SecretInput label="Password" value={f.liPass} onChange={set('liPass')} isSet={!!data.linkedin_password_set} placeholder="Enter password" autoComplete="new-password" />
          <Button variant="primary" size="sm" onClick={saveLinkedIn}>Save LinkedIn</Button>
        </Row>
        <Row title="Cloudflare" desc="Deep website crawling with JavaScript rendering during product training. About $5 a month.">
          <TextField label="Account ID" value={f.cfAccount} onChange={set('cfAccount')} placeholder="Your Cloudflare Account ID" />
          <SecretInput label="API token" value={f.cfToken} onChange={set('cfToken')} isSet={!!data.cloudflare_api_token_set} placeholder="Your Cloudflare API Token" />
          <Button variant="primary" size="sm" onClick={saveCloudflare}>Save Cloudflare</Button>
        </Row>
      </Card>
    </div>
  )
}
