"""/api/conversations carries the whole exchange: the cold emails a reply
answers live in the Outbox, not in the stored thread."""

import asyncio
import json
import sqlite3
from datetime import datetime, timezone

import pytest

import harvey.dashboard as dash

from tests.test_dashboard_signals import client  # noqa: F401  (fixture)
from harvey.state import StateManager


def _sql(db, query, params=()):
    with sqlite3.connect(db) as conn:
        conn.execute(query, params)
    conn.close()


def _seed(db):
    asyncio.run(StateManager(str(db)).init_db())
    _sql(db, "INSERT INTO prospects (id, first_name, email, status) VALUES ('p1', 'Iris', 'i@x.test', 'replied')")
    _sql(db, "INSERT INTO prospects (id, first_name, email, status) VALUES ('p2', 'Ben', 'b@x.test', 'contacted')")
    thread = [{"sender": "prospect", "content": "What does it cost?", "timestamp": "2026-10-18T10:00:00"}]
    _sql(db, "INSERT INTO conversations (id, prospect_id, campaign_id, thread_json, intent, status, updated_at) "
             "VALUES ('c1', 'p1', 'k1', ?, 'question', 'open', '2026-10-18T10:00:00')", (json.dumps(thread),))
    rows = [
        ("o1", "p1", "sequence", 1, "sent", "quick question", "Hi Iris, one.", "2026-10-10T09:00:00", ""),
        ("o2", "p1", "sequence", 2, "sent", "re: booking", "Hi Iris, two.", "2026-10-13T09:00:00", ""),
        ("o3", "p1", "sequence", 3, "cancelled", "closing", "Hi Iris, three.", None, ""),
        ("o4", "p1", "reply", 1, "pending_review", "Re: re: booking", "It's $49 a month.", None, "c1"),
        ("o5", "p1", "reply", 1, "sent", "Re: earlier", "Already in the thread.", "2026-10-18T11:00:00", "c1"),
        ("o6", "p2", "sequence", 1, "sent", "hello Ben", "Hi Ben.", "2026-10-11T09:00:00", ""),
    ]
    for oid, pid, kind, step, status, subject, body, sent_at, conv in rows:
        _sql(db, "INSERT INTO outbox (id, prospect_id, campaign_id, conversation_id, kind, step, to_email, "
                 "subject, body, status, sent_at, send_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
             (oid, pid, "k1" if kind == "sequence" else "", conv, kind, step, "x@x.test", subject, body,
              status, sent_at, "2026-10-19T09:00:00"))


def test_conversations_include_the_emails_they_answer_and_a_waiting_draft(client):
    _seed(client.db_path)
    [convo] = client.get("/api/conversations").json()
    assert [(e["step"], e["subject"]) for e in convo["emails"]] == [(1, "quick question"), (2, "re: booking")]
    assert all("sent_at" in e and "body" in e for e in convo["emails"])   # cancelled step 3 left out
    assert convo["draft"]["id"] == "o4" and convo["draft"]["body"] == "It's $49 a month."
    assert convo["thread"][0]["content"] == "What does it cost?"


def test_a_conversation_without_emails_or_draft(client):
    asyncio.run(StateManager(str(client.db_path)).init_db())
    _sql(client.db_path, "INSERT INTO prospects (id, first_name, email, status) VALUES ('p9', 'Ana', 'a@x.test', 'replied')")
    _sql(client.db_path, "INSERT INTO conversations (id, prospect_id, thread_json, status) VALUES ('c9', 'p9', '[]', 'open')")
    [convo] = client.get("/api/conversations").json()
    assert convo["emails"] == [] and convo["draft"] is None


def test_a_prospect_in_two_campaigns_sees_only_the_sequence_the_reply_answers(client):
    _seed(client.db_path)
    _sql(client.db_path, "INSERT INTO outbox (id, prospect_id, campaign_id, kind, step, to_email, subject, body, "
         "status, sent_at, send_at) VALUES ('o7', 'p1', 'k2', 'sequence', 1, 'x@x.test', 'other campaign', "
         "'Hi again.', 'sent', '2026-09-01T09:00:00', '2026-09-01T09:00:00')")
    [convo] = client.get("/api/conversations").json()
    assert [e["subject"] for e in convo["emails"]] == ["quick question", "re: booking"]
    assert all("campaign_id" not in e for e in convo["emails"])


def test_an_escalated_conversation_shows_on_today_and_can_be_handed_back(client):
    db = client.db_path
    asyncio.run(StateManager(str(db)).init_db())
    _sql(db, "INSERT INTO prospects (id, first_name, email, status) VALUES ('p1', 'Iris', 'i@x.test', 'replied')")
    _sql(db, "INSERT INTO conversations (id, prospect_id, status) VALUES ('c1', 'p1', 'needs_human')")
    today = client.get("/api/today").json()
    assert today["stats"]["needs_human"] == 1
    assert any(i["key"] == "needs-human" and i["tab"] == "conversations" for i in today["items"])

    assert client.post("/api/conversations/c1/status", json={"status": "open"}).json()["success"]
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT status FROM conversations WHERE id='c1'").fetchone()[0] == "open"
        logged = conn.execute("SELECT details_json FROM actions WHERE action_type='conversation_status'").fetchone()
    conn.close()
    assert json.loads(logged[0]) == {"conversation_id": "c1", "from": "needs_human", "to": "open"}
    assert not any(i["key"] == "needs-human" for i in client.get("/api/today").json()["items"])


def test_conversation_status_rejects_bad_input(client):
    asyncio.run(StateManager(str(client.db_path)).init_db())
    _sql(client.db_path, "INSERT INTO conversations (id, status) VALUES ('c1', 'open')")
    assert client.post("/api/conversations/c1/status", json={"status": "needs_human"}).status_code == 400
    assert client.post("/api/conversations/c1/status", json=["closed"]).status_code == 400
    assert client.post("/api/conversations/nope/status", json={"status": "closed"}).status_code == 404


def test_today_does_not_count_a_bot_greeting_as_a_live_conversation(client):
    _seed(client.db_path)   # c1: a real question
    _sql(client.db_path, "INSERT INTO conversations (id, prospect_id, intent, status) VALUES ('c2', 'p2', 'ooo', 'open')")
    today = client.get("/api/today").json()
    assert today["stats"]["open_conversations"] == 1 and today["stats"]["auto_replies"] == 1
    [live] = [i for i in today["items"] if i["key"] == "replies"]
    assert live["title"] == "1 live conversation" and "1 more were only automatic" in live["detail"]


def test_suggested_replies_come_back_and_bad_ones_are_dropped(client, monkeypatch):
    db, c = client.db_path, client
    asyncio.run(StateManager(str(db)).init_db())
    _sql(db, "INSERT INTO prospects (id, first_name, phone, status, company) VALUES ('w1', '', '5511999', 'replied', 'Oficina X')")
    thread = [{"sender": "prospect", "content": "Pode ser", "timestamp": "2026-10-30T14:03:00"}]
    _sql(db, "INSERT INTO conversations (id, prospect_id, channel, thread_json, status) "
             "VALUES ('cw', 'w1', 'whatsapp', ?, 'needs_human')", (json.dumps(thread),))
    seen = {}

    async def fake_think_json(self, prompt, **kw):
        seen["prompt"] = prompt
        return {"suggestions": ["E aí, conseguiu ver?", "", 42, "Bora marcar 10 min?"]}

    monkeypatch.setattr("harvey.brain.Brain.think_json", fake_think_json)
    r = c.post("/api/conversations/cw/suggest").json()
    assert r == {"success": True, "suggestions": ["E aí, conseguiu ver?", "Bora marcar 10 min?"]}
    assert "Cliente: Pode ser" in seen["prompt"]
    assert c.post("/api/conversations/nope/suggest").status_code == 404
    # With the team's draft: one improved version, written from it.
    async def improve(self, prompt, **kw):
        seen["prompt"] = prompt
        return {"suggestions": ["Conseguiu dar uma olhada no modelo?"]}

    monkeypatch.setattr("harvey.brain.Brain.think_json", improve)
    r = c.post("/api/conversations/cw/suggest", json={"draft": "viu o site?"}).json()
    assert r["suggestions"] == ["Conseguiu dar uma olhada no modelo?"]
    assert "viu o site?" in seen["prompt"]


def _wa_chat(db, last_sender, status="needs_human"):
    asyncio.run(StateManager(str(db)).init_db())
    _sql(db, "INSERT INTO prospects (id, first_name, phone, status, company) VALUES ('w1', '', '5511999', 'replied', 'Oficina X')")
    thread = [{"sender": "prospect", "content": "Pode ser", "timestamp": "2026-10-30T14:03:00"}]
    if last_sender == "team":
        thread.append({"sender": "team", "content": "Segue o modelo", "timestamp": "2026-10-30T14:08:00"})
    _sql(db, "INSERT INTO conversations (id, prospect_id, channel, thread_json, status) "
             "VALUES ('cw', 'w1', 'whatsapp', ?, ?)", (json.dumps(thread), status))
    # Harvey's own answer, written before the team stepped in.
    _sql(db, "INSERT INTO outbox (id, prospect_id, conversation_id, kind, step, to_email, subject, body, status, "
             "send_at, channel) VALUES ('old', 'w1', 'cw', 'reply', 1, '5511999', 'WhatsApp', 'Antiga', "
             "'approved', '2026-10-30T17:04:00', 'whatsapp')")


def _outbox(db):
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT id, status, body, send_at FROM outbox ORDER BY created_at").fetchall()
    finally:
        conn.close()


def _convo(db):
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT status, follow_up_at, follow_ups FROM conversations WHERE id='cw'").fetchone()
    finally:
        conn.close()


def test_handing_back_after_the_team_spoke_plans_a_follow_up(client, monkeypatch):
    _wa_chat(client.db_path, "team")
    monkeypatch.setattr("harvey.brain.Brain.think_json", lambda *a, **k: pytest.fail("no model call"))
    r = client.post("/api/conversations/cw/status", json={"status": "open"}).json()
    assert r["success"] and r["next"] == "follow_up" and r["follow_up_at"]
    assert r["follow_up_at"] > datetime.utcnow().isoformat()     # later, not now
    assert _convo(client.db_path) == ("open", r["follow_up_at"], 0)
    assert _outbox(client.db_path)[0][:2] == ("old", "cancelled")   # written before the team spoke


def test_a_lead_who_will_get_back_gets_a_follow_up_not_an_answer(client, monkeypatch):
    _wa_chat(client.db_path, "prospect")
    _sql(client.db_path, "UPDATE prospects SET phone='5511999990000' WHERE id='w1'")

    async def think(self, prompt, **kw):
        assert "Cliente: Pode ser" in prompt
        return {"action": "defer", "reply": "Combinado!", "confidence": "high", "intent": "interested"}
    monkeypatch.setattr("harvey.brain.Brain.think_json", think)
    r = client.post("/api/conversations/cw/status", json={"status": "open"}).json()
    assert r["next"] == "follow_up" and _convo(client.db_path)[1] == r["follow_up_at"]
    assert [row[0] for row in _outbox(client.db_path)] == ["old"]      # nothing said now


def test_a_question_left_open_is_answered_on_hand_back(client, monkeypatch):
    _wa_chat(client.db_path, "prospect")
    _sql(client.db_path, "UPDATE prospects SET phone='5511999990000' WHERE id='w1'")

    async def think(self, prompt, **kw):
        return {"action": "answer", "reply": "Sim, atendemos todo o Brasil.", "confidence": "high"}
    monkeypatch.setattr("harvey.brain.Brain.think_json", think)
    r = client.post("/api/conversations/cw/status", json={"status": "open"}).json()
    assert r["next"] == "reply" and _convo(client.db_path)[1] == ""
    assert "Sim, atendemos todo o Brasil." in [row[2] for row in _outbox(client.db_path)]


def test_cancel_or_hurry_the_planned_follow_up(client):
    _wa_chat(client.db_path, "team", status="open")
    _sql(client.db_path, "UPDATE conversations SET follow_up_at='2030-01-01T12:00:00' WHERE id='cw'")
    r = client.post("/api/conversations/cw/follow-up", json={"action": "now"}).json()
    assert r["success"] and r["follow_up_at"] < "2030"
    assert client.post("/api/conversations/cw/follow-up", json={"action": "cancel"}).json()["success"]
    assert _convo(client.db_path)[1] == ""
    assert client.post("/api/conversations/cw/follow-up", json={"action": "cancel"}).status_code == 409


def test_closing_cancels_what_harvey_still_had_queued(client):
    _wa_chat(client.db_path, "team", status="open")
    _sql(client.db_path, "UPDATE conversations SET follow_up_at='2030-01-01T12:00:00' WHERE id='cw'")
    assert client.post("/api/conversations/cw/status", json={"status": "closed"}).json()["success"]
    assert _outbox(client.db_path)[0][1] == "cancelled"
    assert _convo(client.db_path)[1] == ""


def test_outbox_list_pages_and_searches_with_the_company(client):
    db = client.db_path
    asyncio.run(StateManager(str(db)).init_db())
    _sql(db, "INSERT INTO prospects (id, first_name, phone, status, company) VALUES ('pa', '', '5511', 'replied', 'Oficina Alfa')")
    for i in range(30):
        _sql(db, "INSERT INTO outbox (id, prospect_id, kind, step, to_email, subject, body, status, send_at, error, "
                 "channel) VALUES (?, ?, 'sequence', 1, ?, 'WhatsApp', ?, 'cancelled', '2026-10-30', ?, 'whatsapp')",
             (f"o{i}", "pa" if i == 7 else "", f"55{i:04d}", f"texto {i}", "stop_on_reply"))
    first = client.get("/api/outbox/list?group=failed").json()
    assert first["total"] == 30 and len(first["rows"]) == 25
    assert len(client.get("/api/outbox/list?group=failed&page=2").json()["rows"]) == 5
    [hit] = client.get("/api/outbox/list?group=failed&q=alfa").json()["rows"]
    assert hit["id"] == "o7" and hit["company"] == "Oficina Alfa" and hit["body"] == "texto 7"
    assert client.get("/api/outbox/list?group=failed&q=100%25").json()["total"] == 0   # % is literal
    assert client.get("/api/outbox/list?group=sent").json()["total"] == 0
    assert client.get("/api/outbox/list?group=nope").status_code == 400
