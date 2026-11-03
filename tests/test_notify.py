"""Pushes to the owner's WhatsApp (harvey/notify.py): once per key, only the
events asked for, and the owner's own messages never become a lead."""

import pytest

from harvey import notify
from harvey.config import NotifyConfig
from harvey.integrations.evolution import WhatsAppInbound
from tests.test_whatsapp import (  # noqa: F401  (state is a fixture)
    FakeEvolution, StubBrain, _config, _contacted_prospect, _handler, state)

OWNER = "5511912345678"


def _notifying(**overrides):
    config = _config(allowed_prices=["1.500", "297"], inbound_leads=True)
    config.notify = NotifyConfig(whatsapp_number="(11) 91234-5678", **overrides)
    return config


@pytest.mark.asyncio
async def test_a_push_goes_out_once_per_key(state):
    client = FakeEvolution()
    config = _notifying()
    assert await notify.push(state, config, "hot_lead", "k1", "hello", client)
    assert not await notify.push(state, config, "hot_lead", "k1", "hello again", client)
    assert client.sent == [(OWNER, "hello")]


@pytest.mark.asyncio
async def test_a_failed_push_is_retried_next_time(state):
    client = FakeEvolution()
    client.fail_send = True
    config = _notifying()
    assert not await notify.push(state, config, "system", "k", "x", client)
    client.fail_send = False
    assert await notify.push(state, config, "system", "k", "x", client)


@pytest.mark.asyncio
async def test_no_number_or_an_event_left_out_sends_nothing(state):
    client = FakeEvolution()
    config = _config()
    assert not await notify.push(state, config, "hot_lead", "k", "x", client)
    config = _notifying(events=["handoff"])
    assert not await notify.push(state, config, "hot_lead", "k", "x", client)
    assert client.sent == []


def test_unknown_events_and_stages_are_refused():
    with pytest.raises(ValueError):
        NotifyConfig(events=["everything"])
    with pytest.raises(ValueError):
        NotifyConfig(hot_stages=["warm"])


def test_the_owner_is_recognised_without_the_ninth_digit():
    config = _notifying()
    assert notify.is_owner(config, OWNER)
    assert notify.is_owner(config, "551112345678")
    assert not notify.is_owner(config, "5511987654321")
    assert not notify.is_owner(_config(), OWNER)


@pytest.mark.asyncio
async def test_a_hand_over_pushes_to_the_owner_once(state):
    await _contacted_prospect(state)
    client = FakeEvolution()
    client.inbound = [WhatsAppInbound("M1", "5511987654321", "Em quantos dias fica pronto?")]
    handler = _handler(state, client, StubBrain(
        intent="question", action="handoff", text="Vou confirmar com a equipe e já te respondo."))
    handler.config = _notifying(events=["handoff"])
    await handler.run_whatsapp()

    pushes = [t for n, t in client.sent if n == OWNER]
    assert len(pushes) == 1 and "needs you" in pushes[0] and "Em quantos dias" in pushes[0]
    await notify.push_alerts(state, handler.config, client)     # the heartbeat's sweep
    assert len([n for n, _ in client.sent if n == OWNER]) == 1


@pytest.mark.asyncio
async def test_a_hot_lead_pushes_when_it_enters_a_hot_stage(state):
    await _contacted_prospect(state)
    client = FakeEvolution()
    client.inbound = [WhatsAppInbound("M1", "5511987654321", "Tenho interesse, me conta mais")]
    handler = _handler(state, client, StubBrain(intent="interested", text="Que bom! O que vocês fazem hoje?"))
    handler.config = _notifying(events=["hot_lead"])
    await handler.run_whatsapp()

    [convo] = await state.get_conversations_by_status("open")
    assert convo.stage in handler.config.notify.hot_stages
    [push] = [t for n, t in client.sent if n == OWNER]
    assert "hot lead" in push and convo.stage in push


@pytest.mark.asyncio
async def test_the_owner_writing_back_is_not_a_lead(state):
    client = FakeEvolution()
    client.inbound = [WhatsAppInbound("M1", "551112345678", "ok, vou ver")]
    handler = _handler(state, client, StubBrain())
    handler.config = _notifying()
    assert await handler.run_whatsapp() is True
    assert await state.get_prospect_by_phone("551112345678") is None
    assert await state.get_conversations_by_status("open") == []


@pytest.mark.asyncio
async def test_approvals_push_at_most_once_per_window(state):
    await state.add_outbox_item(prospect_id="p1", to_email="a@x.com", subject="s", body="b",
                                send_at="2020-01-01T00:00:00", status="pending_review")
    client = FakeEvolution()
    config = _notifying(approvals_every_minutes=60)
    await notify.push_alerts(state, config, client)
    await notify.push_alerts(state, config, client)
    [text] = [t for _, t in client.sent]
    assert text.startswith("Roadie Crew: 1 message waiting for approval.") and "a@x.com" in text


@pytest.mark.asyncio
async def test_the_owner_approves_and_rejects_from_whatsapp(state):
    a = await state.add_outbox_item(prospect_id="p1", to_email="a@x.com", subject="s", body="hi A",
                                    send_at="2020-01-01T00:00:00", status="pending_review")
    b = await state.add_outbox_item(prospect_id="p2", to_email="b@x.com", subject="s", body="hi B",
                                    send_at="2020-01-01T00:00:00", status="pending_review")
    client = FakeEvolution()
    client.inbound = [WhatsAppInbound("O1", OWNER, f"ok {a[:6]}"),
                      WhatsAppInbound("O2", OWNER, f"não {b[:6]}"),
                      WhatsAppInbound("O3", OWNER, "no all")]
    handler = _handler(state, client, StubBrain())
    handler.config = _notifying()
    await handler.run_whatsapp()
    await handler.run_whatsapp()     # processed once only

    assert (await state.get_outbox_item(a))["status"] == "approved"
    assert (await state.get_outbox_item(b))["status"] == "rejected"
    assert [t for _, t in client.sent] == [f"Roadie Crew: {a[:6]} approved.", f"Roadie Crew: {b[:6]} rejected."]


@pytest.mark.asyncio
async def test_a_hand_over_push_links_straight_to_the_conversation(state):
    await _contacted_prospect(state)
    client = FakeEvolution()
    client.inbound = [WhatsAppInbound("M1", "5511987654321", "Em quantos dias fica pronto?")]
    handler = _handler(state, client, StubBrain(
        intent="question", action="handoff", text="Vou confirmar com a equipe e já te respondo."))
    handler.config = _notifying(events=[])        # handed over before alerts were on
    handler.config.dashboard.public_url = "https://painel.example.com"
    await handler.run_whatsapp()
    assert not [n for n, _ in client.sent if n == OWNER]

    handler.config.notify.events = ["handoff"]    # the heartbeat's sweep catches it up, with the link
    await notify.push_alerts(state, handler.config, client)
    convo_id = (await state.get_handoffs())[0]["id"]
    pushes = [t for n, t in client.sent if n == OWNER]
    assert len(pushes) == 1 and pushes[0].endswith(f"\nhttps://painel.example.com/?conversation={convo_id}")


@pytest.mark.asyncio
async def test_an_objection_is_not_a_hot_lead(state):
    await _contacted_prospect(state)
    client = FakeEvolution()
    client.inbound = [WhatsAppInbound("M1", "5511987654321", "Agora não dá, estamos sem tempo")]
    handler = _handler(state, client, StubBrain(intent="objection", text="Sem problemas."))
    handler.config = _notifying(events=["hot_lead"])
    await handler.run_whatsapp()

    assert not [t for n, t in client.sent if n == OWNER]


@pytest.mark.asyncio
async def test_writing_after_opting_out_is_never_answered_or_a_lead(state):
    """'Sair', then 'obrigado': the opt-out stands, nobody is pushed or answered."""
    pid = await _contacted_prospect(state)
    await state.update_prospect_status(pid, "opted_out")
    client = FakeEvolution()
    client.inbound = [WhatsAppInbound("M1", "5511987654321", "obrigado")]
    handler = _handler(state, client, StubBrain(intent="interested", text="Que bom!"))
    handler.config = _notifying(events=["hot_lead", "handoff"])
    await handler.run_whatsapp()

    assert (await state.get_prospect_by_phone("5511987654321")).status == "opted_out"
    assert client.sent == []
    assert await state.get_conversations_by_status("open") == []


def test_a_reply_seconds_after_ours_is_flagged_as_likely_automatic():
    from harvey.agents.handler import _timing_note
    sent = ["2026-11-03T14:37:46.711579", None]
    at = 1793716666 + 5      # 2026-11-03 14:37:46 UTC, plus 5 s
    assert "4 seconds" in _timing_note(str(at), sent)
    assert _timing_note(str(at + 300), sent) == ""
    assert _timing_note("", sent) == "" and _timing_note(str(at), [None]) == ""
