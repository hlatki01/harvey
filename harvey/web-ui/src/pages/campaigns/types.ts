export interface Recipient {
  id: string; email: string; phone?: string; first_name?: string; last_name?: string; company?: string; title?: string
  personalization_notes?: string; source?: string; status?: string; email_status?: string
  sent_by_hand?: boolean; can_send_by_hand?: boolean; can_mark_sent?: boolean
  sent?: number; next_at?: string | null
}
export interface Step { step?: number | string; subject?: string; body?: string; delay_days?: number }
export interface Campaign {
  id: string; name?: string; channel?: string; status?: string; created_at?: string; footer?: string; send_as?: string
  prospect_ids?: string[]; sequence?: Step[]; recipients?: Recipient[]
}
export interface Msg {
  who: 'harvey' | 'lead' | 'team'; ts?: string; body?: string; status?: string
  kind?: string; step?: number; subject?: string; send_at?: string; sent_at?: string; error?: string
}
export interface ProspectDetail {
  lead?: Record<string, any>
  company?: Record<string, any> | null
  signals?: string[]
  history?: { ts: string; text: string }[]
  messages?: Msg[]
}

export const isWhatsApp = (c: Campaign) => c.channel === 'whatsapp'
export const personName = (r: Recipient) =>
  [r.first_name, r.last_name].filter(Boolean).join(' ') || r.company || r.email || r.phone || 'Unknown'
