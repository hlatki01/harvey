from types import SimpleNamespace

from harvey.agents.handler import _flat
from harvey.config import DemoConfig
from harvey.decide import HOLDING_REPLY, Decision, apply_policy, find_demo, parse_decision
from harvey.models.prospect import Prospect

DEMO = DemoConfig(label="Salão", url="https://demo-salao.guilhermeh.me", keywords=["salão", "estética"])
BARBER = DemoConfig(label="Barbearia", url="https://demo-barbearia.guilhermeh.me", keywords=["barbearia"])


def _p():
    return Prospect(first_name="Ana", last_name="", email="", phone="5511987654321", phone_verified=True)


def _run(decision, reply_text="oi", demo=DEMO, demo_sent=False, turns=1):
    return apply_policy(decision, prospect=_p(), reply_text=reply_text, demo=demo, demo_sent=demo_sent,
                        assistant_turns=turns, allowed_prices=["1.500"],
                        allowed_links=[DEMO.url], flat=_flat)


def test_find_demo_first_match_wins():
    assert find_demo("Clínica de Estética", [BARBER, DEMO]) is DEMO
    assert find_demo("Barbearia e salão", [BARBER, DEMO]) is BARBER
    assert find_demo("Oficina", [BARBER, DEMO]) is None


def test_parse_decision_rejects_junk():
    assert parse_decision("x") is None and parse_decision({"action": "fly"}) is None
    d = parse_decision({"action": "Answer", "reply": " oi ", "confidence": "??"})
    assert (d.action, d.reply, d.confidence) == ("answer", "oi", "medium")


def test_good_answer_passes():
    d = _run(Decision("answer", "Fica R$ 1.500 no Pix."))
    assert d.action == "answer" and not d.overridden


def test_missing_decision_and_low_confidence_hand_off():
    assert _run(None).reply == HOLDING_REPLY
    d = _run(Decision("answer", "Fica R$ 1.500.", confidence="low"))
    assert d.action == "handoff" and d.reply == HOLDING_REPLY


def test_demo_token_is_swapped_for_the_mapped_url():
    d = _run(Decision("send_demo", "Veja um exemplo: {demo_url}"))
    assert d.action == "send_demo" and DEMO.url in d.reply


def test_demo_problems_hand_off():
    for kw in ({"demo": None}, {"demo_sent": True}):
        assert _run(Decision("send_demo", "Veja: {demo_url}"), **kw).action == "handoff"
    assert _run(Decision("send_demo", "Veja o exemplo")).action == "handoff"


def test_gate_failures_hand_off():
    assert _run(Decision("answer", "Fica R$ 900.")).action == "handoff"
    assert _run(Decision("answer", "Veja https://outro.com/x")).action == "handoff"


def test_wait_has_no_reply():
    assert _run(Decision("wait", "tchau")).reply == ""


def test_hire_words_and_long_chats_hand_off():
    assert _run(Decision("answer", "Fica R$ 1.500."), reply_text="Quero contratar").action == "handoff"
    assert _run(Decision("answer", "Fica R$ 1.500."), turns=6).action == "handoff"


def test_a_handoff_never_asks_the_lead_a_question():
    from harvey.decide import HANDOFF_REPLY

    d = _run(Decision("handoff", "Vou avisar a equipe. Qual a melhor forma de contato?"))
    assert d.action == "handoff" and d.reply == HANDOFF_REPLY
    d = _run(Decision("answer", "Fica R$ 1.500."), reply_text="quero contratar")
    assert d.action == "handoff"


def test_an_automatic_message_is_never_answered_and_ambiguity_asks_for_a_review():
    from harvey.decide import needs_review

    bot = parse_decision({"action": "answer", "reply": "Fica R$ 1.500.", "confidence": "high", "sender": "automatic"})
    d = _run(bot)
    assert d.action == "wait" and d.reply == "" and d.overridden == "automatic message"

    assert needs_review(None)
    assert needs_review(parse_decision({"action": "answer", "reply": "x", "confidence": "medium"}))
    assert needs_review(parse_decision({"action": "answer", "reply": "x", "confidence": "high", "sender": "unclear"}))
    assert not needs_review(parse_decision({"action": "answer", "reply": "x", "confidence": "high"}))
