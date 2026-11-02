"""Companies → Add a company: look one business up on Google Maps, save the
right result, see whether Harvey can open a WhatsApp chat, and queue the
opening for approval."""

import asyncio
import sqlite3

import pytest

import harvey.agents.whatsapp as wa
import harvey.dashboard as dash
from harvey.collectors.discover import PROVIDERS, Business
from harvey.state import StateManager
from tests.test_dashboard_signals import client  # noqa: F401  (fixture)
from tests.test_whatsapp import FakeEvolution

BELLO = Business(name="Bello Faro", phone="(41) 99899-8698", address="R. X, 10 - Curitiba",
                 category="Pet store", rating=4.8, review_count=31,
                 external_id="google_places:ChIJbello", source_url="https://maps.google.com/?cid=1",
                 provider="google_places")


@pytest.fixture
def world(client, monkeypatch):  # noqa: F811
    asyncio.run(StateManager(str(client.db_path)).init_db())
    places = PROVIDERS["google_places"]
    asked = []

    async def fetch(http, env, query):
        asked.append(query)
        places.last_cost += places.REQUEST_COST
        return [BELLO]

    monkeypatch.setattr(places, "fetch", fetch)
    monkeypatch.setattr(places, "configured", lambda env: True)
    evo = FakeEvolution(existing={"5541998998698": "554198998698"})
    monkeypatch.setattr(wa.EvolutionClient, "from_env", classmethod(lambda cls, env: evo))
    site = {"found": ""}

    async def own_site(name, client=None):
        return site["found"]

    monkeypatch.setattr(wa, "find_own_site", own_site)
    client.asked, client.site = asked, site
    return client


def _rows(db, query):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(query).fetchall()
    finally:
        conn.close()


def test_lookup_shows_results_and_records_what_it_cost(world):
    r = world.post("/api/companies/lookup", json={"q": "Bello Faro Curitiba"}).json()
    assert [x["name"] for x in r["results"]] == ["Bello Faro"] and r["results"][0]["known_id"] == ""
    assert world.asked[0].term == "Bello Faro Curitiba" and world.asked[0].limit == 5
    assert _rows(world.db_path, "SELECT provider, status, cost_usd FROM runs") == [("google_places", "completed", 0.035)]
    assert _rows(world.db_path, "SELECT COUNT(*) FROM companies") == [(0,)]     # nothing saved yet


def test_add_saves_it_once_and_says_what_comes_next(world):
    picked = world.post("/api/companies/lookup", json={"q": "Bello Faro"}).json()["results"][0]
    r = world.post("/api/companies/add", json={"business": picked}).json()
    assert r["success"] and r["new"] and r["can_open"]
    assert [c["key"] for c in r["checks"]] == ["mobile", "site", "whatsapp", "demo"]
    assert _rows(world.db_path, "SELECT name, source FROM companies") == [("Bello Faro", "manual")]
    again = world.post("/api/companies/add", json={"business": picked}).json()
    assert again["company_id"] == r["company_id"] and not again["new"]
    assert world.post("/api/companies/lookup", json={"q": "Bello Faro"}).json()["results"][0]["known_id"] == r["company_id"]


def test_a_site_of_its_own_is_a_warning_not_a_block(world):
    world.site["found"] = "bellofaro.com.br"
    picked = world.post("/api/companies/lookup", json={"q": "Bello Faro"}).json()["results"][0]
    r = world.post("/api/companies/add", json={"business": picked}).json()
    site = next(c for c in r["checks"] if c["key"] == "site")
    assert not site["ok"] and site["warning"] and r["can_open"]


def test_a_landline_cannot_get_an_opening(world):
    picked = {**world.post("/api/companies/lookup", json={"q": "x"}).json()["results"][0], "phone": "(41) 3333-4444"}
    r = world.post("/api/companies/add", json={"business": picked}).json()
    assert not r["can_open"] and r["checks"][0]["key"] == "mobile"
    assert world.post(f"/api/companies/{r['company_id']}/whatsapp").status_code == 409


def test_open_whatsapp_queues_the_first_message_for_approval(world, monkeypatch):
    async def think(self, prompt, **kw):
        return "Aqui é o Theo, da guilhermeh. Vi que a Bello Faro ainda não tem site. Posso te mostrar um exemplo?"
    monkeypatch.setattr("harvey.brain.Brain.think", think)
    picked = world.post("/api/companies/lookup", json={"q": "Bello Faro"}).json()["results"][0]
    company_id = world.post("/api/companies/add", json={"business": picked}).json()["company_id"]
    r = world.post(f"/api/companies/{company_id}/whatsapp").json()
    assert r["success"]
    [(to, status, channel)] = _rows(world.db_path, "SELECT to_email, status, channel FROM outbox")
    assert (to, status, channel) == ("554198998698", "pending_review", "whatsapp")
    # A second press finds the number already a lead: nothing is queued twice.
    assert world.post(f"/api/companies/{company_id}/whatsapp").status_code == 409


def test_bad_input_is_refused(world):
    assert world.post("/api/companies/lookup", json={"q": "  "}).status_code == 409
    assert world.post("/api/companies/lookup", json={"q": "https://evil.test/maps/place/X"}).status_code == 409
    assert world.post("/api/companies/add", json={"business": {"name": "X", "external_id": "osm:1"}}).status_code == 409


def test_maps_links_become_a_search():
    url = "https://www.google.com/maps/place/Bello+Faro+Pet+Shop/@-25.4284,-49.2733,17z/data=!3m1"
    assert asyncio.run(dash._maps_query(url)) == ("Bello Faro Pet Shop", "-25.4284,-49.2733,2")
    assert asyncio.run(dash._maps_query("Bello Faro Curitiba")) == ("Bello Faro Curitiba", "")
    assert asyncio.run(dash._maps_query("https://maps.example.com/maps/place/X")) == ("", "")
