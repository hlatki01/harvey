"""Instagram through instagrapi: the client over a fake instagrapi Client, the
gate, the Handler turning DMs into queued answers, cold openings for
discovered businesses, and the channel pausing when Instagram pushes back."""

import os
import tempfile
import time
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest
import pytest_asyncio
from instagrapi.exceptions import ChallengeRequired

from harvey.agents.handler import Handler
from harvey.agents.instagram import PAUSE_KEY, InstagramSender
from harvey.config import (
    EnvConfig, InstagramChannelConfig, NotifyConfig, WhatsAppChannelConfig, load_config,
)
from harvey.gate import instagram_check
from harvey.integrations.instagram import (
    InstagramBlocked, InstagramClient, InstagramInbound, plain_text,
)
from harvey.integrations.mail_provider import SendResult
from harvey.models import Company
from harvey.models.prospect import COMPANY_INSTAGRAM_SOURCE, INBOUND_INSTAGRAM_SOURCE, Prospect
from harvey.signals import seed_signal_catalog
from harvey.state import StateManager

OPT_OUT = WhatsAppChannelConfig().opt_out_line


def _dt(age=0):
    return datetime.fromtimestamp(time.time() - age, tz=timezone.utc)


def _msg(mid, uid, text, mine=False, age=0):
    return NS(id=mid, user_id=uid, text=text, is_sent_by_viewer=mine, timestamp=_dt(age), thread_id=7)


# ── The client, over a fake instagrapi Client ──


class FakeApi:
    user_id = "100"

    def __init__(self, threads=(), pending=(), fail=None):
        self.threads, self.pending, self.fail, self.sent = list(threads), list(pending), fail, []

    def direct_threads(self, amount=20):
        if self.fail:
            raise self.fail
        return self.threads

    def direct_pending_inbox(self, amount=20):
        return self.pending

    def direct_send(self, text, user_ids=()):
        self.sent.append((text, list(user_ids)))
        return NS(id="item9", thread_id=7)

    def search_users(self, q):
        return [NS(pk=1, username="dentalsorriso"), NS(pk=2, username="travado")]

    def user_info(self, pk):
        if pk == 2:
            raise ChallengeRequired("challenge_required")
        return NS(pk=1, username="dentalsorriso", full_name="Dental Sorriso", biography="Agende!",
                  external_url="https://linktr.ee/ds", follower_count=900, category="Dentist",
                  business_category_name=None, contact_phone_number="+5541999990000",
                  public_phone_country_code="", public_phone_number="",
                  is_business=True, is_private=False)


def _thread(users, msgs):
    return NS(users=[NS(pk=pk, username=u) for pk, u in users], messages=msgs)


@pytest.mark.asyncio
async def test_get_replies_reads_inbox_and_requests_and_skips_our_messages():
    api = FakeApi(
        threads=[_thread([(1, "padaria")], [_msg("m2", "1", "Quanto custa?"), _msg("m1", "100", "oi!", mine=True)])],
        pending=[_thread([(2, "novo")], [_msg("m3", "2", "Olá"), _msg("m4", "2", None)])])   # a sticker
    got = {m.provider_id: m for m in await InstagramClient(api=api).get_replies()}
    assert set(got) == {"m2", "m3"}
    assert got["m2"].user_id == "1" and got["m2"].known_contact is True
    assert got["m3"].known_contact is False and got["m3"].username == "novo"


@pytest.mark.asyncio
async def test_send_text_strips_formatting_and_goes_to_the_user_id():
    api = FakeApi()
    result = await InstagramClient(api=api).send_text("42", "Oi **Ana**")
    assert result.ok and result.message_id == "item9"
    assert api.sent == [("Oi Ana", [42])]


@pytest.mark.asyncio
async def test_instagram_pushing_back_becomes_instagram_blocked():
    client = InstagramClient(api=FakeApi(fail=ChallengeRequired("challenge_required")))
    with pytest.raises(InstagramBlocked):
        await client.get_replies()


@pytest.mark.asyncio
async def test_search_keeps_profiles_read_before_a_block():
    out = await InstagramClient(api=FakeApi()).search_profiles("dentista curitiba")
    assert [p["username"] for p in out] == ["dentalsorriso"]
    assert out[0]["phone"] == "+5541999990000" and out[0]["category"] == "Dentist"


def test_not_configured_without_a_login(tmp_path):
    assert not InstagramClient(session_file=tmp_path / "none.json").is_configured()
    assert InstagramClient(sessionid="1%3Aabc", session_file=tmp_path / "none.json").is_configured()


def test_plain_text_drops_chat_formatting():
    assert plain_text("*Oi* _tudo_ ~bem~? [site](https://a.com)") == "Oi tudo bem? site: https://a.com"
    assert plain_text("2*3 = 6") == "2*3 = 6"


# ── The gate ──


def test_gate_checks_recipient_and_the_cold_opt_out():
    p = Prospect(instagram_id="42", source=INBOUND_INSTAGRAM_SOURCE)
    assert instagram_check("42", "Oi! Posso ajudar?", p)
    assert not instagram_check("43", "Oi!", p)                    # someone else's record
    assert not instagram_check("@ana", "Oi!", Prospect(instagram_id="@ana"))
    assert not instagram_check("42", "Custa R$ 999", p)           # unlisted price
    assert not instagram_check("42", "Oi!", p, kind="sequence", opt_out_line=OPT_OUT)
    assert instagram_check("42", f"Oi!\n\n{OPT_OUT}", p, kind="sequence", opt_out_line=OPT_OUT)


# ── Handler and sender ──


@pytest_asyncio.fixture
async def state():
    with tempfile.TemporaryDirectory() as tmp:
        sm = StateManager(os.path.join(tmp, "test.db"))
        await sm.init_db()
        await seed_signal_catalog(sm)
        yield sm


class StubBrain:
    def __init__(self, text="Oi! Fazemos sim. Qual é o seu negócio?", action="answer", intent="question"):
        self.text, self.action, self.intent, self.prompts = text, action, intent, []
        self.paused_reason = ""

    async def think_json(self, prompt, session_id=None, agent="", task="", model=""):
        self.prompts.append((task, prompt))
        return {"action": self.action, "reply": self.text, "intent": self.intent,
                "confidence": "high", "reason": "stub"}

    async def think(self, prompt, session_id=None, agent="", task="", model=""):
        self.prompts.append((task, prompt))
        return self.text

    def load_skills_for_agent(self, *_):
        return ""

    def load_skill(self, *_):
        return ""

    def load_prompt(self, *_a, **_k):
        return ""


class FakeInstagram(InstagramClient):
    def __init__(self):
        super().__init__(sessionid="x" * 40)
        self.inbound, self.comments, self.sent, self.ids, self.block = [], [], [], {}, None

    async def get_replies(self, limit=50):
        if self.block:
            raise self.block
        return list(self.inbound)

    async def get_comments(self, max_age_days=7, media_limit=3):
        return list(self.comments)

    async def user_id_of(self, username):
        if username not in self.ids:
            raise RuntimeError("user not found")
        return self.ids[username]

    async def send_text(self, user_id, text):
        if self.block:
            raise self.block
        self.sent.append((user_id, text))
        return SendResult(ok=True, message_id=f"mid.{len(self.sent)}")


def _config(**ig):
    config = load_config()
    config.channels.whatsapp = WhatsAppChannelConfig()
    config.channels.instagram = InstagramChannelConfig(enabled=True, **ig)
    config.usage.business_hours = {}
    config.notify = NotifyConfig()
    return config


def _setup(state, brain=None, **ig):
    config = _config(**ig)
    env = EnvConfig(instagram_sessionid="x" * 40)
    client = FakeInstagram()
    brain = brain or StubBrain()
    handler = Handler(brain, state, config, env)
    handler.instagram = client
    return handler, InstagramSender(state, config, env, client=client, brain=brain, send_pacing=False), client


async def _convos(state):
    return [c for s in ("open", "needs_human") for c in await state.get_conversations_by_status(s)]


def _dm(mid="m1", uid="11", body="Vocês fazem site?", username="padaria.boa", age=60, **kw):
    return InstagramInbound(mid, uid, username, body, str(int(time.time() - age)), **kw)


@pytest.mark.asyncio
async def test_a_dm_from_someone_new_becomes_a_lead_and_a_reply_waits_for_approval(state):
    company_id = await state.add_company(Company(name="Padaria Boa", industry="padaria",
                                                 external_id="instagram:padaria.boa"))
    handler, sender, client = _setup(state, max_cold_per_day=0)
    client.inbound = [_dm()]
    assert await handler.run_instagram() is True

    prospect = await state.get_prospect_by_instagram_id("11")
    assert prospect.source == INBOUND_INSTAGRAM_SOURCE
    assert prospect.company_id == company_id and prospect.company == "Padaria Boa"   # found by discovery
    [convo] = await _convos(state)
    assert convo.channel == "instagram"
    [item] = await state.get_outbox(channel="instagram")
    assert item["status"] == "pending_review" and item["to_email"] == "11" and item["subject"] == "Instagram"
    assert "Instagram" in handler.brain.prompts[-1][1]

    await sender.run()
    assert client.sent == []                          # not approved yet
    await state.update_outbox_item(item["id"], status="approved")
    await sender.run()
    assert client.sent == [("11", item["body"])]
    [convo] = await _convos(state)
    assert convo.thread[-1].sender == "harvey"

    client.inbound = [_dm()]                          # read again: handled once
    await handler.run_instagram()
    assert len(await state.get_outbox(channel="instagram")) == 1


@pytest.mark.asyncio
async def test_the_inbox_history_and_existing_contacts_are_not_answered(state):
    handler, _, client = _setup(state)
    client.inbound = [_dm("m1", age=2 * 86400), _dm("m2", uid="12", known_contact=True)]
    assert await handler.run_instagram() is True
    assert await state.get_prospect_by_instagram_id("11") is None
    assert await state.get_prospect_by_instagram_id("12") is None
    assert await state.get_outbox(channel="instagram") == []


@pytest.mark.asyncio
async def test_a_keyword_comment_gets_a_dm_on_the_heartbeat_only(state):
    handler, sender, client = _setup(state, comment_keywords=["quero"], auto_approve_replies=True,
                                     max_cold_per_day=0)
    client.comments = [_dm("c1", uid="15", body="QUERO saber mais", username="ana", comment_id="c1"),
                       _dm("c2", uid="16", body="que lindo", username="bia", comment_id="c2")]
    await handler.run_instagram()                     # the live loop: no comment requests
    assert await state.get_outbox(channel="instagram") == []
    await handler.run_instagram(comments=True)
    [item] = await state.get_outbox(channel="instagram")
    assert item["status"] == "approved" and item["to_email"] == "15"
    await sender.run()
    assert [to for to, _ in client.sent] == ["15"]


@pytest.mark.asyncio
async def test_a_discovered_business_gets_one_cold_opening(state):
    await state.add_company(Company(name="Dental Sorriso", industry="dentista",
                                    external_id="instagram:dentalsorriso"))
    await state.add_company(Company(name="Sumida", external_id="instagram:sumida"))  # unreachable @
    handler, sender, client = _setup(state)
    client.ids = {"dentalsorriso": "77"}
    await sender.run()

    [item] = await state.get_outbox(channel="instagram")
    assert item["kind"] == "sequence" and item["status"] == "pending_review"
    assert item["to_email"] == "77" and item["body"].endswith(OPT_OUT)
    task, prompt = handler.brain.prompts[-1]
    assert "FIRST Instagram message" in prompt
    prospect = await state.get_prospect(item["prospect_id"])
    assert prospect.source == COMPANY_INSTAGRAM_SOURCE and prospect.instagram_handle == "dentalsorriso"

    await sender.run()                                # nobody is staged twice
    assert len(await state.get_outbox(channel="instagram")) == 1
    await state.update_outbox_item(item["id"], status="approved")
    await sender.run()
    assert client.sent == [("77", item["body"])]
    assert (await state.get_prospect(item["prospect_id"])).status == "contacted"


@pytest.mark.asyncio
async def test_a_business_with_a_phone_is_left_to_whatsapp_first(state):
    company_id = await state.add_company(Company(name="Pet Feliz", phone="41999990000",
                                                 external_id="instagram:petfeliz"))
    config = _config()
    config.channels.whatsapp.enabled = True
    assert await state.get_instagram_candidates(whatsapp_first=True, limit=5) == []
    # WhatsApp found no account on that number: Instagram may try.
    await state.add_prospect(Prospect(company_id=company_id, source="company_phone", status="no_whatsapp"))
    assert [c.id for c in await state.get_instagram_candidates(whatsapp_first=True, limit=5)] == [company_id]


@pytest.mark.asyncio
async def test_cold_openings_respect_the_daily_limit(state):
    for i in range(4):
        await state.add_company(Company(name=f"Loja {i}", external_id=f"instagram:loja{i}"))
    _, sender, client = _setup(state, max_cold_per_day=3)
    client.ids = {f"loja{i}": str(70 + i) for i in range(4)}
    for _ in range(3):
        await sender.run()
    assert len(await state.get_outbox(channel="instagram")) == 3


@pytest.mark.asyncio
async def test_instagram_pushing_back_pauses_the_channel(state):
    handler, sender, client = _setup(state, auto_approve_replies=True, max_cold_per_day=0)
    client.inbound = [_dm()]
    await handler.run_instagram()
    client.block = InstagramBlocked("ChallengeRequired: challenge_required")
    await sender.run()                                # the send is refused: paused, item kept
    assert "challenge" in await state.get_setting(PAUSE_KEY)
    [item] = await state.get_outbox(channel="instagram")
    assert item["status"] == "approved"
    client.block = None
    assert await handler.run_instagram() is True      # paused: nothing read, nothing sent
    await sender.run()
    assert client.sent == []
    await state.delete_setting(PAUSE_KEY)             # what Settings → Test does on success
    await sender.run()
    assert len(client.sent) == 1


@pytest.mark.asyncio
async def test_kill_switch_holds_instagram(state):
    handler, sender, client = _setup(state, auto_approve_replies=True)
    client.inbound = [_dm()]
    await handler.run_instagram()
    await state.set_setting("sending_paused", "test")
    await sender.run()
    assert client.sent == []


@pytest.mark.asyncio
async def test_disabled_channel_reads_nothing(state):
    config = _config()
    config.channels.instagram.enabled = False
    handler = Handler(StubBrain(), state, config, EnvConfig(instagram_sessionid="x" * 40))
    assert handler.instagram is None and await handler.run_instagram() is True


@pytest.mark.asyncio
async def test_a_browser_sessionid_is_saved_where_the_client_reads_it(tmp_path):
    from harvey.integrations.instagram import save_sessionid
    db = tmp_path / "harvey.db"
    old_session = tmp_path / "instagram_session.json"
    old_session.write_text("{}")
    await save_sessionid(StateManager(db_path=str(db)), "  123%3Aabc%3A9  \n")
    client = InstagramClient(db_file=db, session_file=tmp_path / "s.json")
    assert client._login_values() == ("", "", "123%3Aabc%3A9") and client.is_configured()
    assert not old_session.exists()   # the old login's session is dropped
    for bad in ("", "a b", "x" * 600):
        with pytest.raises(ValueError):
            await save_sessionid(StateManager(db_path=str(db)), bad)
