import { useRef } from 'react'
import { lockIfRefused, JSON_HEADERS } from '@/lib/api'
import { authFetch } from '@/lib/auth'
import { useToast } from '@/components/toast'
import type { Campaign, Recipient, Step } from './types'

// Same values as the Sender's _render, so a hand-sent email reads the same.
export function fillMergeVars(text: string | undefined, r: Recipient): string {
  const vals: Record<string, string> = {
    first_name: r.first_name || (r.source === 'company_inbox' ? 'there' : ''), last_name: r.last_name || '',
    company: r.company || '', title: r.title || '', personalization: r.personalization_notes || '',
  }
  return String(text || '').replace(/\{\{\s*(\w+)\s*\}\}/g, (m, k: string) => {
    k = k.toLowerCase()
    return Object.prototype.hasOwnProperty.call(vals, k) ? vals[k] : m
  })
}

/** Emails the user sends from their own Gmail when Harvey can't send itself. */
export function useHandSend(onChanged: () => void) {
  const toast = useToast()
  const gmailBusy = useRef(false)  // one compose window per click, never two

  /** True only when this call changed it: a status means they replied or
   *  opted out, 'already' means another click or tab got there first. */
  const markSent = async (r: Recipient, sent: boolean): Promise<boolean> => {
    let res: { success?: boolean; message?: string; sent?: boolean; status?: string; already?: boolean } | null = null
    try {
      const resp = await authFetch('/api/prospects/' + encodeURIComponent(r.id) + '/sent', {
        method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ sent }),
      })
      if (await lockIfRefused(resp)) return false
      res = await resp.json()
    } catch { /* falls through to the error toast */ }
    const ok = !!(res && res.success)
    if (!ok) toast((res && res.message) || 'Could not save that. Try again.', 'error')
    else if (!sent && res!.sent) toast('They already replied, so this stays marked as sent.')
    else toast(sent ? 'Marked as sent by you.' : 'Unmarked.', 'success')
    onChanged()
    return ok && sent === !!res!.sent && !res!.status && !res!.already
  }

  // Opens a filled-in Gmail compose window. Nothing is sent from here.
  const openInGmail = async (c: Campaign, step: Step, r: Recipient) => {
    if (gmailBusy.current) return
    // Cold email carries the postal address and opt-out line, sent by hand too.
    const filled = fillMergeVars(step.body, r).trimEnd()
    const body = filled + (c.footer && !filled.endsWith(c.footer) ? '\n\n' + c.footer : '')
    const subject = fillMergeVars(step.subject, r)
    // authuser picks the Google account Harvey sends as, not just whichever one the browser is signed into.
    const url = 'https://mail.google.com/mail/?view=cm&fs=1' +
      (c.send_as ? '&authuser=' + encodeURIComponent(c.send_as) : '') +
      '&to=' + encodeURIComponent(r.email) + '&su=' + encodeURIComponent(subject) + '&body=' + encodeURIComponent(body)
    const left = /\{\{\s*\w+\s*\}\}/.test(subject + fillMergeVars(step.body, r))
    // No 'noopener' feature string: with it window.open always returns null, so a blocked
    // pop-up couldn't be told apart. The tab opens blank inside the click and gets the draft once recorded.
    const w = window.open('', '_blank')
    if (!w) {
      toast('Your browser blocked the Gmail tab. Allow pop-ups for this page and click again.', 'error')
      return
    }
    w.opener = null
    // Record the send BEFORE the user can press Send in Gmail, so Harvey can never email them as well.
    gmailBusy.current = true
    let claimed = !!r.sent_by_hand
    try { if (!claimed) claimed = await markSent(r, true) } finally { gmailBusy.current = false }
    if (!claimed) { w.close(); return }
    w.location.href = url
    if (left) toast('This draft still has a {{placeholder}} in it. Fix it in Gmail before sending.', 'error')
  }

  return { markSent, openInGmail }
}
