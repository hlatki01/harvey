"""Companies tab: paged, searchable, sortable list; edit; delete that keeps
history and that discovery respects."""

import asyncio
import sqlite3

import pytest

from harvey.collectors.discover import Business, DiscoveryQuery, PROVIDERS, run_discovery
from harvey.state import StateManager
from tests.test_dashboard_signals import client  # noqa: F401  (fixture)


def _sql(db, query, params=()):
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute(query, params).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


@pytest.fixture
def db(client):  # noqa: F811
    asyncio.run(StateManager(str(client.db_path)).init_db())
    for i in range(30):
        _sql(client.db_path, "INSERT INTO companies (id, name, industry, location, external_id, created_at) "
                             "VALUES (?, ?, ?, 'Curitiba, PR', ?, ?)",
             (f"c{i:02d}", f"Pet {i:02d}", "Dentist" if i == 7 else "Pet store",
              f"google_places:{i}", f"2026-10-{i % 28 + 1:02d}T10:00:00"))
    return client


def test_the_list_pages_searches_and_sorts(db):
    r = db.get("/api/companies").json()
    assert r["total"] == 30 and len(r["rows"]) == 25 and r["per_page"] == 25
    assert len(db.get("/api/companies", params={"page": 2}).json()["rows"]) == 5
    assert [c["name"] for c in db.get("/api/companies", params={"q": "dentist"}).json()["rows"]] == ["Pet 07"]
    names = [c["name"] for c in db.get("/api/companies", params={"sort": "name", "dir": "asc"}).json()["rows"]]
    assert names[:3] == ["Pet 00", "Pet 01", "Pet 02"]
    # An unknown column can't reach the SQL: it sorts by newest.
    assert db.get("/api/companies", params={"sort": "name; DROP TABLE companies"}).json()["total"] == 30
    assert db.get("/api/companies", params={"q": "100%"}).json()["total"] == 0


def test_edit_changes_only_what_a_person_may_change(db):
    r = db.patch("/api/companies/c01", json={"name": " Bello  Faro ", "website": "https://bellofaro.com.br/",
                                             "source": "hacked"})
    assert r.json()["success"]
    assert _sql(db.db_path, "SELECT name, domain, source FROM companies WHERE id='c01'") == [
        ("Bello Faro", "bellofaro.com.br", "")]
    assert db.patch("/api/companies/c01", json={"name": ""}).status_code == 409
    assert db.patch("/api/companies/c01", json={"website": "javascript:alert(1)"}).status_code == 409
    assert db.patch("/api/companies/nope", json={"name": "X"}).status_code == 404


def test_delete_takes_contacts_and_signals_and_discovery_does_not_bring_it_back(db, monkeypatch):
    _sql(db.db_path, "INSERT INTO prospects (id, company_id, status) VALUES ('p1', 'c02', 'new')")
    _sql(db.db_path, "INSERT INTO outbox (id, prospect_id, status, body) VALUES ('o1', 'p1', 'pending_review', 'oi')")
    assert db.delete("/api/companies/c02").json()["success"]
    assert _sql(db.db_path, "SELECT COUNT(*) FROM companies WHERE id='c02'") == [(0,)]
    assert _sql(db.db_path, "SELECT COUNT(*) FROM prospects WHERE id='p1'") == [(0,)]
    assert _sql(db.db_path, "SELECT COUNT(*) FROM outbox WHERE id='o1'") == [(0,)]

    places = PROVIDERS["google_places"]

    async def fetch(http, env, query):
        return [Business(name="Pet 02", phone="41999990000", external_id="google_places:2", provider="google_places"),
                Business(name="New one", phone="41999990001", external_id="google_places:new", provider="google_places")]
    monkeypatch.setattr(places, "fetch", fetch)
    monkeypatch.setattr(places, "configured", lambda env: True)
    state = StateManager(str(db.db_path))
    from harvey.signals import seed_signal_catalog
    asyncio.run(seed_signal_catalog(state))
    _sql(db.db_path, "UPDATE signal_codes SET status='confirmed' WHERE code='FOUND_IN_SERP'")
    report = asyncio.run(run_discovery(state, None, "google_places", [DiscoveryQuery(term="pet")], max_spend=5))
    assert report.new_companies == 1
    assert _sql(db.db_path, "SELECT name FROM companies WHERE external_id='google_places:2'") == []


def test_a_company_with_history_is_not_deleted(db):
    _sql(db.db_path, "INSERT INTO prospects (id, company_id, status) VALUES ('p3', 'c03', 'contacted')")
    _sql(db.db_path, "INSERT INTO outbox (id, prospect_id, status, body) VALUES ('o3', 'p3', 'sent', 'oi')")
    r = db.delete("/api/companies/c03")
    assert r.status_code == 409 and "history" in r.json()["message"]
    assert _sql(db.db_path, "SELECT COUNT(*) FROM companies WHERE id='c03'") == [(1,)]


def test_industries_are_the_ones_on_file_most_common_first(db):
    assert db.get("/api/companies/industries").json() == [{"name": "Pet store", "n": 29}, {"name": "Dentist", "n": 1}]


@pytest.fixture
def graded(monkeypatch):
    """Email grading without the network: anything at bello.com.br verifies."""
    async def grade(email, env=None):
        return "verified" if email.endswith("@bello.com.br") else "guess"
    monkeypatch.setattr("harvey.integrations.email_finder.verify_published_address", grade)


def test_add_edit_and_delete_a_contact(db, graded):
    r = db.post("/api/companies/c01/contacts", json={
        "first_name": "Ana", "last_name": "Souza", "title": "Dona",
        "email": "Ana@Bello.com.br", "phone": "(41) 99899-8698"}).json()
    assert r["success"] and r["email_status"] == "verified"
    pid = r["id"]
    assert _sql(db.db_path, "SELECT company_id, email, email_status, email_verified, phone, source, status "
                            "FROM prospects WHERE id=?", (pid,)) == [
        ("c01", "ana@bello.com.br", "verified", 1, "5541998998698", "manual", "new")]
    assert [p["first_name"] for p in db.get("/api/companies/c01/contacts").json()] == ["Ana"]

    r = db.patch(f"/api/prospects/{pid}", json={"email": "ana@gmail.com", "title": "Sócia"}).json()
    assert r["success"] and r["email_status"] == "guess"     # a new address is graded again
    assert _sql(db.db_path, "SELECT title, email_status, email_verified FROM prospects WHERE id=?", (pid,)) == [
        ("Sócia", "guess", 0)]

    assert db.delete(f"/api/prospects/{pid}").json()["success"]
    assert db.get("/api/companies/c01/contacts").json() == []


def test_contact_input_is_checked(db, graded):
    assert db.post("/api/companies/c01/contacts", json={"title": "Dona"}).status_code == 409
    assert db.post("/api/companies/c01/contacts", json={"first_name": "A", "email": "not-an-email"}).status_code == 409
    assert db.post("/api/companies/c01/contacts", json={"first_name": "A", "phone": "123"}).status_code == 409
    assert db.post("/api/companies/c01/contacts", json={"first_name": "A", "linkedin_url": "javascript:x"}).status_code == 409
    assert db.post("/api/companies/nope/contacts", json={"first_name": "A"}).status_code == 404
    db.post("/api/companies/c01/contacts", json={"first_name": "A", "phone": "41 99899-8698"})
    dup = db.post("/api/companies/c02/contacts", json={"first_name": "B", "phone": "(41) 99899-8698"})
    assert dup.status_code == 409 and "already a contact" in dup.json()["message"]


def test_a_contact_with_history_is_not_deleted(db):
    _sql(db.db_path, "INSERT INTO prospects (id, company_id, status) VALUES ('p5', 'c05', 'replied')")
    _sql(db.db_path, "INSERT INTO conversations (id, prospect_id, thread_json, status) VALUES ('v5', 'p5', '[]', 'open')")
    r = db.delete("/api/prospects/p5")
    assert r.status_code == 409 and "history" in r.json()["message"]
    assert db.delete("/api/prospects/nope").status_code == 409
