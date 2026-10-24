"""Deterministic pre-send gate — the last line before an email leaves.

LLM instructions are a floor, not a guarantee. Every outgoing email passes
these non-LLM checks at send time; a failure blocks the send and records
why, so a bad generation can never reach a prospect. This is the pattern
the surviving commercial AI-SDR vendors converged on after the 2025
autonomous-agent flameouts: constraints in the prompt, verification in
code.
"""

import re
from dataclasses import dataclass, field

# Spam-filter and AI-tell phrases that must never appear in outgoing mail.
# Mirrors prompts/writer.md; enforced here because prompts can be ignored.
BANNED_PHRASES = (
    "act now", "buy now", "click here", "click below", "limited time",
    "limited offer", "exclusive deal", "special offer", "risk-free",
    "no obligation", "100% free", "make money", "double your",
    "guaranteed results", "winner", "congratulations",
    "i hope this finds you well", "hope this email finds you well",
    "i hope this email finds you", "picture this", "what if i told you",
    "in today's fast-paced world", "game-changer", "game changer",
    "cutting-edge solution", "revolutionize your", "unlock your",
    "supercharge your", "as an ai", "i'm an ai", "i am an ai",
    "as a language model",
)

MAX_SUBJECT_LEN = 90
MAX_BODY_WORDS = 220        # hard ceiling; sequences aim far lower
MAX_LINKS = 1

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
MERGE_TAG_RE = re.compile(r"\{\{\s*\w+\s*\}\}|\{\s*\w+\s*\}(?!\})")
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)

DELIVERABLE_STATUSES = {"verified", "risky"}


@dataclass
class GateResult:
    ok: bool
    reasons: list[str] = field(default_factory=list)

    def __bool__(self):
        return self.ok


def pre_send_check(
    to_email: str,
    subject: str,
    body: str,
    prospect=None,
    allow_risky: bool = False,
    kind: str = "sequence",
    postal_address: str = "",
) -> GateResult:
    """Run every deterministic check. Returns ok=False with reasons on any hit.

    postal_address: when set, a cold (sequence) email must carry it (CAN-SPAM).
    """
    reasons: list[str] = []
    subject = (subject or "").strip()
    body = (body or "").strip()
    to_email = (to_email or "").strip().lower()

    # Recipient sanity
    if not EMAIL_RE.match(to_email):
        reasons.append(f"invalid recipient address '{to_email}'")
    if prospect is not None:
        if (prospect.email or "").strip().lower() != to_email:
            reasons.append("recipient does not match the prospect record")
        allowed = DELIVERABLE_STATUSES if allow_risky else {"verified"}
        # Replies go back to someone who emailed US — deliverability proven.
        if kind == "sequence" and (prospect.email_status or "guess") not in allowed:
            reasons.append(
                f"email_status '{prospect.email_status or 'guess'}' is not deliverable"
            )

    # Content sanity
    if not subject:
        reasons.append("empty subject")
    if not body:
        reasons.append("empty body")
    if len(subject) > MAX_SUBJECT_LEN:
        reasons.append(f"subject too long ({len(subject)} > {MAX_SUBJECT_LEN})")
    word_count = len(body.split())
    if word_count > MAX_BODY_WORDS:
        reasons.append(f"body too long ({word_count} words > {MAX_BODY_WORDS})")

    # Unrendered merge tags — the classic mass-mail embarrassment
    leftover = MERGE_TAG_RE.findall(subject + " " + body)
    if leftover:
        reasons.append(f"unrendered merge tags: {sorted(set(leftover))[:3]}")

    # Banned phrases (spam triggers + AI tells)
    lowered = (subject + " " + body).lower()
    hits = [p for p in BANNED_PHRASES if p in lowered]
    if hits:
        reasons.append(f"banned phrases: {hits[:3]}")

    # Link budget: cold email with links goes to spam
    links = URL_RE.findall(body)
    if len(links) > MAX_LINKS:
        reasons.append(f"too many links ({len(links)} > {MAX_LINKS})")

    if kind == "sequence" and postal_address.strip() and postal_address.strip() not in body:
        reasons.append("postal address footer missing")

    # No HTML — Harvey sends plain text only
    if re.search(r"<\s*(html|body|div|table|img|a)\b", body, re.IGNORECASE):
        reasons.append("HTML markup in body (plain text only)")

    return GateResult(ok=not reasons, reasons=reasons)


MAX_WHATSAPP_WORDS = 90
BARE_DOMAIN_RE = re.compile(
    r"\b[a-z0-9][a-z0-9-]*\.(?:com|net|org|me|io|app|dev|co|br|ai|site|online)\b", re.IGNORECASE)
def _link_key(url: str) -> str:
    return url.strip().rstrip(".,;:)!?/").lower()


def chat_brand(company: str) -> str:
    """The company's name as it can be said in a chat: "acme.me" -> "acme",
    because WhatsApp turns a written address into a link."""
    return company.split(".")[0] if BARE_DOMAIN_RE.search(company) else company


EXTENSION_RE = re.compile(r"(?<![\w.])\.[a-z]{2,}(?:\.[a-z]{2,})?\b", re.IGNORECASE)
PRICE_RE = re.compile(r"R\$\s*([\d][\d.,]*)", re.IGNORECASE)


def _price_digits(amount: str) -> str:
    """"1.500", "R$ 1.500,00" and "1500" are the same amount."""
    amount = re.sub(r"[^\d.,]", "", amount)
    amount = re.sub(r"[.,]00$", "", amount)
    return re.sub(r"\D", "", amount)


def whatsapp_check(
    phone: str,
    body: str,
    prospect=None,
    kind: str = "sequence",
    opt_out_line: str = "",
    allowed_prices: tuple[str, ...] | list[str] = (),
    allowed_links: list[str] | None = None,
) -> GateResult:
    """The pre-send checks for a WhatsApp message.

    opt_out_line: when set, a cold (sequence) message must contain it.
    allowed_prices: the only "R$" amounts the message may state.
    allowed_links: when given, the only URLs the message may carry.
    """
    reasons: list[str] = []
    body = (body or "").strip()
    phone = (phone or "").strip()

    if not phone.isdigit() or not 10 <= len(phone) <= 15:
        reasons.append(f"invalid recipient number '{phone}'")
    if prospect is not None:
        if (prospect.phone or "").strip() != phone:
            reasons.append("recipient does not match the prospect record")
        # Replies go back to someone who wrote to us; a cold message needs a
        # number WhatsApp confirmed exists.
        if kind == "sequence" and not prospect.phone_verified:
            reasons.append("number was not confirmed to have WhatsApp")

    if not body:
        reasons.append("empty body")
    words = len(body.split())
    if words > MAX_WHATSAPP_WORDS:
        reasons.append(f"body too long ({words} words > {MAX_WHATSAPP_WORDS})")
    leftover = MERGE_TAG_RE.findall(body)
    if leftover:
        reasons.append(f"unrendered merge tags: {sorted(set(leftover))[:3]}")
    lowered = body.lower()
    hits = [p for p in BANNED_PHRASES if p in lowered]
    if hits:
        reasons.append(f"banned phrases: {hits[:3]}")
    links = URL_RE.findall(body)
    if len(links) > MAX_LINKS:
        reasons.append(f"too many links ({len(links)} > {MAX_LINKS})")
    if allowed_links is not None:
        known = {_link_key(u) for u in allowed_links}
        if any(_link_key(u) not in known for u in links):
            reasons.append("link is not on the allowed list")
    # WhatsApp turns a bare address ("acme.me") into a link, which a link-free
    # opening must not contain. An extension named on its own (".com.br") is not one.
    scrubbed = EXTENSION_RE.sub(" ", URL_RE.sub(" ", body))
    if BARE_DOMAIN_RE.search(scrubbed):
        reasons.append("web address written without http (WhatsApp makes it a link)")
    # Prices come from the business, never from the model: any amount that is
    # not on the allowed list blocks the message.
    allowed = {_price_digits(p) for p in allowed_prices}
    stated = {_price_digits(m) for m in PRICE_RE.findall(body)}
    if stated - allowed:
        reasons.append(f"states a price that is not on the allowed list: {sorted(stated - allowed)[:3]}")
    if kind == "sequence" and opt_out_line.strip() and opt_out_line.strip() not in body:
        reasons.append("opt-out line missing")
    if re.search(r"<\s*(html|body|div|table|img|a)\b", body, re.IGNORECASE):
        reasons.append("HTML markup in body (plain text only)")

    return GateResult(ok=not reasons, reasons=reasons)
