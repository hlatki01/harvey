"""Google Calendar booking (harvey/integrations/calendar.py): free slots from
freeBusy, an event with a Meet link, and the Handler's offer -> accept -> book."""

import json
from datetime import datetime

import httpx
import pytest
import pytz

from harvey.config import NotifyConfig, OfferConfig
from harvey.gate import whatsapp_check
from harvey.integrations import calendar
from harvey.integrations.calendar import CalendarClient
from harvey.integrations.evolution import WhatsAppInbound
from tests.test_whatsapp import (  # noqa: F401  (state is a fixture)
    FakeEvolution, StubBrain, _config, _contacted_prospect, _handler, state)

SP = pytz.timezone("America/Sao_Paulo")
SLOTS = [{"start": "2030-01-08T10:00:00-03:00", "end": "2030-01-08T10:15:00-03:00"},
         {"start": "2030-01-09T09:00:00-03:00", "end": "2030-01-09T09:15:00-03:00"}]
MEET = "https://meet.google.com/abc-defg-hij"


def test_the_first_free_slot_of_each_working_day():
    now = SP.localize(datetime(2030, 1, 4, 8, 0))          # a Friday, 08:00
    busy = [(SP.localize(datetime(2030, 1, 4, 10, 0)), SP.localize(datetime(2030, 1, 4, 11, 0)))]
    slots = calendar.free_slots(busy, now, SP, "09:00-18:00", 30)
    # Friday: 10:00 is the earliest after the lead time, but busy until 11:00.
    # Saturday and Sunday are skipped.
    assert [s.strftime("%a %H:%M") for s, _ in slots] == ["Fri 11:00", "Mon 09:00", "Tue 09:00"]
    assert all((e - s).seconds == 1800 for s, e in slots)


def test_durations_and_labels():
    assert calendar.meeting_minutes("15 minutes") == 15
    assert calendar.meeting_minutes("1 hour") == 60
    assert calendar.format_slots(SLOTS, True) == "ter 08/01 às 10:00 ou qua 09/01 às 09:00"
    assert calendar.format_slot(SLOTS[0], False).startswith("Tue Jan 8 at 10:00")
    with pytest.raises(ValueError):
        OfferConfig(meeting_hours="18:00-09:00")


async def _token():
    return "TOKEN"


@pytest.mark.asyncio
async def test_slots_come_from_freebusy_and_booking_adds_meet():
    seen = []

    def handle(request):
        seen.append(request)
        if request.url.path.endswith("/freeBusy"):
            return httpx.Response(200, json={"calendars": {"primary": {"busy": []}}})
        return httpx.Response(200, json={"id": "ev1", "hangoutLink": MEET})

    config = _config()
    config.product.offer = OfferConfig(booking_method="google_calendar", meeting_duration="15 minutes")
    client = CalendarClient(config, transport=httpx.MockTransport(handle), token=_token)
    assert client.enabled()
    slots = await client.get_available_slots()
    assert len(slots) == 3 and seen[0].headers["Authorization"] == "Bearer TOKEN"

    assert await client.book_meeting(slots[0], "Demo", attendee_email="ana@acme.com") == {
        "id": "ev1", "link": MEET}
    booked = seen[-1]
    body = json.loads(booked.content)
    assert booked.url.params["sendUpdates"] == "all" and booked.url.params["conferenceDataVersion"] == "1"
    assert body["attendees"] == [{"email": "ana@acme.com"}]
    assert body["conferenceData"]["createRequest"]["conferenceSolutionKey"] == {"type": "hangoutsMeet"}


@pytest.mark.asyncio
async def test_a_refused_call_gives_nothing():
    down = httpx.MockTransport(lambda r: httpx.Response(403, text="insufficient scopes"))
    client = CalendarClient(_config(), transport=down, token=_token)
    assert await client.get_available_slots() == []
    assert await client.book_meeting(SLOTS[0], "Demo") is None


def test_a_meet_link_passes_the_whatsapp_gate():
    assert whatsapp_check("5511987654321", f"Combinado! O link: {MEET}", allowed_links=[])
    assert not whatsapp_check("5511987654321", "Veja https://evil.example/x", allowed_links=[])


class FakeCalendar:
    def __init__(self):
        self.booked = []

    def enabled(self):
        return True

    async def get_available_slots(self):
        return [dict(s) for s in SLOTS]

    async def book_meeting(self, slot, summary, description="", attendee_email=""):
        self.booked.append((slot, attendee_email))
        return {"id": "ev1", "link": MEET}


class BookingBrain(StubBrain):
    async def think_json(self, prompt, session_id=None, agent="", task="", model=""):
        if task == "pick_slot":
            self.prompts.append((task, prompt))
            return {"slot": 2}
        return await super().think_json(prompt, session_id, agent, task, model)


@pytest.mark.asyncio
async def test_whatsapp_offers_times_then_books_the_one_taken(state):
    await _contacted_prospect(state)
    client = FakeEvolution()
    client.inbound = [WhatsAppInbound("M1", "5511987654321", "Quero marcar uma reunião")]
    brain = BookingBrain(intent="interested", text="Ótimo! Tenho estes horários: {slots}. Qual fica melhor?")
    handler = _handler(state, client, brain)
    handler.calendar = FakeCalendar()
    handler.config.notify = NotifyConfig(whatsapp_number="(11) 91234-5678", events=["meeting"])
    await handler.run_whatsapp()

    [convo] = await state.get_conversations_by_status("open")    # offered, not handed over
    assert json.loads(convo.offered_slots) == SLOTS
    [offer] = await state.get_outbox(channel="whatsapp")
    assert "ter 08/01 às 10:00 ou qua 09/01 às 09:00" in offer["body"]
    assert "decide_whatsapp" in [t for t, _ in brain.prompts]
    assert "write {slots}" in brain.prompts[-1][1]

    client.inbound = [WhatsAppInbound("M2", "5511987654321", "Pode ser quarta")]
    await handler.run_whatsapp()

    assert handler.calendar.booked == [(SLOTS[1], "")]
    [convo] = await state.get_conversations_by_status("open")
    assert convo.stage == "closed_won" and convo.offered_slots == ""
    [reply] = [i["body"] for i in await state.get_outbox(channel="whatsapp") if MEET in i["body"]]
    assert reply.startswith("Combinado, reunião marcada para qua 09/01 às 09:00.")

    [push] = [t for n, t in client.sent if n == "5511912345678"]
    assert "meeting booked" in push and MEET in push
