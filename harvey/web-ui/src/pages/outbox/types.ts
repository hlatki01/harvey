export interface DeskContext {
  company?: string
  website?: string
  description?: string
  contact?: string
  contact_title?: string
  contact_source?: string
  email_status?: string
  score?: number | string
  signals?: Record<string, unknown>
  campaign?: string
  notes?: string
}

export interface OutboxItem {
  id: string
  to_email: string
  subject?: string
  body?: string
  step?: number | string
  kind?: string
  channel?: string
  status?: string
  send_at?: string
  sent_at?: string
  updated_at?: string
  error?: string
  context?: DeskContext | null
}

export interface OutboxData {
  error?: string
  pending?: OutboxItem[]
  approved?: OutboxItem[]
  sent?: OutboxItem[]
  failed?: OutboxItem[]
  paused?: string | null
  footer?: string
  compliance_notice?: string
}

export interface Edit { id: string; subject: string; body: string }
