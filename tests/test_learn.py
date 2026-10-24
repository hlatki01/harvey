import pytest
import pytest_asyncio

from harvey.learn import propose_lessons
from harvey.state import StateManager


class Brain:
    def __init__(self, lessons):
        self.lessons, self.calls = lessons, 0

    async def think_json(self, prompt, **kw):
        self.calls += 1
        return {"lessons": self.lessons}


@pytest_asyncio.fixture
async def state(tmp_path):
    s = StateManager(str(tmp_path / "h.db"))
    await s.init_db()
    return s


async def _reject_a_reply(state):
    await state.add_outbox_item(prospect_id="p1", campaign_id="", to_email="5511987654321",
                                subject="WhatsApp", body="Posso te ligar amanhã?", kind="reply",
                                step=0, send_at=None, channel="whatsapp")
    [item] = await state.get_outbox(channel="whatsapp")
    await state.reject_outbox_item(item["id"])


@pytest.mark.asyncio
async def test_corrections_become_proposed_lessons_once(state):
    await _reject_a_reply(state)
    brain = Brain(["Never offer phone calls.", "x" * 300])
    assert await propose_lessons(state, brain) == 1            # the long one is dropped
    assert await propose_lessons(state, brain) == 0            # too soon: no second model call
    assert brain.calls == 1
    [lesson] = await state.get_lessons("proposed")
    assert lesson["text"] == "Never offer phone calls."


@pytest.mark.asyncio
async def test_no_corrections_means_no_model_call(state):
    brain = Brain(["anything"])
    assert await propose_lessons(state, brain) == 0 and brain.calls == 0


@pytest.mark.asyncio
async def test_only_approved_lessons_are_used_and_duplicates_ignored(state):
    assert await state.add_lesson("Be brief.") and not await state.add_lesson("Be brief.")
    [lesson] = await state.get_lessons()
    assert await state.get_lessons("approved") == []
    await state.set_lesson_status(lesson["id"], "approved")
    assert [l["text"] for l in await state.get_lessons("approved")] == ["Be brief."]


@pytest.mark.asyncio
async def test_edited_replies_are_corrections_too(state):
    await state.add_outbox_item(prospect_id="p1", campaign_id="", to_email="5511987654321",
                                subject="WhatsApp", body="novo texto", kind="reply",
                                step=0, send_at=None, channel="whatsapp")
    [item] = await state.get_outbox(channel="whatsapp")
    await state.log_action("outbox_edited", "dashboard", {"outbox_id": item["id"], "old_body": "texto velho"})
    [c] = await state.get_corrections("1970-01-01 00:00:00")
    assert (c["kind"], c["channel"], c["before"], c["after"]) == ("edited", "whatsapp", "texto velho", "novo texto")


@pytest.mark.asyncio
async def test_email_corrections_count_and_approved_lessons_reach_prompts(state):
    await state.add_outbox_item(prospect_id="p1", campaign_id="", to_email="a@b.com",
                                subject="Oi", body="Texto frio", kind="sequence",
                                step=1, send_at=None)
    [item] = await state.get_outbox()
    await state.reject_outbox_item(item["id"])
    [c] = await state.get_corrections("1970-01-01 00:00:00")
    assert (c["kind"], c["channel"]) == ("rejected", "email")

    assert await state.lessons_block() == ""
    await state.add_lesson("Open with their city.")
    [lesson] = await state.get_lessons()
    await state.set_lesson_status(lesson["id"], "approved")
    assert "Open with their city." in await state.lessons_block()
