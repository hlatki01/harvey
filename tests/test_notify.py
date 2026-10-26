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
    assert [t for _, t in client.sent] == [
        "Harvey: 1 message waiting for approval. Nothing goes out until you approve it."]
