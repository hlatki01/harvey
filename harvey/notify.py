"""Push what needs the owner to their WhatsApp (config.notify).

The dashboard's bell only refreshes while someone has it open; a hot lead
can't wait for that. Every push has a key and goes out once (push_log), so
the handler, the live WhatsApp loop and the heartbeat can all call this
without sending the same thing twice.
"""

import json
import logging
import time
from datetime import date

from harvey import crm
from harvey.config import NotifyConfig, load_env
from harvey.integrations.evolution import EvolutionClient
from harvey.phone import normalize_phone

logger = logging.getLogger("harvey.notify")


def _settings(config) -> NotifyConfig:
    return getattr(config, "notify", None) or NotifyConfig()


def owner_number(config) -> str:
    """The number pushes go to, in international digits; "" when unset."""
    number = _settings(config).whatsapp_number
    if not number:
        return ""
    return normalize_phone(number, config.channels.whatsapp.default_country_code)


def _without_ninth(phone: str) -> str:
    # WhatsApp often reports a Brazilian mobile without its ninth digit.
    return phone[:4] + phone[5:] if phone.startswith("55") and len(phone) == 13 else phone


def is_owner(config, phone: str) -> bool:
    """Is this number the one pushes go to?"""
    owner = owner_number(config)
    return bool(owner and phone) and _without_ninth(owner) == _without_ninth(phone)


def _who(row) -> str:
    """A prospect (model or row dict) as a person can read it."""
    get = row.get if isinstance(row, dict) else (lambda k: getattr(row, k, ""))
    name = " ".join(x for x in (get("first_name"), get("last_name")) if x)
    company = get("company") or ""
    if name and company and company != name:
        return f"{name} ({company})"
    return name or company or "A lead"


async def push(state, config, event: str, key: str, text: str, client=None) -> bool:
    """Send `text` to the owner once per `key`. Never raises; True if it went out."""
    number = owner_number(config)
    if not number or event not in _settings(config).events:
        return False
    if not await state.claim_push(key):
        return False
    client = client or EvolutionClient.from_env(load_env())
    try:
        result = await client.send_text(number, text)
        ok, error = result.ok, result.error
    except Exception as e:  # a push must never break the agent that raised it
        ok, error = False, str(e)
    if not ok:
        await state.release_push(key)
        logger.warning(f"Notify: push '{key}' failed: {error}")
    return ok


async def on_stage_change(state, config, prospect, convo, old_stage: str, reply_text: str,
                          client=None) -> None:
    """A conversation moved stage: tell the CRM webhook, and push a hot one."""
    if convo.stage == old_stage:
        return
    await crm.post_stage_change(config, prospect, convo, old_stage, reply_text)
    if convo.stage not in _settings(config).hot_stages:
        return
    await push(state, config, "hot_lead", f"hot:{convo.id}:{convo.stage}",
               f"Harvey: hot lead, {_who(prospect)} is now {convo.stage} ({convo.channel}).\n"
               f"\"{reply_text[:300]}\"", client)


async def push_handoff(state, config, prospect, convo, reply_text: str, client=None) -> None:
    # One push per conversation: a chat handed over twice is still the same chat on your list.
    await push(state, config, "handoff", f"handoff:{convo.id}",
               f"Harvey: {_who(prospect)} needs you ({convo.channel}).\n\"{reply_text[:300]}\"",
               client)


async def state_alerts(state) -> list[tuple]:
    """What needs attention that the database alone can tell, as
    (key, title, detail, tone, tab, target, signature). Shared by the
    dashboard's bell and the heartbeat's pushes."""
    raised = []
    for c in await state.get_handoffs():
        try:
            thread = json.loads(c["thread_json"] or "[]")
        except (json.JSONDecodeError, TypeError):
            thread = []
        last = next((m["content"] for m in reversed(thread) if m.get("sender") == "prospect"), "")
        raised.append((f"handoff:{c['id']}", f"{_who(c)} needs you",
                       last[:140] or "Harvey handed this conversation over.",
                       "bad", "conversations", c["id"], str(len(thread))))
    pending = await state.get_outbox(status="pending_review", limit=500)
    if pending:
        n = len(pending)
        raised.append(("approvals", f"{n} message{'s' if n != 1 else ''} waiting for approval",
                       "Nothing goes out until you approve it.", "warn", "outbox", "", str(n)))
    paused = await state.get_setting("sending_paused")
    if paused:
        raised.append(("system:paused", "Sending is paused", str(paused), "bad", "outbox", "", ""))
    return raised


async def push_alerts(state, config, client=None) -> None:
    """The heartbeat's sweep: anything state_alerts raises that wasn't pushed yet."""
    if not owner_number(config):
        return
    window = int(time.time() // (_settings(config).approvals_every_minutes * 60))
    for key, title, detail, *_ in await state_alerts(state):
        if key.startswith("handoff:"):
            event, push_key = "handoff", key
        elif key == "approvals":
            event, push_key = "approvals", f"approvals:{window}"
        else:
            event, push_key = "system", f"{key}:{date.today().isoformat()}"
        await push(state, config, event, push_key, f"Harvey: {title}. {detail}", client)
