"""Contact data model — a person at a company."""

from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator


# Source tag for a prospect that is a company's general inbox, not a person.
COMPANY_INBOX_SOURCE = "company_inbox"
# Source tag for a prospect that is a business's own phone line (WhatsApp):
# no named person, reached through the number the company lists.
COMPANY_PHONE_SOURCE = "company_phone"
# Someone who wrote to us first on WhatsApp; nothing is known about them yet.
INBOUND_WHATSAPP_SOURCE = "inbound_whatsapp"
INBOUND_EMAIL_SOURCE = "inbound_email"


def _utcnow() -> datetime:
    """Naive UTC now (consistent with DB storage; avoids deprecated utcnow)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Prospect(BaseModel):
    id: str = ""
    company_id: str = ""       # FK to companies table
    first_name: str = ""       # required for a valid contact
    last_name: str = ""        # required for a valid contact
    email: str = ""
    email_verified: bool = False
    # verified / risky (catch-all) / guess (never auto-sent) / invalid / ""
    email_status: str = ""
    phone: str = ""
    phone_verified: bool = False
    linkedin_url: str = ""
    title: str = ""            # required for a valid contact
    seniority: str = ""        # c_suite, vp, director, manager, individual
    department: str = ""
    source: str = ""           # how we found them
    source_url: str = ""       # where we found the info
    status: str = "new"        # new/contacted/replied/meeting/closed/lost
    score: int = 0
    personalization_notes: str = ""
    # legacy fields kept for backwards compat with existing code
    company: str = ""
    industry: str = ""
    company_size: str = ""
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, v: str) -> str:
        """Emails are dedup keys — normalize so 'Jane@X.com' == 'jane@x.com'."""
        return (v or "").strip().lower()

    @field_validator("linkedin_url")
    @classmethod
    def _strip_linkedin(cls, v: str) -> str:
        return (v or "").strip()

    @property
    def is_company_inbox(self) -> bool:
        """A company's published general address (info@, office@) with no named person."""
        return self.source == COMPANY_INBOX_SOURCE

    @property
    def is_company_phone(self) -> bool:
        """A business's listed phone number with no named person behind it."""
        return self.source == COMPANY_PHONE_SOURCE

    def full_name(self) -> str:
        name = f"{self.first_name} {self.last_name}".strip()
        if not name and (self.is_company_inbox or self.is_company_phone):
            return f"{self.company} team" if self.company else "the team"
        return name

    def is_valid(self) -> bool:
        """A contact must have a name and title — or be a company inbox with an address."""
        if self.is_company_inbox:
            return bool(self.email and self.title)
        if self.is_company_phone:
            return bool(self.phone)
        return bool(self.first_name and self.last_name and self.title)
