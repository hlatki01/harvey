import { Activity as ActivityIcon } from 'lucide-react'
import { Card, SectionHead } from '@/components/ui/card'
import { EmptyState, OfflineState, Skeleton } from '@/components/ui/states'
import { dayLabel, timeOfDay } from '@/lib/format'
import { useApi } from '@/lib/hooks'
import { TimelineItem, type ActivityRow } from './today/timeline'

export function ActivityPage() {
  const { data } = useApi<ActivityRow[]>('/api/activity', { interval: 15000 })
  let day = ''
  return (
    <div>
      <SectionHead title="Activity">A running log of every action Harvey's agents have taken.</SectionHead>
      {data === undefined ? <Skeleton /> : !data ? <OfflineState /> : !data.length ? (
        <EmptyState icon={<ActivityIcon className="size-5" />} title="No activity yet">
          Harvey hasn't taken any actions. Every prospect found, email written, and reply handled will appear here the moment it happens.
        </EmptyState>
      ) : (
        <Card>
          {data.map((a, i) => {
            const label = dayLabel((a as any).created_at)
            const head = label !== day
            day = label
            return (
              <div key={i}>
                {head && <div className="mb-1 mt-3 text-[12px] font-medium uppercase tracking-wide text-text-3 first:mt-0">{label}</div>}
                <TimelineItem a={a} time={timeOfDay((a as any).created_at)} quiet={a.agent === 'main'} />
              </div>
            )
          })}
        </Card>
      )}
    </div>
  )
}
