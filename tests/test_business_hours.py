from datetime import datetime, timezone

import pytest

from types import SimpleNamespace

from harvey.config import UsageConfig, closed_until

WEEK = {d: "09:00-18:00" for d in ("mon", "tue", "wed", "thu", "fri")} | {"sat": "09:00-13:00"}


def usage(**kw):
    return UsageConfig(business_hours=WEEK, quiet_hours={"timezone": "America/Sao_Paulo"}, **kw)


def at(text):  # São Paulo is UTC-3
    return datetime.fromisoformat(text + "-03:00").astimezone(timezone.utc)


def opens(now):
    o = closed_until(SimpleNamespace(usage=usage()), at(now))
    return o and o.strftime("%Y-%m-%d %H:%M")


def test_open_inside_window():
    assert opens("2026-11-02T10:00") is None   # Monday
    assert opens("2026-11-07T12:59") is None   # Saturday morning


def test_closed_waits_for_next_opening():
    assert opens("2026-11-02T08:00") == "2026-11-02 09:00"   # before opening
    assert opens("2026-11-02T18:00") == "2026-11-03 09:00"   # closing time itself is closed
    assert opens("2026-11-07T13:30") == "2026-11-09 09:00"   # Saturday afternoon → Monday
    assert opens("2026-11-08T10:00") == "2026-11-09 09:00"   # Sunday


def test_national_holidays_are_closed():
    br = SimpleNamespace(usage=usage(holidays_country="br"))
    o = lambda now: closed_until(br, at(now)).strftime("%Y-%m-%d %H:%M")
    assert o("2026-11-02T10:00") == "2026-11-03 09:00"   # Finados (Monday)
    assert o("2026-04-02T19:00") == "2026-04-04 09:00"   # Sexta-feira Santa → Saturday
    with pytest.raises(ValueError):
        UsageConfig(holidays_country="XX")


def test_empty_means_any_time():
    assert closed_until(SimpleNamespace(usage=UsageConfig())) is None
    assert closed_until(SimpleNamespace()) is None  # a config without usage


@pytest.mark.parametrize("bad", [{"mo": "09:00-18:00"}, {"mon": "18:00-09:00"}, {"mon": "9-18"}, {"mon": "25:00-26:00"}])
def test_rejects_bad_hours(bad):
    with pytest.raises(ValueError):
        UsageConfig(business_hours=bad)


def test_outbox_rows_say_when_business_hours_let_them_go(monkeypatch):
    import harvey.config as cfgmod
    import harvey.dashboard as dash
    monkeypatch.setattr(cfgmod, "load_config", lambda path=None: SimpleNamespace(usage=usage(holidays_country="BR")))
    rows = [{"send_at": "2026-10-31T14:20:00"},   # overdue, today is a holiday
            {"send_at": "2026-11-03T15:00:00"},   # Tuesday noon: open, left alone
            {"send_at": "2026-11-08T15:00:00"},   # Sunday
            {"send_at": None}]
    dash._add_sends_from(rows, now=at("2026-11-02T10:00"))
    assert rows[0]["sends_from"] == "2026-11-03T12:00:00"
    assert "sends_from" not in rows[1]
    assert rows[2]["sends_from"] == "2026-11-09T12:00:00"
    assert "sends_from" not in rows[3]
