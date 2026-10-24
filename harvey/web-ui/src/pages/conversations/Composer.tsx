import { useEffect, useRef, useState } from 'react'
import { Mic, Paperclip, X } from 'lucide-react'
import { api, JSON_HEADERS } from '@/lib/api'
import { failMsg } from '@/lib/format'
import { useToast } from '@/components/toast'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/field'
import type { Chat } from './useChat'
import { Spin } from './Chat'

const MAX_FILE = 16 * 1024 * 1024

function fileSize(n: number) { return n > 1048576 ? (n / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(n / 1024)) + ' KB' }

function readBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const fr = new FileReader()
    fr.onload = () => resolve(String(fr.result).split(',')[1] || '')
    fr.onerror = () => reject(fr.error)
    fr.readAsDataURL(file)
  })
}

const MIC_ERRORS: Record<string, string> = {
  NotAllowedError: 'Microphone not allowed. Click the lock next to the address, set Microphone to Allow, and try again.',
  NotFoundError: 'No microphone was found on this computer.',
  NotReadableError: 'The microphone is in use by another app.',
}

interface Rec { recorder: MediaRecorder; chunks: Blob[]; stream: MediaStream; started: number; send: boolean; type: string }

export function Composer({ id, chat }: { id: string; chat: Chat }) {
  const toast = useToast()
  const [text, setText] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [sending, setSending] = useState(false)
  const [recSecs, setRecSecs] = useState<number | null>(null)   // null = not recording
  const box = useRef<HTMLTextAreaElement>(null)
  const pick = useRef<HTMLInputElement>(null)
  const rec = useRef<Rec | null>(null)
  const sendingRef = useRef(false)
  const textRef = useRef('')
  textRef.current = text
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      const r = rec.current
      if (r) { r.send = false; r.recorder.state !== 'inactive' && r.recorder.stop(); r.stream.getTracks().forEach(t => t.stop()) }
    }
  }, [])

  useEffect(() => {
    if (recSecs === null) return
    const t = setInterval(() => setRecSecs(Math.floor((Date.now() - (rec.current?.started ?? Date.now())) / 1000)), 500)
    return () => clearInterval(t)
  }, [recSecs === null])  // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {   // grow with the text, up to a few lines
    const b = box.current
    if (b) { b.style.height = 'auto'; b.style.height = Math.min(b.scrollHeight, 120) + 'px' }
  }, [text])

  async function send(msg: string, f: File | null) {
    if (sendingRef.current) return
    msg = msg.trim()
    if (!msg && !f) return
    // The message leaves the box at once and shows as "Sending…" in the chat.
    if (!f || !f.type.startsWith('audio/')) setText('')
    setFile(null)
    chat.addPending({ text: msg, file: f ? f.name : '', at: Math.floor(Date.now() / 1000) })
    sendingRef.current = true; setSending(true)
    const base = '/api/conversations/' + encodeURIComponent(id)
    let r: { success?: boolean } | null
    try {
      r = f
        ? await api(base + '/attach', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({
            filename: f.name, mimetype: f.type || 'application/octet-stream', data: await readBase64(f), caption: msg }) })
        : await api(base + '/send', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ text: msg }) })
    } catch { r = null }
    if (r && r.success) {
      await chat.refresh()
    } else {
      // Nothing was sent: take the bubble back out and give the text (and file) back.
      chat.dropPending()
      if (mounted.current) { setText(t => t || msg); if (f) setFile(f) }
      toast(failMsg(r, 'Could not send the message.'), 'error')
    }
    sendingRef.current = false
    if (mounted.current) { setSending(false); box.current?.focus() }
  }

  function onPick(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0]
    e.target.value = ''
    if (!f) return
    if (f.size > MAX_FILE) { toast('That file is over 16 MB.', 'error'); return }
    setFile(f)
  }

  async function startRecording() {
    if (rec.current || sendingRef.current) return
    if (!navigator.mediaDevices || !window.MediaRecorder) { toast('This browser cannot record audio.', 'error'); return }
    let stream: MediaStream
    try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }) }
    catch (e) {
      // The browser asks the first time; say what went wrong when it does not record.
      const name = (e as { name?: string } | null)?.name
      toast((name && Object.prototype.hasOwnProperty.call(MIC_ERRORS, name) ? MIC_ERRORS[name] : '') ||
        'Could not start the microphone (' + (name || 'error') + ').', 'error')
      return
    }
    if (!mounted.current) { stream.getTracks().forEach(t => t.stop()); return }
    const type = ['audio/ogg;codecs=opus', 'audio/webm;codecs=opus', 'audio/webm'].find(t => MediaRecorder.isTypeSupported(t)) || ''
    const recorder = new MediaRecorder(stream, type ? { mimeType: type } : undefined)
    const r: Rec = { recorder, chunks: [], stream, started: Date.now(), send: false, type: recorder.mimeType || type || 'audio/webm' }
    rec.current = r
    recorder.ondataavailable = e => { if (e.data && e.data.size) r.chunks.push(e.data) }
    recorder.onstop = () => {
      r.stream.getTracks().forEach(t => t.stop())
      rec.current = null
      if (mounted.current) setRecSecs(null)
      if (r.send && r.chunks.length && mounted.current) {
        const ext = r.type.includes('ogg') ? 'ogg' : 'webm'
        send(textRef.current, new File(r.chunks, 'voice-' + new Date().toTimeString().slice(0, 5).replace(':', '') + '.' + ext, { type: r.type }))
      }
    }
    recorder.start()
    setRecSecs(0)
  }

  function stopRecording(sendIt: boolean) {
    const r = rec.current
    if (!r) return
    r.send = sendIt
    r.recorder.stop()
  }

  if (recSecs !== null) {
    return (
      <div className="mt-3 flex items-center gap-3 px-1 py-1.5">
        <span className="size-2.5 animate-pulse rounded-full bg-bad" aria-hidden />
        <span className="text-[13px] tabular-nums" role="timer">{Math.floor(recSecs / 60) + ':' + String(recSecs % 60).padStart(2, '0')}</span>
        <span className="flex-1 text-[12px] text-text-3">Recording…</span>
        <Button size="sm" variant="ghost" onClick={() => stopRecording(false)}>Cancel</Button>
        <Button size="sm" variant="primary" onClick={() => stopRecording(true)}>Send</Button>
      </div>
    )
  }

  return (
    <div className="mt-3">
      {file && (
        <div className="mb-2 inline-flex items-center gap-2 rounded-full bg-accent-soft py-1 pl-3 pr-1.5 text-[12px] shadow-[inset_0_0_0_1px_var(--accent-line)]">
          {file.name} · {fileSize(file.size)}
          <button type="button" aria-label="Remove file" className="px-1 text-text-2" onClick={() => setFile(null)}><X className="size-3.5" /></button>
        </div>
      )}
      <div className="flex items-end gap-2">
        <input ref={pick} type="file" hidden onChange={onPick}
          accept="image/*,video/*,audio/*,.pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.zip" />
        <Button variant="ghost" size="sm" className="px-2" title="Attach a file" aria-label="Attach a file" disabled={sending} onClick={() => pick.current?.click()}>
          <Paperclip className="size-[18px]" />
        </Button>
        <Button variant="ghost" size="sm" className="px-2" title="Record a voice message" aria-label="Record a voice message" disabled={sending} onClick={startRecording}>
          <Mic className="size-[18px]" />
        </Button>
        <Textarea
          ref={box} rows={1} value={text} aria-label="Reply on WhatsApp"
          placeholder="Reply on WhatsApp as the team…" className="min-h-8 flex-1 resize-none py-1.5"
          onChange={e => setText(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(text, file) } }}
        />
        <Button variant="primary" size="sm" className="min-w-[76px]" disabled={sending} onClick={() => send(text, file)}>
          {sending ? <><Spin className="mr-0" />Sending</> : 'Send'}
        </Button>
      </div>
    </div>
  )
}
