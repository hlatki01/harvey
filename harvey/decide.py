"""The decision engine behind Theo's WhatsApp answers.

The model does not just write a reply: it proposes a decision
(``{action, reply, confidence, reason}``) and this module checks it against
rules that do not depend on the model. A proposal that breaks a rule is
turned into a handoff to the team, never sent as written.

Actions
  answer     a fact the knowledge base covers
  qualify    one question to learn what the business needs
  send_demo  show the example site for the lead's kind of business
  handoff    a person must take over (the reply says "nossa equipe")
  wait       nothing to answer (a thanks, an "ok")

The model writes ``{demo_url}`` where the example link goes; the code swaps
in the right URL, because a model that types URLs invents them.
"""

import logging
from dataclasses import dataclass

from harvey.gate import whatsapp_check

logger = logging.getLogger("harvey.decide")

INTENTS = ("interested", "objection", "not_interested", "unsubscribe", "escalate", "ooo", "wrong_person", "question")
ACTIONS = ("answer", "qualify", "send_demo", "handoff", "wait")
DEMO_TOKEN = "{demo_url}"
SLOTS_TOKEN = "{slots}"     # where the free meeting times go (integrations/calendar.py)
HANDOFF_REPLY = "Combinado! Nossa equipe vai falar com você por aqui mesmo, é só aguardar."
HOLDING_REPLY = "Boa pergunta! Vou confirmar com a nossa equipe e já te respondo por aqui."

# A lead who says these is ready for the team, not the assistant.
HANDOFF_WORDS = (
    "contratar", "quero fechar", "vamos fechar", "fechar negócio", "fechar negocio",
    "quero começar", "quero comecar", "vamos começar", "vamos comecar", "como pago",
    "proposta", "reunião", "reuniao", "marcar", "agendar", "ligar", "ligação", "ligacao",
    "falar com alguém", "falar com alguem", "falar com o luis", "falar com uma pessoa",
)
# After this many answers from the assistant, a person takes over.
MAX_ASSISTANT_TURNS = 6


@dataclass
class Decision:
    action: str
    reply: str = ""
    confidence: str = "high"      # high | medium | low
    reason: str = ""
    overridden: str = ""          # why the policy changed the model's proposal
    sender: str = "person"        # person | automatic | unclear: who wrote the last message
    intent: str = "question"      # what the last message is, as the classifier used to say


def find_demo(category: str, demos):
    """The first configured demo whose keywords appear in the category."""
    text = (category or "").lower()
    return next((d for d in demos if any(k.lower() in text for k in d.keywords)), None)


def parse_decision(raw) -> Decision | None:
    """A Decision from the model's JSON, or None when it is not usable."""
    if not isinstance(raw, dict):
        return None
    action = str(raw.get("action") or "").strip().lower()
    if action not in ACTIONS:
        return None
    confidence = str(raw.get("confidence") or "high").strip().lower()
    sender = str(raw.get("sender") or "person").strip().lower()
    intent = str(raw.get("intent") or "question").strip().lower()
    return Decision(
        intent=intent if intent in INTENTS else "question",
        sender=sender if sender in ("person", "automatic", "unclear") else "unclear",
        action=action,
        reply=str(raw.get("reply") or "").strip(),
        confidence=confidence if confidence in ("high", "medium", "low") else "medium",
        reason=str(raw.get("reason") or "").strip(),
    )


def needs_review(decision: Decision | None) -> bool:
    """A proposal worth a second opinion before it is acted on: nothing usable,
    not confident, or unsure whether a person or a bot wrote the message."""
    return decision is None or decision.confidence != "high" or decision.sender == "unclear"


def wants_a_human(reply_text: str, assistant_turns: int, flat) -> bool:
    """Asks to hire, meet or talk to a person; or the chat has gone on long."""
    text = flat(reply_text)
    return assistant_turns >= MAX_ASSISTANT_TURNS or any(w in text for w in HANDOFF_WORDS)


def _handoff(decision: Decision | None, why: str) -> Decision:
    kept = decision.reply if decision and decision.action == "handoff" and decision.reply else ""
    kept = "" if "?" in kept else kept   # a question would invite an answer nobody reads
    return Decision(action="handoff", reply=kept or HOLDING_REPLY, confidence="high",
                    reason=decision.reason if decision else "", overridden=why,
                    intent=decision.intent if decision else "question")


def apply_policy(decision: Decision | None, *, prospect, reply_text: str, demo, demo_sent: bool,
                 assistant_turns: int, allowed_prices, allowed_links, flat,
                 slots_text: str = "") -> Decision:
    """Check a proposed decision against the rules; return what Harvey does."""
    if decision is None:
        return _handoff(None, "the model gave no usable decision")
    if decision.sender == "automatic":
        # A business's bot, menu or away message: there is no one to answer.
        return Decision(action="wait", reason=decision.reason, sender="automatic", intent="ooo",
                        overridden="automatic message")
    if decision.action == "wait":
        decision.reply = ""
        return decision
    if decision.confidence == "low":
        return _handoff(decision, "low confidence")

    reply = decision.reply
    if DEMO_TOKEN in reply:
        if demo is None or demo_sent:
            reply = reply.replace(DEMO_TOKEN, "").strip()
            if decision.action == "send_demo":
                return _handoff(decision, "no demo to send" if demo is None else "demo already sent")
        else:
            reply = reply.replace(DEMO_TOKEN, demo.url)
    elif decision.action == "send_demo":
        return _handoff(decision, "send_demo without the demo link")
    offers_times = SLOTS_TOKEN in reply
    if offers_times:
        if not slots_text:
            return _handoff(decision, "no free meeting times to offer")
        reply = reply.replace(SLOTS_TOKEN, slots_text)
    if not reply:
        return _handoff(decision, "empty reply")
    decision.reply = reply

    gate = whatsapp_check(prospect.phone, reply, prospect, kind="reply",
                          allowed_prices=allowed_prices, allowed_links=allowed_links)
    if not gate:
        return _handoff(decision, "gate: " + "; ".join(gate.reasons)[:200])

    # Offering meeting times is the answer to "vamos marcar", not a hand-over.
    if (decision.action != "handoff" and not offers_times
            and wants_a_human(reply_text, assistant_turns, flat)):

        decision.action = "handoff"
        decision.overridden = "asked for a person, or the chat is long"
    if decision.action == "handoff" and "?" in decision.reply:
        # The team answers from here on: a question to the lead would go unread.
        decision.reply = HANDOFF_REPLY
    return decision
