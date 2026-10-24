export interface ThreadMsg { sender: string; content: string; timestamp: string }
export interface SentEmail { step: number | string; subject: string; body: string; sent_at: string }
export interface Draft { id: string; subject?: string; body: string; send_at?: string }

export interface Convo {
  id: string
  channel?: string
  status: string
  stage?: string
  intent?: string
  first_name?: string | null
  last_name?: string | null
  company?: string | null
  prospect_email?: string | null
  updated_at?: string
  thread?: ThreadMsg[]
  emails?: SentEmail[]
  draft?: Draft | null
}

export interface ChatMedia { kind: string; filename?: string }
export interface ChatMsg {
  id: string
  ts: number
  from_me: boolean
  who?: string
  text?: string
  media?: ChatMedia | null
}

export interface LeadInfo {
  lead?: Record<string, any>
  conversation?: Record<string, any>
  company?: Record<string, any> | null
  signals?: string[]
  history?: { ts: string; text: string }[]
}

export const STAGE_LABELS: Record<string, string> = {
  initial_outreach: 'Outreach', engaged: 'Engaged', qualifying: 'Qualifying', presenting: 'Presenting',
  negotiating: 'Negotiating', closing: 'Closing', closed_won: 'Won', closed_lost: 'Lost',
}

export const convoName = (c: Convo, fallback = 'Unknown') =>
  [c.first_name, c.last_name].filter(Boolean).join(' ') || c.company || fallback
