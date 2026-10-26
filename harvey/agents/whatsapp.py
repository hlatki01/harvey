"""WhatsApp sender — opens conversations with businesses and drains the outbox.

Same approval ladder as email: every message becomes an outbox row
(channel='whatsapp') that waits for a human unless require_approval is off.
The target is a business's own listed phone, so each cycle this agent

  1. picks businesses with a phone that were never tried (optionally only
     those carrying chosen signals, e.g. NO_WEBSITE),
  2. asks WhatsApp whether the number exists, and records the answer as a
     'company_phone' prospect either way so it is never retried,
  3. has the model write ONE short opening message grounded in what Harvey
     observed, and stages it,
  4. puts every opening in a campaign, like email: one 3-message sequence
     per kind of business, whose follow-ups 2 and 3 are staged behind the
     opening (openings sent before campaigns existed are adopted too),
  5. sends whatever is approved and due, a few at a time with long, random
     gaps between messages, each follow-up only after the one before it.

Replies are read by the Handler (run_whatsapp), which also cancels anything
still queued for someone who answered.
"""

import asyncio
import json
import logging
import random
from datetime import datetime, timedelta, timezone

from harvey.agents.sender import follow_up_order
from harvey.agents.writer import Writer
from harvey.brain import Brain, BrainUnavailable
from harvey.config import HarveyConfig
from harvey.decide import find_demo
from harvey.site_probe import find_own_site
from harvey.gate import chat_brand, whatsapp_check
from harvey.integrations.evolution import EvolutionClient
from harvey.models.campaign import Campaign
from harvey.models.prospect import COMPANY_PHONE_SOURCE, Prospect
from harvey.phone import looks_like_mobile, normalize_phone
from harvey.state import StateManager

logger = logging.getLogger("harvey.whatsapp")

KILL_SWITCH_KEY = "sending_paused"
WHATSAPP_SUBJECT = "WhatsApp"  # the Outbox desk shows and edits a subject
# Few and slow: a burst of cold messages is what gets a number banned.
MAX_SENDS_PER_CYCLE = 4
SEND_JITTER_SECONDS = (45, 150)
STOP_STATUSES = {"replied", "opted_out", "lost", "meeting", "closed"}
SENDABLE_STATUSES = {"new", "queued"}
# Who still gets follow-ups when an opening joins a campaign.
FOLLOW_UP_STATUSES = {"new", "queued", "contacted"}

# What each signal means for a message, in plain words the model may use.
SIGNAL_FACTS = {
    "NO_WEBSITE": "has no website",
    "UNCLAIMED_LISTING": "its Google Business Profile is unclaimed",
    "NO_ONLINE_BOOKING": "customers can't book online",
    "NO_SCHEMA_MARKUP": "its site is missing structured data for search",
    "BLOG_STALE": "its blog hasn't been updated in a long time",
    "RUNNING_GOOGLE_ADS": "it is running Google Ads",
    "RUNNING_META_ADS": "it is running Facebook/Instagram ads",
}


class WhatsAppSender:
    def __init__(self, brain: Brain, state: StateManager, config: HarveyConfig,
                 env, client: EvolutionClient | None = None, send_pacing: bool = True):
        self.brain = brain
        self.state = state
        self.config = config
        self.cfg = config.channels.whatsapp
        self.client = client or EvolutionClient.from_env(env)
        self.send_pacing = send_pacing
        self.skills = ""

    @property
    def is_active(self) -> bool:
        return self.cfg.enabled and self.client.is_configured()

    async def run(self):
        paused = await self.state.get_setting(KILL_SWITCH_KEY)
        if paused:
            logger.warning(f"WhatsApp: SENDING PAUSED ({paused}).")
            return
        stale = await self.state.fail_stale_sending_outbox()
        if stale:
            logger.warning(
                f"WhatsApp: {stale} message(s) were interrupted mid-send and won't be "
                "retried. Check the phone; they're under Didn't send.")
        self.skills = self.brain.load_skills_for_agent("whatsapp") + await self.state.lessons_block()
        try:
            await self._stage()
            await self._campaigns()
        except BrainUnavailable as e:
            logger.error(f"WhatsApp: staging stopped; the model is unavailable ({e}).")
        except Exception as e:
            logger.error(f"WhatsApp: staging failed: {e}")
        await self._drain()

    # ── staging ──────────────────────────────────────────────────────

    async def _queued_count(self) -> int:
        # Due now only: follow-ups scheduled for later days don't take today's room.
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        n = 0
        for status in ("pending_review", "approved"):
            n += len(await self.state.get_outbox(
                status=status, channel="whatsapp", due_before=now, limit=500))
        return n

    async def _stage(self):
        room = self.cfg.max_daily_sends - await self._queued_count()
        want = min(self.cfg.max_new_per_cycle, room)
        if want <= 0:
            return
        candidates = await self.state.get_whatsapp_candidates(
            self.cfg.require_signals, self.cfg.exclude_signals, limit=want * 3)
        staged = 0
        for company in candidates:
            if staged >= want:
                break
            if await self._stage_company(company):
                staged += 1
        if staged:
            mode = "awaiting your approval" if self.cfg.require_approval else "approved"
            logger.info(f"WhatsApp: staged {staged} message(s) ({mode}). Review in the Outbox tab.")
            await self.state.log_action(
                action_type="stage_whatsapp", agent="whatsapp",
                details={"staged": staged, "mode": mode})

    async def _record_prospect(self, company, phone: str, verified: bool, status: str) -> Prospect:
        prospect = Prospect(
            company_id=company.id, company=company.name, industry=company.industry,
            phone=phone, phone_verified=verified, source=COMPANY_PHONE_SOURCE,
            source_url=company.source_url, status=status,
        )
        prospect.id = await self.state.add_prospect(prospect)
        return prospect

    async def _stage_company(self, company) -> bool:
        phone = normalize_phone(company.phone, self.cfg.default_country_code)
        if not phone or not looks_like_mobile(phone):
            await self._record_prospect(company, "", False, "no_whatsapp")
            return False
        site = await find_own_site(company.name)
        if site:
            # The listing had no site, but the business does: not who we sell to.
            await self._record_prospect(company, "", False, "has_website")
            await self.state.log_action(
                action_type="site_found", agent="whatsapp",
                details={"company": company.name, "domain": site, "stage": "staging"})
            return False
        known = (await self.client.check_numbers([phone])).get(phone)
        if not known:
            await self._record_prospect(company, "", False, "no_whatsapp")
            return False
        if await self.state.get_prospect_by_phone(known):
            # Two businesses list the same number: only the first is contacted.
            await self._record_prospect(company, "", False, "duplicate")
            return False
        prospect = await self._record_prospect(company, known, True, "new")

        body = await self._draft(company, prospect)
        gate = whatsapp_check(known, body, prospect=prospect, opt_out_line=self.cfg.opt_out_line)
        if not body or not gate:
            logger.warning(f"WhatsApp: draft for {company.name} rejected: "
                           f"{gate.reasons if body else 'no draft'}")
            await self.state.update_prospect_status(prospect.id, "write_failed")
            return False

        item_id = await self.state.add_outbox_item(
            prospect_id=prospect.id, to_email=known, subject=WHATSAPP_SUBJECT, body=body,
            send_at=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            status="pending_review" if self.cfg.require_approval else "approved",
            provider=self.client.name, channel="whatsapp",
        )
        if item_id:
            await self.state.queue_prospect_if_unsent(prospect.id)
        return bool(item_id)

    async def _draft(self, company, prospect) -> str:
        signals = await self.state.company_signal_codes(company.id)
        facts = [f"- {SIGNAL_FACTS[s]}" for s in signals if s in SIGNAL_FACTS]
        persona, product = self.config.persona, self.config.product
        demo = find_demo(company.industry, self.cfg.demos)
        demo_rule = (f"\n- We already have a ready example site for a {demo.label} business: say so in a few words "
                     "(no link, no address); the link comes later if they want it." if demo else "")
        prompt = f"""You are {persona.name}, {persona.role} at {chat_brand(persona.company)}. Tone: {persona.tone}.
You sell: {product.name} - {product.description}

Write the FIRST WhatsApp message to the business below, in {self.cfg.language}.

Business: {company.name}
Type: {company.industry or "unknown"}
Place: {company.location or "unknown"}
Observed facts (use at most one; never invent others):
{chr(10).join(facts) or "- none"}

Rules:
- One message, at most 45 words, plain text, no links, no markdown.
- Say who you are in the first sentence. Mention one observed fact if there is one, otherwise ask what they do.
- End with one easy yes/no question. Do not pitch a price or a meeting.
- Sound like a person typing on a phone. No greeting formulas, no emojis, no sales words.
- Never mention a city or region, and never say what kind of business they are (the listed type can be wrong): say "pequenos negócios".
- Refer to the company without any web address (no ".com" or ".me").
- Do not add an opt-out line; the system adds it.{demo_rule}
{self.skills}

Write ONLY the message text."""
        text = await self.brain.think(
            prompt, session_id="harvey-whatsapp", agent="whatsapp", task="draft_opening")
        if not text:
            paused = getattr(self.brain, "paused_reason", "")
            if paused:
                raise BrainUnavailable(paused)
            return ""
        text = text.strip().strip('"')
        if text.lower().startswith(("i can't", "i cannot", "as an ai", "sorry,")):
            return ""
        return f"{text}\n\n{self.cfg.opt_out_line}".strip()

    # ── campaigns ────────────────────────────────────────────────────

    async def _campaigns(self):
        """Put every opening into a campaign, as the Writer does for email:
        one sequence per kind of business, its follow-ups staged behind the
        opening. Openings left out (written before campaigns, or the sequence
        couldn't be written yet) are picked up on a later cycle."""
        openers = await self.state.get_whatsapp_openers_without_campaign()
        if not openers:
            return
        campaigns = {c.name: c for c in await self.state.get_campaigns_by_status("active")
                     if c.channel == "whatsapp"}
        unwritten: set[str] = set()
        for item in openers:
            prospect = await self.state.get_prospect(item["prospect_id"])
            if prospect is None:
                continue
            name = f"{prospect.industry or 'general'}-whatsapp"
            campaign = campaigns.get(name)
            if campaign is None:
                if name in unwritten:
                    continue
                sequence = await self._write_sequence(prospect)
                if not sequence:
                    paused = getattr(self.brain, "paused_reason", "")
                    if paused:
                        raise BrainUnavailable(paused)
                    logger.warning(f"WhatsApp: could not write a sequence for '{name}'.")
                    unwritten.add(name)
                    continue
                campaign = Campaign(id="", name=name, channel="whatsapp",
                                    sequence=sequence, status="active")
                await self.state.add_campaign(campaign)
                campaigns[name] = campaign
                await self.state.log_action(
                    action_type="write_campaign", agent="whatsapp",
                    details={"campaign_id": campaign.id, "campaign_name": name,
                             "steps": len(sequence), "channel": "whatsapp"})
            # Follow-ups first: if this stops half-way, the opening is still
            # unassigned and the next cycle redoes it (the slots can't double).
            if prospect.status in FOLLOW_UP_STATUSES:
                await self._stage_follow_ups(campaign, prospect, item)
            if prospect.id not in campaign.prospect_ids:
                campaign.prospect_ids.append(prospect.id)
                await self.state.update_campaign(
                    campaign.id, prospect_ids_json=json.dumps(campaign.prospect_ids))
            await self.state.update_outbox_item(item["id"], campaign_id=campaign.id)

    async def _stage_follow_ups(self, campaign: Campaign, prospect, opener: dict):
        try:
            base = datetime.fromisoformat(str(opener.get("sent_at")))
        except (TypeError, ValueError):
            base = datetime.now(timezone.utc).replace(tzinfo=None)
        status = "pending_review" if self.cfg.require_approval else "approved"
        days = 0
        for step in campaign.sequence[1:]:
            days += max(0, step.delay_days)
            body = step.body.replace("{{company}}", prospect.company).replace(
                "{{ company }}", prospect.company).strip()
            await self.state.add_outbox_item(
                prospect_id=prospect.id, campaign_id=campaign.id, step=step.step,
                to_email=opener["to_email"], subject=WHATSAPP_SUBJECT,
                body=f"{body}\n\n{self.cfg.opt_out_line}".strip(),
                send_at=(base + timedelta(days=days)).isoformat(),
                status=status, provider=self.client.name, channel="whatsapp",
            )

    async def _write_sequence(self, prospect) -> list:
        """The campaign's 3 messages. Message 1 is only the campaign's
        template: each business's real opening is written for it alone."""
        persona, product = self.config.persona, self.config.product
        prompt = f"""You are {persona.name}, {persona.role} at {chat_brand(persona.company)}. Tone: {persona.tone}.
You sell: {product.name} - {product.description}

Write a 3-message WhatsApp sequence, in {self.cfg.language}, for small businesses like this one:
Type: {prospect.industry or "unknown"}

- Message 1: who you are + one easy yes/no question. At most 45 words.
- Message 2: 3 days later, if they didn't answer. A different angle, one concrete example. At most 35 words.
- Message 3: 4 days after that. A short, gracious last message. At most 25 words.
- Use {{{{company}}}} for the business's name; no other merge variables.
- Plain text, no links, no markdown, no emojis, no prices, no meetings.
- Sound like a person typing on a phone. Never mention a city or region.
- Do not add an opt-out line; the system adds it.
{self.skills}

Return ONLY a JSON array:
[{{"step": 1, "body": "...", "delay_days": 0}},
 {{"step": 2, "body": "...", "delay_days": 3}},
 {{"step": 3, "body": "...", "delay_days": 4}}]"""
        result = await self.brain.think_json(
            prompt, session_id="harvey-whatsapp", agent="whatsapp", task="write_sequence")
        if isinstance(result, list):
            for raw in result:
                if isinstance(raw, dict):
                    raw["subject"] = WHATSAPP_SUBJECT
        steps = Writer._parse_sequence(result)
        return steps if len(steps) >= 2 else []

    # ── sending ──────────────────────────────────────────────────────

    async def _blocked(self, item: dict, prospect) -> tuple[str, str] | None:
        """Why this message must not go out, as (status, error), or None."""
        if prospect is None:
            return "failed", "prospect missing"
        if item["kind"] == "sequence":
            if prospect.status in STOP_STATUSES:
                return "cancelled", f"prospect status '{prospect.status}'"
            if item["step"] == 1 and prospect.status == "contacted":
                return "cancelled", "already contacted"
        if item["kind"] == "sequence" and item["step"] == 1:
            # It may have been approved a while ago: look again before saying "no site".
            site = await find_own_site(prospect.company)
            if site:
                await self.state.log_action(
                    action_type="site_found", agent="whatsapp",
                    details={"company": prospect.company, "domain": site, "stage": "sending"})
                return "cancelled", f"the business has a website ({site})"
        gate = whatsapp_check(
            item["to_email"], item["body"], prospect=prospect, kind=item["kind"],
            opt_out_line=self.cfg.opt_out_line, allowed_prices=self.cfg.allowed_prices,
            allowed_links=[d.url for d in self.cfg.demos])
        if not gate:
            logger.warning(f"WhatsApp: gate blocked message to {item['to_email']}: {gate.reasons}")
            return "failed", "gate: " + "; ".join(gate.reasons)[:300]
        return None

    async def send_replies(self):
        """Send approved replies now (the live loop). Openings wait for the
        heartbeat, which also stages them and keeps quiet hours."""
        if not await self.state.get_setting(KILL_SWITCH_KEY):
            await self._drain(replies_only=True)

    async def _drain(self, replies_only: bool = False):
        if not self.client.is_configured():
            logger.warning("WhatsApp: Evolution API is not configured; the outbox is holding.")
            return
        sent_today = await self.state.count_outbox_sent_today(channel="whatsapp")
        budget = min(MAX_SENDS_PER_CYCLE, self.cfg.max_daily_sends - sent_today)
        if budget <= 0:
            logger.info(f"WhatsApp: daily cap reached ({sent_today}/{self.cfg.max_daily_sends}).")
            return
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        due = await self.state.get_outbox(
            status="approved", due_before=now, limit=budget * 3, channel="whatsapp",
            skip_unready_follow_ups=True)
        if replies_only:
            due = [i for i in due if i["kind"] == "reply"]
        sent = 0
        for item in due:
            if sent >= budget:
                break
            prospect = await self.state.get_prospect(item["prospect_id"])
            blocked = await self._blocked(item, prospect)
            if blocked:
                await self.state.update_outbox_item(item["id"], status=blocked[0], error=blocked[1])
                continue
            order = await follow_up_order(self.state, item)
            if order == "wait":
                continue
            if order:
                await self.state.update_outbox_item(item["id"], status=order[0], error=order[1])
                continue
            # Claim it: a reply may have cancelled it since the queue was read.
            if not await self.state.claim_outbox_item(item["id"]):
                continue
            item = await self.state.get_outbox_item(item["id"]) or item
            prospect = await self.state.get_prospect(item["prospect_id"])
            blocked = await self._blocked(item, prospect)
            if blocked:
                await self.state.update_outbox_item(item["id"], status=blocked[0], error=blocked[1])
                continue

            try:
                result = await self.client.send_text(item["to_email"], item["body"])
            except Exception as e:
                # It may have gone out before the error: never retry it.
                await self.state.update_outbox_item(
                    item["id"], status="failed", error=f"send error: {e}"[:300])
                logger.warning(f"WhatsApp: send to {item['to_email']} raised: {e}")
                continue
            if not result.ok:
                await self.state.update_outbox_item(
                    item["id"], status="failed", error=result.error[:300])
                continue

            sent += 1
            await self.state.update_outbox_item(
                item["id"], status="sent",
                sent_at=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                message_id=result.message_id, thread_ref=result.thread_ref)
            if prospect.status in ("new", "queued"):
                await self.state.update_prospect_status(prospect.id, "contacted")
            await self._record_reply_in_thread(item)
            await self.state.log_action(
                action_type="whatsapp_sent", agent="whatsapp",
                details={"to": item["to_email"], "kind": item["kind"], "step": item["step"],
                         "message_id": result.message_id})
            logger.info(f"WhatsApp: sent {item['kind']} to {item['to_email']}.")
            if self.send_pacing and not replies_only and sent < budget:
                await asyncio.sleep(random.uniform(*SEND_JITTER_SECONDS))

    async def _record_reply_in_thread(self, item: dict):
        """A sent reply belongs in its conversation thread."""
        if item["kind"] != "reply" or not item.get("conversation_id"):
            return
        try:
            from harvey.models.conversation import Message
            convo = await self.state.get_conversation(item["conversation_id"])
            if convo:
                convo.thread.append(Message(sender="harvey", content=item["body"]))
                await self.state.update_conversation(convo.id, thread_json=convo.thread_json())
        except Exception as e:
            logger.debug(f"WhatsApp: conversation append failed: {e}")
