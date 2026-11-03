"""DISCOVER → PROFILE chaining.

The stages are joined by a query, not a hand-off. These tests pin that
property down, because it is what makes a crashed run resumable and
re-observation a time series instead of a duplicate.
"""

import os
import tempfile

import pytest
import pytest_asyncio

from harvey import pipeline as P
from harvey.collectors import discover as D
from harvey.config import load_config
from harvey.models import Company
from harvey.signals import SIGNAL_CATALOG, seed_signal_catalog
from harvey.state import StateManager


@pytest_asyncio.fixture
async def state():
    with tempfile.TemporaryDirectory() as tmp:
        sm = StateManager(os.path.join(tmp, "test.db"))
        await sm.init_db()
        await seed_signal_catalog(sm)
        for sig in SIGNAL_CATALOG:
            await sm.set_signal_status(sig["code"], "confirmed")
        yield sm


@pytest.fixture
def config():
    return load_config()


# ── The join: which companies still need a look ──


@pytest.mark.asyncio
async def test_a_new_company_needs_profiling(state):
    await state.add_company(Company(name="Baker", domain="baker.com"))
    pending = await state.companies_needing_profile()
    assert [c["domain"] for c in pending] == ["baker.com"]
    assert await state.count_companies_needing_profile() == 1


@pytest.mark.asyncio
async def test_a_profiled_company_drops_out(state):
    cid = await state.add_company(Company(name="Baker", domain="baker.com"))
    await state.add_observation("TECH_STACK", company_id=cid,
                                collector="profile", value_text="wordpress")
    assert await state.count_companies_needing_profile() == 0


@pytest.mark.asyncio
async def test_a_stale_profile_comes_back_round(state):
    """Re-reading on a schedule is what makes 'they dropped their agency'
    visible. A company profiled last year is not done forever."""
    cid = await state.add_company(Company(name="Baker", domain="baker.com"))
    await state.add_observation("TECH_STACK", company_id=cid, collector="profile",
                                value_text="wordpress",
                                observed_at="2020-01-01 00:00:00")
    assert await state.count_companies_needing_profile(stale_days=90) == 1
    assert await state.count_companies_needing_profile(stale_days=99999) == 0


@pytest.mark.asyncio
async def test_discovery_observations_do_not_count_as_a_profile(state):
    """DISCOVER writes observations too. Only PROFILE's own count."""
    cid = await state.add_company(Company(name="Baker", domain="baker.com"))
    await state.add_observation("SERP_RANK", company_id=cid,
                                collector="discover", value_num=14)
    assert await state.count_companies_needing_profile() == 1


@pytest.mark.asyncio
async def test_websiteless_companies_are_never_queued_for_profiling(state):
    """No site to read. That is the NO_WEBSITE cohort, not a backlog."""
    await state.add_company(Company(name="No Site", external_id="osm:node/1"))
    assert await state.count_companies_needing_profile() == 0


# ── The chain ──


class FakeDiscovery(D.DiscoveryProvider):
    key = "fake"
    label = "Fake"

    def __init__(self, businesses):
        self.businesses = businesses

    def configured(self, env):
        return True

    def estimate(self, queries):
        return 0.0

    async def fetch(self, client, env, query):
        return self.businesses


@pytest.fixture
def chain(monkeypatch):
    """A fake discovery provider and a profile stage that records its input."""
    seen = {"profiled": []}

    def install(businesses):
        monkeypatch.setitem(D.PROVIDERS, "fake", FakeDiscovery(businesses))

        async def fake_profile(state, companies):
            seen["profiled"] = [c["domain"] for c in companies]
            for company in companies:
                await state.add_observation(
                    "TECH_STACK", company_id=company["id"],
                    collector="profile", value_text="wordpress")
            return len(companies), "run-x"

        monkeypatch.setattr("harvey.collectors.profile.profile_companies",
                            fake_profile)
        return seen

    async def offline(state, city, radius_km=40, client=None):
        return ""
    monkeypatch.setattr(D, "geocode_city", offline)
    return install


@pytest.mark.asyncio
async def test_discovery_flows_straight_into_profiling(state, config, chain):
    seen = chain([
        D.Business(name="Baker", domain="baker.com", provider="fake"),
        D.Business(name="Metro", domain="metro.com", provider="fake"),
    ])
    report = await P.run_prospecting(
        state, config, "fake", [D.DiscoveryQuery(term="roofer")])

    assert report.discover["new_companies"] == 2
    assert sorted(seen["profiled"]) == ["baker.com", "metro.com"]
    assert report.profiled_companies == 2
    assert report.profile_observations == 2


@pytest.mark.asyncio
async def test_profiling_sweeps_up_what_an_earlier_run_left(state, config, chain):
    """The stages join through a query, so a run interrupted before profiling
    is finished by the next one — nothing has to remember anything."""
    await state.add_company(Company(name="Left Over", domain="leftover.com"))
    seen = chain([D.Business(name="Baker", domain="baker.com", provider="fake")])

    await P.run_prospecting(state, config, "fake", [D.DiscoveryQuery(term="roofer")])
    assert sorted(seen["profiled"]) == ["baker.com", "leftover.com"]


@pytest.mark.asyncio
async def test_profiling_runs_even_when_discovery_finds_nothing(state, config, chain):
    await state.add_company(Company(name="Left Over", domain="leftover.com"))
    seen = chain([])

    report = await P.run_prospecting(state, config, "fake",
                                     [D.DiscoveryQuery(term="roofer")])
    assert report.discover["new_companies"] == 0
    assert seen["profiled"] == ["leftover.com"]


@pytest.mark.asyncio
async def test_no_profile_stops_at_discovery(state, config, chain):
    seen = chain([D.Business(name="Baker", domain="baker.com", provider="fake")])
    report = await P.run_prospecting(state, config, "fake",
                                     [D.DiscoveryQuery(term="roofer")],
                                     profile=False)
    assert report.discover["new_companies"] == 1
    assert seen["profiled"] == []
    assert report.skipped == "profiling skipped"


@pytest.mark.asyncio
async def test_a_dry_run_neither_discovers_nor_profiles(state, config, chain):
    seen = chain([D.Business(name="Baker", domain="baker.com", provider="fake")])
    report = await P.run_prospecting(state, config, "fake",
                                     [D.DiscoveryQuery(term="roofer")],
                                     dry_run=True)
    assert report.skipped == "dry run"
    assert seen["profiled"] == []
    assert await state.count_companies_needing_profile() == 0


@pytest.mark.asyncio
async def test_a_failing_profile_stage_does_not_lose_the_discovery(
        state, config, monkeypatch, chain):
    chain([D.Business(name="Baker", domain="baker.com", provider="fake")])

    async def boom(state, companies):
        raise RuntimeError("network gone")
    monkeypatch.setattr("harvey.collectors.profile.profile_companies", boom)

    report = await P.run_prospecting(state, config, "fake",
                                     [D.DiscoveryQuery(term="roofer")])
    assert report.discover["new_companies"] == 1, "the companies are still saved"
    assert any("network gone" in e for e in report.errors)


@pytest.mark.asyncio
async def test_profile_stage_is_a_noop_with_nothing_to_do(state):
    companies, observations, run_id = await P.run_profile_stage(state)
    assert (companies, observations, run_id) == (0, 0, "")


@pytest.mark.asyncio
async def test_state_summary_reports_the_profiling_backlog(state):
    """The heartbeat reads this to decide whether to profile this cycle."""
    await state.add_company(Company(name="Baker", domain="baker.com"))
    summary = await state.get_state_summary()
    assert summary["unprofiled"] == 1


# ── Cohorts read the value, not just the row ──


@pytest.mark.asyncio
async def test_a_false_boolean_is_recorded_but_not_in_the_cohort(state):
    """PROFILE records RUNNING_GOOGLE_ADS=0 for everyone it checks — knowing
    you looked is worth keeping, and a later 0→1 flip is a real event. But
    "companies running Google Ads" must not silently mean "companies we
    checked for Google Ads", which is everyone.
    """
    running = await state.add_company(Company(name="Spender", domain="a.com"))
    checked = await state.add_company(Company(name="Not spending", domain="b.com"))
    await state.add_observation("RUNNING_GOOGLE_ADS", company_id=running,
                                collector="profile", value_num=1)
    await state.add_observation("RUNNING_GOOGLE_ADS", company_id=checked,
                                collector="profile", value_num=0)

    assert await state.cohort(["RUNNING_GOOGLE_ADS"]) == [running]

    counts = {c["signal_code"]: c for c in await state.signal_counts()}
    assert counts["RUNNING_GOOGLE_ADS"]["companies"] == 1

    # The negative finding is still on disk.
    rows = await state.get_observations(company_id=checked)
    assert rows[0]["value_num"] == 0


@pytest.mark.asyncio
async def test_text_signals_count_by_their_existence(state):
    """INCUMBENT_AGENCY has no numeric value — the row IS the finding."""
    cid = await state.add_company(Company(name="Baker", domain="a.com"))
    await state.add_observation("INCUMBENT_AGENCY", company_id=cid,
                                collector="profile", value_text="Studio3")
    assert await state.cohort(["INCUMBENT_AGENCY"]) == [cid]


@pytest.mark.asyncio
async def test_a_cohort_reflects_the_newest_observation(state):
    """They dropped their agency last quarter. The cohort should say so."""
    cid = await state.add_company(Company(name="Baker", domain="a.com"))
    await state.add_observation("RUNNING_GOOGLE_ADS", company_id=cid,
                                collector="profile", value_num=1,
                                observed_at="2026-01-01 00:00:00")
    assert await state.cohort(["RUNNING_GOOGLE_ADS"]) == [cid]

    await state.add_observation("RUNNING_GOOGLE_ADS", company_id=cid,
                                collector="profile", value_num=0,
                                observed_at="2026-06-01 00:00:00")
    assert await state.cohort(["RUNNING_GOOGLE_ADS"]) == []

    # Both observations survive — that time series is the durable asset.
    assert len(await state.get_observations(company_id=cid)) == 2


@pytest.mark.asyncio
async def test_exclusions_also_read_the_value(state):
    """Excluding "has online booking" must not exclude everyone who was
    merely checked for it."""
    a = await state.add_company(Company(name="A", domain="a.com"))
    b = await state.add_company(Company(name="B", domain="b.com"))
    for cid, booking in ((a, 1), (b, 0)):
        await state.add_observation("NO_SCHEMA_MARKUP", company_id=cid,
                                    collector="profile", value_num=1)
        await state.add_observation("NO_ONLINE_BOOKING", company_id=cid,
                                    collector="profile", value_num=booking)

    both = await state.cohort(["NO_SCHEMA_MARKUP"])
    assert sorted(both) == sorted([a, b])

    # Only A actually lacks booking, so excluding it leaves B.
    assert await state.cohort(["NO_SCHEMA_MARKUP"], exclude=["NO_ONLINE_BOOKING"]) == [b]


# ── Scheduled discovery ──


@pytest.mark.asyncio
async def test_auto_discovery_is_off_until_enabled_and_walks_the_query_list(state, config, monkeypatch):
    calls = []

    async def fake_run(st, cfg, provider, queries, max_spend=1.0, dry_run=False):
        calls.append([q.keyword() for q in queries])
        return D.DiscoveryReport(provider=provider, queries=len(queries))

    monkeypatch.setattr(D, "run_discovery", fake_run)
    config.icp.industries = ["a", "b", "c"]
    config.icp.geography = ["X"]
    config.discovery.enabled = False  # whatever harvey.local.yaml says
    config.discovery.provider = "osm"

    assert await P.run_auto_discovery(state, config) is None  # opt-in

    config.discovery.enabled = True
    config.discovery.queries_per_run = 2
    config.discovery.min_hours_between_runs = 0.0001
    assert await P.run_auto_discovery(state, config) is not None
    await state.start_run("discover", provider="osm")  # a run is on record now
    assert await P.run_auto_discovery(state, config) is None  # too soon after it
    assert len(calls) == 1 and len(calls[0]) == 2

    async with state._connect() as db:
        await db.execute("DELETE FROM runs")
        await db.commit()
    await P.run_auto_discovery(state, config)
    assert calls[1][0] != calls[0][0]  # the cursor moved on
    assert len(calls[1]) == 2


# ── Searches that wore out, and the cities Harvey adds ──


@pytest.mark.asyncio
async def test_worn_out_searches_are_skipped_and_fresh_ones_go_first(state, config, monkeypatch):
    calls = []

    async def fake_run(st, cfg, provider, queries, max_spend=1.0, dry_run=False):
        calls.append([(q.term, q.location) for q in queries])
        return D.DiscoveryReport(provider=provider, queries=len(queries))

    monkeypatch.setattr(D, "run_discovery", fake_run)
    config.icp.industries, config.icp.geography = ["a", "b"], ["X", "Y"]
    config.discovery.enabled, config.discovery.provider = True, "osm"
    config.discovery.queries_per_run, config.discovery.min_hours_between_runs = 2, 0.0001
    config.discovery.expand_cities = False
    await state.record_query_yield("a", "X", 50, 2)      # worn out: 4% new
    await state.record_query_yield("a", "Y", 50, 40)     # still rich, but already run
    await P.run_auto_discovery(state, config)
    assert sorted(calls[0]) == [("b", "X"), ("b", "Y")]   # never run first; ("a","X") skipped


class CityBrain:
    def __init__(self, *cities):
        self.cities, self.prompts = list(cities), []

    async def think_json(self, prompt, **kw):
        self.prompts.append(prompt)
        return {"city": self.cities.pop(0), "radius_km": 30, "why": "Grande e perto de Curitiba."}


@pytest.mark.asyncio
async def test_harvey_adds_a_city_at_most_as_often_as_allowed(state, config, monkeypatch):
    async def geocode(st, city, radius_km=40, client=None):
        return "" if city == "Lugar Nenhum, XX" else f"-25.0,-50.0,{radius_km}"
    monkeypatch.setattr(D, "geocode_city", geocode)
    pushed = []

    async def push(*a, **k):
        pushed.append(a[4])
    monkeypatch.setattr("harvey.notify.push", push)
    config.icp.geography = ["Curitiba, PR"]
    config.discovery.city_additions_per_week = 2

    brain = CityBrain("Lugar Nenhum, XX", "Guarapuava, PR", "Irati, PR")
    assert await P.expand_cities(state, config, brain) == ""            # not on the map
    async with state._connect() as db:      # that attempt was days ago
        await db.execute("UPDATE discovery_cities SET added_at = '2020-01-01 00:00:00'")
        await db.commit()
    assert await P.expand_cities(state, config, brain) == "Guarapuava, PR"
    assert "Lugar Nenhum" in brain.prompts[1]                            # never proposed again
    assert await P.expand_cities(state, config, brain) == ""            # spread across the week
    assert len(brain.prompts) == 2 and pushed and "Guarapuava" in pushed[0]

    queries = await P.discovery_queries(state, config)
    assert {q.location for q in queries} == {"Curitiba, PR", "Guarapuava, PR"}
    assert [q.coordinate for q in queries if q.location == "Guarapuava, PR"][0] == "-25.0,-50.0,30"
    assert await state.remove_discovery_city("Guarapuava, PR")
    assert {q.location for q in await P.discovery_queries(state, config)} == {"Curitiba, PR"}


@pytest.mark.asyncio
async def test_a_cancelled_profile_run_is_closed_not_left_running(state, monkeypatch):
    """The heartbeat's cycle timeout cancels profiling; the run row must not
    stay 'running' or the dashboard says the job stopped before finishing."""
    import asyncio
    from harvey.collectors import profile as PR

    async def hang(self, companies, run_id=""):
        raise asyncio.CancelledError
    monkeypatch.setattr(PR.ProfileCollector, "run", hang)
    with pytest.raises(asyncio.CancelledError):
        await PR.profile_companies(state, [{"id": "x", "domain": "baker.com"}])
    assert [r["status"] for r in await state.get_runs()] == ["cancelled"]


@pytest.mark.asyncio
async def test_a_slow_agent_does_not_throw_away_the_ones_that_finished(monkeypatch):
    """One slow agent used to cancel the whole cycle and drop every outcome,
    so a finished read_replies looked unread and sending was held."""
    import asyncio
    from harvey import main as M
    monkeypatch.setattr(M, "CYCLE_TIMEOUT_SECONDS", 0.2)

    async def quick():
        return True

    async def slow():
        await asyncio.sleep(10)

    out = await M._run_agents([("read_replies", quick()), ("profile", slow())])
    assert out == {"read_replies": True}
