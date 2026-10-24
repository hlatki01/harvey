import type { ReactNode } from 'react'
import { Card, Subhead } from '@/components/ui/card'
import { StatusBadge } from '@/components/ui/badge'
import { formatDate } from '@/lib/format'
import type { OutboxItem } from './types'

function Table({ title, rows, cols }: {
  title: string
  rows?: OutboxItem[]
  cols: { head: string; cell: (r: OutboxItem) => ReactNode; muted?: boolean }[]
}) {
  if (!rows || !rows.length) return null
  return (
    <>
      <Subhead hint={rows.length}>{title}</Subhead>
      <Card flush className="overflow-x-auto">
        <table className="w-full whitespace-nowrap text-left text-[13px]">
          <thead>
            <tr className="border-b border-line text-[12px] text-text-3">
              {cols.map(c => <th key={c.head} className="px-3 py-2 font-medium">{c.head}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.id} className="border-b border-line last:border-0">
                {cols.map(c => (
                  <td key={c.head} className={'max-w-[320px] truncate px-3 py-2' + (c.muted ? ' text-text-2' : '')}>{c.cell(r)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </>
  )
}

export function OutboxTables({ approved, sent, failed }: {
  approved?: OutboxItem[]; sent?: OutboxItem[]; failed?: OutboxItem[]
}) {
  return (
    <>
      <Table title="Approved & scheduled" rows={approved} cols={[
        { head: 'To', cell: r => r.to_email },
        { head: 'Step', cell: r => r.step },
        { head: 'Subject', cell: r => r.subject },
        { head: 'Sends', muted: true, cell: r => r.status === 'sending' ? <StatusBadge status={r.status} /> : formatDate(r.send_at) },
      ]} />
      <Table title="Recently sent" rows={sent} cols={[
        { head: 'To', cell: r => r.to_email },
        { head: 'Step', cell: r => r.step },
        { head: 'Subject', cell: r => r.subject },
        { head: 'Sent', muted: true, cell: r => formatDate(r.sent_at) },
      ]} />
      <Table title="Didn't send" rows={failed} cols={[
        { head: 'To', cell: r => r.to_email },
        { head: 'Status', cell: r => <StatusBadge status={r.status} /> },
        { head: 'Reason', muted: true, cell: r => r.error },
        { head: 'Updated', muted: true, cell: r => formatDate(r.updated_at) },
      ]} />
    </>
  )
}
