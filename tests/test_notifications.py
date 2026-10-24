import pytest
import pytest_asyncio

from harvey.state import StateManager


@pytest_asyncio.fixture
async def state(tmp_path):
    s = StateManager(str(tmp_path / "h.db"))
    await s.init_db()
    return s


async def _raise(state, key, title, signature="", tab="", target="", keep=()):
    """Raise one notification the way a dashboard refresh does (others in `keep` stay raised)."""
    items = [(k, k.upper(), "", "warn", "", "", "1") for k in keep]
    await state.sync_notifications(items + [(key, title, "", "warn", tab, target, signature)])


async def _one(state, view="inbox"):
    [n] = await state.get_notifications(view)
    return n


@pytest.mark.asyncio
async def test_read_comes_back_only_when_something_new_happens(state):
    await _raise(state, "k", "Lead", "1", "conversations", "c1")
    n = await _one(state)
    assert n["status"] == "new" and await state.count_unread_notifications() == 1
    await state.set_notification_status(n["id"], "read")
    await _raise(state, "k", "Lead", "1")
    assert (await _one(state))["status"] == "read"
    await _raise(state, "k", "Lead", "2")
    assert (await _one(state))["status"] == "new"


@pytest.mark.asyncio
async def test_muted_and_archived_stay_put_and_resolved_ones_vanish(state):
    await state.sync_notifications([("a", "A", "", "warn", "", "", "1"), ("b", "B", "", "warn", "", "", "1")])
    na = next(n for n in await state.get_notifications() if n["key"] == "a")
    nb = next(n for n in await state.get_notifications() if n["key"] == "b")
    await state.set_notification_status(na["id"], "muted")
    await state.set_notification_status(nb["id"], "archived")
    await _raise(state, "a", "A", "2", keep=("b",))
    assert (await _one(state, "muted"))["status"] == "muted"
    assert await state.get_notifications("inbox") == []
    await _raise(state, "c", "C", "1", keep=("a", "b"))
    await state.sync_notifications([("a", "A", "", "warn", "", "", "2"), ("b", "B", "", "warn", "", "", "1")])
    assert await state.get_notifications("inbox") == []        # C's cause is gone
    assert len(await state.get_notifications("archived")) == 1  # kept


@pytest.mark.asyncio
async def test_read_all_and_delete(state):
    await _raise(state, "a", "A", "1")
    await state.mark_all_notifications_read()
    assert await state.count_unread_notifications() == 0
    n = await _one(state)
    assert await state.delete_notification(n["id"]) == 1
    assert await state.get_notifications() == []
