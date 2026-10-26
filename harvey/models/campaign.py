"""Campaign data model."""

import json
import zlib
from datetime import datetime, timezone

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    """Naive UTC now (consistent with DB storage; avoids deprecated utcnow)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# The email 1 subject A/B test: each prospect gets one style, fixed by its id.
SUBJECT_STYLES = {"A": "names one specific thing about them",
                  "B": "is a short question"}


def subject_variant(prospect_id: str) -> str:
    return "AB"[zlib.crc32(prospect_id.encode()) % 2]


def pick_subject(subject: str, subject_b: str, prospect_id: str) -> tuple[str, str]:
    """(subject, variant) for this prospect; variant "" when there is no B to test."""
    if not subject_b:
        return subject, ""
    v = subject_variant(prospect_id)
    return (subject if v == "A" else subject_b), v


class EmailStep(BaseModel):
    step: int
    subject: str
    body: str
    delay_days: int = 0  # days after previous step
    subject_b: str = ""  # step 1 only: the B subject of the A/B test



class Campaign(BaseModel):
    id: str
    name: str = ""
    channel: str = "email"  # email/linkedin
    instantly_campaign_id: str = ""
    sequence: list[EmailStep] = Field(default_factory=list)
    status: str = "draft"  # draft/active/paused/completed
    prospect_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utcnow)

    def sequence_json(self) -> str:
        return json.dumps([s.model_dump() for s in self.sequence])

    @classmethod
    def sequence_from_json(cls, data: str | None) -> list[EmailStep]:
        """Parse a stored sequence; tolerates NULL/empty/corrupt JSON."""
        if not data:
            return []
        try:
            return [EmailStep(**s) for s in json.loads(data)]
        except (json.JSONDecodeError, TypeError):
            return []
