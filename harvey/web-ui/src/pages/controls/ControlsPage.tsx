import { useLayoutEffect, useRef, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { post } from '@/lib/api'
import { useApi, useInvalidate } from '@/lib/hooks'
import { failMsg, formatDate } from '@/lib/format'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Card, SectionHead } from '@/components/ui/card'
import { useToast } from '@/components/toast'

interface HarveyStatus { running?: boolean; pid?: number | string; started_at?: string }
interface Logs { lines?: string[] }

const NO_LOGS = 'No logs yet. Start Harvey to see activity.'

function AgentCard() {
  const toast = useToast()
  const invalidate = useInvalidate()
  // Same key as the shell's header poll, so one request serves both.
  const { data } = useApi<HarveyStatus>('/api/harvey/status', { interval: 8000 })
  const [busy, setBusy] = useState(false)
  const running = !!data?.running

  async function act(kind: 'start' | 'stop') {
    setBusy(true)
    const r = await post<{ success?: boolean; message?: string }>(`/api/harvey/${kind}`)
    if (r && r.success) toast(kind === 'start' ? 'Harvey started.' : 'Harvey stopped.', 'success')
    else toast(failMsg(r, kind === 'start' ? 'Failed to start.' : 'Failed to stop.'), 'error')
    setBusy(false)
    invalidate('/api/harvey/status')
  }

  return (
    <Card>
      <h3 className="mb-3 text-[15px] font-semibold">Agent</h3>
      <div className="flex items-center gap-2.5">
        <span className={cn('size-3 rounded-full', running ? 'bg-good' : 'bg-text-3')} />
        <span className={cn('text-[20px] font-semibold', running ? 'text-good' : 'text-text-2')}>{running ? 'Running' : 'Stopped'}</span>
      </div>
      <p className="mb-4 mt-1.5 text-[13px] text-text-2">
        {running && data?.pid
          ? <>PID {String(data.pid)}{data.started_at && <> &middot; started {formatDate(data.started_at)}</>}</>
          : 'Harvey wakes every few minutes, does what needs doing, and sleeps.'}
      </p>
      {running
        ? <Button variant="danger" busy={busy} onClick={() => act('stop')}>Stop Harvey</Button>
        : <Button variant="primary" busy={busy} onClick={() => act('start')}>Start Harvey</Button>}
    </Card>
  )
}

function LogsCard() {
  const { data, refetch } = useApi<Logs>('/api/harvey/logs', { interval: 15000 })
  const ref = useRef<HTMLPreElement>(null)
  // Follow the tail only while the reader is already at the bottom.
  const stick = useRef(true)
  const text = data?.lines?.length ? data.lines.join('\n') : NO_LOGS

  useLayoutEffect(() => {
    const el = ref.current
    if (el && stick.current) el.scrollTop = el.scrollHeight
  }, [text])

  return (
    <Card>
      <div className="mb-3 flex items-center">
        <h3 className="text-[15px] font-semibold">Recent logs</h3>
        <Button variant="ghost" size="sm" className="ml-auto" onClick={() => refetch()}>
          <RefreshCw className="size-3.5" aria-hidden />Refresh
        </Button>
      </div>
      <pre
        ref={ref}
        tabIndex={0}
        aria-label="Harvey logs"
        onScroll={e => { const el = e.currentTarget; stick.current = el.scrollTop + el.clientHeight >= el.scrollHeight - 30 }}
        className="m-0 h-[420px] overflow-auto whitespace-pre-wrap break-words rounded-md bg-surface-2 p-3 font-mono text-[12px] leading-relaxed text-text-2"
      >{text}</pre>
    </Card>
  )
}

export function ControlsPage() {
  return (
    <>
      <SectionHead title="Controls">Start and stop Harvey's heartbeat loop, and watch what it's doing.</SectionHead>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,320px)_minmax(0,1fr)]">
        <AgentCard />
        <LogsCard />
      </div>
    </>
  )
}
