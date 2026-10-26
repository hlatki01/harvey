"""Google Calendar booking, on the Gmail OAuth token (harvey gmail auth).

On when product.offer.booking_method is "google_calendar". Harvey offers the
first free slot of the next few working days (freeBusy on the primary
calendar, inside offer.meeting_hours) and books the one the lead takes as an
event with a Meet link. Every method degrades to [] / None, never raises.
"""

import logging
import re
import uuid
from datetime import datetime, time as dtime, timedelta, timezone

import httpx
import pytz

from harvey.integrations import gmail

logger = logging.getLogger("harvey.calendar")

API_BASE = "https://www.googleapis.com/calendar/v3"
SCOPES = ("https://www.googleapis.com/auth/calendar.events "
          "https://www.googleapis.com/auth/calendar.freebusy")
STEP = timedelta(minutes=30)
LEAD_TIME = timedelta(hours=2)    # never offer a slot that starts sooner than this
PT_DAYS = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")


def enabled(config) -> bool:
    offer = getattr(getattr(config, "product", None), "offer", None)
    return getattr(offer, "booking_method", "") == "google_calendar"


def meeting_minutes(duration: str) -> int:
    """"15 minutes" -> 15, "1 hour" -> 60."""
    m = re.search(r"\d+", duration or "")
    n = int(m.group()) if m else 15
    return n * 60 if "hour" in (duration or "").lower() and n < 10 else n


def free_slots(busy, now: datetime, tz, hours: str, minutes: int,
               count: int = 3, days_ahead: int = 7) -> list[tuple[datetime, datetime]]:
    """The first free slot of each working day, for up to `count` days.

    busy: (start, end) aware datetimes. hours: "09:00-18:00" in tz.
    """
    open_at, close_at = (dtime.fromisoformat(h.strip()) for h in hours.split("-"))
    length = timedelta(minutes=minutes)
    earliest = now + LEAD_TIME
    today = now.astimezone(tz).date()
    found = []
    for offset in range(days_ahead + 1):
        day = today + timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        start = tz.localize(datetime.combine(day, open_at))
        close = tz.localize(datetime.combine(day, close_at))
        while start + length <= close:
            end = start + length
            if start >= earliest and not any(b0 < end and start < b1 for b0, b1 in busy):
                found.append((start, end))
                break
            start += STEP
        if len(found) == count:
            break
    return found


def _parse(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def format_slot(slot: dict, portuguese: bool) -> str:
    start = _parse(slot["start"])
    if portuguese:
        return f"{PT_DAYS[start.weekday()]} {start:%d/%m} às {start:%H:%M}"
    return f"{start:%a %b} {start.day} at {start:%H:%M} ({start.tzname() or 'UTC'})"


def format_slots(slots: list[dict], portuguese: bool) -> str:
    labels = [format_slot(s, portuguese) for s in slots]
    if len(labels) < 2:
        return "".join(labels)
    return ", ".join(labels[:-1]) + (" ou " if portuguese else " or ") + labels[-1]


class CalendarClient:
    def __init__(self, config, env=None, transport=None, token=None):
        self.config = config
        self.env = env
        self.transport = transport
        self._token_fn = token

    def enabled(self) -> bool:
        return enabled(self.config)

    def _tz(self):
        name = getattr(getattr(getattr(self.config, "usage", None), "quiet_hours", None),
                       "timezone", "") or "UTC"
        return pytz.timezone(name)

    async def _token(self) -> str | None:
        if self._token_fn is not None:
            return await self._token_fn()
        granted = (gmail._load_token() or {}).get("scope", "")
        if "calendar.events" not in granted or "calendar.freebusy" not in granted:
            logger.warning("Calendar: the Google token has no calendar access. "
                           "Run 'harvey gmail auth' again.")
            return None
        if self.env is None:
            from harvey.config import load_env
            self.env = load_env()
        return await gmail.GmailProvider(self.config, self.env)._token()

    async def _call(self, path: str, body: dict, params: dict | None = None) -> dict | None:
        token = await self._token()
        if not token:
            return None
        try:
            async with httpx.AsyncClient(timeout=gmail.TIMEOUT, transport=self.transport) as client:
                r = await client.post(f"{API_BASE}{path}", json=body, params=params,
                                      headers={"Authorization": f"Bearer {token}"})
        except httpx.HTTPError as e:
            logger.warning(f"Calendar: {path} failed ({e or type(e).__name__}).")
            return None
        if r.status_code >= 300:
            logger.warning(f"Calendar: {path} returned HTTP {r.status_code}: {r.text[:200]}")
            return None
        return r.json()

    async def get_available_slots(self, count: int = 3, days_ahead: int = 7) -> list[dict]:
        """Free slots as [{"start": iso, "end": iso}], or [] when it can't tell."""
        offer = self.config.product.offer
        tz = self._tz()
        now = datetime.now(timezone.utc)
        data = await self._call("/freeBusy", {
            "timeMin": now.isoformat(),
            "timeMax": (now + timedelta(days=days_ahead + 1)).isoformat(),
            "timeZone": tz.zone,
            "items": [{"id": "primary"}],
        })
        if data is None:
            return []
        busy = [(_parse(b["start"]), _parse(b["end"]))
                for b in data.get("calendars", {}).get("primary", {}).get("busy", [])]
        return [{"start": s.isoformat(), "end": e.isoformat()}
                for s, e in free_slots(busy, now, tz, offer.meeting_hours,
                                       meeting_minutes(offer.meeting_duration), count, days_ahead)]

    async def book_meeting(self, slot: dict, summary: str, description: str = "",
                           attendee_email: str = "") -> dict | None:
        """Create the event with a Meet link; {"id", "link"} or None."""
        event = {
            "summary": summary,
            "description": description,
            "start": {"dateTime": slot["start"]},
            "end": {"dateTime": slot["end"]},
            "conferenceData": {"createRequest": {
                "requestId": uuid.uuid4().hex,
                "conferenceSolutionKey": {"type": "hangoutsMeet"}}},
        }
        if attendee_email:
            event["attendees"] = [{"email": attendee_email}]
        data = await self._call("/calendars/primary/events", event,
                                params={"conferenceDataVersion": 1, "sendUpdates": "all"})
        if data is None:
            return None
        return {"id": data.get("id", ""), "link": data.get("hangoutLink", "")}
