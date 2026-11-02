"""IMAP reads mail the owner already opened; opt-out rates are per channel."""

import os
import tempfile
from types import SimpleNamespace

import pytest
import pytest_asyncio

from harvey.agents.analyst import Analyst
from harvey.integrations import smtp_mail
from harvey.models.conversation import Conversation
from harvey.models.prospect import Prospect
from harvey.state import StateManager


def _raw(sender, subject):
    return (f"From: {sender}\r\nTo: luis@ex.me\r\nSubject: {subject}\r\n"
            f"Message-ID: <{subject}@x>\r\n\r\nbody\r\n").encode()


class FakeImap:
    mailbox = {b"1": _raw("ana@shop.com", "re-oi"), b"2": _raw("luis@ex.me", "mine"),
               b"3": _raw("mailer-daemon@ex.me", "Delayed Mail (still being retried)")}
    fetches = []

    def __init__(self, host, port):
        pass

    def login(self, user, pw):
        pass

    def select(self, box, readonly=False):
        assert readonly, "must never mark the owner's mail as read"
        return "OK", [b"3"]

    def response(self, code):
        return code, [b"77"]

    def uid(self, cmd, *args):
        if cmd == "SEARCH":
            assert args[1] == "SINCE"
            return "OK", [b" ".join(self.mailbox)]
        assert args[1] == "(BODY.PEEK[])"
        self.fetches.append(args[0])
        return "OK", [(b"x", self.mailbox[args[0]])]

    def logout(self):
        pass


@pytest.mark.asyncio
async def test_imap_reads_opened_mail_once_and_skips_our_own(monkeypatch):
    monkeypatch.setattr(smtp_mail.imaplib, "IMAP4_SSL", FakeImap)
    env = SimpleNamespace(smtp_host="mail.ex.me", smtp_username="luis@ex.me", smtp_password="p")
    provider = smtp_mail.SmtpImapProvider(SimpleNamespace(persona=SimpleNamespace(email="luis@ex.me")), env)

    first = await provider.get_replies()
    second = await provider.get_replies()

    assert [m.subject for m in first] == ["re-oi", "Delayed Mail (still being retried)"]
    assert [m.subject for m in second] == [m.subject for m in first]  # handler dedups, not IMAP flags
    assert FakeImap.fetches == [b"1", b"2", b"3"]  # downloaded once each


@pytest_asyncio.fixture
async def state():
    with tempfile.TemporaryDirectory() as tmpdir:
        sm = StateManager(os.path.join(tmpdir, "test.db"))
        await sm.init_db()
        yield sm


@pytest.mark.asyncio
async def test_optouts_count_each_person_once_per_channel(state):
    async def reached(channel, status="contacted", unsubscribed=False):
        pid = await state.add_prospect(Prospect(first_name="A", email=f"{os.urandom(4).hex()}@x.com"))
        await state.update_prospect_status(pid, status)
        await state.add_outbox_item(prospect_id=pid, to_email="a@x.com", subject="s", body="b",
                                    send_at="2026-01-01", status="sent", channel=channel)
        if unsubscribed:
            await state.add_conversation(Conversation(id=pid, prospect_id=pid, channel=channel,
                                                      intent="unsubscribe"))

    await reached("whatsapp", "opted_out", unsubscribed=True)  # both signals: one person
    for _ in range(11):
        await reached("whatsapp")
    for _ in range(12):
        await reached("email")

    assert await state.optouts_by_channel() == {
        "whatsapp": {"reached": 12, "opted_out": 1}, "email": {"reached": 12, "opted_out": 0}}

    insights = Analyst(state)._generate_insights({"optouts": await state.optouts_by_channel()})
    urgent = [i for i in insights if i.startswith("URGENT")]
    assert urgent == [urgent[0]] and "whatsapp opt-out rate is 8.3% (1 of 12" in urgent[0]
