// Formatting helpers and the one status vocabulary, ported from the old app.js.
// React escapes text for us, so nothing here returns HTML.

export type Tone = 'waiting' | 'active' | 'good' | 'bad' | 'idle' | 'note'

/** Lookup tables keyed by stored strings: "constructor" must not hit Object's. */
export function own<T>(table: Record<string, T>, key: string): T | undefined {
  return Object.prototype.hasOwnProperty.call(table, key) ? table[key] : undefined
}

/** Links built from collected data open only as http(s): a javascript: URL
 *  from a scraped page must never run in the dashboard. Returns '' if unsafe. */
export function safeHref(u: unknown): string {
  const s = String(u || '').trim()
  return /^https?:\/\//i.test(s) ? s : ''
}

/** The reason a failed action gave, or a fallback when it gave none. */
export function failMsg(data: any, fallback: string): string {
  return (data && (data.message || data.error || data.detail)) || fallback
}

// Harvey's tables each carry their own words for state; everything resolves
// through this map so a status reads the same wherever it came from.
const STATUS: Record<string, [string, Tone]> = {
  new: ['New', 'active'], contacted: ['Contacted', 'active'], replied: ['Replied', 'good'],
  interested: ['Interested', 'good'], meeting: ['Meeting booked', 'note'],
  not_interested: ['Not interested', 'bad'], bounced: ['Bounced', 'bad'],
  write_failed: ["Couldn't write", 'bad'], queued: ['Queued', 'active'], opted_out: ['Opted out', 'bad'],
  draft: ['Draft', 'waiting'], active: ['Active', 'good'], completed: ['Completed', 'idle'], paused: ['Paused', 'bad'],
  open: ['Open', 'active'], closed: ['Closed', 'idle'], closed_won: ['Won', 'good'], closed_lost: ['Lost', 'bad'],
  objection: ['Objection', 'waiting'], question: ['Question', 'active'], ooo: ['Auto-reply', 'idle'],
  wrong_person: ['Wrong person', 'waiting'], unsubscribe: ['Unsubscribed', 'bad'],
  escalate: ['Needs a human', 'bad'], needs_human: ['Needs you', 'waiting'],
  pending_review: ['Waiting on you', 'waiting'], approved: ['Approved', 'good'], sending: ['Sending now', 'active'],
  scheduled: ['Scheduled', 'active'], sent: ['Sent', 'good'], failed: ['Failed', 'bad'],
  rejected: ['Rejected', 'idle'], cancelled: ['Cancelled', 'idle'],
  proposed: ['Waiting on you', 'waiting'], confirmed: ['Confirmed', 'good'],
  verified: ['Verified', 'good'], risky: ['Catch-all', 'waiting'], guess: ['Unverified', 'idle'], invalid: ['Invalid', 'bad'],
  running: ['Running', 'active'], stale: ['Stale', 'bad'], stalled: ['Stopped', 'bad'],
}

export function statusMeta(status: unknown): { label: string; tone: Tone } {
  const key = String(status || '').toLowerCase().replace(/[^a-z0-9]+/g, '_')
  const hit = own(STATUS, key)
  if (hit) return { label: hit[0], tone: hit[1] }
  const label = String(status || 'unknown').replace(/_/g, ' ')
  return { label: label.charAt(0).toUpperCase() + label.slice(1), tone: 'idle' }
}

/** Stored times are UTC, often without a zone ("2026-10-21 03:07:57"). */
export function parseTs(d: unknown): Date {
  let t = String(d || '').trim().replace(' ', 'T')
  if (t && !/[zZ]$|[+-]\d\d:?\d\d$/.test(t)) t += 'Z'
  return new Date(t)
}

export function formatDate(d: unknown): string {
  if (!d) return ''
  const dt = parseTs(d)
  if (isNaN(dt.getTime())) return String(d)
  return dt.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

/** "3m ago", "5h ago", "Yesterday", then a date: recent things read faster. */
export function relTime(d: unknown): string {
  if (!d) return ''
  const dt = parseTs(d)
  if (isNaN(dt.getTime())) return String(d)
  const mins = Math.round((Date.now() - dt.getTime()) / 60000)
  if (mins < -1 && mins > -60) return 'in ' + -mins + 'm'
  if (mins < -1 && mins > -60 * 24) return 'in ' + Math.round(-mins / 60) + 'h'
  if (mins >= -1 && mins < 1) return 'just now'
  if (mins >= 1 && mins < 60) return mins + 'm ago'
  if (mins >= 60 && mins < 60 * 24) return Math.round(mins / 60) + 'h ago'
  if (mins >= 60 * 24 && mins < 60 * 48) return 'Yesterday'
  const opts: Intl.DateTimeFormatOptions = { month: 'short', day: 'numeric' }
  if (dt.getFullYear() !== new Date().getFullYear()) opts.year = 'numeric'
  return dt.toLocaleDateString('en-US', opts)
}

export function dayLabel(d: unknown): string {
  const dt = parseTs(d)
  if (isNaN(dt.getTime())) return ''
  const today = new Date(); today.setHours(0, 0, 0, 0)
  const that = new Date(dt); that.setHours(0, 0, 0, 0)
  const diff = Math.round((today.getTime() - that.getTime()) / 86400000)
  if (diff === 0) return 'Today'
  if (diff === 1) return 'Yesterday'
  return dt.toLocaleDateString('en-US', { weekday: 'long', month: 'short', day: 'numeric' })
}

export function timeOfDay(d: unknown): string {
  const dt = parseTs(d)
  return isNaN(dt.getTime()) ? '' : dt.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })
}

export function initials(first: unknown, last: unknown): string {
  const ch = (x: string) => Array.from(x)[0] || ''  // whole characters, not UTF-16 halves
  const s = ch(String(first || '').trim()) + ch(String(last || '').trim())
  return (s || '?').toUpperCase()
}

const SOURCE_LABELS: Record<string, string> = {
  company_website: 'Team page', known_company_backfill: 'Team page',
  scraped_mailto: 'Published address', hunter_domain_search: 'Hunter', linkedin_search: 'LinkedIn',
  job_board: 'Job board', web_search: 'Web search', company_inbox: 'Company inbox',
  osm: 'OpenStreetMap', dataforseo_listings: 'DataForSEO', dataforseo_serp: 'DataForSEO SERP',
  serper: 'Serper', google_places: 'Google Places',
}
export function sourceLabel(s: unknown): string {
  if (!s) return ''
  const key = String(s)
  return own(SOURCE_LABELS, key) ?? key.replace(/_/g, ' ').replace(/^./, c => c.toUpperCase())
}

export interface ActionRow { agent?: string; action_type?: string; details?: Record<string, any>; created_at?: string }

/** One plain sentence per logged action, from the details each agent records. */
export function actionText(a: ActionRow): string {
  const d = a.details || {}
  const who = String(d.prospect_email || d.prospect || d.to || '')
  const camp = String(d.campaign_name || d.campaign || '')
  const intent = d.intent ? ' · ' + String(d.intent).replace(/_/g, ' ') : ''
  const n = (v: unknown, one: string, many: string) => (Number(v) || 0) + ' ' + ((Number(v) || 0) === 1 ? one : many)
  switch (a.action_type) {
    case 'prospect': return 'Found ' + n(d.prospects_found, 'new contact', 'new contacts')
    case 'write_campaign': return 'Wrote “' + camp + '” for ' + n(d.prospect_count, 'contact', 'contacts')
    case 'write_failed': return 'Couldn’t write for some contacts' + (camp ? ' in “' + camp + '”' : '')
    case 'stage_campaign': return 'Queued ' + n(d.staged, 'email', 'emails') + ' from “' + camp + '”' + (d.mode ? ' · ' + d.mode : '')
    case 'send_campaign': return 'Sent “' + camp + '” to Instantly · ' + n(d.leads_added, 'lead', 'leads')
    case 'email_sent': return d.kind === 'reply' ? 'Sent a reply to ' + who : 'Sent email ' + String(d.step || 1) + ' to ' + who
    case 'reply': return 'Answered ' + who + intent
    case 'reply_queued': return 'Drafted a reply to ' + who + intent
    case 'opt_out': return who + ' opted out'
    case 'escalation': return 'Flagged ' + who + ' for you' + (d.reason ? ' · ' + d.reason : '')
    case 'bounce': return 'Email to ' + who + ' bounced'
    case 'manual_send': return 'You marked a contact as emailed by hand'
    case 'manual_send_undone': return 'You undid a hand-sent mark'
    case 'outbox_edited': return 'You edited a draft'
    case 'conversation_status':
      return d.to === 'closed' ? 'You closed a conversation'
        : d.from === 'needs_human' ? 'You handed a conversation back to Harvey' : 'You reopened a conversation'
  }
  const text = String(a.action_type || '').replace(/_/g, ' ')
  return a.agent === 'main' ? 'Heartbeat: ' + text : text
}
