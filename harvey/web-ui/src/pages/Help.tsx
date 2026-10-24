import type { ReactNode } from 'react'
import { Card, SectionHead } from '@/components/ui/card'

// Static content only: Help is deliberately never polled.

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Card className="mb-4 space-y-2 text-text-2">
      <h3 className="text-[15px] font-semibold text-text">{title}</h3>
      {children}
    </Card>
  )
}

function Faq({ q, children }: { q: string; children: ReactNode }) {
  return (
    <details className="group rounded-md border border-line px-3 py-2">
      <summary className="cursor-pointer font-medium text-text">{q}</summary>
      <div className="mt-2 space-y-1.5 text-[13px] text-text-2">{children}</div>
    </details>
  )
}

const Code = ({ children }: { children: ReactNode }) => (
  <code className="rounded-xs bg-surface-2 px-1 py-px font-mono text-[12px] text-text">{children}</code>
)
const Ext = ({ href, children }: { href: string; children: ReactNode }) => (
  <a href={href} target="_blank" rel="noopener" className="text-accent underline">{children}</a>
)

const FILES: [string, string][] = [
  ['.env', 'Your API keys and credentials (never shared or committed)'],
  ['harvey.yaml', 'Your product info, target customers, and behavior settings'],
  ['skills/', 'Sales knowledge files. Edit these to change how Harvey writes and sells.'],
  ['skills/product_knowledge.md', 'Everything Harvey knows about your product (auto-generated from training)'],
  ['prompts/', 'Prompt templates for each agent. Advanced customization.'],
  ['data/harvey.db', 'Database with all prospects, campaigns, and conversations'],
  ['data/harvey.log', 'Log file showing what Harvey is doing'],
]

export function HelpPage() {
  return (
    <div>
      <SectionHead title="Help">What Harvey is, how it works, and how to fix the usual problems.</SectionHead>

      <Section title="What is Harvey?">
        <p>Harvey is an autonomous AI sales agent. Once set up, Harvey runs on its own: finds people who match your ideal customer, writes personalized cold emails, sends them through your email platform, reads every reply, handles objections, and works toward booking a meeting. You review everything through this dashboard.</p>
        <p>Harvey runs on your Claude Max subscription (or, if you opt in, your ChatGPT plan through Codex), so there are no extra AI costs. Everything stays on your machine in one folder.</p>
      </Section>

      <Section title="Getting started">
        <p>There are three things to do:</p>
        <pre className="overflow-x-auto rounded-md bg-surface-2 p-3 font-mono text-[12px] text-text">{`1. Go to the Settings tab and connect an email provider (Gmail recommended)
2. Train Harvey on your product (through Claude or the command line)
3. Go to the Controls tab and click Start`}</pre>
        <p>Today shows exactly what's done and what still needs to happen.</p>
      </Section>

      <Section title="Where everything lives">
        <p>Everything Harvey needs is inside this one project folder. Nothing is stored elsewhere.</p>
        <table className="w-full text-[13px]">
          <tbody>
            {FILES.map(([f, d]) => (
              <tr key={f} className="border-t border-line">
                <td className="py-1.5 pr-4 align-top"><Code>{f}</Code></td>
                <td className="py-1.5">{d}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>

      <Section title="Getting your API keys">
        <Faq q="Instantly API key (legacy)">
          <p>Instantly is the legacy sending option; Gmail or SMTP is recommended.</p>
          <p>1. Sign up at <Ext href="https://instantly.ai">instantly.ai</Ext> (you need the Growth plan for API access)</p>
          <p>2. Go to Settings &gt; Integrations</p>
          <p>3. Copy your API key</p>
          <p>4. Paste it in the Settings tab here</p>
        </Faq>
        <Faq q="LinkedIn credentials (optional)">
          <p>If you want Harvey to find prospects on LinkedIn, enter your LinkedIn email and password. Harvey uses a real browser to search LinkedIn like a human would, with random delays and rate limits to avoid detection.</p>
          <p>If you skip this, Harvey will find prospects through Google searches and company website scraping instead.</p>
        </Faq>
        <Faq q="Cloudflare Browser Rendering (optional)">
          <p>This is only used during product training (when Harvey crawls your website to learn about your product). It handles JavaScript-heavy websites that a basic crawler can't read.</p>
          <p>1. Sign up at <Ext href="https://dash.cloudflare.com">Cloudflare</Ext> (paid Workers plan, ~$5/month)</p>
          <p>2. Go to Workers &amp; Pages &gt; Browser Rendering</p>
          <p>3. Create an API token with Browser Rendering Edit permissions</p>
          <p>4. Enter your Account ID and API Token in the Settings tab</p>
          <p>Without this, Harvey uses a built-in crawler that works fine for most websites but can't render JavaScript.</p>
        </Faq>
      </Section>

      <Section title="Common issues">
        <Faq q={'"command not found: harvey"'}>
          <p>You need to activate the virtual environment first: <Code>source .venv/bin/activate</Code></p>
        </Faq>
        <Faq q="Instantly API returns 401">
          <p>Your API key is wrong, or you need the Growth plan (the free plan doesn't include API access). Double-check the key in Settings &gt; Integrations in your Instantly dashboard.</p>
        </Faq>
        <Faq q="Claude headless mode fails">
          <p>Make sure you've run <Code>claude login</Code> in your terminal and have an active Claude Max subscription. Harvey uses your existing subscription, not a separate API key.</p>
        </Faq>
        <Faq q="Codex calls fail">
          <p>With <Code>brain.provider: codex</Code>, set <Code>brain.model</Code> to an available model ID, including Luna, Sol, or Terra (for example <Code>gpt-6.1-sol</Code>), or leave it blank to select a listed model. Harvey runs these models without tools; <Code>gpt-5.5</Code> also remains supported when present in your account's catalog. Run <Code>codex login</Code> and choose "Sign in with ChatGPT" (API-key logins are refused). Harvey never renews your Codex login: if the log says it has expired or is due for renewal, use Codex once or run <Code>codex login</Code>. Harvey needs Codex CLI 0.159.3 on macOS or Linux (Codex isn't supported on Windows; use Claude there) and refuses other versions. While Codex can't be used, Harvey pauses (Today shows "Codex is paused" and why), leaves replies and queued emails untouched, and resumes once it's fixed; <Code>harvey status --check</Code> checks on demand. If the log says isolation could not be verified, a Codex config layer is forcing a tool, hook, MCP server or custom endpoint back on — Harvey refuses to send prompts until it is removed. Harvey never falls back to Claude on its own.</p>
        </Faq>
        <Faq q="Harvey isn't finding prospects">
          <p>Check that your ICP (ideal customer profile) in harvey.yaml has realistic titles, industries, and geography. If LinkedIn is set up, check that the credentials are correct. Check the Activity tab to see what Harvey has been trying to do.</p>
        </Faq>
      </Section>
    </div>
  )
}
