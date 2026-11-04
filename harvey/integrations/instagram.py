"""Instagram through instagrapi: the app's private API, logged in as a real account.

Unofficial: Instagram can challenge, throttle or ban the account, and the
library breaks when the app changes. Every such answer becomes
InstagramBlocked, and the callers pause the whole channel (agents/instagram.py)
instead of insisting. Use an account you can afford to lose.

Login: the browser's `sessionid` cookie, or username + password, saved from
Settings → Integrations into the database's settings table (LOGIN_KEYS); the
INSTAGRAM_SESSIONID / INSTAGRAM_USERNAME / INSTAGRAM_PASSWORD env vars are the
fallback. Read on every use, so a login saved while Harvey runs is picked up.
The session is kept in data/instagram_session.json so the password is used
once, not every poll.

Everything is polled, like Evolution: Harvey needs no inbound endpoint.
instagrapi is synchronous, so each call runs in a worker thread, one at a time.
"""

import asyncio
import logging
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import timezone
from pathlib import Path

from harvey.integrations.mail_provider import SendResult

logger = logging.getLogger("harvey.instagram")

SESSION_FILE = Path("data") / "instagram_session.json"
# settings-table keys the dashboard saves the login under
LOGIN_KEYS = ("instagram_username", "instagram_password", "instagram_sessionid")


def saved_login(db_file: Path | str) -> dict[str, str]:
    """The login saved from the dashboard, {key: value}; {} when there is none."""
    db_file = Path(db_file)
    if not db_file.is_file():
        return {}
    try:
        with closing(sqlite3.connect(f"{db_file.resolve().as_uri()}?mode=ro", uri=True, timeout=5)) as db:
            rows = db.execute("SELECT key, value FROM settings WHERE key IN (?, ?, ?)",
                              LOGIN_KEYS).fetchall()
    except sqlite3.Error:
        return {}
    return {k: v for k, v in rows if v}


class InstagramBlocked(RuntimeError):
    """Instagram challenged, throttled or logged the account out: stop using it."""


def plain_text(text: str) -> str:
    """Instagram shows markdown and WhatsApp markers literally: drop them."""
    text = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m[1] or m[2], text)
    text = re.sub(r"(?<!\w)[*_~](\S(?:.*?\S)?)[*_~](?!\w)", r"\1", text)
    return re.sub(r"\[([^\]]+)\]\((\S+?)\)",
                  lambda m: m[2] if m[1] == m[2] else f"{m[1]}: {m[2]}", text)


def _epoch(dt) -> int:
    if dt is None:
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _patch_password_key():
    """instagrapi 3.2.2 reads the password key from qe/sync, which Instagram now
    404s; launcher/sync still sends it."""
    from instagrapi.mixins.password import PasswordMixin

    def password_publickeys(self):
        r = self.private.post("https://i.instagram.com/api/v1/launcher/sync/",
                              data={"id": self.uuid, "server_config_retrieval": "1"})
        key_id = r.headers.get("ig-set-password-encryption-key-id")
        key = r.headers.get("ig-set-password-encryption-pub-key")
        if not key_id or not key:
            raise InstagramBlocked(f"Instagram sent no password key (HTTP {r.status_code})")
        return int(key_id), key

    PasswordMixin.password_publickeys = password_publickeys


@dataclass
class InstagramInbound:
    provider_id: str      # message id (DM) or comment id, used for dedup
    user_id: str          # the sender's Instagram user id: what a reply goes to
    username: str
    body: str
    date: str = ""        # epoch seconds, as text (like WhatsAppInbound)
    comment_id: str = ""  # set when this is a comment, not a DM
    known_contact: bool = False  # the account already wrote in this DM thread


class InstagramClient:
    name = "instagram"

    def __init__(self, username: str = "", password: str = "", sessionid: str = "",
                 session_file: Path | str = SESSION_FILE, api=None, db_file: Path | str | None = None):
        self._env = {"instagram_username": (username or "").strip(), "instagram_password": password or "",
                     "instagram_sessionid": (sessionid or "").strip()}
        self.session_file = Path(session_file)
        # The database next to the session file: the dashboard saves the login there.
        self.db_file = Path(db_file) if db_file else self.session_file.parent / "harvey.db"
        self._api = api          # an instagrapi Client (tests pass a fake)
        self._lock = asyncio.Lock()

    @classmethod
    def from_env(cls, env, db_file: Path | str | None = None) -> "InstagramClient":
        return cls(env.instagram_username, env.instagram_password, env.instagram_sessionid,
                   db_file=db_file)

    def _login_values(self) -> tuple[str, str, str]:
        """(username, password, sessionid): the dashboard's when it saved any, else the env's."""
        src = saved_login(self.db_file) or self._env
        return (src.get("instagram_username", "").strip(), src.get("instagram_password", ""),
                src.get("instagram_sessionid", "").strip())

    def is_configured(self) -> bool:
        username, password, sessionid = self._login_values()
        return bool(self._api or sessionid or (username and password) or self.session_file.exists())

    # ── plumbing ──

    def _login(self):
        try:
            from instagrapi import Client
        except ImportError as e:
            raise RuntimeError("instagrapi is not installed: pip install instagrapi") from e
        _patch_password_key()
        api = Client()
        api.delay_range = [1, 3]     # pause between requests, like a person
        username, password, sessionid = self._login_values()
        if self.session_file.exists():
            api.load_settings(self.session_file)
        if sessionid:
            api.login_by_sessionid(sessionid)
        elif username and password:
            api.login(username, password)   # reuses the saved session when it's valid
        elif not self.session_file.exists():
            raise RuntimeError("set INSTAGRAM_SESSIONID or INSTAGRAM_USERNAME + INSTAGRAM_PASSWORD")
        self.session_file.parent.mkdir(parents=True, exist_ok=True)
        api.dump_settings(self.session_file)
        return api

    def _blocked(self, e: Exception) -> bool:
        from instagrapi import exceptions as x
        return isinstance(e, (x.ChallengeError, x.FeedbackRequired, x.LoginRequired,
                              x.PleaseWaitFewMinutes, x.RateLimitError, x.ClientThrottledError,
                              x.AccountSuspended, x.SentryBlock, x.ClientLoginRequired,
                              x.ReloginAttemptExceeded, x.BadPassword, x.BadCredentials,
                              x.ClientUnauthorizedError, x.ClientForbiddenError))

    async def _call(self, fn):
        """Run fn(api) in a thread; Instagram pushing back raises InstagramBlocked."""
        async with self._lock:
            def run():
                if self._api is None:
                    self._api = self._login()
                return fn(self._api)
            try:
                return await asyncio.to_thread(run)
            except InstagramBlocked:
                raise
            except Exception as e:
                if type(e).__module__.startswith("instagrapi") and self._blocked(e):
                    raise InstagramBlocked(f"{type(e).__name__}: {str(e)[:200]}") from e
                raise

    async def my_id(self) -> str:
        return str(await self._call(lambda api: api.user_id))

    # ── discovery ──

    async def search_profiles(self, query: str, limit: int = 20) -> list[dict]:
        """Accounts matching `query`, each read in full: a dict per profile
        (username, full_name, biography, external_url, follower_count, category,
        phone, is_business, is_private). Profiles that fail to read are skipped."""
        users = await self._call(lambda api: api.search_users(query))
        out = []
        for u in users[:limit]:
            try:
                i = await self._call(lambda api, pk=u.pk: api.user_info(pk))
            except InstagramBlocked:
                if out:
                    logger.warning("instagram: blocked after %d profiles; keeping them", len(out))
                    return out
                raise
            except Exception as e:
                logger.debug("instagram: reading @%s failed: %s", u.username, e)
                continue
            phone = i.contact_phone_number or (
                f"+{i.public_phone_country_code}{i.public_phone_number}"
                if i.public_phone_number else "")
            out.append({
                "pk": str(i.pk), "username": i.username, "full_name": i.full_name or "",
                "biography": i.biography or "", "external_url": str(i.external_url or ""),
                "follower_count": i.follower_count, "category": i.category or i.business_category_name or "",
                "phone": phone, "is_business": bool(i.is_business), "is_private": bool(i.is_private),
            })
        return out

    async def user_id_of(self, username: str) -> str:
        return str(await self._call(lambda api: api.user_id_from_username(username)))

    # ── messages ──

    async def get_replies(self, limit: int = 50) -> list[InstagramInbound]:
        """The newest text DMs people sent the account, including message
        requests (our own messages are left out)."""
        def read(api):
            me = str(api.user_id)
            return me, api.direct_threads(amount=20) + api.direct_pending_inbox(amount=20)

        me, threads = await self._call(read)
        out: list[InstagramInbound] = []
        for t in threads:
            names = {str(u.pk): u.username for u in t.users}
            ours = any(m.is_sent_by_viewer or str(m.user_id) == me for m in t.messages)
            for m in t.messages:
                body = (m.text or "").strip()
                if m.is_sent_by_viewer or str(m.user_id) == me or not body or not m.user_id:
                    continue
                out.append(InstagramInbound(
                    provider_id=str(m.id), user_id=str(m.user_id),
                    username=names.get(str(m.user_id), ""), body=body,
                    date=str(_epoch(m.timestamp)), known_contact=ours))
        out.sort(key=lambda m: int(m.date or 0))
        return out[-limit:]

    async def get_chat(self, user_id: str, limit: int = 30, before: int = 0) -> tuple[list[dict], bool]:
        """The DM thread with `user_id`, oldest first, in the dashboard's chat
        shape ({id, from_me, ts, text, media}), and whether older ones exist."""
        def read(api):
            found = api.direct_thread_by_participants([int(user_id)]).get("thread") or {}
            tid = found.get("thread_id")
            if not tid:
                return str(api.user_id), []
            return str(api.user_id), api.direct_messages(int(tid), amount=(limit if not before else 100) + 1)

        me, raw = await self._call(read)
        msgs = []
        for m in raw:
            ts = _epoch(m.timestamp)
            if before and ts >= before:
                continue
            msgs.append({"id": str(m.id), "from_me": bool(m.is_sent_by_viewer) or str(m.user_id) == me,
                         "ts": ts, "text": m.text or "[media]", "media": None})
        msgs.sort(key=lambda m: m["ts"], reverse=True)
        return msgs[:limit][::-1], len(msgs) > limit

    async def get_comments(self, max_age_days: int = 7, media_limit: int = 3) -> list[InstagramInbound]:
        """Comments on the account's newest posts (a request per post: kept few)."""
        import time

        def read(api):
            me = str(api.user_id)
            medias = api.user_medias(api.user_id, amount=media_limit)
            return me, [c for md in medias for c in api.media_comments(md.id, amount=50)]

        me, comments = await self._call(read)
        cutoff = time.time() - max_age_days * 86400
        out = []
        for c in comments:
            ts = _epoch(c.created_at_utc)
            if str(c.user.pk) == me or ts < cutoff:
                continue
            out.append(InstagramInbound(
                provider_id=str(c.pk), user_id=str(c.user.pk), username=c.user.username,
                body=(c.text or "").strip(), date=str(ts), comment_id=str(c.pk)))
        return out

    async def send_text(self, user_id: str, text: str) -> SendResult:
        """A DM to the user. InstagramBlocked propagates: the caller pauses the channel."""
        try:
            m = await self._call(lambda api: api.direct_send(plain_text(text), user_ids=[int(user_id)]))
        except InstagramBlocked:
            raise
        except Exception as e:
            return SendResult(ok=False, error=str(e)[:300])
        return SendResult(ok=True, message_id=str(m.id), thread_ref=str(m.thread_id or ""))

    async def test_connection(self) -> tuple[bool, str]:
        if not self.is_configured():
            return False, "set INSTAGRAM_SESSIONID or INSTAGRAM_USERNAME + INSTAGRAM_PASSWORD"
        try:
            me = await self._call(lambda api: api.account_info())
        except Exception as e:
            return False, str(e)[:300]
        return True, f"connected as @{me.username}"
