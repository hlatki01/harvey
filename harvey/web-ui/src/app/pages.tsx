import { lazy, type ComponentType } from 'react'
import type { TabId } from './nav'

// One chunk per tab: the shell loads first, a tab's code only when it is opened.
const page = <K extends string>(load: () => Promise<Record<K, ComponentType>>, name: K) =>
  lazy(() => load().then(m => ({ default: m[name] })))

export const PAGES: Record<TabId, ComponentType> = {
  today: page(() => import('@/pages/Today'), 'TodayPage'),
  outbox: page(() => import('@/pages/Outbox'), 'OutboxPage'),
  conversations: page(() => import('@/pages/Conversations'), 'ConversationsPage'),
  signals: page(() => import('@/pages/Signals'), 'SignalsPage'),
  discover: page(() => import('@/pages/Discover'), 'DiscoverPage'),
  companies: page(() => import('@/pages/Companies'), 'CompaniesPage'),
  prospects: page(() => import('@/pages/Contacts'), 'ContactsPage'),
  campaigns: page(() => import('@/pages/Campaigns'), 'CampaignsPage'),
  activity: page(() => import('@/pages/Activity'), 'ActivityPage'),
  usage: page(() => import('@/pages/Usage'), 'UsagePage'),
  controls: page(() => import('@/pages/Controls'), 'ControlsPage'),
  settings: page(() => import('@/pages/Settings'), 'SettingsPage'),
  help: page(() => import('@/pages/Help'), 'HelpPage'),
}
