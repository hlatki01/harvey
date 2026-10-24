"""WhatsApp through Evolution API (self-hosted, v2).

Three calls: check which numbers have WhatsApp, send a text, and read the
replies that arrived. Replies are polled (``chat/findMessages``) rather than
pushed by webhook, so Harvey needs no inbound HTTP endpoint; the instance
must persist messages (DATABASE_SAVE_DATA_NEW_MESSAGE=true).

Paths and payloads follow the Evolution v2 REST API; they have been checked
against mocked responses only, never against a live instance.
"""

import logging
from dataclasses import dataclass

import httpx

from harvey.integrations.mail_provider import SendResult

logger = logging.getLogger("harvey.evolution")

TIMEOUT = 30
USER_JID_SUFFIX = "@s.whatsapp.net"
# Typing time shown to the recipient before the text arrives, in ms.
TYPING_DELAY_MS = 2500


@dataclass
class WhatsAppInbound:
    provider_id: str      # WhatsApp message id, used for reply dedup
    phone: str            # international digits
    body: str
    date: str = ""
    push_name: str = ""   # the name the sender set for themselves


def _text_of(message: dict) -> str:
    """Plain text of an Evolution message, or "" for media and the rest."""
    message = message or {}
    return (message.get("conversation")
            or (message.get("extendedTextMessage") or {}).get("text")
            or "")


MEDIA_KINDS = {"imageMessage": "image", "videoMessage": "video", "audioMessage": "audio",
               "documentMessage": "document", "stickerMessage": "image"}


def normalize_message(rec: dict) -> dict | None:
    """One Evolution record as the dashboard's chat sees it, or None for
    what is not a message to show (reactions, protocol notices).

    {id, from_me, ts, text, media: {kind, mimetype, filename} | None}"""
    key = rec.get("key") or {}
    msg = rec.get("message") or {}
    if not key.get("id") or not msg or "reactionMessage" in msg or "protocolMessage" in msg:
        return None
    # A document with a caption arrives wrapped one level deeper.
    msg = ((msg.get("documentWithCaptionMessage") or {}).get("message")) or msg
    out = {"id": key["id"], "from_me": bool(key.get("fromMe")),
           "ts": int(rec.get("messageTimestamp") or 0), "text": "", "media": None}
    for field, kind in MEDIA_KINDS.items():
        if field in msg:
            body = msg[field] or {}
            out["text"] = body.get("caption") or ""
            out["media"] = {"kind": kind, "mimetype": body.get("mimetype") or "",
                            "filename": body.get("fileName") or ""}
            return out
    out["text"] = _text_of(msg) or "[mensagem não suportada]"
    return out


class EvolutionClient:
    name = "evolution"

    def __init__(self, base_url: str, api_key: str, instance: str,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key
        self.instance = instance
        self._transport = transport

    @classmethod
    def from_env(cls, env) -> "EvolutionClient":
        return cls(env.evolution_api_url, env.evolution_api_key, env.evolution_instance)

    def is_configured(self) -> bool:
        return bool(self.base_url and self.api_key and self.instance)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=TIMEOUT, transport=self._transport,
            headers={"apikey": self.api_key, "Content-Type": "application/json"},
        )

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path}/{self.instance}"

    @staticmethod
    def _raise_for_error(r: httpx.Response) -> None:
        if r.status_code >= 400:
            try:
                body = r.json()
                detail = (body.get("response") or {}).get("message") or body.get("message") or body
            except Exception:
                detail = r.text[:200]
            raise RuntimeError(f"Evolution API {r.status_code}: {detail}")

    async def check_numbers(self, numbers: list[str]) -> dict[str, str]:
        """{asked number: the number WhatsApp knows it as}, only for numbers
        that have WhatsApp. They can differ (Brazilian mobiles registered
        without the ninth digit), and the known one is what must be messaged."""
        if not numbers:
            return {}
        async with self._client() as client:
            r = await client.post(self._url("chat/whatsappNumbers"),
                                  json={"numbers": numbers})
        self._raise_for_error(r)
        found: dict[str, str] = {}
        for item in r.json() or []:
            if not item.get("exists"):
                continue
            known = (item.get("jid") or "").split("@")[0] or item.get("number") or ""
            asked = item.get("number") or known
            if known:
                found[asked] = known
        return found

    async def send_text(self, number: str, text: str) -> SendResult:
        async with self._client() as client:
            r = await client.post(self._url("message/sendText"),
                                  json={"number": number, "text": text,
                                        "delay": TYPING_DELAY_MS})
        if r.status_code >= 400:
            try:
                error = str(r.json())[:200]
            except Exception:
                error = r.text[:200]
            return SendResult(ok=False, error=f"Evolution API {r.status_code}: {error}")
        data = r.json() or {}
        key = data.get("key") or {}
        return SendResult(ok=True, message_id=key.get("id") or "",
                          thread_ref=key.get("remoteJid") or "")

    async def get_replies(self, limit: int = 50) -> list[WhatsAppInbound]:
        """The newest inbound text messages from individual chats."""
        async with self._client() as client:
            r = await client.post(
                self._url("chat/findMessages"),
                json={"where": {"key": {"fromMe": False}}, "page": 1, "offset": limit},
            )
        self._raise_for_error(r)
        data = r.json() or []
        # v2 pages its result: {"messages": {"records": [...]}}; older builds
        # answer with the bare list.
        records = (data.get("messages") or {}).get("records") if isinstance(data, dict) else data
        out: list[WhatsAppInbound] = []
        for rec in records or []:
            key = rec.get("key") or {}
            jid = key.get("remoteJid") or ""
            if jid.endswith("@lid"):
                # WhatsApp's anonymous chat id: the phone, when the server
                # knows it, comes alongside. Without it the sender is unknown.
                alt = key.get("remoteJidAlt") or key.get("senderPn") or ""
                if alt.endswith(USER_JID_SUFFIX):
                    jid = alt
            body = _text_of(rec.get("message")).strip()
            if key.get("fromMe") or not jid.endswith(USER_JID_SUFFIX) or not body or not key.get("id"):
                continue  # groups, broadcasts, media, our own messages
            out.append(WhatsAppInbound(
                provider_id=key["id"],
                phone=jid.split("@")[0],
                body=body,
                date=str(rec.get("messageTimestamp") or ""),
                push_name=rec.get("pushName") or "",
            ))
        out.sort(key=lambda m: m.date)
        return out[-limit:]

    async def _chat_page(self, where: dict, page: int, size: int) -> tuple[list[dict], int]:
        async with self._client() as client:
            r = await client.post(self._url("chat/findMessages"),
                                  json={"where": where, "page": page, "offset": size})
        self._raise_for_error(r)
        data = r.json() or []
        if isinstance(data, dict):
            box = data.get("messages") or {}
            return box.get("records") or [], int(box.get("pages") or 1)
        return data, 1

    async def get_chat(self, phone: str, limit: int = 30, before: int = 0) -> tuple[list[dict], bool]:
        """The chat with `phone`, newest `limit` messages older than `before`
        (epoch seconds; 0 = latest), oldest first, and whether older ones exist.

        A chat is stored under two ids: the phone (what we sent) and, for
        people WhatsApp hides the number of, an anonymous one that carries the
        phone as remoteJidAlt (what they sent). Both are read and merged."""
        jid = phone + USER_JID_SUFFIX
        merged: dict[str, dict] = {}
        more = False
        for where in ({"key": {"remoteJid": jid}}, {"key": {"remoteJidAlt": jid}}):
            page, size, have = 1, max(limit, 30) if not before else 100, 0
            while page <= 20:
                records, pages = await self._chat_page(where, page, size)
                for rec in records:
                    m = normalize_message(rec)
                    if m and (not before or m["ts"] < before):
                        merged[m["id"]] = m
                        have += 1
                if have > limit or page >= pages or not records:
                    more = more or page < pages
                    break
                page += 1
        ordered = sorted(merged.values(), key=lambda m: m["ts"], reverse=True)
        return ordered[:limit][::-1], more or len(ordered) > limit

    async def get_media(self, message_id: str) -> tuple[bytes, str, str]:
        """(bytes, mimetype, filename) of a media message."""
        import base64
        async with self._client() as client:
            r = await client.post(self._url("chat/getBase64FromMediaMessage"),
                                  json={"message": {"key": {"id": message_id}}})
        self._raise_for_error(r)
        data = r.json() or {}
        raw = data.get("base64") or ""
        if not raw:
            raise RuntimeError("Evolution returned no media")
        return base64.b64decode(raw.split(",")[-1]), data.get("mimetype") or "", data.get("fileName") or ""

    async def send_media(self, number: str, filename: str, mimetype: str, b64: str,
                         caption: str = "") -> SendResult:
        """An image, video, audio note or document."""
        kind = mimetype.split("/")[0]
        if kind == "audio":
            path, payload = "message/sendWhatsAppAudio", {"number": number, "audio": b64, "encoding": True}
        else:
            path = "message/sendMedia"
            payload = {"number": number, "mediatype": kind if kind in ("image", "video") else "document",
                       "mimetype": mimetype, "caption": caption, "media": b64, "fileName": filename}
        async with self._client() as client:
            r = await client.post(self._url(path), json=payload)
        if r.status_code >= 400:
            return SendResult(ok=False, error=f"Evolution API {r.status_code}: {r.text[:200]}")
        key = (r.json() or {}).get("key") or {}
        return SendResult(ok=True, message_id=key.get("id") or "", thread_ref=key.get("remoteJid") or "")

    async def is_new_contact(self, phone: str, own_ids: frozenset | set = frozenset()) -> bool:
        """True when this account has never written in the chat with `phone`:
        a stranger, not a friend or customer already in the owner's history.
        Messages in own_ids are Harvey's own outreach and do not count."""
        async with self._client() as client:
            r = await client.post(
                self._url("chat/findMessages"),
                json={"where": {"key": {"remoteJid": phone + USER_JID_SUFFIX}}, "page": 1, "offset": 50},
            )
        self._raise_for_error(r)
        data = r.json() or []
        records = (data.get("messages") or {}).get("records") if isinstance(data, dict) else data
        return not any((rec.get("key") or {}).get("fromMe") and (rec.get("key") or {}).get("id") not in own_ids
                       for rec in records or [])

    async def test_connection(self) -> tuple[bool, str]:
        if not self.is_configured():
            return False, "EVOLUTION_API_URL, EVOLUTION_API_KEY and EVOLUTION_INSTANCE must all be set"
        try:
            async with self._client() as client:
                r = await client.get(self._url("instance/connectionState"))
            self._raise_for_error(r)
            state = ((r.json() or {}).get("instance") or {}).get("state", "")
        except Exception as e:
            return False, str(e)
        if state != "open":
            return False, f"instance '{self.instance}' is '{state or 'unknown'}', not connected (scan its QR code)"
        return True, f"instance '{self.instance}' is connected"
