"""Dashboard accounts: invites, sign-in over the public address, roles.

The owner (loopback token) invites people; invitees reach the dashboard only
through dashboard.public_url, with a session cookie. Operators are refused
the admin-only routes.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

import harvey.dashboard as dash
from harvey.integrations.mail_provider import SendResult
from harvey.state import StateManager

LOCAL = "http://127.0.0.1:5555"
PUBLIC = "https://painel.example.com"
GOOD = "correct-horse-battery"


@pytest.fixture
def env(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(dash, "DB_PATH", tmp_path / "data" / "harvey.db")
    monkeypatch.setattr(dash, "ENV_FILE", tmp_path / ".env")
    dash.token_store.configure(tmp_path / "data" / "dashboard-token")
    monkeypatch.setattr(dash, "_public_cache", (float("inf"), PUBLIC))
    asyncio.run(StateManager(db_path=str(dash.DB_PATH)).init_db())
    sent = []

    class FakeMail:
        def is_configured(self):
            return True

        async def send_email(self, to, subject, body, **kw):
            sent.append((to, subject, body))
            return SendResult(ok=True, message_id="m1")

    monkeypatch.setattr("harvey.integrations.mail_provider.get_mail_provider", lambda c, e: FakeMail())
    monkeypatch.setattr("harvey.config.load_config", lambda *a, **k: type("C", (), {})())
    monkeypatch.setattr("harvey.config.load_env", lambda: object())
    return {"sent": sent, "token": dash.token_store.get()}


@pytest.fixture
def owner(env):
    with TestClient(dash.app, base_url=LOCAL, headers={"Authorization": f"Bearer {env['token']}"}) as c:
        yield c


def _public():
    return TestClient(dash.app, base_url=PUBLIC, headers={"Origin": PUBLIC}, follow_redirects=False)


def _invite(owner, email="ana@example.com", role="operator"):
    r = owner.post("/api/users/invite", json={"email": email, "name": "Ana", "role": role})
    assert r.status_code == 200 and r.json()["email_sent"], r.text
    return r.json()["invite_url"].split("#invite=")[1]


def _join(owner, email="ana@example.com", role="operator"):
    token = _invite(owner, email, role)
    c = _public()
    r = c.post("/api/auth/accept", json={"token": token, "password": GOOD})
    assert r.status_code == 200, r.text
    return c


def test_invite_is_emailed_with_a_link_on_the_public_address(owner, env):
    token = _invite(owner)
    [(to, subject, body)] = env["sent"]
    assert to == "ana@example.com" and f"{PUBLIC}/#invite={token}" in body
    users = owner.get("/api/users").json()["users"]
    assert [(u["email"], u["status"], u["role"]) for u in users] == [("ana@example.com", "invited", "operator")]
    assert "invite_hash" not in users[0] and "password_hash" not in users[0]


def test_accepting_signs_in_and_the_token_works_once(owner):
    token = _invite(owner)
    c = _public()
    assert c.post("/api/auth/invite-info", json={"token": token}).json()["email"] == "ana@example.com"
    assert c.post("/api/auth/accept", json={"token": token, "password": "short"}).status_code == 409
    assert c.post("/api/auth/accept", json={"token": token, "password": GOOD}).status_code == 200
    assert c.get("/api/auth/me").json() == {"email": "ana@example.com", "name": "Ana",
                                            "role": "operator", "owner": False}
    assert _public().post("/api/auth/accept", json={"token": token, "password": GOOD}).status_code == 404


def test_login_logout_and_wrong_password(owner):
    _join(owner)
    c = _public()
    assert c.post("/api/auth/login", json={"email": "ana@example.com", "password": "nope-nope-nope"}).status_code == 401
    assert c.post("/api/auth/login", json={"email": "who@example.com", "password": GOOD}).status_code == 401
    assert c.get("/api/auth/me").status_code == 401
    assert c.post("/api/auth/login", json={"email": "ANA@example.com", "password": GOOD}).status_code == 200
    assert c.get("/api/auth/me").status_code == 200
    c.post("/api/auth/logout")
    assert c.get("/api/auth/me").status_code == 401


def test_the_session_cookie_is_locked_down(owner):
    token = _invite(owner)
    r = _public().post("/api/auth/accept", json={"token": token, "password": GOOD})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=strict" in cookie


def test_operators_are_refused_admin_routes_and_admins_are_not(owner):
    op = _join(owner, "op@example.com", "operator")
    ad = _join(owner, "ad@example.com", "admin")
    assert op.get("/api/lessons").status_code == 200
    for method, path in [("GET", "/api/settings"), ("GET", "/api/users"), ("POST", "/api/users/invite"),
                         ("POST", "/api/sending/pause"), ("POST", "/api/harvey/stop"),
                         ("GET", "/api/export/prospects.csv"), ("POST", "/api/signals/status")]:
        assert op.request(method, path, json={} if method == "POST" else None).status_code == 403, path
    assert ad.get("/api/users").status_code == 200


def test_the_loopback_token_is_not_accepted_on_the_public_address_nor_cookies_on_loopback(owner, env):
    c = _join(owner)
    with TestClient(dash.app, base_url=PUBLIC, headers={"Origin": PUBLIC,
                                                         "Authorization": f"Bearer {env['token']}"}) as t:
        assert t.get("/api/lessons").status_code == 401
    cookie = c.cookies.get("harvey_session")
    with TestClient(dash.app, base_url=LOCAL, cookies={"harvey_session": cookie}) as t:
        assert t.get("/api/lessons").status_code == 401


def test_unknown_hosts_and_cross_origin_requests_are_refused(owner):
    c = _join(owner)
    assert TestClient(dash.app, base_url="https://evil.example").get("/").status_code == 400
    r = c.post("/api/auth/logout", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_disabling_ends_the_session_and_blocks_login(owner):
    c = _join(owner)
    uid = owner.get("/api/users").json()["users"][0]["id"]
    assert owner.post(f"/api/users/{uid}/status", json={"status": "disabled"}).json()["success"]
    assert c.get("/api/auth/me").status_code == 401
    assert _public().post("/api/auth/login", json={"email": "ana@example.com", "password": GOOD}).status_code == 401
    assert owner.post(f"/api/users/{uid}/status", json={"status": "active"}).json()["success"]
    assert _public().post("/api/auth/login", json={"email": "ana@example.com", "password": GOOD}).status_code == 200


def test_role_change_delete_and_no_self_service(owner):
    c = _join(owner, "ad@example.com", "admin")
    uid = owner.get("/api/users").json()["users"][0]["id"]
    assert c.post(f"/api/users/{uid}/role", json={"role": "operator"}).status_code == 409   # not yourself
    assert c.delete(f"/api/users/{uid}").status_code == 409
    assert owner.post(f"/api/users/{uid}/role", json={"role": "operator"}).json()["success"]
    assert c.get("/api/users").status_code == 403
    assert owner.delete(f"/api/users/{uid}").json()["success"]
    assert c.get("/api/auth/me").status_code == 401


def test_invite_validation_and_resend(owner, env):
    assert owner.post("/api/users/invite", json={"email": "not-an-email"}).status_code == 409
    assert owner.post("/api/users/invite", json={"email": "a@b.com", "role": "root"}).status_code == 409
    _join(owner)
    assert owner.post("/api/users/invite", json={"email": "ana@example.com"}).status_code == 409
    owner.post("/api/users/invite", json={"email": "new@example.com"})
    uid = next(u["id"] for u in owner.get("/api/users").json()["users"] if u["email"] == "new@example.com")
    before = len(env["sent"])
    assert owner.post(f"/api/users/{uid}/resend").json()["email_sent"]
    assert len(env["sent"]) == before + 1


def test_an_expired_invite_is_dead(owner):
    token = _invite(owner)
    from harvey.users import hash_token

    async def expire():
        import aiosqlite
        async with aiosqlite.connect(str(dash.DB_PATH)) as db:
            await db.execute("UPDATE users SET invite_expires = '2000-01-01T00:00:00' WHERE invite_hash = ?",
                             (hash_token(token),))
            await db.commit()

    asyncio.run(expire())
    assert _public().post("/api/auth/accept", json={"token": token, "password": GOOD}).status_code == 404


def test_login_is_rate_limited(owner, monkeypatch):
    monkeypatch.setattr(dash, "_login_failures", {})
    c = _public()
    codes = [c.post("/api/auth/login", json={"email": "x@example.com", "password": "bad-bad-bad-1"}).status_code
             for _ in range(dash.LOGIN_MAX + 1)]
    assert codes[:dash.LOGIN_MAX] == [401] * dash.LOGIN_MAX and codes[-1] == 429
