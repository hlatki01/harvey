"""Handler — monitors replies and manages conversations.

Works against Instantly (legacy) or a native mail provider (Gmail/SMTP).
The native path also owns bounce handling: a bounce marks the address
invalid, cancels the prospect's queued sends, and — past a bounce-rate
threshold — flips the global kill switch so a bad list can't torch the
sending domain while nobody's watching.
"""

import asyncio
import html
import json
import logging
import re
from datetime import datetime, timezone

from harvey.brain import Brain, BrainUnavailable
from harvey.config import (
    DEFAULT_OPT_OUT_LINE, HarveyConfig, EnvConfig, compliance_of, legacy_sending_held,
)
from harvey.decide import (
    DEMO_TOKEN, SLOTS_TOKEN, Decision, apply_policy, find_demo, needs_review, parse_decision)
from harvey.gate import chat_brand
from harvey import notify
from harvey.humanize import humanize, humanizer_enabled
from harvey.integrations import calendar
from harvey.integrations.calendar import CalendarClient
from harvey.integrations.instantly import InstantlyClient
from harvey.integrations.evolution import EvolutionClient
from harvey.integrations.mail_provider import (
    NATIVE_PROVIDERS, get_mail_provider, strip_quoted_reply,
)
from harvey.models.conversation import Conversation, Message
from harvey.models.prospect import INBOUND_EMAIL_SOURCE, INBOUND_WHATSAPP_SOURCE, Prospect
from harvey.state import StateManager

logger = logging.getLogger("harvey.handler")

KILL_SWITCH_KEY = "sending_paused"
BOUNCE_COUNT_KEY = "bounce_count"
# Kill switch only engages after this many sends (a tiny sample lies).
MIN_SENDS_FOR_KILL_SWITCH = 10

INTENT_LABELS = {
    "interested",
    "objection",
    "not_interested",
    "ooo",
    "wrong_person",
    "question",
    "unsubscribe",
    "escalate",
}

# Intents that get an automated reply. Everything else is closed,
# deferred, or escalated to a human.
AUTO_REPLY_INTENTS = {"interested", "objection", "question", "wrong_person"}

# What the WhatsApp assistant may and may not do. It answers from the
# knowledge base and nothing else; what it cannot ground there goes to the
# team. The decision engine (harvey/decide.py) checks every proposal against
# rules that do not depend on the model. (The email rules further down ask
# for a call, which is wrong for a chat with an assistant.)
INBOUND_ORIGIN_NOTE = (
    "This person wrote to us first and we know nothing about them. Introduce yourself in a few words "
    "(you are the virtual assistant of the company). If the message is not a business inquiry "
    "(personal, a wrong number, spam), use wait and do not answer.\n")
WHATSAPP_AUTO_NOTE = (
    'An automatic message from a business (a bot, a menu, opening hours, ordering instructions, '
    '"acesse", "digite 1", "mensagem automática") is "ooo", even when it contains a link or a question. '
    'So is a canned greeting that answers our first message as if we were a customer who found them: '
    'it welcomes us, thanks us for the contact, mentions their ad, gives opening hours, says someone '
    'will answer soon, or asks our name or "como posso ajudar" without reacting to anything we said. '
    'An attendant\'s name in it ("Aqui é a Pamela") changes nothing: receptionists use the same script. '
    'Only a message that responds to what we actually wrote (our offer, sites, our question) is from a person.\n')
WHATSAPP_OPT_OUT_CONFIRMATION = "Tudo certo, não vou mais te mandar mensagens. Obrigado pela atenção!"
NO_TIMES_RULE = "- NEVER offer to phone them or propose days or times: you cannot call or schedule"
# With Google Calendar on (integrations/calendar.py) the assistant offers times instead.
BOOKING_RULE = (f"- When they want to meet or talk, use answer and offer a video call: write {SLOTS_TOKEN} "
                "where the free times go (the system lists them); never write a day or time yourself, "
                "never offer to phone them")
INTENTS_THAT_CLOSE = ("unsubscribe", "escalate", "not_interested")

WHATSAPP_DECISION_RULES = f"""
Choose ONE action for the next message:
- answer: the knowledge base covers what they asked
- qualify: ask ONE question to learn what their business needs (only if you do not know yet)
- send_demo: show the example site of their kind of business (only when one is available and not yet sent); write {DEMO_TOKEN} where the link goes, never a URL yourself
- handoff: a person must take over: they want to hire, start, meet or talk to someone; or the knowledge base does not cover the question; or it needs a decision. The reply is a statement, never a question, saying "nossa equipe" (our team) will talk to them right here in this chat; never name a person
- wait: nothing needs an answer (a thanks, an "ok", an emoji)

Rules for the reply:
- You are an automated virtual assistant answering in a chat: one to three short sentences, plain text, no greeting or signature
- Answer ONLY from the knowledge base; never invent a service, price, deadline, discount, feature or technology
- State prices only exactly as the knowledge base gives them
- Answer what they asked first, then at most one question
{NO_TIMES_RULE}
- A demo is an example, never something built for a real client
- If they ask whether you are a person or a bot, say you are a virtual assistant
- Refer to the company without any web address (no ".com" or ".me")

Answer with ONLY this JSON:
{{"action": "answer|qualify|send_demo|handoff|wait", "reply": "...", "confidence": "high|medium|low", "reason": "one short sentence", "industry": "their kind of business, only if they just said it, else empty", "sender": "person|automatic|unclear", "intent": "interested|objection|not_interested|unsubscribe|escalate|ooo|wrong_person|question"}}
intent: what their last message is. "unsubscribe" when they ask to stop being contacted; "escalate" when angry or threatening; "ooo" for any automatic message; "not_interested" for a clear no
sender: "automatic" when the last message is a business bot, menu, away or ordering message; "person" when someone typed it; "unclear" when you cannot tell
{WHATSAPP_AUTO_NOTE}When it is automatic, sender is "automatic" and intent is "ooo".
Use confidence low when you are guessing."""


# Hard opt-out phrases. If any of these appear, we ALWAYS honor the
# opt-out regardless of what the LLM classifier says.
OPT_OUT_PATTERNS = (
    "unsubscribe",
    "opt out",
    "opt-out",
    "remove me",
    "take me off",
    "stop emailing",
    "stop contacting",
    "stop sending",
    "do not contact",
    "do not email",
    "don't contact",
    "don't email",
    "never contact",
    "delete my info",
    "delete my data",
    # Portuguese (WhatsApp)
    "descadastrar",
    "sair da lista",
    "me tire da lista",
    "me remova",
    "remover meu número",
    "remover meu numero",
    "não quero receber",
    "nao quero receber",
    "não me envie",
    "nao me envie",
    "não me mande",
    "nao me mande",
    "pare de enviar",
    "pare de mandar",
    "parem de enviar",
    "parem de mandar",
    "pare de me enviar",
    "pare de me mandar",
)
# "No thanks" (what cold email's footer asks for) as a phrase: not "no
# Thanksgiving", "Bruno / Thanks," or "yes or no, thanks".
# Checked one line at a time: "...the answer is no / Thanks!" is a sign-off.
NO_THANKS_RE = re.compile(
    r"(?<!\bor )\bno,?[ \t]+thank(?:s|[ \t]+you)\b(?![ \t]+(?:needed|necessary|required))")
# A reply that says only this (signature aside) is an opt-out ("Reply STOP").
BARE_OPT_OUTS = {"stop", "unsubscribe", "remove", "remove me", "opt out",
                 "no thanks", "no thank you",
                 "sair", "pare", "parar", "remover", "descadastrar"}
_MOBILE_SIGNATURE_RE = re.compile(
    r"^[ \t]*(?:sent from my \w[^\n]*|get outlook for \w[^\n]*|sent from \w+ mail[^\n]*)$",
    re.IGNORECASE | re.MULTILINE)
# A line of their reply at least this long that repeats what Harvey wrote
# for this person is our text, not theirs. Shorter unmarked lines are always
# theirs, so a reply of "No thanks" or "STOP" is never taken for a quote.
OWN_TEXT_MIN_CHARS = 20
# Outlook's plain-text quotes add "<mailto:...>" / "<https://...>" after links.
_LINK_ANNOTATION_RE = re.compile(r"<(?:mailto:|https?://)[^>\s]*>", re.IGNORECASE)
_MERGE_TAG_RE = re.compile(r"\{\{\s*\w+\s*\}\}")


def _flat(text: str) -> str:
    """Lowercase, with quote markers, HTML entities, link annotations, curly
    quotes and spacing evened out, so text compares however a mail client
    re-quoted it."""
    text = _LINK_ANNOTATION_RE.sub("", html.unescape(text or ""))
    text = re.sub(r"^[ \t>]+", "", text, flags=re.MULTILINE)
    text = (text.replace("\u201c", '"').replace("\u201d", '"')
            .replace("\u2018", "'").replace("\u2019", "'"))
    return " ".join(text.lower().split())


class OurText:
    """What Harvey wrote for one person, to tell their words from our quote."""

    def __init__(self, texts):
        self.lines = set()
        for text in texts:
            # Merge tags weren't filled in templates: compare the parts around them.
            for part in _MERGE_TAG_RE.split(text or ""):
                for line in part.split("\n"):
                    line = _flat(line)
                    if len(line) >= OWN_TEXT_MIN_CHARS:
                        self.lines.add(line)
        self.joined = "\n".join(sorted(self.lines))

    def _is_ours(self, line: str) -> bool:
        flat = _flat(line)
        if not flat or flat not in self.joined:
            return False
        # A ">" line is quoted anyway; an unmarked one must be long enough
        # that it can't be their own short "No thanks".
        return line.lstrip().startswith(">") or len(flat) >= OWN_TEXT_MIN_CHARS

    def remove(self, text: str) -> str:
        """Their reply without our text: lines that repeat ours are dropped,
        and any of our lines inside a longer line (an HTML reply flattened to
        one line) is cut out of it."""
        kept = []
        for line in (text or "").split("\n"):
            if self._is_ours(line):
                continue
            flat = _flat(line)
            hits = [ours for ours in self.lines if ours in flat]
            if hits:
                for ours in sorted(hits, key=len, reverse=True):
                    flat = flat.replace(ours, " ")
                line = " ".join(flat.split())
            kept.append(line)
        return "\n".join(kept)


def _is_opt_out(their_text: str, own_words: str) -> bool:
    """their_text: everything they wrote, our text removed. own_words: their
    reply without the quote. Longer phrases match across a line wrap; "no
    thanks" only within a line."""
    if any(p in _flat(their_text) for p in OPT_OUT_PATTERNS):
        return True
    if any(NO_THANKS_RE.search(_flat(line)) for line in their_text.split("\n")):
        return True
    bare = _flat(_MOBILE_SIGNATURE_RE.sub("", own_words or ""))
    return re.sub(r"[^\w\s-]", "", bare).strip() in BARE_OPT_OUTS

# Phrases that mean a human must take over. Never auto-reply to these.
ESCALATION_PATTERNS = (
    "lawyer",
    "attorney",
    "legal action",
    "lawsuit",
    "cease and desist",
    "harassment",
    "harassing",
    "report you",
    "reporting you",
    "spam complaint",
    "ftc",
    "gdpr",
    "can-spam",
    "blacklist",
    # Portuguese (WhatsApp)
    "procon",
    "advogado",
    "processar",
    "lgpd",
    "anpd",
    "denunciar",
    "denúncia",
    "denuncia",
    "golpe",
    "como conseguiu",
    "onde conseguiu",
    "como você conseguiu",
)


class Handler:
    def __init__(
        self,
        brain: Brain,
        state: StateManager,
        config: HarveyConfig,
        env: EnvConfig,
    ):
        self.brain = brain
        self.state = state
        self.config = config
        self.instantly = InstantlyClient(env.instantly_api_key)
        self.provider = get_mail_provider(config, env)
        whatsapp = getattr(config.channels, "whatsapp", None)
        self.whatsapp = (EvolutionClient.from_env(env)
                         if getattr(whatsapp, "enabled", False) else None)
        self.calendar = CalendarClient(config, env)
        self.skills = ""
        self._whatsapp_lock = asyncio.Lock()

    @property
    def is_native(self) -> bool:
        return (
            self.config.channels.email.provider in NATIVE_PROVIDERS
            and self.provider is not None
        )

    async def run(self):
        """Check for new replies and handle them.

        Returns True only if every reply could be fetched and handled. The
        heartbeat sends nothing in a cycle where this isn't True, because a
        reply that wasn't read can't have cancelled its queued follow-ups.
        """
        logger.info("Handler: Checking for replies...")

        # Load foundational skills for this agent
        self.skills = self.brain.load_skills_for_agent("handler") + await self.state.lessons_block()

        if not self.config.channels.email.enabled:
            return True

        if self.is_native:
            return await self._run_native()

        # 1. Fetch active campaigns
        active_campaigns = await self.state.get_campaigns_by_status("active")
        if not active_campaigns:
            logger.info("Handler: No active campaigns to monitor.")
            return True

        total_handled = 0
        complete = True

        for campaign in active_campaigns:
            if not campaign.instantly_campaign_id:
                continue

            # 2. Get replies from Instantly
            try:
                replies = await self.instantly.get_replies(campaign.instantly_campaign_id)
            except Exception as e:
                logger.error(f"Handler: Failed to fetch replies for {campaign.name}: {e}")
                complete = False
                continue
            if not replies:
                continue

            for reply in replies:
                if not isinstance(reply, dict):
                    continue
                try:
                    handled = await self._handle_reply(reply, campaign)
                    if handled:
                        total_handled += 1
                except BrainUnavailable as e:
                    logger.error(f"Handler: stopping; replies are left for later ({e}).")
                    return False
                except Exception as e:
                    logger.error(f"Handler: Error processing reply: {e}")
                    complete = False

        if total_handled:
            logger.info(f"Handler: Processed {total_handled} replies.")
        else:
            logger.info("Handler: No new replies.")
        return complete

    async def _handle_reply(self, reply: dict, campaign) -> bool:
        """Process a single reply. Returns True if a new reply was handled."""
        lead_email = (reply.get("lead_email") or reply.get("from_email") or "").strip().lower()
        reply_text = (reply.get("body") or reply.get("text") or "").strip()
        reply_uuid = str(reply.get("uuid") or reply.get("id") or "")

        if not lead_email or not reply_text:
            return False

        # Dedup: skip if we already processed this reply
        if reply_uuid and await self.state.is_reply_processed(reply_uuid):
            logger.debug(f"Handler: Reply {reply_uuid} already processed. Skipping.")
            return False

        logger.info(f"Handler: Reply from {lead_email}")

        consumed = True
        try:
            await self._process_reply(lead_email, reply_text, reply_uuid, campaign.id)
        except BrainUnavailable:
            # Nothing was decided or recorded about this reply yet, so leave
            # it for the next cycle instead of losing it to a provider outage.
            consumed = False
            raise
        finally:
            # Otherwise ALWAYS mark processed — even on early exits (opt-out,
            # OOO, unknown prospect) — so the same reply is never re-handled
            # or double-replied on the next cycle.
            if reply_uuid and consumed:
                await self.state.mark_reply_processed(reply_uuid)
        return True

    async def _our_text(self, prospect) -> OurText:
        """Everything a reply from this person might quote back from us: what
        Harvey wrote for them (footers as sent) and today's footer."""
        compliance = compliance_of(self.config)
        return OurText([
            *await self.state.get_texts_for_prospect(prospect.id),
            *await self.state.get_footer_history(),
            compliance.postal_address, compliance.opt_out_line, DEFAULT_OPT_OUT_LINE,
        ])

    async def _process_reply(
        self,
        lead_email: str,
        reply_text: str,
        reply_uuid: str,
        campaign_id: str = "",
        reply_meta: dict | None = None,
        prospect=None,
        channel: str = "email",
    ):
        """Classify, record, and respond to a single reply.

        lead_email is the sender's address; for another channel it is just a
        label for the logs, and the caller passes the prospect it matched.
        """
        if prospect is None:
            # Find the prospect by email (indexed lookup)
            prospect = await self.state.get_prospect_by_email(lead_email)
        if not prospect:
            logger.warning(f"Handler: No prospect found for {lead_email}")
            return

        # Their words, without what we sent them. The keyword checks below
        # read every line they wrote, even under or between quoted lines;
        # the model, the stored thread and the answer read their reply
        # without the quoted email. Either way, a line repeating what we
        # sent (our footer says "no thanks") is ours, not theirs.
        ours = await self._our_text(prospect)
        their_text = ours.remove(reply_text)
        keyword_text = _flat(their_text)
        stripped = strip_quoted_reply(reply_text) or reply_text
        reply_text = ours.remove(stripped).strip() or stripped

        # Update prospect status + stop-on-reply: a human answered, so every
        # queued sequence email for them is now wrong to send.
        await self.state.update_prospect_status(prospect.id, "replied")
        try:
            cancelled = await self.state.cancel_pending_outbox_for_prospect(prospect.id)
            if cancelled:
                logger.info(
                    f"Handler: cancelled {cancelled} queued email(s) for "
                    f"{lead_email} (they replied)."
                )
        except Exception as e:
            logger.debug(f"Handler: outbox cancel failed: {e}")

        # 1. Classify intent. Hard keyword checks run FIRST and override
        # the LLM — opt-outs and legal threats must never be missed.
        if _is_opt_out(their_text, reply_text):
            intent = "unsubscribe"
        elif any(p in keyword_text for p in ESCALATION_PATTERNS):
            intent = "escalate"
        elif channel == "whatsapp":
            intent = ""   # one model call decides intent and answer together, below
        else:
            intent = await self._classify_intent(reply_text, prospect, channel)
        if intent:
            logger.info(f"Handler: Intent for {lead_email}: {intent}")

        # 2. The conversation as it will be, in memory only. Nothing about
        # this reply is stored until every model call it needs has succeeded:
        # if the provider goes down part-way (BrainUnavailable), the reply is
        # left unprocessed and the retry starts from the same stored state,
        # so the thread never gets the message twice or its stage advanced
        # twice. (Stop-on-reply above is idempotent and stays first.)
        existing_convos = await self.state.get_conversations_by_status("open")
        stored = next(
            (c for c in existing_convos if c.prospect_id == prospect.id), None
        )
        # A WhatsApp chat handed to a human stays theirs: the person's next
        # messages join the same thread and Harvey does not answer them.
        human_turn = False
        if stored is None and channel == "whatsapp":
            stored = next(
                (c for c in await self.state.get_conversations_by_status("needs_human")
                 if c.prospect_id == prospect.id and c.channel == "whatsapp"), None)
            human_turn = stored is not None
        if stored is not None:
            convo = stored.model_copy(deep=True)
        else:
            convo = Conversation(
                id="",
                prospect_id=prospect.id,
                campaign_id=campaign_id,
                channel=channel,
                intent=intent,
                status="open",
            )
        convo.thread.append(Message(sender="prospect", content=reply_text))
        old_stage = convo.stage

        # 2a. They may be taking one of the meeting times we offered.
        slot = None
        if convo.offered_slots and not human_turn and intent not in INTENTS_THAT_CLOSE:
            slot = await self._accepted_slot(reply_text, convo)

        # 2b. Draft the reply now, while nothing is stored yet. A held
        # Instantly setup can't send it, so don't spend a model call on it.
        held = (channel == "email" and not self.is_native
                and legacy_sending_held(self.config))
        response = ""
        decision: Decision | None = None
        meeting = None
        if slot is not None:
            # Booked before anything is stored: no model call follows that could fail.
            meeting = await self._book(prospect, convo, slot)
            intent = intent or "interested"
            response = self._booked_text(prospect, convo, slot, meeting) if meeting else ""
            if channel == "whatsapp":
                decision = Decision(action="answer", reply=response, intent=intent,
                                    reason="booked a meeting" if meeting else "booking failed")
        elif not intent:
            # WhatsApp: the decision engine reads the message once and returns
            # what it is (intent) together with what to do about it.
            if human_turn:
                intent = "question"          # theirs to answer; Harvey stays out
            else:
                decision = await self._decide_whatsapp("", reply_text, prospect, convo)
                intent, response = decision.intent, decision.reply
            logger.info(f"Handler: Intent for {lead_email}: {intent}")
        convo.intent = intent
        convo.stage = "closed_won" if meeting else self._determine_stage(intent, convo.stage, reply_text)
        if (decision is None and slot is None and intent in AUTO_REPLY_INTENTS
                and not held and not human_turn):
            if channel == "whatsapp":
                decision = await self._decide_whatsapp(intent, reply_text, prospect, convo)
                response = decision.reply
            else:
                response = await self._generate_response(intent, reply_text, prospect, convo)

        # 2c. Store the conversation in one write.
        if stored is None:
            convo.id = await self.state.add_conversation(convo)
        else:
            await self.state.update_conversation(
                convo.id,
                thread_json=convo.thread_json(),
                intent=intent,
                stage=convo.stage,
                offered_slots=convo.offered_slots,
            )
        if convo.stage != old_stage:
            logger.info(f"Handler: Stage for {lead_email}: {old_stage} -> {convo.stage}")
            await notify.on_stage_change(self.state, self.config, prospect, convo, old_stage,
                                         reply_text, self.whatsapp)
        if meeting:
            await self._booked(prospect, convo, slot, meeting)

        if human_turn and intent in AUTO_REPLY_INTENTS:
            await self.state.log_action(
                action_type="human_turn", agent="handler",
                details={"prospect_phone": prospect.phone, "reply_preview": reply_text[:200]})
            logger.info(f"Handler: {lead_email} wrote again; the chat is with a human, no auto-reply.")
            return

        # 3. Route based on intent
        if intent == "unsubscribe":
            # Honor opt-outs ALWAYS. No reply, no future contact.
            await self._close_lost(prospect, convo, reply_text)
            await self.state.update_prospect_status(prospect.id, "opted_out")
            await self.state.log_action(
                action_type="opt_out",
                agent="handler",
                details={"prospect_email": lead_email},
            )
            if channel == "whatsapp":
                # One fixed line so they know it worked; never a model's words.
                await self._queue_whatsapp_reply(
                    WHATSAPP_OPT_OUT_CONFIRMATION, prospect, convo, intent, action="wait",
                    force_auto=True)
            logger.info(f"Handler: {lead_email} opted out. Suppressed permanently.")
            return

        if intent == "escalate":
            # Angry / legal / compliance replies go to a human. Never auto-reply.
            await self._hand_over(prospect, convo, reply_text)
            await self.state.log_action(
                action_type="escalation",
                agent="handler",
                details={
                    "prospect_email": lead_email,
                    "reply_preview": reply_text[:200],
                },
            )
            logger.warning(
                f"Handler: ESCALATED reply from {lead_email} — needs human review. No auto-reply sent."
            )
            return

        if intent == "not_interested":
            await self._close_lost(prospect, convo, reply_text)
            await self.state.update_prospect_status(prospect.id, "lost")
            logger.info(f"Handler: {lead_email} not interested. Closing. One no is enough.")
            return

        if intent == "ooo":
            logger.info(f"Handler: {lead_email} is OOO. Will follow up later.")
            if channel == "whatsapp" and decision is not None:
                await self.state.log_action(
                    action_type="decision", agent="handler",
                    details={"prospect_phone": prospect.phone, "action": decision.action,
                             "confidence": decision.confidence, "reason": decision.reason,
                             "overridden": decision.overridden, "reply_preview": reply_text[:200]})
            return

        if intent not in AUTO_REPLY_INTENTS:
            logger.warning(f"Handler: No auto-reply policy for intent '{intent}'. Skipping reply.")
            return

        if held:
            # Instantly has no approval queue: a human answers this one.
            await self._hand_over(prospect, convo, reply_text)
            await self.state.log_action(
                action_type="escalation", agent="handler",
                details={"prospect_email": lead_email, "reason": "instantly_approval_required",
                         "reply_preview": reply_text[:200]},
            )
            logger.warning(
                f"Handler: reply from {lead_email} held for a human: Instantly can't "
                "queue replies for approval while require_approval is true."
            )
            return

        if decision is not None and decision.action == "wait":
            await self.state.log_action(
                action_type="decision", agent="handler",
                details={"prospect_phone": prospect.phone, "action": "wait",
                         "reason": decision.reason, "reply_preview": reply_text[:200]})
            logger.info(f"Handler: {lead_email} needs no answer ({decision.reason or 'wait'}).")
            return

        # For interested, objection, question, wrong_person — the reply drafted above
        if not response:
            # No usable draft from a working provider: hand it to a human
            # rather than leave an answered-looking thread with no reply.
            await self._hand_over(prospect, convo, reply_text)
            await self.state.log_action(
                action_type="escalation",
                agent="handler",
                details={"prospect_email": lead_email, "reason": "no usable reply draft",
                         "reply_preview": reply_text[:200]},
            )
            logger.warning(
                f"Handler: Could not generate a response for {lead_email}. "
                "Flagged for human review."
            )
            return

        # Send the response — natively through the outbox approval ladder,
        # or immediately via Instantly (legacy).
        if channel == "whatsapp":
            await self._queue_whatsapp_reply(response, prospect, convo, intent, decision.action)
            await self.state.log_action(
                action_type="decision", agent="handler",
                details={"prospect_phone": prospect.phone, "action": decision.action,
                         "confidence": decision.confidence, "reason": decision.reason,
                         "overridden": decision.overridden, "reply_preview": reply_text[:200]})
            if decision.action == "handoff":
                # The knowledge base ran out, or they want a person: put the
                # chat on the dashboard's "needs you" list and stop answering.
                await self._hand_over(prospect, convo, reply_text)
                await self.state.log_action(
                    action_type="handoff", agent="handler",
                    details={"prospect_phone": prospect.phone, "intent": intent,
                             "reason": decision.overridden or decision.reason,
                             "reply_preview": reply_text[:200]})
                logger.warning(f"Handler: {prospect.phone} is ready for a human; chat handed over.")
        elif self.is_native:
            await self._queue_native_reply(
                response, prospect, convo, reply_meta or {}, intent
            )
        elif reply_uuid:
            result = await self.instantly.send_reply(reply_uuid, response)
            if result is not None:
                # Add our response to conversation
                convo.thread.append(Message(sender="harvey", content=response))
                await self.state.update_conversation(
                    convo.id,
                    thread_json=convo.thread_json(),
                )
                logger.info(f"Handler: Replied to {lead_email}")

                await self.state.log_action(
                    action_type="reply",
                    agent="handler",
                    details={
                        "prospect_email": lead_email,
                        "intent": intent,
                        "response_preview": response[:100],
                    },
                )

    # ── Native provider path (Gmail / SMTP) ──

    async def _run_native(self) -> bool:
        """Poll the mailbox, split bounces from human replies, handle both.
        Returns True only if the mailbox was read and every message handled."""
        if not self.provider.is_configured():
            logger.debug(
                f"Handler: provider '{self.provider.name}' not configured yet."
            )
            return True  # nothing to read; the sender can't send through it either

        try:
            inbound = await self.provider.get_replies()
        except Exception as e:
            logger.error(f"Handler: fetching replies failed: {e}")
            return False

        handled = 0
        complete = True
        for msg in inbound:
            dedup_key = msg.provider_id or msg.message_id
            if not dedup_key or await self.state.is_reply_processed(dedup_key):
                continue
            consumed = True
            try:
                if msg.is_bounce:
                    await self._handle_bounce(msg)
                elif msg.body.strip():
                    await self._adopt_inbound_email(msg)
                    await self._process_reply(
                        msg.from_email,
                        msg.body.strip(),
                        reply_uuid="",
                        reply_meta={
                            "thread_ref": msg.thread_ref,
                            "message_id": msg.message_id,
                            "subject": msg.subject,
                        },
                    )
                handled += 1
            except BrainUnavailable as e:
                consumed = False
                complete = False
                logger.error(f"Handler: stopping; replies are left for later ({e}).")
                break
            except Exception as e:
                logger.error(f"Handler: error processing {msg.from_email}: {e}")
                complete = False
            finally:
                if consumed:
                    await self.state.mark_reply_processed(dedup_key)

        if handled:
            logger.info(f"Handler: processed {handled} inbound message(s).")
        else:
            logger.info("Handler: no new replies.")
        return complete

    async def _handle_bounce(self, msg):
        """A bounce is a data bug AND a reputation threat. Fix both."""
        outbox_item = await self.state.find_outbox_by_message_id(msg.in_reply_to)
        prospect = None
        if outbox_item:
            prospect = await self.state.get_prospect(outbox_item["prospect_id"])

        if prospect:
            await self.state.update_prospect_email(
                prospect.id, prospect.email, "invalid"
            )
            cancelled = await self.state.cancel_pending_outbox_for_prospect(
                prospect.id, reason="bounced"
            )
            logger.warning(
                f"Handler: BOUNCE for {prospect.email} — marked invalid, "
                f"cancelled {cancelled} queued email(s)."
            )
        else:
            logger.warning(
                f"Handler: bounce received ({msg.subject[:60]}) but couldn't "
                "match it to a sent email."
            )

        bounces = await self.state.increment_setting(BOUNCE_COUNT_KEY)
        total_sent = await self.state.count_outbox_sent()
        await self.state.log_action(
            action_type="bounce",
            agent="handler",
            details={"prospect": prospect.email if prospect else "unknown",
                     "bounces": bounces, "total_sent": total_sent},
        )

        max_rate = getattr(self.config.channels.email, "max_bounce_rate", 0.05)
        if (
            max_rate > 0
            and total_sent >= MIN_SENDS_FOR_KILL_SWITCH
            and bounces / total_sent > max_rate
        ):
            reason = (
                f"bounce rate {bounces}/{total_sent} exceeded "
                f"{max_rate:.0%} — check list quality before resuming"
            )
            await self.state.set_setting(KILL_SWITCH_KEY, reason)
            logger.error(f"Handler: KILL SWITCH ENGAGED — {reason}")

    async def run_whatsapp(self) -> bool:
        """Read WhatsApp replies and handle each one like an email reply.
        Returns True only if every message was read and handled. One run at a
        time: the live loop and the heartbeat both call this."""
        async with self._whatsapp_lock:
            return await self._run_whatsapp()

    async def _adopt_inbound_email(self, msg):
        """Mail addressed to the persona's own address, from someone we never
        contacted, becomes a lead. Only that address: the mailbox also holds
        invoices, newsletters and personal mail, which must not get a reply.
        The reply still goes through the outbox approval ladder."""
        cfg = getattr(self.config.channels.email, "inbound_leads", False)
        persona = self.config.persona.email.strip().lower()
        h = {k.lower(): v.lower() for k, v in (msg.headers or {}).items()}
        addressed = persona and persona in " ".join(
            h.get(k, "") for k in ("to", "delivered-to", "x-original-to"))
        automated = (
            any(w in msg.from_email for w in ("noreply", "no-reply", "donotreply", "do-not-reply", "naoresponda", "nao-responda"))
            or h.get("auto-submitted", "no") != "no" or h.get("precedence") in ("bulk", "list", "junk")
            or "list-unsubscribe" in h)
        if not (cfg and addressed) or automated or await self.state.get_prospect_by_email(msg.from_email):
            return
        from email.utils import parseaddr
        raw_from = next((v for k, v in (msg.headers or {}).items() if k.lower() == "from"), "")
        name = parseaddr(raw_from)[0]
        prospect = Prospect(
            first_name=name.split()[0] if name else "", last_name=" ".join(name.split()[1:]),
            company=name, email=msg.from_email, email_status="verified", email_verified=True,
            source=INBOUND_EMAIL_SOURCE, status="new")
        await self.state.add_prospect(prospect)
        await self.state.log_action(
            action_type="inbound_lead", agent="handler", details={"email": msg.from_email})
        logger.info(f"Handler: new inbound lead from {msg.from_email}.")

    async def _adopt_inbound_lead(self, msg):
        """A number we never contacted wrote first: make it a lead, if it is a
        stranger (this account never wrote to it) and the message is fresh.
        Friends and customers already in the owner's chats are left alone."""
        cfg = self.config.channels.whatsapp
        if not cfg.inbound_leads:
            return None
        try:
            age = datetime.now(timezone.utc).timestamp() - float(msg.date)
        except ValueError:
            age = 0
        if age > cfg.inbound_max_age_minutes * 60 or not await self.whatsapp.is_new_contact(
                msg.phone, await self.state.get_whatsapp_sent_ids()):
            return None
        name = msg.push_name.strip()
        prospect = Prospect(
            first_name=name.split()[0] if name else "", last_name=" ".join(name.split()[1:]),
            company=name, phone=msg.phone, phone_verified=True,
            source=INBOUND_WHATSAPP_SOURCE, status="new")
        prospect.id = await self.state.add_prospect(prospect)
        await self.state.log_action(
            action_type="inbound_lead", agent="handler",
            details={"phone": msg.phone, "name": name})
        logger.info(f"Handler: new inbound lead from {msg.phone}.")
        return prospect

    async def _run_whatsapp(self) -> bool:
        if self.whatsapp is None or not self.whatsapp.is_configured():
            return True
        if not self.skills:
            self.skills = self.brain.load_skills_for_agent("handler")
        try:
            inbound = await self.whatsapp.get_replies()
        except Exception as e:
            logger.error(f"Handler: fetching WhatsApp replies failed: {e}")
            return False

        handled = 0
        complete = True
        for msg in inbound:
            dedup_key = f"wa:{msg.provider_id}"
            if notify.is_owner(self.config, msg.phone):
                continue   # the owner answering a push is not a lead
            if await self.state.is_reply_processed(dedup_key):
                continue
            consumed = True
            try:
                prospect = await self.state.get_prospect_by_phone(msg.phone)
                if prospect is None:
                    prospect = await self._adopt_inbound_lead(msg)
                if prospect:  # chats with anyone else are not ours to answer
                    await self._process_reply(
                        msg.phone, msg.body, reply_uuid="",
                        prospect=prospect, channel="whatsapp")
                    handled += 1
            except BrainUnavailable as e:
                consumed = False
                complete = False
                logger.error(f"Handler: stopping; replies are left for later ({e}).")
                break
            except Exception as e:
                logger.error(f"Handler: error processing WhatsApp from {msg.phone}: {e}")
                complete = False
            finally:
                if consumed:
                    await self.state.mark_reply_processed(dedup_key)
        if handled:
            logger.info(f"Handler: processed {handled} WhatsApp message(s).")
        return complete

    async def _close_lost(self, prospect, convo, reply_text: str):
        await self.state.update_conversation(convo.id, status="closed", stage="closed_lost")
        if convo.stage != "closed_lost":    # else the stage change was already reported
            before, convo.stage = convo.stage, "closed_lost"
            await notify.on_stage_change(self.state, self.config, prospect, convo, before,
                                         reply_text, self.whatsapp)

    def _portuguese(self, channel: str) -> bool:
        # shortcut: email times are written in English; add an email language setting if needed.
        return channel == "whatsapp" and "portug" in self.config.channels.whatsapp.language.lower()

    async def _offer_slots(self, convo) -> str:
        """The free meeting times as text for the reply, remembered on the
        conversation as offered; "" when the calendar has none to give."""
        now = datetime.now(timezone.utc)
        try:
            kept = json.loads(convo.offered_slots) if convo.offered_slots else []
        except json.JSONDecodeError:
            kept = []
        if not kept or any(calendar._parse(s["start"]) <= now for s in kept):
            kept = await self.calendar.get_available_slots() if self.calendar.enabled() else []
        convo.offered_slots = json.dumps(kept) if kept else ""
        return calendar.format_slots(kept, self._portuguese(convo.channel))

    async def _accepted_slot(self, reply_text: str, convo) -> dict | None:
        """The offered time their reply accepts, or None."""
        try:
            slots = json.loads(convo.offered_slots)
        except json.JSONDecodeError:
            return None
        pt = self._portuguese(convo.channel)
        listed = "\n".join(f"{i}. {calendar.format_slot(s, pt)}" for i, s in enumerate(slots, 1))
        raw = await self.brain.think_json(
            f"""We offered these meeting times:
{listed}

Their reply:
{reply_text}

Did they accept one of these times? Answer with ONLY this JSON: {{"slot": <its number, or 0 if they did not clearly accept one>}}""",
            session_id="harvey-handler", agent="handler", task="pick_slot",
            **self._model_kw(convo.channel))
        if raw is None and getattr(self.brain, "paused_reason", ""):
            raise BrainUnavailable(self.brain.paused_reason)
        try:
            n = int(raw.get("slot", 0)) if isinstance(raw, dict) else 0
        except (TypeError, ValueError):
            return None
        return slots[n - 1] if 1 <= n <= len(slots) else None

    async def _book(self, prospect, convo, slot: dict) -> dict | None:
        convo.offered_slots = ""
        product = self.config.product
        return await self.calendar.book_meeting(
            slot, summary=f"{product.name}: {notify._who(prospect)}",
            description=f"Booked by Harvey from the {convo.channel} conversation.",
            attendee_email=prospect.email)

    def _booked_text(self, prospect, convo, slot: dict, meeting: dict) -> str:
        when = calendar.format_slot(slot, self._portuguese(convo.channel))
        link = meeting.get("link", "")
        if self._portuguese(convo.channel):
            text = f"Combinado, reunião marcada para {when}."
            text += f" O link da chamada: {link}" if link else ""
            return text + (" Também mandei o convite para o seu email." if prospect.email else "")
        text = f"Great, you're booked for {when}."
        text += f" Here's the video link: {link}" if link else ""
        return text + (" A calendar invite is on its way." if prospect.email else "")

    async def _booked(self, prospect, convo, slot: dict, meeting: dict):
        when = calendar.format_slot(slot, self._portuguese(convo.channel))
        await self.state.log_action(
            action_type="meeting_booked", agent="handler",
            details={"conversation_id": convo.id, "start": slot["start"],
                     "event_id": meeting.get("id", ""), "link": meeting.get("link", "")})
        logger.info(f"Handler: meeting booked with {notify._who(prospect)} for {when}.")
        await notify.push(self.state, self.config, "meeting", f"meeting:{convo.id}:{slot['start']}",
                          f"Harvey: meeting booked with {notify._who(prospect)} for {when}. "
                          f"{meeting.get('link', '')}".strip(), self.whatsapp)

    async def _hand_over(self, prospect, convo, reply_text: str):

        """Put the conversation on the human's list, and tell them."""
        await self.state.update_conversation(convo.id, status="needs_human")
        await notify.push_handoff(self.state, self.config, prospect, convo, reply_text, self.whatsapp)

    async def _queue_whatsapp_reply(self, response: str, prospect, convo, intent: str,
                                    action: str = "", force_auto: bool = False):
        """Route Harvey's WhatsApp reply through the outbox. The approval
        ladder applies unless replies, or this kind of action, are auto."""
        cfg = self.config.channels.whatsapp
        auto = force_auto or cfg.auto_approve_replies or action in cfg.auto_approve_actions
        require_approval = cfg.require_approval and not auto
        item_id = await self.state.add_outbox_item(
            prospect_id=prospect.id,
            conversation_id=convo.id,
            kind="reply",
            to_email=prospect.phone,
            subject="WhatsApp",
            body=response,
            send_at=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            status="pending_review" if require_approval else "approved",
            provider=self.whatsapp.name,
            channel="whatsapp",
        )
        if item_id:
            mode = "queued for your approval" if require_approval else "queued to send"
            logger.info(f"Handler: WhatsApp reply to {prospect.phone} {mode} ({intent}).")
            await self.state.log_action(
                action_type="reply_queued", agent="handler",
                details={"prospect_phone": prospect.phone, "intent": intent,
                         "response_preview": response[:100]},
            )

    async def _queue_native_reply(
        self, response: str, prospect, convo, reply_meta: dict, intent: str
    ):
        """Route Harvey's reply through the outbox (approval ladder applies)."""
        require_approval = getattr(
            self.config.channels.email, "require_approval", True
        )
        subject = reply_meta.get("subject", "")
        if subject and not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"

        item_id = await self.state.add_outbox_item(
            prospect_id=prospect.id,
            conversation_id=convo.id,
            kind="reply",
            to_email=prospect.email,
            subject=subject or "Re: your note",
            body=response,
            send_at=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            status="pending_review" if require_approval else "approved",
            provider=self.provider.name if self.provider else "",
            thread_ref=reply_meta.get("thread_ref", ""),
            in_reply_to=reply_meta.get("message_id", ""),
        )
        if item_id:
            mode = "queued for your approval" if require_approval else "queued to send"
            logger.info(f"Handler: reply to {prospect.email} {mode} ({intent}).")
            await self.state.log_action(
                action_type="reply_queued",
                agent="handler",
                details={"prospect_email": prospect.email, "intent": intent,
                         "response_preview": response[:100]},
            )

    def _model_kw(self, channel: str) -> dict:
        """A per-call model override for chat, when one is configured."""
        wa = getattr(self.config.channels, "whatsapp", None)
        model = getattr(wa, "model", "") if channel == "whatsapp" else ""
        return {"model": model} if model else {}

    async def _classify_intent(self, reply_text: str, prospect, channel: str = "email") -> str:
        """Ask the brain to classify the reply's intent."""
        auto_note = WHATSAPP_AUTO_NOTE if channel == "whatsapp" else ""
        prompt = f"""Classify this email reply into exactly ONE category:
- "interested" — wants to learn more, open to a meeting, positive response
- "objection" — has concerns but hasn't said no (price, timing, competition)
- "not_interested" — a clear no, not now, or "we're all set"
- "unsubscribe" — asks to stop being contacted, remove from list, opt out
- "escalate" — angry, hostile, threatens legal action, or mentions spam/compliance
- "ooo" — out of office / auto-reply
- "wrong_person" — not the right contact, suggests someone else
- "question" — asking for more information before deciding

{auto_note}When in doubt between "not_interested" and "unsubscribe", choose "unsubscribe".
When in doubt between anything and "escalate", choose "escalate".

Reply from {prospect.full_name()} ({prospect.title} at {prospect.company}):
\"\"\"{reply_text}\"\"\"

Respond with ONLY the category label, nothing else."""

        result = await self.brain.think(
            prompt, session_id="harvey-handler",
            agent="handler", task="classify_intent",
            **self._model_kw(channel),
        )
        if not result:
            paused = getattr(self.brain, "paused_reason", "")
            if paused:
                # The provider is down (login, setup, usage limit), not this
                # reply: don't escalate it, leave it unprocessed.
                raise BrainUnavailable(paused)
            # Classifier failed. Do NOT auto-reply blind — flag for a human.
            logger.warning("Handler: Intent classifier returned nothing. Escalating.")
            return "escalate"

        # Robust extraction: take the first recognized label anywhere in
        # the response (models sometimes add explanation despite instructions).
        cleaned = result.strip().strip('"').strip("'").lower()
        if cleaned in INTENT_LABELS:
            return cleaned
        for label in sorted(INTENT_LABELS, key=len, reverse=True):
            if label in cleaned:
                return label

        logger.warning(f"Handler: Unknown intent '{cleaned[:80]}'. Defaulting to 'question'.")
        return "question"

    def _determine_stage(self, intent: str, current_stage: str, reply_text: str) -> str:
        """Advance the conversation stage based on intent and context."""
        text_lower = reply_text.lower()

        # Terminal states
        if intent in ("not_interested", "unsubscribe"):
            return "closed_lost"

        if intent == "escalate":
            return current_stage  # Human decides what happens next

        # Stage advancement rules
        if intent == "interested":
            # Meeting/call signals from an interested prospect → closing
            if any(w in text_lower for w in ["let's meet", "schedule", "calendar", "book a call", "free on", "available"]):
                return "closing"
            if current_stage == "initial_outreach":
                return "engaged"
            if current_stage == "engaged":
                # Check if they're asking about specifics → presenting
                if any(w in text_lower for w in ["price", "cost", "how much", "pricing", "demo", "trial"]):
                    return "presenting"
                return "qualifying"
            if current_stage in ("qualifying", "presenting"):
                return "negotiating"
            if current_stage == "negotiating":
                return "closing"

        if intent == "objection":
            # Objections typically happen during presenting or negotiating
            if current_stage in ("initial_outreach", "engaged"):
                return "qualifying"
            # Stay in current stage during objection handling

        if intent == "question":
            if current_stage == "initial_outreach":
                return "engaged"
            if current_stage == "engaged":
                return "qualifying"

        return current_stage

    async def _decide_whatsapp(
        self, intent: str, reply_text: str, prospect, convo: Conversation
    ) -> Decision:
        """The model proposes a decision for this chat; the policy in
        harvey/decide.py turns it into what Harvey actually does."""
        cfg = self.config.channels.whatsapp
        demo = find_demo(prospect.industry, cfg.demos)
        said = [m.content for m in convo.thread if m.sender == "harvey"]
        queued = [i["body"] for i in await self.state.get_outbox(channel="whatsapp", limit=500)
                  if i["prospect_id"] == prospect.id
                  and i["status"] in ("pending_review", "approved", "sending", "sent")]
        demo_sent = bool(demo and any(demo.url in t for t in said + queued))
        history = "\n".join(
            f"{'Theo' if m.sender == 'harvey' else 'Cliente'}: {m.content}"
            for m in convo.thread[-8:])
        persona = self.config.persona
        lessons = await self.state.lessons_block()
        if demo is None:
            demo_line = ("none for this kind of business: do not offer an example site"
                         if prospect.industry else
                         "unknown until they tell you their kind of business (examples exist for: "
                         + ", ".join(d.label for d in cfg.demos) + ")")
        elif demo_sent:
            demo_line = f'"{demo.label}", but it was already sent: do not send it again'
        else:
            demo_line = f'"{demo.label}" is ready: use {DEMO_TOKEN} for the link'
        origin = (INBOUND_ORIGIN_NOTE if prospect.source == INBOUND_WHATSAPP_SOURCE and not said else "")
        rules = WHATSAPP_DECISION_RULES
        if self.calendar.enabled():
            rules = rules.replace(NO_TIMES_RULE, BOOKING_RULE).replace(
                "they want to hire, start, meet or talk to someone", "they want to hire or start")
        prompt = f"""You are {persona.name}, {persona.role} at {chat_brand(persona.company)}, chatting on WhatsApp with a small business. Tone: {persona.tone}. Write in {cfg.language}.

## The lead
Business: {prospect.company or "unknown"}
Kind of business: {prospect.industry or "unknown"}
Example site available: {demo_line}
{("Their last message was classified as: " + intent) if intent else ""}
{origin}
## Conversation
{history}

## KNOWLEDGE BASE (the only source for facts)
{self.brain.load_skill("knowledge_base")}

## How to chat
{self.brain.load_skill("whatsapp_outreach")}
{rules}
{lessons}"""
        raw = await self.brain.think_json(
            prompt, session_id="harvey-handler", agent="handler", task="decide_whatsapp",
            **self._model_kw("whatsapp"))
        if raw is None:
            paused = getattr(self.brain, "paused_reason", "")
            if paused:
                # The provider went down mid-chat; nothing is stored yet, so
                # leave the message for a clean retry.
                raise BrainUnavailable(paused)
        first = parse_decision(raw)
        if cfg.review_ambiguous and needs_review(first):
            # The fast model was unsure: a stronger one looks again, with its
            # proposal in front of it, before the chat is bothered or handed over.
            proposal = json.dumps(raw, ensure_ascii=False) if isinstance(raw, dict) else "nothing usable"
            reviewed = await self.brain.think_json(
                prompt + "\n\n## Second opinion\nA fast assistant proposed: " + proposal +
                "\nIt was not sure. Decide for yourself, from the conversation and the knowledge base, "
                "and answer with the same JSON. Do not hand over to a person unless it is really needed.",
                session_id="harvey-handler", agent="handler", task="review_whatsapp",
                **({"model": cfg.review_model} if cfg.review_model else {}))
            if isinstance(reviewed, dict) and parse_decision(reviewed):
                raw = reviewed
                await self.state.log_action(
                    action_type="decision_review", agent="handler",
                    details={"prospect_phone": prospect.phone, "first": proposal[:300],
                             "final_action": reviewed.get("action", "")})
        told = str(raw.get("industry") or "").strip()[:60] if isinstance(raw, dict) else ""
        if told and not prospect.industry:
            # They just said what their business is: remember it, and the
            # example site for it becomes available in this very answer.
            prospect.industry = told
            await self.state.set_prospect_industry(prospect.id, told)
            demo = find_demo(told, cfg.demos)
            demo_sent = bool(demo and any(demo.url in t for t in said + queued))
        decision = parse_decision(raw)
        slots_text = (await self._offer_slots(convo)
                      if decision and SLOTS_TOKEN in decision.reply else "")
        return apply_policy(
            decision, prospect=prospect, reply_text=reply_text, demo=demo,
            demo_sent=demo_sent, assistant_turns=len(said),
            allowed_prices=cfg.allowed_prices, allowed_links=[d.url for d in cfg.demos],
            flat=_flat, slots_text=slots_text)

    async def _generate_response(
        self, intent: str, reply_text: str, prospect, convo: Conversation
    ) -> str:
        """Generate an appropriate response based on intent."""
        # Build conversation history for context
        history = "\n".join(
            f"{'Harvey' if m.sender == 'harvey' else prospect.full_name()}: {m.content}"
            for m in convo.thread[-6:]  # Last 6 messages for context
        )

        objection_context = ""
        if intent == "objection":
            # Check if we have a pre-configured response
            for trigger, response in self.config.product.objection_responses.items():
                if trigger.lower() in reply_text.lower():
                    objection_context = f"\nSuggested approach for this objection: {response}"
                    break

        prompt = self.brain.load_prompt("handler", stage=convo.stage)
        if not prompt:
            prompt = f"""You are {self.config.persona.name}, {self.config.persona.role} at {self.config.persona.company}.
Your tone is: {self.config.persona.tone}
Product: {self.config.product.name} — {self.config.product.description}"""

        # Inject objection handling + sales methodology skills
        if self.skills:
            prompt += "\n\n" + self.skills

        prompt += f"""

Conversation so far:
{history}

The prospect's intent is: {intent}
{objection_context}

Write a reply that:"""

        if intent == "interested" and self.calendar.enabled():
            prompt += f"""
- Acknowledges their interest warmly
- Offers a short video call: write {SLOTS_TOKEN} where the free times go (the system lists them); never write a day or time yourself
- Asks them to reply with the time that suits them
- Keeps it short (under 80 words)"""
        elif intent == "interested":
            prompt += """
- Acknowledges their interest warmly
- Suggests a specific next step (brief call or meeting)
- Keeps it short (under 80 words)
- Includes a clear CTA with flexibility on timing"""

        elif intent == "objection":
            prompt += """
- Addresses the concern directly and empathetically
- Provides evidence or a reframe
- Doesn't argue — redirect toward value
- Keeps it under 100 words"""
        elif intent == "question":
            prompt += """
- Answers their question clearly and concisely
- Ties the answer back to value for them
- Ends with a soft CTA
- Under 100 words"""
        elif intent == "wrong_person":
            prompt += """
- Thanks them politely
- Asks who the right person would be
- Makes it easy for them to refer (one-line ask)
- Under 50 words"""

        prompt += "\n\nWrite ONLY the email body. No subject line, no greeting label, no signature block, no markdown."

        response = await self.brain.think(
            prompt, session_id="harvey-handler",
            agent="handler", task="generate_response",
        )
        if not response:
            paused = getattr(self.brain, "paused_reason", "")
            if paused:
                # The provider went down mid-reply; nothing is stored yet,
                # so leave the reply for a clean retry.
                raise BrainUnavailable(paused)
            return ""
        response = response.strip()
        # Guard against the model returning meta-text instead of an email
        if response.lower().startswith(("i can't", "i cannot", "as an ai", "sorry,")):
            logger.warning("Handler: Model returned meta-text instead of an email. Discarding.")
            return ""
        polished = await humanize(
            self.brain, [{"subject": "", "body": response}], agent="handler",
            enabled=humanizer_enabled(self.config),
        )
        body = polished[0]["body"]
        if SLOTS_TOKEN in body:
            # No free times to offer: "" hands the reply to a person.
            times = await self._offer_slots(convo)
            body = body.replace(SLOTS_TOKEN, times) if times else ""
        return body

