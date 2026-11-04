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
  evolution_api_url?: string; evolution_instance?: string; evolution_api_key_set?: boolean
  instagram_username?: string; instagram_password_set?: boolean; instagram_sessionid_set?: boolean
  serper_api_key_set?: boolean
}

type Form = Record<string, string> & { imapSame: string }
type Test = { state: 'pending' | 'success' | 'error'; text: string } | null
type Tested = 'instantly' | 'whatsapp' | 'instagram'

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
    evoUrl: s(d.evolution_api_url), evoInstance: s(d.evolution_instance), evoKey: '',
    igUser: s(d.instagram_username), igPass: '', igSession: '', serper: '',
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
  const [tests, setTests] = useState<Partial<Record<Tested, Test>>>({})
  const setTest = (k: Tested, t: Test) => setTests(p => ({ ...p, [k]: t }))
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

  function saveWhatsApp() {
    const p: Record<string, string> = { EVOLUTION_API_URL: f.evoUrl.trim(), EVOLUTION_INSTANCE: f.evoInstance.trim() }
    if (f.evoKey.trim()) p.EVOLUTION_API_KEY = f.evoKey.trim()
    save(p, 'WhatsApp settings saved. Press Test to check the connection.')
  }

  function saveInstagram() {
    const p: Record<string, string> = { INSTAGRAM_USERNAME: f.igUser.trim() }
    if (f.igPass) p.INSTAGRAM_PASSWORD = f.igPass
    if (f.igSession.trim()) p.INSTAGRAM_SESSIONID = f.igSession.trim()
    save(p, 'Instagram settings saved. Press Test to check the connection.')
  }

  function saveSerper() {
    const key = f.serper.trim()
    if (!key) return need('Enter a new key first. The saved one is kept.')
    save({ SERPER_API_KEY: key }, 'Serper API key saved.')
  }

  // WhatsApp and Instagram test what is saved; Instantly tests the key typed in the box.
  async function runTest(k: Tested, path: string, body: Record<string, string> = {}) {
    setTest(k, { state: 'pending', text: 'Testing…' })
    const r = await post<{ success?: boolean }>(path, body)
    if (!r) setTest(k, { state: 'error', text: 'Could not reach the dashboard server.' })
    else setTest(k, { state: r.success ? 'success' : 'error', text: failMsg(r, '') })
  }
  const result = (k: Tested) => <TestResult test={tests[k] ?? null} />

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
            extra={<Button onClick={() => runTest('instantly', '/api/settings/test-instantly', { api_key: f.instantlyKey.trim() })}>Test</Button>}
          />
          {result('instantly')}
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

      <Subhead hint={<>Turn a channel on with <code className={code}>channels.whatsapp.enabled</code> / <code className={code}>channels.instagram.enabled</code> in <code className={code}>harvey.yaml</code></>}>Chat</Subhead>
      <Card>
        <Row
          title={<>WhatsApp{data.evolution_api_key_set && data.evolution_api_url ? <StatusChip tone="good">set</StatusChip> : <StatusChip tone="idle">not set up</StatusChip>}</>}
          desc={<>Through a self-hosted <a className="text-accent underline" href="https://doc.evolution-api.com" target="_blank" rel="noopener noreferrer">Evolution API</a> instance. Use a dedicated number: cold messages to strangers get numbers banned.</>}
        >
          <TextField label="Evolution API URL" value={f.evoUrl} onChange={set('evoUrl')} placeholder="https://evolution.example.com" />
          <TextField label="Instance name" value={f.evoInstance} onChange={set('evoInstance')} placeholder="harvey" />
          <SecretInput
            label="API key" value={f.evoKey} onChange={set('evoKey')} isSet={!!data.evolution_api_key_set}
            placeholder="Your Evolution API key"
            extra={<Button onClick={() => runTest('whatsapp', '/api/settings/test-whatsapp')}>Test</Button>}
          />
          {result('whatsapp')}
          <Button variant="primary" size="sm" onClick={saveWhatsApp}>Save WhatsApp</Button>
        </Row>
        <Row
          title={<>Instagram{(data.instagram_username && data.instagram_password_set) || data.instagram_sessionid_set ? <StatusChip tone="good">set</StatusChip> : <StatusChip tone="idle">not set up</StatusChip>}</>}
          desc={<>DMs, openings to businesses the Instagram source found, and the Instagram discovery source, logged in as a real account through the app's unofficial API. Instagram can challenge or block it: use an account you can afford to lose. When it pushes back, Instagram pauses; press Test after fixing it in the app to resume. Instagram usually refuses a password login from a server: run <code className={code}>harvey instagram login</code> on your computer (add <code className={code}>--ssh &lt;server&gt;</code> when Harvey runs there), log in in the window it opens, and the session is saved here. Pasting the browser's <code className={code}>sessionid</code> cookie works too.</>}
        >
          <TextField label="Username" value={f.igUser} onChange={set('igUser')} placeholder="yourbusiness" />
          <SecretInput
            label="Password" value={f.igPass} onChange={set('igPass')} isSet={!!data.instagram_password_set}
            placeholder="The account's password"
          />
          <SecretInput
            label="Session id (optional)" value={f.igSession} onChange={set('igSession')} isSet={!!data.instagram_sessionid_set}
            placeholder="The browser's sessionid cookie"
            extra={<Button onClick={() => runTest('instagram', '/api/settings/test-instagram')}>Test</Button>}
          />
          {result('instagram')}
          <Button variant="primary" size="sm" onClick={saveInstagram}>Save Instagram</Button>
        </Row>
      </Card>

      <Subhead>Prospecting</Subhead>
      <Card>
        <Row
          title={<>Serper{setChip(data.serper_api_key_set)}</>}
          desc={<>Google search for discovery. <a className="text-accent underline" href="https://serper.dev" target="_blank" rel="noopener noreferrer">2,500 free searches</a>, no card.</>}
        >
          <SecretInput label="API key" value={f.serper} onChange={set('serper')} isSet={!!data.serper_api_key_set} placeholder="Your Serper API key" />
          <Button variant="primary" size="sm" onClick={saveSerper}>Save Serper</Button>
        </Row>
        <Row title="LinkedIn" desc="For automated LinkedIn prospecting. Roadie Crew logs in and searches like a human.">
          <TextField label="Email or username" value={f.liEmail} onChange={set('liEmail')} placeholder="your@email.com" />
          <SecretInput label="Password" value={f.liPass} onChange={set('liPass')} isSet={!!data.linkedin_password_set} placeholder="Enter password" autoComplete="new-password" />
          <Button variant="primary" size="sm" onClick={saveLinkedIn}>Save LinkedIn</Button>
        </Row>
      </Card>

      <Subhead>Crawling</Subhead>
      <Card>
        <Row title="Cloudflare" desc="Deep website crawling with JavaScript rendering during product training. About $5 a month.">
          <TextField label="Account ID" value={f.cfAccount} onChange={set('cfAccount')} placeholder="Your Cloudflare Account ID" />
          <SecretInput label="API token" value={f.cfToken} onChange={set('cfToken')} isSet={!!data.cloudflare_api_token_set} placeholder="Your Cloudflare API Token" />
          <Button variant="primary" size="sm" onClick={saveCloudflare}>Save Cloudflare</Button>
        </Row>
      </Card>
    </div>
  )
}

function TestResult({ test }: { test: Test }) {
  if (!test) return null
  return (
    <div
      role="status"
      className={cn('mb-3 rounded-md border px-3 py-2 text-[13px]',
        test.state === 'success' ? 'border-good-line bg-good-bg text-good'
          : test.state === 'error' ? 'border-bad-line bg-bad-bg text-bad'
            : 'border-line bg-surface-2 text-text-2')}
    >{test.text}</div>
  )
}
