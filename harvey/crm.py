"""Conversation stage changes, POSTed to config.crm.webhook_url.

One JSON body per change, signed with HMAC-SHA256 when HARVEY_WEBHOOK_SECRET
is set, so whatever receives it (n8n, Make, a CRM) can check it came from
this Harvey.
"""

import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone

import httpx

from harvey.config import load_env

logger = logging.getLogger("harvey.crm")

SIGNATURE_HEADER = "X-Harvey-Signature"


def sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def stage_change_payload(prospect, convo, old_stage: str, reply_text: str) -> dict:
    return {
        "event": "stage_changed",
        "at": datetime.now(timezone.utc).isoformat(),
        "conversation_id": convo.id,
        "channel": convo.channel,
        "old_stage": old_stage,
        "new_stage": convo.stage,
        "intent": convo.intent,
        "last_reply": reply_text,
        "prospect": {k: getattr(prospect, k, "") for k in (
            "id", "first_name", "last_name", "email", "phone", "title",
            "company", "company_id", "industry")},
    }


async def post_stage_change(config, prospect, convo, old_stage: str, reply_text: str,
                            transport=None) -> bool:
    """POST the change to the webhook, if one is set. Never raises; True if it was accepted."""
    crm = getattr(config, "crm", None)
    url = crm.webhook_url if crm else ""
    if not url:
        return False
    body = json.dumps(stage_change_payload(prospect, convo, old_stage, reply_text)).encode()
    headers = {"Content-Type": "application/json"}
    secret = load_env().harvey_webhook_secret
    if secret:
        headers[SIGNATURE_HEADER] = sign(body, secret)
    # shortcut: no retry or queue; a change posted while the receiver is down is only logged.
    # Queue it like the outbox if lost events start to matter.
    try:
        async with httpx.AsyncClient(timeout=10, transport=transport) as client:
            r = await client.post(url, content=body, headers=headers)
        if r.status_code < 300:
            return True
        error = f"HTTP {r.status_code}"
    except httpx.HTTPError as e:
        error = str(e) or type(e).__name__
    logger.warning(f"CRM webhook: stage change for {convo.id} not delivered ({error}).")
    return False
