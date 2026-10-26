"""Signal-change triggers (pipeline.apply_triggers): a re-read that finds a new
value for a trigger signal boosts that company's not-yet-contacted prospects."""

import os
import tempfile

import pytest
import pytest_asyncio

from harvey.config import TriggersConfig
from harvey.models.company import Company
from harvey.models.prospect import Prospect
from harvey.pipeline import apply_triggers
from harvey.signals import seed_signal_catalog
from harvey.state import StateManager


@pytest_asyncio.fixture
async def state():
    with tempfile.TemporaryDirectory() as tmp:
        sm = StateManager(os.path.join(tmp, "t.db"))
        await sm.init_db()
        await seed_signal_catalog(sm)
        yield sm


async def _read(state, run, cid, code, num=None, text=""):
    await state.add_observations([{"company_id": cid, "signal_code": code, "collector": "profile",
                                   "value_num": num, "value_text": text}], run_id=run)


@pytest.mark.asyncio
async def test_a_changed_value_is_a_change_and_the_same_one_is_not(state):
    cid = await state.add_company(Company(name="Acme", domain="acme.com"))
    await _read(state, "r1", cid, "RUNNING_GOOGLE_ADS", num=0)
    assert await state.signal_changes("r1", ["RUNNING_GOOGLE_ADS"]) == []   # first look
    await _read(state, "r2", cid, "RUNNING_GOOGLE_ADS", num=0)
    assert await state.signal_changes("r2", ["RUNNING_GOOGLE_ADS"]) == []
    await _read(state, "r3", cid, "RUNNING_GOOGLE_ADS", num=1)
    assert await state.signal_changes("r3", ["RUNNING_GOOGLE_ADS"]) == [
        {"company_id": cid, "signal_code": "RUNNING_GOOGLE_ADS", "old": ["0"], "new": ["1"]}]
    assert await state.signal_changes("r3", ["HIRING_ROLE"]) == []


@pytest.mark.asyncio
async def test_a_new_value_among_several_is_the_change(state):
    cid = await state.add_company(Company(name="Acme", domain="acme.com"))
    await _read(state, "r1", cid, "HIRING_ROLE", text="Installer")
    await _read(state, "r2", cid, "HIRING_ROLE", text="Installer")
    await _read(state, "r2", cid, "HIRING_ROLE", text="Sales Manager")
    [change] = await state.signal_changes("r2", ["HIRING_ROLE"])
    assert change["new"] == ["Sales Manager"]


@pytest.mark.asyncio
async def test_a_change_boosts_only_prospects_not_yet_contacted(state):
    cid = await state.add_company(Company(name="Acme", domain="acme.com"))
    fresh = await state.add_prospect(Prospect(first_name="Ana", company="Acme", company_id=cid,
                                              email="ana@acme.com", score=50))
    reached = await state.add_prospect(Prospect(first_name="Bia", company="Acme", company_id=cid,
                                                email="bia@acme.com", score=50, status="contacted"))
    await _read(state, "r1", cid, "RUNNING_GOOGLE_ADS", num=0)
    await _read(state, "r2", cid, "RUNNING_GOOGLE_ADS", num=1)

    assert await apply_triggers(state, "r2", TriggersConfig(signals=["RUNNING_GOOGLE_ADS"])) == 1
    ana, bia = await state.get_prospect(fresh), await state.get_prospect(reached)
    assert ana.score == 70 and "Running Google Ads: yes (was no)" in ana.personalization_notes
    assert bia.score == 50 and bia.personalization_notes == ""


def test_only_profile_signals_can_trigger():
    with pytest.raises(ValueError):
        TriggersConfig(signals=["SERP_RANK"])
    with pytest.raises(ValueError):
        TriggersConfig(refresh_days=0)
