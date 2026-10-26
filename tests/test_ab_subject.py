"""Email 1 subject A/B test: a fixed variant per prospect, sent/replied counts
per variant, and a winner only with enough sends and a significant gap."""

import os
import tempfile

import pytest
import pytest_asyncio

from harvey.agents.analyst import Analyst, subject_winner
from harvey.agents.writer import Writer
from harvey.models.campaign import pick_subject, subject_variant
from harvey.models.conversation import Conversation
from harvey.state import StateManager


@pytest_asyncio.fixture
async def state():
    with tempfile.TemporaryDirectory() as tmp:
        sm = StateManager(os.path.join(tmp, "t.db"))
        await sm.init_db()
        yield sm


def test_each_prospect_keeps_one_variant_and_both_occur():
    ids = [f"p{i}" for i in range(40)]
    assert {subject_variant(i) for i in ids} == {"A", "B"}
    assert all(subject_variant(i) == subject_variant(i) for i in ids)
    a = next(i for i in ids if subject_variant(i) == "A")
    b = next(i for i in ids if subject_variant(i) == "B")
    assert pick_subject("your booking page", "quick question?", a) == ("your booking page", "A")
    assert pick_subject("your booking page", "quick question?", b) == ("quick question?", "B")
    assert pick_subject("your booking page", "", b) == ("your booking page", "")


def test_only_email_1_keeps_a_b_subject():
    steps = Writer._parse_sequence([
        {"step": 1, "subject": "a", "subject_b": "b?", "body": "x"},
        {"step": 2, "subject": "c", "subject_b": "d?", "body": "y", "delay_days": 3}])
    assert [s.subject_b for s in steps] == ["b?", ""]


async def _sent(state, pid, variant, replied):
    await state.add_outbox_item(prospect_id=pid, to_email=f"{pid}@x.com", subject="s", body="b",
                                send_at="2030-01-01T00:00:00", status="sent", campaign_id="c1",
                                variant=variant)
    if replied:
        await state.add_conversation(Conversation(id="", prospect_id=pid))


@pytest.mark.asyncio
async def test_stats_count_sends_and_replies_per_variant(state):
    await _sent(state, "p1", "A", True)
    await _sent(state, "p2", "A", False)
    await _sent(state, "p3", "B", False)
    await _sent(state, "p4", "", True)             # not in the test
    assert await state.get_variant_stats() == {"A": {"sent": 2, "replied": 1},
                                               "B": {"sent": 1, "replied": 0}}


def test_a_winner_needs_enough_sends_and_a_real_gap():
    assert subject_winner({"A": {"sent": 29, "replied": 20}, "B": {"sent": 29, "replied": 0}}) is None
    assert subject_winner({"A": {"sent": 40, "replied": 4}, "B": {"sent": 40, "replied": 5}}) is None
    assert subject_winner({"A": {"sent": 40, "replied": 2}, "B": {"sent": 40, "replied": 12}}) == (
        "B", 0.3, 0.05)


@pytest.mark.asyncio
async def test_the_analyst_proposes_the_winner_once(state):
    for i in range(40):
        await _sent(state, f"a{i}", "A", i < 2)
        await _sent(state, f"b{i}", "B", i < 12)
    await Analyst(state).run()
    await Analyst(state).run()
    [lesson] = await state.get_lessons()
    assert lesson["status"] == "proposed" and "short question" in lesson["text"]
    assert "30% vs 5%" in lesson["evidence"]
