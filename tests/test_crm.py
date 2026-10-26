"""The CRM webhook (harvey/crm.py): one signed POST per stage change."""

import json

import httpx
import pytest

from harvey import crm
from harvey.config import CrmConfig
from harvey.integrations.evolution import WhatsAppInbound
from harvey.models.conversation import Conversation
from harvey.models.prospect import Prospect
from tests.test_whatsapp import (  # noqa: F401  (state is a fixture)
    FakeEvolution, StubBrain, _config, _contacted_prospect, _handler, state)

URL = "https://hooks.example.test/harvey"


def _crm_config():
    config = _config()
    config.crm = CrmConfig(webhook_url=URL)
    return config


def _convo():
    return Conversation(id="c1", prospect_id="p1", channel="email", stage="engaged",
                        intent="interested")


@pytest.mark.asyncio
async def test_a_stage_change_is_posted_and_signed(monkeypatch):
    monkeypatch.setenv("HARVEY_WEBHOOK_SECRET", "s3cret")
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(200)

    prospect = Prospect(id="p1", first_name="Ana", email="ana@acme.com", company="Acme")
    ok = await crm.post_stage_change(_crm_config(), prospect, _convo(), "initial_outreach",
                                     "Tell me more", transport=httpx.MockTransport(handle))
    assert ok
    [req] = seen
    assert str(req.url) == URL
    body = json.loads(req.content)
    assert body["old_stage"] == "initial_outreach" and body["new_stage"] == "engaged"
    assert body["prospect"]["email"] == "ana@acme.com" and body["last_reply"] == "Tell me more"
    assert req.headers[crm.SIGNATURE_HEADER] == crm.sign(req.content, "s3cret")


@pytest.mark.asyncio
async def test_no_url_posts_nothing_and_a_failure_does_not_raise(monkeypatch):
    monkeypatch.delenv("HARVEY_WEBHOOK_SECRET", raising=False)
    never = httpx.MockTransport(lambda r: pytest.fail("posted without a url"))
    assert not await crm.post_stage_change(_config(), Prospect(), _convo(), "", "", transport=never)

    down = httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("refused")))
    assert not await crm.post_stage_change(_crm_config(), Prospect(), _convo(), "", "", transport=down)
    err = httpx.MockTransport(lambda r: httpx.Response(500))
    assert not await crm.post_stage_change(_crm_config(), Prospect(), _convo(), "", "", transport=err)


def test_the_url_must_be_http():
    with pytest.raises(ValueError):
        CrmConfig(webhook_url="ftp://x")


@pytest.mark.asyncio
async def test_an_opt_out_reports_closed_lost_once(state, monkeypatch):
    posted = []

    async def record(config, prospect, convo, old_stage, reply_text, transport=None):
        posted.append((old_stage, convo.stage))
        return True

    monkeypatch.setattr(crm, "post_stage_change", record)
    await _contacted_prospect(state)
    client = FakeEvolution()
    client.inbound = [WhatsAppInbound("M1", "5511987654321", "SAIR")]
    handler = _handler(state, client, StubBrain())
    handler.config.crm = CrmConfig(webhook_url=URL)
    await handler.run_whatsapp()
    assert [new for _, new in posted] == ["closed_lost"]
