import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Download, Loader2 } from 'lucide-react'
import { cn } from '@/lib/utils'
import { own } from '@/lib/format'
import type { Chat } from './useChat'
import type { ChatMsg } from './types'

export function Spin({ className }: { className?: string }) {
  return <Loader2 className={cn('mr-1.5 inline size-3.5 animate-spin align-[-2px]', className)} aria-hidden />
}

export function chatTime(ts: number) {
  const d = new Date(ts * 1000), now = new Date()
  const hm = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  return d.toDateString() === now.toDateString() ? hm : d.toLocaleDateString([], { day: '2-digit', month: 'short' }) + ' ' + hm
}

/** A scrolling thread that keeps you at the bottom if you were there, and
 *  otherwise keeps your place (older messages arriving above don't jump it). */
export function Scroller({ children, onTop, className }: { children: React.ReactNode; onTop?: () => void; className?: string }) {
  const box = useRef<HTMLDivElement>(null)
  const inner = useRef<HTMLDivElement>(null)
  const fromBottom = useRef(0)   // distance to the bottom, kept while the content grows
  useLayoutEffect(() => {
    const b = box.current, i = inner.current
    if (!b || !i) return
    b.scrollTop = b.scrollHeight
    const ro = new ResizeObserver(() => { b.scrollTop = b.scrollHeight - b.clientHeight - fromBottom.current })
    ro.observe(i)
    return () => ro.disconnect()
  }, [])
  return (
    <div
      ref={box}
      onScroll={e => {
        const t = e.currentTarget
        const d = t.scrollHeight - t.scrollTop - t.clientHeight
        fromBottom.current = d < 40 ? 0 : d
        if (t.scrollTop < 60) onTop?.()
      }}
      className={cn('flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto overscroll-contain pr-1', className)}
    >
      <div ref={inner} className="flex flex-col gap-2">{children}</div>
    </div>
  )
}

export const bubble = (sent: boolean) => cn(
  'max-w-[min(78%,62ch)] whitespace-pre-wrap break-words rounded-[14px] px-3.5 py-2.5 text-[13px] leading-relaxed max-sm:max-w-[92%]',
  sent ? 'self-end rounded-br-[4px] bg-accent-soft shadow-[inset_0_0_0_1px_var(--accent-line)]' : 'self-start rounded-bl-[4px] bg-surface-2',
)

const fileBtn = 'mb-1 inline-flex max-w-full items-center gap-2 break-words rounded-[10px] border border-line bg-surface px-3 py-2 text-[13px] text-text'

function Media({ cid, m, chat, onImage }: { cid: string; m: ChatMsg; chat: Chat; onImage: (src: string) => void }) {
  const md = m.media!
  const url = chat.urls[m.id]
  const ref = useRef<HTMLDivElement>(null)
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState(false)
  const auto = md.kind === 'image' || md.kind === 'audio'   // video and files load when you ask
  const load = async () => {
    setBusy(true); setFailed(false)
    const ok = await chat.loadMedia(m.id)
    setBusy(false); setFailed(!ok)
  }
  const loadRef = useRef(load)
  loadRef.current = load
  useEffect(() => {
    const el = ref.current
    if (url || !auto || !el || !('IntersectionObserver' in window)) return
    const io = new IntersectionObserver(es => { if (es.some(e => e.isIntersecting)) { io.disconnect(); loadRef.current() } }, { rootMargin: '200px' })
    io.observe(el)
    return () => io.disconnect()
  }, [url, auto, cid])

  if (url && md.kind === 'image') {
    return <button type="button" className="mb-1 block cursor-zoom-in" onClick={() => onImage(url)} aria-label="View image"><img src={url} alt="" className="block max-h-70 max-w-[min(100%,320px)] rounded-[10px]" /></button>
  }
  if (url && md.kind === 'video') return <video src={url} controls preload="metadata" className="mb-1 block max-h-70 max-w-[min(100%,320px)] rounded-[10px]" />
  if (url && md.kind === 'audio') return <audio src={url} controls preload="metadata" className="mb-1 max-w-full" />
  if (url) return <a className={fileBtn} href={url} download={md.filename || 'file'}><Download className="size-3.5" aria-hidden />{md.filename || 'Download file'}</a>
  if (failed) {
    return <button type="button" className={fileBtn} onClick={load}>Could not load. Tap to retry</button>
  }
  if (auto) {
    return (
      <div ref={ref} className="mb-1 flex items-center justify-center rounded-[10px] bg-surface-2 px-6 py-8 text-[12px] text-text-3">
        <Spin />{md.kind === 'audio' ? 'Loading audio…' : 'Loading image…'}
      </div>
    )
  }
  const label = md.kind === 'video' ? 'Load video' : (md.filename || 'Load file')
  return (
    <button type="button" className={fileBtn} onClick={load} disabled={busy}>
      {busy ? <><Spin />Loading…</> : <><Download className="size-3.5" aria-hidden />{label}</>}
    </button>
  )
}

const WHO: Record<string, string> = { harvey: 'Harvey', team: 'Equipe', team_phone: 'Equipe (celular)' }

export function ChatBubbles({ id, first, chat, onImage }: { id: string; first: string; chat: Chat; onImage: (src: string) => void }) {
  return (
    <>
      {chat.more && (
        <div className="self-center py-1 text-[12px] text-text-3">
          {chat.loadingOlder ? <><Spin />Loading older messages…</> : 'Scroll up for older messages'}
        </div>
      )}
      {chat.msgs.map(m => (
        <div key={m.id} className={bubble(m.from_me)}>
          <div className="mb-0.5 text-[11px] opacity-70">{(m.who && own(WHO, m.who)) || first} &middot; {chatTime(m.ts)}</div>
          {m.media && <Media cid={id} m={m} chat={chat} onImage={onImage} />}
          {m.text && <div>{m.text}</div>}
        </div>
      ))}
      {chat.pending.map((p, i) => (
        <div key={'p' + i} className={cn(bubble(true), 'opacity-70')}>
          <div className="mb-0.5 text-[11px]"><Spin />Sending…</div>
          {p.file && <div className={fileBtn}><Download className="size-3.5" aria-hidden />{p.file}</div>}
          {p.text && <div>{p.text}</div>}
        </div>
      ))}
    </>
  )
}
