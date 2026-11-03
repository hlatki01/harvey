"""Instagram sender: opens conversations with businesses and drains the outbox.

Same approval ladder as WhatsApp (harvey/agents/whatsapp.py). Each cycle:

  1. businesses the instagram discovery source found, that nobody contacted
     yet, get ONE short opening written (the WhatsApp writer, told it is
     Instagram) and staged, at most channels.instagram.max_cold_per_day a day.
     A business with a phone is left to WhatsApp first; Instagram takes it
     only once WhatsApp found no account on that number.
  2. approved, due DMs go out: replies right away, openings a few per cycle
     with long random gaps, inside business hours, the daily cap and the kill
     switch.

The account is a real one driven through instagrapi: the moment Instagram
pushes back (challenge, throttling, logout) the whole channel pauses and the
owner is told, instead of insisting. Settings → Integrations → Test resumes it.
"""

import asyncio
import logging
import random
from datetime import datetime, timezone

from harvey import notify
from harvey.agents.whatsapp import KILL_SWITCH_KEY, WhatsAppSender
from harvey.brain import BrainUnavailable
from harvey.config import HarveyConfig, closed_until
from harvey.gate import instagram_check
from harvey.integrations.instagram import InstagramBlocked, InstagramClient
from harvey.models.conversation import Message
from harvey.models.prospect import COMPANY_INSTAGRAM_SOURCE, Prospect
from harvey.state import StateManager

logger = logging.getLogger("harvey.instagram")

PAUSE_KEY = "instagram_paused"
MAX_COLD_PER_CYCLE = 2
COLD_JITTER_SECONDS = (60, 180)


def _now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


async def instagram_paused(state: StateManager) -> str:
    return await state.get_setting(PAUSE_KEY) or ""


async def pause_instagram(state: StateManager, config, reason: str):
    """Stop every Instagram request until a human resumes it (Settings → Test)."""
    if await instagram_paused(state):
        return
    await state.set_setting(PAUSE_KEY, f"{_now()[:16]} {reason}"[:300])
    logger.error(f"Instagram: PAUSED, Instagram pushed back ({reason}).")
    await state.log_action(action_type="instagram_paused", agent="instagram",
                           details={"reason": reason[:300]})
    await notify.push(state, config, "system", f"instagram_paused:{_now()[:13]}",
                      "Instagram pausado: o Instagram bloqueou ou pediu verificação da conta. "
                      f"Motivo: {reason[:150]}. Resolva no app e clique em Testar em Settings → Integrations.")


class InstagramSender:
    def __init__(self, state: StateManager, config: HarveyConfig, env,
                 client: InstagramClient | None = None, brain=None, send_pacing: bool = True):
        self.state = state
        self.config = config
        self.cfg = config.channels.instagram
        self.client = client or InstagramClient.from_env(env)
        self.brain = brain
        self.send_pacing = send_pacing
        # The WhatsApp writer drafts the openings: same language, opt-out and example sites.
        self.writer = WhatsAppSender(brain, state, config, env, client=self.client) if brain else None

    @property
    def is_active(self) -> bool:
        return self.cfg.enabled and self.client.is_configured()

    async def run(self, stage: bool = True):
        paused = await self.state.get_setting(KILL_SWITCH_KEY)
        if paused:
            logger.warning(f"Instagram: SENDING PAUSED ({paused}).")
            return
        if await instagram_paused(self.state):
            return
        try:
            if stage and self.writer and self.cfg.max_cold_per_day:
                await self._stage()
            if not closed_until(self.config):   # else approved items wait for the next opening
                await self._drain()
        except InstagramBlocked as e:
            await pause_instagram(self.state, self.config, str(e))
        except BrainUnavailable as e:
            logger.error(f"Instagram: staging stopped; the model is unavailable ({e}).")

    # ── staging ──

    async def _cold_today(self) -> int:
        """Openings sent today plus those already waiting: today's room is what's left."""
        n = await self.state.count_outbox_sent_today(kind="sequence", channel="instagram")
        for status in ("pending_review", "approved"):
            n += sum(1 for i in await self.state.get_outbox(status=status, channel="instagram", limit=500)
                     if i["kind"] == "sequence")
        return n

    async def _stage(self):
        want = min(MAX_COLD_PER_CYCLE, self.cfg.max_cold_per_day - await self._cold_today())
        if want <= 0:
            return
        self.writer.skills = (self.writer.brain.load_skills_for_agent("whatsapp")
                              + await self.state.lessons_block())
        staged = 0
        for company in await self.state.get_instagram_candidates(
                whatsapp_first=self.config.channels.whatsapp.enabled, limit=want * 3):
            if staged >= want:
                break
            if await self._stage_company(company):
                staged += 1
        if staged:
            mode = "awaiting your approval" if self.cfg.require_approval else "approved"
            logger.info(f"Instagram: staged {staged} opening(s) ({mode}). Review in the Outbox tab.")
            await self.state.log_action(action_type="stage_instagram", agent="instagram",
                                        details={"staged": staged, "mode": mode})

    async def _stage_company(self, company) -> bool:
        handle = company.external_id.split(":", 1)[1]
        prospect = Prospect(
            company_id=company.id, company=company.name, industry=company.industry,
            instagram_handle=handle, source=COMPANY_INSTAGRAM_SOURCE,
            source_url=company.source_url, status="new")
        try:
            prospect.instagram_id = await self.client.user_id_of(handle)
        except InstagramBlocked:
            raise
        except Exception as e:
            logger.info(f"Instagram: @{handle} can't be reached ({e}).")
            prospect.status = "no_instagram"
        prospect.id = await self.state.add_prospect(prospect)   # recorded either way: never retried
        if prospect.status != "new":
            return False
        body = await self.writer._draft(company, prospect, app="Instagram")
        wa = self.config.channels.whatsapp
        gate = instagram_check(prospect.instagram_id, body, prospect=prospect, kind="sequence",
                               opt_out_line=wa.opt_out_line, allowed_prices=wa.allowed_prices,
                               allowed_links=[d.url for d in wa.demos])
        if not body or not gate:
            logger.warning(f"Instagram: draft for {company.name} rejected: "
                           f"{gate.reasons if body else 'no draft'}")
            await self.state.update_prospect_status(prospect.id, "write_failed")
            return False
        item_id = await self.state.add_outbox_item(
            prospect_id=prospect.id, to_email=prospect.instagram_id, subject="Instagram", body=body,
            send_at=_now(), status="pending_review" if self.cfg.require_approval else "approved",
            provider=self.client.name, channel="instagram")
        if item_id:
            await self.state.queue_prospect_if_unsent(prospect.id)
        return bool(item_id)

    # ── sending ──

    async def _drain(self):
        budget = self.cfg.max_daily_sends - await self.state.count_outbox_sent_today(channel="instagram")
        if budget <= 0:
            logger.info("Instagram: daily cap reached.")
            return
        due = await self.state.get_outbox(status="approved", due_before=_now(),
                                          limit=budget, channel="instagram")
        replies = [i for i in due if i["kind"] != "sequence"]
        cold = [i for i in due if i["kind"] == "sequence"][:MAX_COLD_PER_CYCLE]
        for item in replies:
            await self._send(item)
        for n, item in enumerate(cold):
            if n and self.send_pacing:
                await asyncio.sleep(random.uniform(*COLD_JITTER_SECONDS))
            await self._send(item)

    async def _blocked(self, item: dict, prospect) -> tuple[str, str] | None:
        if prospect is None:
            return "failed", "prospect missing"
        if prospect.status == "opted_out" and item["body"] != _opt_out_line():
            return "cancelled", "prospect opted out"
        if item["kind"] == "sequence" and prospect.status not in ("new", "queued"):
            return "cancelled", f"prospect is '{prospect.status}': the opening is moot"
        wa = self.config.channels.whatsapp
        gate = instagram_check(item["to_email"], item["body"], prospect=prospect, kind=item["kind"],
                               opt_out_line=wa.opt_out_line, allowed_prices=wa.allowed_prices,
                               allowed_links=[d.url for d in wa.demos])
        if not gate:
            return "cancelled", "gate: " + "; ".join(gate.reasons)[:300]
        return None

    async def _send(self, item: dict):
        prospect = await self.state.get_prospect(item["prospect_id"])
        blocked = await self._blocked(item, prospect)
        if blocked:
            await self.state.update_outbox_item(item["id"], status=blocked[0], error=blocked[1])
            logger.warning(f"Instagram: not sending to {item['to_email']}: {blocked[1]}")
            return
        # Claim it: a reply may have cancelled it since the queue was read.
        if not await self.state.claim_outbox_item(item["id"]):
            return
        try:
            result = await self.client.send_text(item["to_email"], item["body"])
        except InstagramBlocked:
            # Instagram refused it: nothing went out, so it can go once resumed.
            await self.state.update_outbox_item(item["id"], status="approved")
            raise
        except Exception as e:
            # It may have gone out before the error: never retry it.
            await self.state.update_outbox_item(item["id"], status="failed", error=f"send error: {e}"[:300])
            return
        if not result.ok:
            await self.state.update_outbox_item(item["id"], status="failed", error=result.error[:300])
            return
        await self.state.update_outbox_item(item["id"], status="sent", sent_at=_now(),
                                            message_id=result.message_id, thread_ref=result.thread_ref)
        if item["kind"] == "sequence":
            await self.state.update_prospect_status(prospect.id, "contacted")
        convo = (await self.state.get_conversation(item["conversation_id"])
                 if item.get("conversation_id") else None)
        if convo:
            convo.thread.append(Message(sender="harvey", content=item["body"]))
            await self.state.update_conversation(convo.id, thread_json=convo.thread_json())
        await self.state.log_action(
            action_type="instagram_sent", agent="instagram",
            details={"to": item["to_email"], "kind": item["kind"], "message_id": result.message_id})
        logger.info(f"Instagram: sent {'opening' if item['kind'] == 'sequence' else 'reply'} "
                    f"to {item['to_email']}.")


def _opt_out_line() -> str:
    from harvey.agents.handler import WHATSAPP_OPT_OUT_CONFIRMATION
    return WHATSAPP_OPT_OUT_CONFIRMATION
