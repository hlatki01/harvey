"""Phone numbers as WhatsApp wants them: international digits, no "+".

Prospect.phone and outbox rows hold these digits ("5511987654321"), so one
person is one string everywhere: dedup, reply matching and sending.
"""

import re

BRAZIL = "55"


def normalize_phone(raw: str, default_country: str = BRAZIL) -> str:
    """International digits for a raw phone, or "" when it can't be one.

    A leading "+" or "00" means the country code is already there. Without
    one, a number that doesn't start with the default country code gets it
    added. Brazil's national numbers (area code + 8 or 9 digits) are checked
    strictly; elsewhere any 10-15 digit number passes.
    """
    text = (raw or "").strip()
    digits = re.sub(r"\D", "", text)
    if not digits:
        return ""
    if text.startswith("+"):
        pass
    elif digits.startswith("00"):
        digits = digits[2:]
    else:
        digits = digits.lstrip("0")  # trunk prefix: 011 9... -> 11 9...
        cc = re.sub(r"\D", "", default_country)
        # 12-13 digits starting with the country code are already international
        # (Brazil: 55 + 2-digit area + 8-9 digits).
        if not (digits.startswith(cc) and len(digits) >= len(cc) + 10
                and (cc != BRAZIL or len(digits) in (12, 13))):
            digits = cc + digits
    if digits.startswith(BRAZIL):
        if len(digits) not in (12, 13):
            return ""
    elif not 10 <= len(digits) <= 15:
        return ""
    return digits


def looks_like_mobile(phone: str) -> bool:
    """False for numbers that can't have WhatsApp, saving a lookup.

    Only Brazil is judged: a landline is area code + 8 digits starting 2-5.
    Everything else is left to the WhatsApp lookup.
    """
    if not phone.startswith(BRAZIL):
        return bool(phone)
    national = phone[2:]
    subscriber = national[2:]
    if len(subscriber) == 9:
        return subscriber[0] == "9"
    return len(subscriber) == 8 and subscriber[0] in "6789"
