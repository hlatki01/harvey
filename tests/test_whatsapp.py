"""WhatsApp channel: phone handling, the Evolution client, the outbox channel
split, the WhatsApp sender (stage / drain) and reading replies."""

import json
import os
import tempfile

import httpx
import pytest
import pytest_asyncio

from harvey.agents.handler import Handler
from harvey.agents.whatsapp import WhatsAppSender
from harvey.config import DemoConfig, EnvConfig, WhatsAppChannelConfig, load_config
from harvey.gate import whatsapp_check
from harvey.integrations.evolution import EvolutionClient, WhatsAppInbound
from harvey.integrations.mail_provider import SendResult
from harvey.models.company import Company
from harvey.models.conversation import Conversation
from harvey.models.prospect import COMPANY_PHONE_SOURCE, Prospect
from harvey.phone import looks_like_mobile, normalize_phone
from harvey.signals import seed_signal_catalog
from harvey.state import StateManager

OPT_OUT = "Se preferir não receber mensagens, responda SAIR."


# ── phone ──


@pytest.mark.parametrize("raw,expected", [
    ("(11) 98765-4321", "5511987654321"),
    ("11 98765-4321", "5511987654321"),
    ("011 98765-4321", "5511987654321"),
    ("+55 11 98765-4321", "5511987654321"),
    ("5511987654321", "5511987654321"),
    ("0055 11 98765-4321", "5511987654321"),
    ("(11) 4000-1111", "551140001111"),          # landline: valid number
    ("98765-4321", ""),                            # no area code
    ("", ""),
    ("abc", ""),
])
def test_normalize_phone_brazil(raw, expected):
    assert normalize_phone(raw) == expected


def test_normalize_phone_other_country_and_default():
    assert normalize_phone("+1 (303) 555-0100") == "13035550100"
    assert normalize_phone("303 555 0100", default_country="1") == "13035550100"


def test_looks_like_mobile_filters_brazilian_landlines():
    assert looks_like_mobile("5511987654321")        # 9 digits starting with 9
    assert not looks_like_mobile("551140001111")     # 8 digits starting with 4
    assert looks_like_mobile("551187654321")         # old 8-digit mobile
    assert looks_like_mobile("13035550100")          # not Brazil: left to the lookup


# ── gate ──


def _prospect(phone="5511987654321", verified=True):
    return Prospect(phone=phone, phone_verified=verified, source=COMPANY_PHONE_SOURCE)


def test_whatsapp_gate_accepts_a_clean_opening():
    body = f"Oi, aqui é o Luis, da Acme. Vi que vocês não têm site. Faz sentido conversar?\n\n{OPT_OUT}"
    assert whatsapp_check("5511987654321", body, _prospect(), opt_out_line=OPT_OUT)


def test_whatsapp_gate_blocks_what_it_must():
    p = _prospect()
    cases = {
        "invalid recipient": whatsapp_check("123", f"oi\n{OPT_OUT}", p, opt_out_line=OPT_OUT),
        "does not match": whatsapp_check("5511900000000", f"oi\n{OPT_OUT}", p, opt_out_line=OPT_OUT),
        "not confirmed": whatsapp_check("5511987654321", f"oi\n{OPT_OUT}",
                                         _prospect(verified=False), opt_out_line=OPT_OUT),
        "opt-out line missing": whatsapp_check("5511987654321", "oi tudo bem?", p, opt_out_line=OPT_OUT),
        "too many links": whatsapp_check("5511987654321", f"https://a.com https://b.com\n{OPT_OUT}",
                                         p, opt_out_line=OPT_OUT),
        "banned phrases": whatsapp_check("5511987654321", f"act now!\n{OPT_OUT}", p, opt_out_line=OPT_OUT),
        "unrendered merge tags": whatsapp_check("5511987654321", f"oi {{{{first_name}}}}\n{OPT_OUT}",
                                                p, opt_out_line=OPT_OUT),
        "too long": whatsapp_check("5511987654321", ("palavra " * 100) + OPT_OUT, p, opt_out_line=OPT_OUT),
    }
    for needle, result in cases.items():
        assert not result, needle
        assert any(needle in r for r in result.reasons), (needle, result.reasons)


def test_whatsapp_gate_blocks_loose_web_addresses_and_prices_but_allows_a_demo_link():
    p = _prospect()
    loose = f"Oi, aqui é o Theo da guilhermeh.me. Posso perguntar?\n\n{OPT_OUT}"
    assert any("without http" in r for r in whatsapp_check("5511987654321", loose, p, opt_out_line=OPT_OUT).reasons)
    priced = f"Fica R$ 500 por mês.\n\n{OPT_OUT}"
    assert any("price" in r for r in whatsapp_check("5511987654321", priced, p, opt_out_line=OPT_OUT).reasons)
    demo = "Veja um exemplo: https://demo-salao.guilhermeh.me"
    assert whatsapp_check("5511987654321", demo, p, kind="reply")


def test_whatsapp_gate_reply_needs_neither_opt_out_nor_confirmed_number():
    p = _prospect(verified=False)
    assert whatsapp_check("5511987654321", "Claro, te explico.", p, kind="reply", opt_out_line=OPT_OUT)


# ── Evolution client ──


def _client(handler):
    return EvolutionClient("http://evo.test/", "KEY", "inst", transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_check_numbers_returns_the_number_whatsapp_knows():
    seen = {}

    def handler(request):
        seen["path"], seen["key"] = request.url.path, request.headers["apikey"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=[
            {"exists": True, "jid": "551187654321@s.whatsapp.net", "number": "5511987654321"},
            {"exists": False, "jid": "5511900000000@s.whatsapp.net", "number": "5511900000000"},
        ])

    found = await _client(handler).check_numbers(["5511987654321", "5511900000000"])
    assert found == {"5511987654321": "551187654321"}  # registered without the ninth digit
    assert seen["path"] == "/chat/whatsappNumbers/inst" and seen["key"] == "KEY"
    assert seen["body"] == {"numbers": ["5511987654321", "5511900000000"]}


@pytest.mark.asyncio
async def test_send_text_ok_and_error():
    def ok(request):
        body = json.loads(request.content)
        assert body["number"] == "5511987654321" and body["text"] == "oi" and body["delay"] > 0
        assert request.url.path == "/message/sendText/inst"
        return httpx.Response(201, json={"key": {"id": "ABC", "remoteJid": "5511987654321@s.whatsapp.net"}})

    result = await _client(ok).send_text("5511987654321", "oi")
    assert result.ok and result.message_id == "ABC"

    result = await _client(lambda r: httpx.Response(400, json={"message": "bad number"})
                           ).send_text("5511987654321", "oi")
    assert not result.ok and "bad number" in result.error


def _record(msg_id, jid, text=None, from_me=False, ts=1, extended=False):
    message = ({"extendedTextMessage": {"text": text}} if extended
               else {"conversation": text}) if text is not None else {"imageMessage": {}}
    return {"key": {"id": msg_id, "remoteJid": jid, "fromMe": from_me},
            "message": message, "messageTimestamp": ts, "pushName": "Zé"}


@pytest.mark.asyncio
async def test_get_replies_keeps_only_inbound_text_from_individuals():
    records = [
        _record("1", "5511987654321@s.whatsapp.net", "quero saber mais", ts=5),
        _record("2", "5511987654321@s.whatsapp.net", "segunda", ts=9, extended=True),
        _record("3", "120363@g.us", "mensagem de grupo"),                       # group
        _record("4", "5511987654321@s.whatsapp.net", "eu mesmo", from_me=True),  # ours
        _record("5", "5511987654321@s.whatsapp.net", None),                      # media
    ]
    for payload in ({"messages": {"records": records}}, records):  # v2 paged, and bare list
        out = await _client(lambda r: httpx.Response(200, json=payload)).get_replies()
        assert [(m.provider_id, m.phone, m.body) for m in out] == [
            ("1", "5511987654321", "quero saber mais"),
            ("2", "5511987654321", "segunda"),
        ]


@pytest.mark.asyncio
async def test_get_replies_maps_anonymous_chat_ids_to_the_phone_when_known():
    with_phone = _record("7", "98765432101234@lid", "oi, sou eu", ts=3)
    with_phone["key"]["remoteJidAlt"] = "5511987654321@s.whatsapp.net"
    unknown = _record("8", "11112222333344@lid", "quem sou eu?", ts=4)
    out = await _client(lambda r: httpx.Response(200, json=[with_phone, unknown])).get_replies()
    assert [(m.provider_id, m.phone) for m in out] == [("7", "5511987654321")]


@pytest.mark.asyncio
async def test_test_connection_states():
    assert not (await EvolutionClient("", "", "").test_connection())[0]
    ok, detail = await _client(
        lambda r: httpx.Response(200, json={"instance": {"state": "open"}})).test_connection()
    assert ok and "connected" in detail
    ok, detail = await _client(
        lambda r: httpx.Response(200, json={"instance": {"state": "close"}})).test_connection()
    assert not ok and "QR" in detail
    ok, detail = await _client(lambda r: httpx.Response(401, json={"message": "Unauthorized"})).test_connection()
    assert not ok and "401" in detail


# ── state: channel split, candidates ──


@pytest_asyncio.fixture
async def state():
    with tempfile.TemporaryDirectory() as tmp:
        sm = StateManager(os.path.join(tmp, "test.db"))
        await sm.init_db()
        await seed_signal_catalog(sm)
        yield sm


async def _company(state, name="Oficina do Zé", phone="(11) 98765-4321", signals=()):
    cid = await state.add_company(Company(
        name=name, phone=phone, industry="Oficina mecânica", location="São Paulo, SP",
        external_id=f"google_places:{name}"))
    await state.add_observations([
        {"company_id": cid, "signal_code": code, "collector": "test", "value_num": 1.0}
        for code in signals])
    return cid


@pytest.mark.asyncio
async def test_outbox_channel_isolation_and_counts(state):
    await state.add_outbox_item(prospect_id="p1", to_email="a@x.com", subject="s", body="b",
                                send_at="2020-01-01T00:00:00", status="approved")
    await state.add_outbox_item(prospect_id="p2", to_email="5511987654321", subject="WhatsApp",
                                body="b", send_at="2020-01-01T00:00:00", status="approved",
                                channel="whatsapp")
    assert [i["to_email"] for i in await state.get_outbox(status="approved", channel="email")] == ["a@x.com"]
    assert [i["to_email"] for i in await state.get_outbox(status="approved", channel="whatsapp")] == ["5511987654321"]
    assert len(await state.get_outbox(status="approved")) == 2          # the desk sees both
    assert await state.has_outbox_for_prospect("p2", "whatsapp")
    assert not await state.has_outbox_for_prospect("p2", "email")


@pytest.mark.asyncio
async def test_whatsapp_candidates_respect_signals_and_tried_companies(state):
    nosite = await _company(state, "Sem Site", signals=["NO_WEBSITE"])
    await _company(state, "Com Site", phone="(11) 97777-0000")
    await _company(state, "Sem Telefone", phone="")

    everyone = {c.name for c in await state.get_whatsapp_candidates([], [], 10)}
    assert everyone == {"Sem Site", "Com Site"}
    only = await state.get_whatsapp_candidates(["NO_WEBSITE"], [], 10)
    assert [c.name for c in only] == ["Sem Site"]
    excluded = await state.get_whatsapp_candidates([], ["NO_WEBSITE"], 10)
    assert [c.name for c in excluded] == ["Com Site"]

    await state.add_prospect(Prospect(company_id=nosite, source=COMPANY_PHONE_SOURCE,
                                      status="no_whatsapp"))
    assert {c.name for c in await state.get_whatsapp_candidates([], [], 10)} == {"Com Site"}


# ── WhatsApp sender ──


class FakeEvolution(EvolutionClient):
    def __init__(self, existing=None):
        super().__init__("http://evo.test", "KEY", "inst")
        self.existing = existing or {}      # asked number -> known number
        self.checked, self.sent, self.inbound = [], [], []
        self.fail_send = False

    async def check_numbers(self, numbers):
        self.checked += numbers
        return {n: self.existing[n] for n in numbers if n in self.existing}

    async def send_text(self, number, text):
        if self.fail_send:
            return SendResult(ok=False, error="boom")
        self.sent.append((number, text))
        return SendResult(ok=True, message_id=f"WA{len(self.sent)}")

    async def get_replies(self, limit=50):
        return self.inbound

    stranger = True     # the account never wrote to this number

    async def is_new_contact(self, phone, own_ids=frozenset()):
        return self.stranger


class StubBrain:
    """think() writes the opening message and classifies intent; think_json()
    answers the decision engine with `action` and `text` (or `decision`, whole)."""

    def __init__(self, intent="interested",
                 text="Oi, aqui é o Luis, da Acme. Vi que vocês não têm site. Faz sentido?",
                 action="answer", confidence="high", decision=None):
        self.intent, self.text, self.prompts = intent, text, []
        self.action, self.confidence, self.decision = action, confidence, decision
        self.paused_reason = ""

    async def think_json(self, prompt, session_id=None, agent="", task="", model=""):
        self.prompts.append((task, prompt))
        if self.decision is not None:
            return self.decision
        return {"action": self.action, "reply": self.text, "intent": self.intent,
                "confidence": self.confidence, "reason": "stub"}

    def load_skills_for_agent(self, *_):
        return ""

    def load_skill(self, *_):
        return ""

    def load_prompt(self, *_a, **_k):
        return ""

    async def think(self, prompt, session_id=None, agent="", task=""):
        self.prompts.append((task, prompt))
        if task == "classify_intent":
            return self.intent
        return self.text


def _config(**overrides):
    config = load_config()
    config.channels.whatsapp = WhatsAppChannelConfig(enabled=True, **overrides)
    return config


def _env():
    return EnvConfig(evolution_api_url="http://evo.test", evolution_api_key="KEY",
                     evolution_instance="inst")


def _sender(state, client, brain=None, **cfg):
    sender = WhatsAppSender(brain or StubBrain(), state, _config(**cfg), _env(),
                            client=client, send_pacing=False)
    return sender


@pytest.mark.asyncio
async def test_opening_prompt_names_the_company_without_its_web_address(state):
    await _company(state)
    client = FakeEvolution({"5511987654321": "5511987654321"})
    config = _config()
    config.persona.company = "acme.me"
    brain = StubBrain()
    await WhatsAppSender(brain, state, config, _env(), client=client, send_pacing=False)._stage()
    first_line = brain.prompts[0][1].splitlines()[0]
    assert " at acme. " in first_line and "acme.me" not in first_line


@pytest.mark.asyncio
async def test_stage_drafts_one_message_with_the_opt_out_line(state):
    cid = await _company(state, signals=["NO_WEBSITE"])
    client = FakeEvolution({"5511987654321": "5511987654321"})
    brain = StubBrain()
    await _sender(state, client, brain)._stage()

    [item] = await state.get_outbox(channel="whatsapp")
    assert item["status"] == "pending_review" and item["subject"] == "WhatsApp"
    assert item["to_email"] == "5511987654321" and item["kind"] == "sequence"
    assert item["body"].endswith(OPT_OUT) and "não têm site" in item["body"]
    assert "has no website" in brain.prompts[0][1]            # the observed fact reached the prompt
    prospect = await state.get_prospect(item["prospect_id"])
    assert prospect.source == COMPANY_PHONE_SOURCE and prospect.phone_verified
    assert prospect.company_id == cid and prospect.status == "queued"


@pytest.mark.asyncio
async def test_stage_records_unreachable_numbers_and_never_retries_them(state):
    await _company(state, "Fixo", phone="(11) 4000-1111")                # landline: no lookup
    await _company(state, "Sem Zap", phone="(11) 98888-0000")            # lookup says no
    client = FakeEvolution()
    sender = _sender(state, client)
    await sender._stage()

    assert await state.get_outbox(channel="whatsapp") == []
    assert client.checked == ["5511988880000"]                          # the landline cost nothing
    assert await state.get_whatsapp_candidates([], [], 10) == []        # both are marked tried
    await sender._stage()
    assert client.checked == ["5511988880000"]


@pytest.mark.asyncio
async def test_stage_sends_to_the_number_whatsapp_knows(state):
    await _company(state)
    client = FakeEvolution({"5511987654321": "551187654321"})
    await _sender(state, client)._stage()
    [item] = await state.get_outbox(channel="whatsapp")
    assert item["to_email"] == "551187654321"
    assert (await state.get_prospect_by_phone("551187654321")).id == item["prospect_id"]


@pytest.mark.asyncio
async def test_stage_skips_a_number_two_businesses_share(state):
    await _company(state, "Um", phone="(11) 98765-4321")
    await _company(state, "Dois", phone="(11) 98765-4321")
    client = FakeEvolution({"5511987654321": "5511987654321"})
    await _sender(state, client, max_new_per_cycle=5)._stage()
    assert len(await state.get_outbox(channel="whatsapp")) == 1


@pytest.mark.asyncio
async def test_stage_rejects_a_draft_that_fails_the_gate(state):
    await _company(state)
    client = FakeEvolution({"5511987654321": "5511987654321"})
    sender = _sender(state, client, StubBrain(text="Aproveite, act now! https://a.com https://b.com"))
    await sender._stage()
    assert await state.get_outbox(channel="whatsapp") == []
    prospect = await state.get_prospect_by_phone("5511987654321")
    assert prospect.status == "write_failed"


@pytest.mark.asyncio
async def test_stage_respects_the_daily_cap_and_per_cycle_limit(state):
    for i in range(6):
        await _company(state, f"Empresa {i}", phone=f"(11) 9{i}000-0000")
    client = FakeEvolution({f"55119{i}0000000": f"55119{i}0000000" for i in range(6)})
    sender = _sender(state, client, max_new_per_cycle=2, max_daily_sends=3)
    await sender._stage()
    assert len(await state.get_outbox(channel="whatsapp")) == 2
    await sender._stage()                                   # 1 of the 3 left
    assert len(await state.get_outbox(channel="whatsapp")) == 3
    await sender._stage()                                   # full
    assert len(await state.get_outbox(channel="whatsapp")) == 3


@pytest.mark.asyncio
async def test_nothing_is_sent_until_approved_then_it_goes_once(state):
    await _company(state)
    client = FakeEvolution({"5511987654321": "5511987654321"})
    sender = _sender(state, client)
    await sender._stage()
    await sender._drain()
    assert client.sent == []                                  # pending_review is not sent

    [item] = await state.get_outbox(channel="whatsapp")
    await state.approve_outbox(item["id"])
    await sender._drain()
    await sender._drain()
    assert len(client.sent) == 1 and client.sent[0][0] == "5511987654321"
    done = await state.get_outbox_item(item["id"])
    assert done["status"] == "sent" and done["message_id"] == "WA1"
    assert (await state.get_prospect(item["prospect_id"])).status == "contacted"


@pytest.mark.asyncio
async def test_auto_approve_mode_stages_as_approved(state):
    await _company(state)
    client = FakeEvolution({"5511987654321": "5511987654321"})
    sender = _sender(state, client, require_approval=False)
    await sender._stage()
    await sender._drain()
    assert len(client.sent) == 1


@pytest.mark.asyncio
async def test_drain_blocks_a_prospect_who_replied_and_records_failures(state):
    await _company(state, "Um")
    await _company(state, "Dois", phone="(11) 97777-0000")
    client = FakeEvolution({"5511987654321": "5511987654321", "5511977770000": "5511977770000"})
    sender = _sender(state, client, require_approval=False)
    await sender._stage()
    first, second = await state.get_outbox(channel="whatsapp")
    await state.update_prospect_status(first["prospect_id"], "replied")
    client.fail_send = True
    await sender._drain()
    assert (await state.get_outbox_item(first["id"]))["status"] == "cancelled"
    failed = await state.get_outbox_item(second["id"])
    assert failed["status"] == "failed" and "boom" in failed["error"]
    assert client.sent == []


@pytest.mark.asyncio
async def test_daily_cap_counts_only_whatsapp(state):
    await state.add_outbox_item(prospect_id="e", to_email="e@x.com", subject="s", body="b",
                                send_at="2020-01-01T00:00:00", status="approved")
    item = await state.get_outbox(channel="email")
    await state.claim_outbox_item(item[0]["id"])
    await state.update_outbox_item(item[0]["id"], status="sent",
                                   sent_at="2099-01-01T00:00:00")
    assert await state.count_outbox_sent_today(channel="whatsapp") == 0


# ── reading replies ──


async def _contacted_prospect(state, phone="5511987654321"):
    cid = await _company(state)
    pid = await state.add_prospect(Prospect(
        company_id=cid, company="Oficina do Zé", phone=phone, phone_verified=True,
        source=COMPANY_PHONE_SOURCE, status="contacted"))
    return pid


def _handler(state, client, brain):
    handler = Handler(brain, state, _config(allowed_prices=["1.500", "297"]), _env())
    handler.whatsapp = client
    return handler


@pytest.mark.asyncio
async def test_a_reply_becomes_a_queued_whatsapp_answer(state):
    pid = await _contacted_prospect(state)
    client = FakeEvolution()
    client.inbound = [WhatsAppInbound("M1", "5511987654321", "Oi! Vocês atendem aqui no bairro?")]
    handler = _handler(state, client, StubBrain(intent="question", text="Atendemos sim. O que vocês fazem hoje?"))

    assert await handler.run_whatsapp() is True

    prospect = await state.get_prospect(pid)
    assert prospect.status == "replied"
    [convo] = await state.get_conversations_by_status("open")
    assert convo.channel == "whatsapp" and convo.thread[0].content == "Oi! Vocês atendem aqui no bairro?"
    [item] = await state.get_outbox(channel="whatsapp")
    assert item["kind"] == "reply" and item["status"] == "pending_review"
    assert item["to_email"] == "5511987654321" and item["conversation_id"] == convo.id
    assert item["subject"] == "WhatsApp"
    # Not an email reply: nothing leaks into the email channel.
    assert await state.get_outbox(channel="email") == []


@pytest.mark.asyncio
async def test_wanting_to_hire_hands_the_chat_to_a_human_and_stops_answering(state):
    pid = await _contacted_prospect(state)
    client = FakeEvolution()
    client.inbound = [WhatsAppInbound("M1", "5511987654321", "Gostei, quero contratar")]
    brain = StubBrain(intent="interested", text="Ótimo! Vou pedir para o Luis falar com você. Qual a melhor forma de contato?")
    handler = _handler(state, client, brain)
    await handler.run_whatsapp()

    # Harvey still answered once (the answer promised the owner would follow up)...
    [reply] = await state.get_outbox(channel="whatsapp")
    assert reply["kind"] == "reply" and reply["status"] == "pending_review"
    # ...and the chat now waits for the owner, on the dashboard's "needs you" list.
    assert await state.get_conversations_by_status("open") == []
    [convo] = await state.get_conversations_by_status("needs_human")
    assert convo.channel == "whatsapp"

    # Their next message joins the same chat; nothing more is drafted.
    client.inbound.append(WhatsAppInbound("M2", "5511987654321", "pode ser por aqui mesmo"))
    brain.prompts.clear()
    await handler.run_whatsapp()
    assert len(await state.get_outbox(channel="whatsapp")) == 1
    assert not any(t == "generate_response" for t, _ in brain.prompts)
    [convo] = await state.get_conversations_by_status("needs_human")
    assert [m.content for m in convo.thread][-1] == "pode ser por aqui mesmo"
    assert await state.get_conversations_by_status("open") == []


@pytest.mark.asyncio
async def test_a_price_question_is_answered_from_the_knowledge_base_not_handed_over(state):
    await _contacted_prospect(state)
    client = FakeEvolution()
    client.inbound = [WhatsAppInbound("M1", "5511987654321", "Quanto custa uma landing page?")]
    brain = StubBrain(intent="question", text="A landing page é R$ 1.500, com domínio e hospedagem por 1 ano.")
    await _handler(state, client, brain).run_whatsapp()
    assert len(await state.get_conversations_by_status("open")) == 1       # still the assistant's chat
    assert await state.get_conversations_by_status("needs_human") == []
    prompt = next(p for t, p in brain.prompts if t == "decide_whatsapp")
    assert "KNOWLEDGE BASE" in prompt


@pytest.mark.asyncio
async def test_a_question_outside_the_knowledge_base_gets_a_holding_answer_and_a_human(state):
    await _contacted_prospect(state)
    client = FakeEvolution()
    client.inbound = [WhatsAppInbound("M1", "5511987654321", "Em quantos dias fica pronto?")]
    await _handler(state, client, StubBrain(intent="question", action="handoff", text="Boa pergunta! Vou confirmar com a nossa equipe e já te respondo por aqui.")).run_whatsapp()
    [reply] = await state.get_outbox(channel="whatsapp")
    assert "confirmar com a nossa equipe" in reply["body"] and "ESCALAR" not in reply["body"]
    assert len(await state.get_conversations_by_status("needs_human")) == 1


@pytest.mark.asyncio
async def test_a_long_chat_goes_to_a_human(state):
    from harvey.decide import MAX_ASSISTANT_TURNS
    from harvey.models.conversation import Conversation, Message

    pid = await _contacted_prospect(state)
    thread = []
    for i in range(MAX_ASSISTANT_TURNS):
        thread += [Message(sender="prospect", content=f"pergunta {i}"),
                   Message(sender="harvey", content=f"resposta {i}")]
    await state.add_conversation(Conversation(
        id="", prospect_id=pid, channel="whatsapp", status="open", thread=thread))
    client = FakeEvolution()
    client.inbound = [WhatsAppInbound("M1", "5511987654321", "e mais uma dúvida")]
    await _handler(state, client, StubBrain(intent="question")).run_whatsapp()
    assert len(await state.get_conversations_by_status("needs_human")) == 1


def test_gate_allows_only_listed_prices_and_extensions_are_not_addresses():
    p = _prospect()
    ok = "Fica R$ 1.500, domínio .com.br e hospedagem por 1 ano. Depois R$ 297/ano."
    assert whatsapp_check("5511987654321", ok, p, kind="reply", allowed_prices=["1.500", "297"])
    assert whatsapp_check("5511987654321", "R$ 1.500,00 no Pix", p, kind="reply", allowed_prices=["1.500"])
    for body in ("Fica R$ 1.200 com desconto.", "Fica R$ 1.500 mais R$ 99 por mês."):
        bad = whatsapp_check("5511987654321", body, p, kind="reply", allowed_prices=["1.500", "297"])
        assert any("allowed list" in r for r in bad.reasons), body
    assert not whatsapp_check("5511987654321", "Fica R$ 1.500.", p, kind="reply")   # nothing listed: no price


@pytest.mark.asyncio
async def test_opt_out_still_works_after_a_handoff(state):
    pid = await _contacted_prospect(state)
    client = FakeEvolution()
    client.inbound = [WhatsAppInbound("M1", "5511987654321", "quero contratar")]
    handler = _handler(state, client, StubBrain(intent="interested"))
    await handler.run_whatsapp()
    client.inbound.append(WhatsAppInbound("M2", "5511987654321", "SAIR"))
    await handler.run_whatsapp()
    assert (await state.get_prospect(pid)).status == "opted_out"
    assert await state.get_conversations_by_status("needs_human") == []
    confirmation = next(i for i in await state.get_outbox(channel="whatsapp") if "não vou mais" in i["body"])
    assert confirmation["status"] == "approved" and "não vou mais" in confirmation["body"]


@pytest.mark.parametrize("auto,expected", [(False, "pending_review"), (True, "approved")])
@pytest.mark.asyncio
async def test_auto_approve_replies_skips_review_for_answers_only(state, auto, expected):
    await _contacted_prospect(state)
    client = FakeEvolution()
    client.inbound = [WhatsAppInbound("M1", "5511987654321", "Oi, vocês atendem aqui?")]
    handler = Handler(StubBrain(intent="question"), state,
                      _config(require_approval=True, auto_approve_replies=auto), _env())
    handler.whatsapp = client
    await handler.run_whatsapp()
    [reply] = await state.get_outbox(channel="whatsapp")
    assert reply["status"] == expected
    # Opening messages keep waiting: that setting is untouched.
    await _company(state, "Outra", phone="(11) 97777-0000")
    other = FakeEvolution({"5511977770000": "5511977770000"})
    await _sender(state, other, require_approval=True, auto_approve_replies=auto)._stage()
    openings = [i for i in await state.get_outbox(channel="whatsapp") if i["kind"] == "sequence"]
    assert [i["status"] for i in openings] == ["pending_review"]


@pytest.mark.asyncio
async def test_each_message_is_handled_once(state):
    await _contacted_prospect(state)
    client = FakeEvolution()
    client.inbound = [WhatsAppInbound("M1", "5511987654321", "Quanto custa?")]
    handler = _handler(state, client, StubBrain(intent="question"))
    await handler.run_whatsapp()
    await handler.run_whatsapp()
    assert len(await state.get_outbox(channel="whatsapp")) == 1


@pytest.mark.asyncio
async def test_chats_with_strangers_are_ignored(state):
    await _contacted_prospect(state)
    client = FakeEvolution()
    client.inbound = [WhatsAppInbound("M9", "5521999990000", "oi, sou a sua prima")]
    brain = StubBrain()
    assert await _handler(state, client, brain).run_whatsapp() is True
    assert brain.prompts == [] and await state.get_outbox() == []


@pytest.mark.parametrize("text", ["SAIR", "sair", "Pare de me mandar mensagem", "não quero receber isso"])
@pytest.mark.asyncio
async def test_portuguese_opt_out_stops_everything(state, text):
    pid = await _contacted_prospect(state)
    await state.add_outbox_item(prospect_id=pid, to_email="5511987654321", subject="WhatsApp",
                                body="follow", send_at="2099-01-01T00:00:00",
                                status="approved", channel="whatsapp")
    client = FakeEvolution()
    client.inbound = [WhatsAppInbound("M2", "5511987654321", text)]
    brain = StubBrain(intent="interested")                    # the model must not be able to override it
    await _handler(state, client, brain).run_whatsapp()

    assert (await state.get_prospect(pid)).status == "opted_out"
    items = await state.get_outbox(channel="whatsapp")
    queued = next(i for i in items if i["body"] == "follow")
    goodbye = next(i for i in items if i["body"] != "follow")
    assert queued["status"] == "cancelled"      # the follow-up never goes out
    assert goodbye["status"] == "approved" and goodbye["kind"] == "reply"
    assert not any(t == "generate_response" for t, _ in brain.prompts)


@pytest.mark.asyncio
async def test_portuguese_legal_threat_goes_to_a_human(state):
    pid = await _contacted_prospect(state)
    client = FakeEvolution()
    client.inbound = [WhatsAppInbound("M3", "5511987654321", "Como conseguiu meu número? Vou ao Procon")]
    brain = StubBrain(intent="interested")
    await _handler(state, client, brain).run_whatsapp()
    [convo] = await state.get_conversations_by_status("needs_human")
    assert convo.channel == "whatsapp"
    assert await state.get_outbox(channel="whatsapp") == []


@pytest.mark.asyncio
async def test_unreadable_inbox_reports_incomplete(state):
    class Down(FakeEvolution):
        async def get_replies(self, limit=50):
            raise RuntimeError("connection refused")

    assert await _handler(state, Down(), StubBrain()).run_whatsapp() is False


@pytest.mark.asyncio
async def test_disabled_channel_reads_nothing(state):
    config = load_config()
    config.channels.whatsapp = WhatsAppChannelConfig(enabled=False)
    handler = Handler(StubBrain(), state, config, _env())
    assert handler.whatsapp is None
    assert await handler.run_whatsapp() is True


# ── the email sender leaves WhatsApp alone ──


@pytest.mark.asyncio
async def test_email_sender_does_not_send_approved_whatsapp_rows(state):
    from tests.test_outbox_native import FakeProvider, make_sender, seed_prospect

    await seed_prospect(state)
    wa_pid = await _contacted_prospect(state)
    await state.add_outbox_item(prospect_id=wa_pid, to_email="5511987654321", subject="WhatsApp",
                                body="oi", send_at="2020-01-01T00:00:00",
                                status="approved", channel="whatsapp")
    provider = FakeProvider()
    await make_sender(state, provider)._drain_due()
    assert provider.sent == []
    [item] = await state.get_outbox(channel="whatsapp")
    assert item["status"] == "approved"


# ── the Outbox desk ──


@pytest.fixture
def desk(monkeypatch):
    from pathlib import Path

    from fastapi.testclient import TestClient

    import harvey.dashboard as dash

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "harvey.db"
        monkeypatch.setattr(dash, "DB_PATH", db)
        monkeypatch.setattr(dash, "WEB_DIR", Path(__file__).resolve().parent.parent / "harvey" / "web")
        dash.token_store.configure(Path(tmp) / "data" / "dashboard-token")
        token = dash.token_store.get()
        with TestClient(dash.app, base_url="http://127.0.0.1:5555",
                        headers={"Authorization": f"Bearer {token}"}) as c:
            c.db_path = db
            yield c


async def _seed_draft(db_path):
    state = StateManager(str(db_path))
    await state.init_db()
    pid = await _contacted_prospect(state)
    item_id = await state.add_outbox_item(
        prospect_id=pid, to_email="5511987654321", subject="WhatsApp",
        body=f"Oi, aqui é o Luis.\n\n{OPT_OUT}", send_at="2020-01-01T00:00:00",
        status="pending_review", channel="whatsapp")
    return state, item_id


def test_desk_lists_whatsapp_drafts_next_to_email(desk):
    import asyncio

    asyncio.run(_seed_draft(desk.db_path))
    pending = desk.get("/api/outbox").json()["pending"]
    assert [p["to_email"] for p in pending] == ["5511987654321"]


def test_desk_edit_uses_the_whatsapp_gate(desk):
    import asyncio

    state, item_id = asyncio.run(_seed_draft(desk.db_path))
    good = f"Oi, aqui é o Luis, da Acme. Posso te perguntar uma coisa?\n\n{OPT_OUT}"
    r = desk.post(f"/api/outbox/{item_id}/edit", json={"subject": "WhatsApp", "body": good})
    assert r.json() == {"success": True}
    assert asyncio.run(state.get_outbox_item(item_id))["body"] == good

    r = desk.post(f"/api/outbox/{item_id}/edit",
                  json={"subject": "WhatsApp", "body": "Oi, tudo bem?"})   # opt-out line removed
    assert r.status_code == 400 and "opt-out" in r.json()["message"]
    assert asyncio.run(state.get_outbox_item(item_id))["body"] == good


@pytest.mark.asyncio
async def test_live_loop_answers_a_message_without_waiting_for_the_heartbeat(state):
    import asyncio

    from harvey.main import whatsapp_live

    await _contacted_prospect(state)
    client = FakeEvolution()
    client.inbound = [WhatsAppInbound("M1", "5511987654321", "Quanto custa uma landing page?")]
    config = _config(allowed_prices=["1.500"], auto_approve_actions=["answer"], poll_seconds=2)
    brain = StubBrain(intent="question", text="A landing page é R$ 1.500, com domínio e hospedagem por 1 ano.")
    handler = Handler(brain, state, config, _env())
    handler.whatsapp = client
    sender = WhatsAppSender(brain, state, config, _env(), client=client, send_pacing=False)

    stop = asyncio.Event()
    task = asyncio.create_task(whatsapp_live(handler, sender, stop))
    for _ in range(40):
        if client.sent:
            break
        await asyncio.sleep(0.1)
    stop.set()
    await asyncio.wait_for(task, 5)
    assert client.sent and "1.500" in client.sent[0][1]


@pytest.mark.asyncio
async def test_chat_calls_use_the_configured_fast_model(state):
    seen = []

    class ModelBrain(StubBrain):
        async def think(self, prompt, session_id=None, agent="", task="", model=""):
            seen.append((task, model))
            return await super().think(prompt, session_id=session_id, agent=agent, task=task)

        async def think_json(self, prompt, session_id=None, agent="", task="", model=""):
            seen.append((task, model))
            return await super().think_json(prompt, session_id=session_id, agent=agent, task=task)

    await _contacted_prospect(state)
    client = FakeEvolution()
    client.inbound = [WhatsAppInbound("M1", "5511987654321", "Quanto custa?")]
    handler = Handler(ModelBrain(intent="question"), state,
                      _config(allowed_prices=["1.500"], model="haiku"), _env())
    handler.whatsapp = client
    await handler.run_whatsapp()
    assert seen == [("decide_whatsapp", "haiku")]          # one call: intent and answer together


def _stranger_handler(state, client, brain, **cfg):
    cfg.setdefault("allowed_prices", ["1.500"])
    handler = Handler(brain, state, _config(inbound_leads=True, auto_approve_actions=["answer", "qualify", "send_demo"], **cfg), _env())
    handler.whatsapp = client
    return handler


def _fresh(body="Oi, quanto custa um site?", phone="5541999990000", age=30):
    import time
    return WhatsAppInbound("S1", phone, body, date=str(int(time.time()) - age), push_name="Marina Souza")


@pytest.mark.asyncio
async def test_a_stranger_who_writes_first_becomes_a_lead_and_is_answered(state):
    client = FakeEvolution()
    client.inbound = [_fresh()]
    brain = StubBrain(intent="question", text="Oi! Aqui é o Theo, assistente virtual. A landing page é R$ 1.500.")
    await _stranger_handler(state, client, brain).run_whatsapp()
    lead = await state.get_prospect_by_phone("5541999990000")
    assert lead and lead.source == "inbound_whatsapp" and lead.first_name == "Marina"
    [reply] = await state.get_outbox(channel="whatsapp")
    assert reply["status"] == "approved" and "R$ 1.500" in reply["body"]


@pytest.mark.asyncio
@pytest.mark.parametrize("why", ["old", "known_chat", "disabled"])
async def test_strangers_are_ignored_unless_new_fresh_and_enabled(state, why):
    client = FakeEvolution()
    client.inbound = [_fresh(age=3 * 3600 if why == "old" else 30)]
    client.stranger = why != "known_chat"
    handler = _stranger_handler(state, client, StubBrain(intent="question"))
    if why == "disabled":
        handler.config.channels.whatsapp.inbound_leads = False
    await handler.run_whatsapp()
    assert await state.get_prospect_by_phone("5541999990000") is None
    assert await state.get_outbox(channel="whatsapp") == []


@pytest.mark.asyncio
async def test_the_business_they_name_unlocks_its_example_site(state):
    client = FakeEvolution()
    client.inbound = [_fresh("Tenho uma barbearia, queria um site")]
    demo = DemoConfig(label="Barbearia", url="https://demo-barbearia.guilhermeh.me", keywords=["barbearia"])
    decision = {"action": "send_demo", "confidence": "high", "industry": "barbearia",
                "reply": "Oi! Aqui é o Theo, assistente virtual. Veja um exemplo: {demo_url}"}
    handler = _stranger_handler(state, client, StubBrain(intent="interested", decision=decision), demos=[demo])
    await handler.run_whatsapp()
    lead = await state.get_prospect_by_phone("5541999990000")
    assert lead.industry == "barbearia"
    [reply] = await state.get_outbox(channel="whatsapp")
    assert "https://demo-barbearia.guilhermeh.me" in reply["body"]


@pytest.mark.asyncio
@pytest.mark.parametrize("headers, becomes_lead", [
    ({"to": "Theo <theo@guilhermeh.me>", "from": "Ana Lima <ana@loja.com>"}, True),
    ({"to": "luis@guilhermeh.me"}, False),                                   # not the persona address
    ({"to": "theo@guilhermeh.me", "list-unsubscribe": "<mailto:x@y>"}, False),   # newsletter
    ({"to": "theo@guilhermeh.me", "auto-submitted": "auto-replied"}, False),
])
async def test_email_to_the_persona_address_becomes_a_lead_only_when_human(state, headers, becomes_lead):
    from harvey.integrations.mail_provider import InboundMessage

    config = _config()
    config.channels.email.inbound_leads = True
    config.persona.email = "theo@guilhermeh.me"
    handler = Handler(StubBrain(), state, config, _env())
    await handler._adopt_inbound_email(InboundMessage(
        provider_id="m1", from_email="ana@loja.com", body="Oi, quero um site", headers=headers))
    lead = await state.get_prospect_by_email("ana@loja.com")
    assert bool(lead) is becomes_lead
    if lead:
        assert lead.source == "inbound_email" and lead.first_name == "Ana"


@pytest.mark.asyncio
async def test_live_replies_are_never_paced(state, monkeypatch):
    import harvey.agents.whatsapp as wa

    pid = await _contacted_prospect(state)
    for i in range(2):
        await state.add_outbox_item(prospect_id=pid, to_email="5511987654321", subject="WhatsApp",
                                    body=f"resposta {i}", kind="reply", step=1, send_at="2000-01-01T00:00:00",
                                    status="approved", channel="whatsapp")
    naps = []

    async def fake_sleep(s):
        naps.append(s)

    monkeypatch.setattr(wa.asyncio, "sleep", fake_sleep)
    client = FakeEvolution()
    sender = WhatsAppSender(StubBrain(), state, _config(), _env(), client=client, send_pacing=True)
    await sender.send_replies()
    assert len(client.sent) == 2 and naps == []


@pytest.mark.asyncio
async def test_the_team_can_answer_a_whatsapp_chat_from_the_dashboard(state, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import harvey.dashboard as dash
    from harvey.models.conversation import Conversation, Message

    pid = await _contacted_prospect(state)
    convo_id = await state.add_conversation(Conversation(
        id="", prospect_id=pid, channel="whatsapp", status="needs_human",
        thread=[Message(sender="prospect", content="quero contratar")]))
    client = FakeEvolution()
    monkeypatch.setattr(dash, "_state", lambda: state)
    monkeypatch.setattr(EvolutionClient, "from_env", classmethod(lambda cls, env: client))

    async def fake_query(sql, params=()):
        return [{"channel": "whatsapp", "phone": "5511987654321", "pstatus": "replied"}]

    monkeypatch.setattr(dash, "query_db", fake_query)
    dash.token_store.configure(tmp_path / "data" / "dashboard-token")
    http = TestClient(dash.app, base_url="http://127.0.0.1:5555",
                      headers={"Authorization": f"Bearer {dash.token_store.get()}"})
    r = http.post(f"/api/conversations/{convo_id}/send", json={"text": "Oi, aqui é a equipe!"})
    assert r.status_code == 200, r.text
    assert client.sent == [("5511987654321", "Oi, aqui é a equipe!")]
    convo = await state.get_conversation(convo_id)
    assert [m.sender for m in convo.thread] == ["prospect", "team"]


def _rec(i, ts, from_me=False, message=None):
    return {"key": {"id": i, "fromMe": from_me}, "messageTimestamp": ts,
            "message": message or {"conversation": f"msg {i}"}}


def test_chat_messages_are_normalized_for_the_dashboard():
    from harvey.integrations.evolution import normalize_message

    assert normalize_message(_rec("a", 5, True))["text"] == "msg a"
    img = normalize_message(_rec("b", 6, message={"imageMessage": {"caption": "foto", "mimetype": "image/jpeg"}}))
    assert img["media"]["kind"] == "image" and img["text"] == "foto"
    doc = normalize_message(_rec("c", 7, message={"documentWithCaptionMessage": {"message": {
        "documentMessage": {"fileName": "orcamento.pdf", "mimetype": "application/pdf", "caption": "segue"}}}}))
    assert doc["media"]["filename"] == "orcamento.pdf" and doc["text"] == "segue"
    assert normalize_message(_rec("d", 8, message={"reactionMessage": {"text": "👍"}})) is None


@pytest.mark.asyncio
async def test_chat_merges_both_ids_of_a_chat_and_pages_back_in_time():
    import httpx

    sent = [_rec(f"s{i}", 100 + i * 10, True) for i in range(4)]          # under the phone id
    got = [_rec(f"g{i}", 105 + i * 10) for i in range(4)]                 # under the anonymous id

    def handler(request):
        import json
        where = json.loads(request.content)["where"]["key"]
        records = sorted(sent if "remoteJid" in where else got, key=lambda r: -r["messageTimestamp"])
        return httpx.Response(200, json={"messages": {"records": records, "pages": 1}})

    client = EvolutionClient("http://evo.test", "KEY", "inst", transport=httpx.MockTransport(handler))
    page, more = await client.get_chat("5511987654321", limit=3)
    assert [m["id"] for m in page] == ["g2", "s3", "g3"] and more         # newest 3, oldest first
    older, more = await client.get_chat("5511987654321", limit=3, before=page[0]["ts"])
    assert [m["id"] for m in older] == ["g1", "s2", "s1"][::-1] or [m["id"] for m in older] == ["s1", "g1", "s2"]
    assert more


@pytest.mark.asyncio
async def test_media_goes_out_as_the_right_kind_of_message():
    import httpx

    seen = []

    def handler(request):
        import json
        seen.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"key": {"id": "WA1", "remoteJid": "x"}})

    client = EvolutionClient("http://evo.test", "KEY", "inst", transport=httpx.MockTransport(handler))
    await client.send_media("5511987654321", "foto.jpg", "image/jpeg", "QUJD", "olha")
    await client.send_media("5511987654321", "nota.ogg", "audio/ogg", "QUJD")
    await client.send_media("5511987654321", "a.pdf", "application/pdf", "QUJD")
    assert seen[0][0].endswith("/message/sendMedia/inst") and seen[0][1]["mediatype"] == "image"
    assert seen[1][0].endswith("/message/sendWhatsAppAudio/inst") and seen[1][1]["audio"] == "QUJD" and seen[1][1]["encoding"] is True
    assert seen[2][1]["mediatype"] == "document" and seen[2][1]["fileName"] == "a.pdf"


@pytest.mark.asyncio
async def test_dashboard_chat_and_attachment_endpoints(state, monkeypatch, tmp_path):
    import base64

    from fastapi.testclient import TestClient

    import harvey.dashboard as dash

    class ChatClient(FakeEvolution):
        async def get_chat(self, phone, limit=30, before=0):
            return [{"id": "L1", "from_me": False, "ts": 1, "text": "oi", "media": None},
                    {"id": "H1", "from_me": True, "ts": 2, "text": "aqui é o Theo", "media": None},
                    {"id": "T1", "from_me": True, "ts": 3, "text": "equipe", "media": None},
                    {"id": "P1", "from_me": True, "ts": 4, "text": "do celular", "media": None}], False

        async def send_media(self, number, filename, mimetype, b64, caption=""):
            self.sent.append((number, filename, mimetype, caption))
            return SendResult(ok=True, message_id="WAM1")

    await _contacted_prospect(state)
    await state.log_action("whatsapp_sent", "whatsapp", {"message_id": "H1", "kind": "reply"})
    await state.log_action("whatsapp_sent", "dashboard", {"message_id": "T1", "kind": "team"})
    convo_id = await state.add_conversation(Conversation(
        id="", prospect_id=(await state.get_prospect_by_phone("5511987654321")).id,
        channel="whatsapp", status="needs_human", thread=[]))
    client = ChatClient()
    monkeypatch.setattr(dash, "_state", lambda: state)
    monkeypatch.setattr(dash, "_evolution", lambda: client)

    async def fake_query(sql, params=()):
        return [{"channel": "whatsapp", "phone": "5511987654321", "pstatus": "replied"}]

    monkeypatch.setattr(dash, "query_db", fake_query)
    dash.token_store.configure(tmp_path / "data" / "dashboard-token")
    http = TestClient(dash.app, base_url="http://127.0.0.1:5555",
                      headers={"Authorization": f"Bearer {dash.token_store.get()}"})

    chat = http.get(f"/api/conversations/{convo_id}/chat").json()
    assert [m["who"] for m in chat["messages"]] == ["lead", "harvey", "team", "team_phone"]

    ok = http.post(f"/api/conversations/{convo_id}/attach", json={
        "filename": "foto.png", "mimetype": "image/png", "caption": "olha",
        "data": base64.b64encode(b"abc").decode()})
    assert ok.status_code == 200 and client.sent == [("5511987654321", "foto.png", "image/png", "olha")]
    assert "WAM1" in await state.get_whatsapp_sent_ids()
    assert http.post(f"/api/conversations/{convo_id}/attach", json={"filename": "x", "data": "!!"}).status_code == 400


@pytest.mark.asyncio
async def test_lead_panel_shows_the_person_the_company_and_every_contact(state, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import harvey.dashboard as dash

    company_id = await _company(state)
    pid = await _contacted_prospect(state)
    convo_id = await state.add_conversation(Conversation(
        id="", prospect_id=pid, channel="whatsapp", status="needs_human", thread=[]))
    await state.add_outbox_item(prospect_id=pid, to_email="5511987654321", subject="WhatsApp",
                                body="Oi, aqui é o Theo", kind="sequence", step=1, send_at=None,
                                status="sent", channel="whatsapp")
    await state.log_action("handoff", "handler", {"prospect_phone": "5511987654321", "reason": "pediu um humano"})
    monkeypatch.setattr(dash, "_state", lambda: state)

    async def fake_query(sql, params=()):
        import aiosqlite
        async with aiosqlite.connect(state.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(sql, params) as cur:
                return [dict(r) for r in await cur.fetchall()]

    monkeypatch.setattr(dash, "query_db", fake_query)
    dash.token_store.configure(tmp_path / "data" / "dashboard-token")
    http = TestClient(dash.app, base_url="http://127.0.0.1:5555",
                      headers={"Authorization": f"Bearer {dash.token_store.get()}"})
    d = http.get(f"/api/conversations/{convo_id}/lead").json()
    assert d["lead"]["phone"] == "5511987654321"
    texts = " | ".join(e["text"] for e in d["history"])
    assert "Opening message" in texts and "pediu um humano" in texts
    assert http.get("/api/conversations/nope/lead").status_code == 404


@pytest.fixture(autouse=True)
def _no_site_lookups(monkeypatch):
    """Tests never touch the network: no business has a site unless a test says so."""
    import harvey.agents.whatsapp as wa

    async def none(name, client=None):
        return ""

    monkeypatch.setattr(wa, "find_own_site", none)


def test_candidate_addresses_for_a_business_name():
    from harvey.site_probe import candidate_domains

    assert candidate_domains("Restaurante Santana")[:2] == ["restaurantesantana.com.br", "restaurantesantana.com"]
    assert "santana.com.br" in candidate_domains("Restaurante Santana")
    assert candidate_domains("Mãos de fada")[0] == "maosfada.com.br"


@pytest.mark.asyncio
async def test_a_site_is_found_only_when_it_answers_and_belongs_to_the_name():
    import httpx

    from harvey.site_probe import find_own_site

    def handler(request):
        return httpx.Response(200 if request.url.host == "restaurantesantana.com.br" else 404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert await find_own_site("Restaurante Santana", client) == "restaurantesantana.com.br"
    assert await find_own_site("Padaria Lua", client) == ""


@pytest.mark.asyncio
async def test_a_business_that_already_has_a_site_is_not_contacted(state, monkeypatch):
    import harvey.agents.whatsapp as wa

    async def has_site(name, client=None):
        return "acme.com.br"

    monkeypatch.setattr(wa, "find_own_site", has_site)
    await _company(state)
    client = FakeEvolution({"5511987654321": "5511987654321"})
    await _sender(state, client)._stage()
    assert await state.get_outbox(channel="whatsapp") == []
    assert client.checked == []                                   # it never got as far as the number


@pytest.mark.asyncio
async def test_an_approved_opening_is_cancelled_if_the_business_turns_out_to_have_a_site(state, monkeypatch):
    import harvey.agents.whatsapp as wa

    pid = await _contacted_prospect(state)
    await state.update_prospect_status(pid, "new")
    await state.add_outbox_item(prospect_id=pid, to_email="5511987654321", subject="WhatsApp",
                                body="Oi, aqui é o Theo.\n\nSe preferir não receber mensagens, responda SAIR.",
                                kind="sequence", step=1, send_at="2000-01-01T00:00:00",
                                status="approved", channel="whatsapp")

    async def has_site(name, client=None):
        return "acme.com.br"

    monkeypatch.setattr(wa, "find_own_site", has_site)
    client = FakeEvolution()
    await _sender(state, client)._drain()
    [item] = await state.get_outbox(channel="whatsapp")
    assert client.sent == [] and item["status"] == "cancelled" and "website" in item["error"]


@pytest.mark.asyncio
async def test_a_business_bot_is_recognised_in_the_same_call_that_decides(state):
    await _contacted_prospect(state)
    client = FakeEvolution()
    client.inbound = [WhatsAppInbound("M1", "5511987654321", "Olá! Os pedidos são feitos pelo site, acesse: https://x.com.br")]
    bot = {"action": "answer", "reply": "Oi!", "confidence": "high", "sender": "automatic", "intent": "ooo"}
    brain = StubBrain(decision=bot)
    await _handler(state, client, brain).run_whatsapp()
    assert [t for t, _ in brain.prompts] == ["decide_whatsapp"]       # no separate classification
    [convo] = await state.get_conversations_by_status("open")
    assert convo.intent == "ooo" and convo.stage == "initial_outreach"
    assert await state.get_outbox(channel="whatsapp") == []


class SecondOpinionBrain(StubBrain):
    """The fast model hesitates; the reviewer gives the real decision."""

    def __init__(self, first, second):
        super().__init__(intent="question")
        self.answers, self.tasks = [first, second], []

    async def think_json(self, prompt, session_id=None, agent="", task="", model=""):
        self.tasks.append((task, model))
        return self.answers.pop(0) if self.answers else None


@pytest.mark.asyncio
async def test_an_unsure_decision_gets_a_second_opinion_before_a_human_is_bothered(state):
    await _contacted_prospect(state)
    client = FakeEvolution()
    client.inbound = [WhatsAppInbound("M1", "5511987654321", "e aí, como funciona?")]
    unsure = {"action": "handoff", "reply": "Vou confirmar com a equipe.", "confidence": "low", "sender": "person"}
    final = {"action": "answer", "reply": "A landing page custa R$ 1.500, com domínio e hospedagem.",
             "confidence": "high", "sender": "person"}
    brain = SecondOpinionBrain(unsure, final)
    handler = Handler(brain, state, _config(allowed_prices=["1.500"], review_model="sonnet"), _env())
    handler.whatsapp = client
    await handler.run_whatsapp()
    assert [t for t, _ in brain.tasks] == ["decide_whatsapp", "review_whatsapp"]
    assert brain.tasks[1][1] == "sonnet"
    assert await state.get_conversations_by_status("needs_human") == []      # nobody was bothered
    [reply] = await state.get_outbox(channel="whatsapp")
    assert "R$ 1.500" in reply["body"]


@pytest.mark.asyncio
async def test_a_confident_decision_is_not_reviewed_and_a_bot_message_is_left_alone(state):
    await _contacted_prospect(state)
    client = FakeEvolution()
    client.inbound = [WhatsAppInbound("M1", "5511987654321", "Os pedidos são feitos pelo site, acesse: https://x.com.br")]
    bot = {"action": "answer", "reply": "Oi!", "confidence": "high", "sender": "automatic"}
    brain = SecondOpinionBrain(bot, None)
    handler = Handler(brain, state, _config(), _env())
    handler.whatsapp = client
    await handler.run_whatsapp()
    assert [t for t, _ in brain.tasks] == ["decide_whatsapp"]
    assert await state.get_outbox(channel="whatsapp") == []


@pytest.mark.asyncio
async def test_the_today_widget_reports_whatsapp_use_and_connection(state, monkeypatch):
    import harvey.config as cfgmod
    import harvey.dashboard as dash

    monkeypatch.setattr(cfgmod, "load_config", lambda: _config(max_daily_sends=10))
    monkeypatch.setattr(cfgmod, "load_env", lambda: _env())

    async def connected(self):
        return True, "ok"

    monkeypatch.setattr(EvolutionClient, "test_connection", connected)

    async def no_chats(sql, params=()):
        return [{"id": "c1"}, {"id": "c2"}]

    monkeypatch.setattr(dash, "query_db", no_chats)
    pid = await _contacted_prospect(state)
    await state.add_outbox_item(prospect_id=pid, to_email="5511987654321", subject="WhatsApp", body="oi",
                                kind="reply", step=1, send_at=None, status="sent", channel="whatsapp")
    await state.update_outbox_item((await state.get_outbox(channel="whatsapp"))[0]["id"],
                                   sent_at=__import__("datetime").datetime.utcnow().isoformat())
    wa = await dash._whatsapp_today(state)
    assert wa == {"enabled": True, "connected": True, "sent_today": 1, "cap": 10, "chats": 2}
