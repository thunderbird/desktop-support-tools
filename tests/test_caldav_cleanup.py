# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Tests for the three CalDAV tools: making a calendar, and the two that clean up.

Everything here runs offline. The server is a stand-in that answers from a
script of replies, so what is under test is the part that gets a real calendar
wrong: how a reply is read, when a listing is believed, and what is chosen for
deletion. The network itself is not interesting and is not exercised.

Most of these cases exist because a real server behaved this way. Thundermail's
Stalwart caps every listing, does not continue a truncated first synchronisation,
and does not say which of its calendars is the default -- and each of those, taken
at face value, deletes a fraction of what was asked for while reporting success.
Those are the assertions worth having.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import caldav_asking as asking_tool  # noqa: E402
import caldav_delete_calendars as calendars_tool  # noqa: E402
import caldav_delete_events as events_tool  # noqa: E402
import caldav_make_calendar as make_tool  # noqa: E402
from caldav_delete_calendars import Account  # noqa: E402
from caldav_make_calendar import Maker, address_for  # noqa: E402
from caldav_delete_events import (  # noqa: E402
    Calendar,
    described,
    entries_in,
    ids_in,
    looks_scrubbed,
    times_in,
)

# Which name each tool signs in through, so a test can stand in for the server
# without caring which of the three it is driving.
SIGNS_IN = {events_tool: "Calendar", calendars_tool: "Account", make_tool: "Maker"}

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
CALENDARS = sorted(
    path for path in FIXTURES.glob("ics-*.ics") if not path.name.endswith(".expected.ics")
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def no_inherited_credentials(monkeypatch) -> None:
    """Nothing here may depend on what happens to be exported in your shell.

    Both variables are real ones somebody testing against a server will have set,
    and a test that passes only because of that is worse than no test.
    """
    monkeypatch.delenv("CALDAV_USER", raising=False)
    monkeypatch.delenv("CALDAV_PASSWORD", raising=False)


# --------------------------------------------------------------------------
# Reading a calendar


@pytest.mark.parametrize("path", CALENDARS, ids=lambda p: p.stem)
def test_times_survive_scrubbing(path: Path) -> None:
    """The times are what a scrubbed calendar shares with the original.

    This is the whole basis of matching on dates: the scrubber replaces titles,
    people and links, and leaves every date and repeat rule exactly as it was.
    If that ever stops being true, matching a server's copy against the
    unscrubbed original silently stops finding anything.
    """
    original = times_in(read(path))
    scrubbed = times_in(read(FIXTURES / f"{path.stem}.expected.ics"))
    assert original, f"{path.name} has no times to match on"
    assert original == scrubbed


@pytest.mark.parametrize("path", CALENDARS, ids=lambda p: p.stem)
def test_scrubbing_is_recognisable(path: Path) -> None:
    """A scrubbed calendar can be told from an unscrubbed one by sight.

    This is what identifies an import when the file that made it is long gone,
    so it has to hold for every fixture rather than for a convenient one.
    """
    assert not looks_scrubbed(read(path))
    assert looks_scrubbed(read(FIXTURES / f"{path.stem}.expected.ics"))


@pytest.mark.parametrize("path", CALENDARS, ids=lambda p: p.stem)
def test_identifiers_do_not_survive_scrubbing(path: Path) -> None:
    """Scrubbing gives every entry a new identifier, so none carry over.

    A random identifier per run is deliberate -- a predictable one could collide
    with an entry already in the profile it is imported into -- and it is also
    why a re-scrubbed copy is no use for finding what an earlier copy imported.
    """
    assert not ids_in(read(path)) & ids_in(read(FIXTURES / f"{path.stem}.expected.ics"))


def test_alarms_are_not_part_of_an_entry() -> None:
    """An alarm's own duration belongs to the alarm, not to the meeting.

    Folding it in would make two identical meetings with different reminders
    look like different meetings, and two different meetings with the same
    reminder look alike.
    """
    text = (
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:a\r\nDTSTART:20260101T090000Z\r\n"
        "BEGIN:VALARM\r\nTRIGGER:-PT15M\r\nDURATION:PT5M\r\nEND:VALARM\r\n"
        "END:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    kind, lines = entries_in(text)[0]
    assert kind == "VEVENT"
    assert not any("DURATION" in line for line in lines)


def test_a_folded_identifier_is_read_whole() -> None:
    """A value split across lines is one value, not a truncated one."""
    identifier = "a" * 70 + "-and-the-rest"
    text = (
        f"BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:{'a' * 70}\r\n"
        " -and-the-rest\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    assert ids_in(text) == {identifier}


def test_a_repeating_entry_and_its_changed_occurrence_share_an_identifier() -> None:
    """Two components, one entry -- which is what the server stores."""
    text = read(FIXTURES / "ics-recurrence-override.expected.ics")
    assert len(entries_in(text)) == 2
    assert len(ids_in(text)) == 1


# --------------------------------------------------------------------------
# Talking to a server


DAV = "DAV:"


def multistatus(body: str = "", token: str | None = None) -> bytes:
    tail = f"<D:sync-token>{token}</D:sync-token>" if token else ""
    return (
        '<D:multistatus xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">'
        f"{body}{tail}</D:multistatus>"
    ).encode()


def entry(href: str, etag: str = "e", status: str = "200 OK", data: str | None = None) -> str:
    calendar_data = f"<C:calendar-data>{data}</C:calendar-data>" if data else ""
    return (
        f"<D:response><D:href>{href}</D:href><D:propstat><D:prop>"
        f"<D:getetag>&quot;{etag}&quot;</D:getetag><D:resourcetype/>{calendar_data}"
        f"</D:prop></D:propstat><D:status>HTTP/1.1 {status}</D:status></D:response>"
    )


def talking(calendar: Calendar, reply):
    """Answer the calendar's requests with reply(method, path, body)."""
    calls: list[tuple[str, str]] = []

    def request(method, path, body=None, headers=None):
        calls.append((method, path))
        return reply(method, path, body or "")

    calendar.request = request
    return calls


def test_a_bad_password_says_what_to_try() -> None:
    calendar = Calendar("https://host/c/", "u", "p")
    talking(calendar, lambda *_: (401, b""))
    with pytest.raises(SystemExit) as raised:
        calendar.addresses()
    assert "app password" in str(raised.value)


def test_a_wrong_address_says_where_to_look() -> None:
    calendar = Calendar("https://host/c/", "u", "p")
    talking(calendar, lambda *_: (404, b""))
    with pytest.raises(SystemExit) as raised:
        calendar.addresses()
    assert "Location field" in str(raised.value)


def test_the_calendar_itself_is_not_one_of_its_entries() -> None:
    calendar = Calendar("https://host/c/", "u", "p")
    collection = (
        "<D:response><D:href>/c/</D:href><D:propstat><D:prop><D:resourcetype>"
        "<D:collection/></D:resourcetype></D:prop></D:propstat></D:response>"
    )
    talking(calendar, lambda *_: (207, multistatus(collection + entry("/c/a.ics"), "t")))
    found, _ = calendar.addresses()
    assert [href for href, _ in found] == ["/c/a.ics"]


def test_version_tags_come_from_the_listing() -> None:
    """So emptying a calendar needs no second request per entry to get them."""
    calendar = Calendar("https://host/c/", "u", "p")
    talking(calendar, lambda *_: (207, multistatus(entry("/c/a.ics", etag="v1"), "t")))
    found, _ = calendar.addresses()
    assert found == [("/c/a.ics", '"v1"')]


def test_an_entry_already_gone_is_not_one_to_delete() -> None:
    """A paged listing reports removals too, and those are not ours to chase."""
    calendar = Calendar("https://host/c/", "u", "p")
    listing = entry("/c/a.ics") + entry("/c/gone.ics", status="404 Not Found")
    talking(calendar, lambda *_: (207, multistatus(listing, "t")))
    found, _ = calendar.addresses()
    assert [href for href, _ in found] == ["/c/a.ics"]


def paging(pages: list[bytes]):
    """A server that answers each listing request with the next page."""
    state = {"n": 0}

    def reply(method, path, body):
        page = pages[min(state["n"], len(pages) - 1)]
        state["n"] += 1
        return (207, page)

    return reply


def test_a_full_page_then_silence_means_more_to_come() -> None:
    """Stalwart's behaviour, and the one that quietly loses most of a calendar.

    Its listing stops at the requested size and hands back a token meaning "you
    are up to date", so asking again returns nothing. Silence after a full page
    is not evidence of having seen everything -- it is evidence of a cap.
    """
    calendar = Calendar("https://host/c/", "u", "p", page=3)
    pages = [multistatus("".join(entry(f"/c/{i}.ics") for i in range(3)), "tok"),
             multistatus("", "tok")]
    talking(calendar, paging(pages))
    found, more = calendar.addresses()
    assert len(found) == 3
    assert more is True


def test_paging_to_a_short_page_means_the_end() -> None:
    calendar = Calendar("https://host/c/", "u", "p", page=3)
    pages = [
        multistatus("".join(entry(f"/c/{i}.ics") for i in range(3)), "a"),
        multistatus(entry("/c/3.ics"), "b"),
        multistatus("", "b"),
    ]
    talking(calendar, paging(pages))
    found, more = calendar.addresses()
    assert len(found) == 4
    assert more is False


def test_a_calendar_smaller_than_a_page_is_complete() -> None:
    calendar = Calendar("https://host/c/", "u", "p", page=10)
    talking(calendar, paging([multistatus(entry("/c/a.ics"), "t"), multistatus("", "t")]))
    found, more = calendar.addresses()
    assert len(found) == 1
    assert more is False


def test_a_server_with_no_paging_falls_back_and_admits_it() -> None:
    """The plain listing has no next page, so a capped one is all there is."""
    calendar = Calendar("https://host/c/", "u", "p")

    def reply(method, path, body):
        if "sync-collection" in body:
            return (403, b"")
        capped = entry("/c/a.ics") + "<D:error><D:number-of-matches-within-limits/></D:error>"
        return (207, multistatus(capped))

    talking(calendar, reply)
    found, more = calendar.addresses()
    assert [href for href, _ in found] == ["/c/a.ics"]
    assert more is True


def test_entries_are_read_in_batches() -> None:
    calendar = Calendar("https://host/c/", "u", "p", batch=2)
    bodies: list[str] = []

    def reply(method, path, body):
        bodies.append(body)
        return (207, multistatus())

    talking(calendar, reply)
    calendar.contents([(f"/c/{i}&x.ics", "e") for i in range(5)])
    assert [body.count("<D:href>") for body in bodies] == [2, 2, 1]
    assert all("&amp;" in body for body in bodies), "an address must survive being asked for"


def test_deleting_sends_the_version_tag_back() -> None:
    """So the server refuses if the entry changed since it was listed."""
    calendar = Calendar("https://host/c/", "u", "p")
    seen: dict = {}

    def request(method, path, body=None, headers=None):
        seen.update(method=method, path=path, headers=headers)
        return (204, b"")

    calendar.request = request
    assert calendar.delete("https://host/c/a%20b.ics", '"v1"') == (True, "")
    assert seen["path"] == "/c/a%20b.ics", "an address is already escaped; escaping it again moves it"
    assert seen["headers"] == {"If-Match": '"v1"'}


def test_an_entry_that_is_already_gone_counts_as_deleted() -> None:
    calendar = Calendar("https://host/c/", "u", "p")
    talking(calendar, lambda *_: (404, b""))
    assert calendar.delete("/c/a.ics", "") == (True, "")


def test_an_entry_that_changed_is_reported_rather_than_forced() -> None:
    calendar = Calendar("https://host/c/", "u", "p")
    talking(calendar, lambda *_: (412, b""))
    gone, why = calendar.delete("/c/a.ics", '"old"')
    assert not gone
    assert "changed" in why


# --------------------------------------------------------------------------
# Emptying a calendar


SCRUBBED_ENTRY = "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:s\r\nSUMMARY:Anonymized Data\r\nEND:VEVENT\r\nEND:VCALENDAR"
MY_ENTRY = "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:m\r\nSUMMARY:Dentist\r\nEND:VEVENT\r\nEND:VCALENDAR"


def server_holding(store: dict[str, str], page: int):
    """A server that caps its listing and forgets what is deleted."""

    def reply(method, path, body):
        if method == "DELETE":
            store.pop(path, None)
            return (204, b"")
        if "sync-collection" in body:
            shown = sorted(store)[:page]
            return (207, multistatus("".join(entry(href) for href in shown), "tok"))
        asked = [line.split(">")[1].split("<")[0] for line in body.splitlines() if "<D:href>" in line]
        listed = "".join(entry(href, data=store[href]) for href in asked if href in store)
        return (207, multistatus(listed))

    return reply


def answering(monkeypatch, answer) -> None:
    """Type this at whatever gets asked, or these in turn if there are several.

    Running out is end of input, not a repeat: a test that asks one question more
    than it meant to must fail rather than answer itself.
    """
    if not isinstance(answer, list):
        monkeypatch.setattr("builtins.input", lambda *_: answer)
        return

    queue = list(answer)

    def typed(*_):
        if not queue:
            raise EOFError
        return queue.pop(0)

    monkeypatch.setattr("builtins.input", typed)


def run(tool, argv, monkeypatch, calendar, answer="", terminal=True) -> int:
    monkeypatch.setenv("CALDAV_PASSWORD", "p")
    monkeypatch.setattr(tool, SIGNS_IN[tool], lambda *a, **k: calendar)
    monkeypatch.setattr(asking_tool, "interactive", lambda: terminal)
    answering(monkeypatch, answer)
    return tool.main(argv)


def signed_in_as(tool, argv, monkeypatch, calendar, answer="", terminal=True):
    """Run a tool and report the credentials it signed in with, if it got that far.

    Neither variable is set here, unlike run(), because what is being tested is
    where the credentials came from. Each test sets what it means to be there.
    """
    used: list[tuple[str, str]] = []

    def sign_in(url, user, password, **rest):
        used.append((user, password))
        return calendar

    monkeypatch.setattr(tool, SIGNS_IN[tool], sign_in)
    monkeypatch.setattr(asking_tool, "interactive", lambda: terminal)
    answering(monkeypatch, answer)
    return tool.main(argv), used


def test_a_capped_calendar_is_emptied_in_passes(monkeypatch, capsys) -> None:
    """The point of the whole exercise: the cap must not decide how much goes.

    A calendar of five behind a server that shows two at a time still ends up
    empty, because each pass deletes what it can see and then looks again.
    """
    store = {f"/c/{i}.ics": MY_ENTRY for i in range(5)}
    calendar = Calendar("https://host/c/", "u", "p", page=2)
    talking(calendar, server_holding(store, page=2))
    code = run(events_tool, ["https://host/c/", "-u", "u", "--everything", "--delete"],
               monkeypatch, calendar, answer="2")
    assert code == 0
    assert store == {}
    assert "Deleted 5 entries" in capsys.readouterr().out


def test_emptying_a_calendar_reads_nothing(monkeypatch, capsys) -> None:
    """Deleting everything needs no reason for any of it, so it asks for none.

    Reading the entries would be a download of the whole calendar per pass to
    learn something that cannot change the outcome.
    """
    store = {"/c/a.ics": MY_ENTRY}
    calendar = Calendar("https://host/c/", "u", "p")
    calls = talking(calendar, server_holding(store, page=10))
    run(events_tool, ["https://host/c/", "-u", "u", "--everything", "--delete"],
        monkeypatch, calendar, answer="1")
    assert not any(method == "REPORT" and path.endswith("multiget") for method, path in calls)
    assert sum(1 for method, _ in calls if method == "DELETE") == 1


def test_only_what_the_scrubber_made_is_deleted(monkeypatch, capsys) -> None:
    store = {"/c/theirs.ics": SCRUBBED_ENTRY, "/c/mine.ics": MY_ENTRY}
    calendar = Calendar("https://host/c/", "u", "p")
    talking(calendar, server_holding(store, page=10))
    code = run(events_tool, ["https://host/c/", "-u", "u", "--delete"], monkeypatch, calendar)
    assert code == 0
    assert list(store) == ["/c/mine.ics"]


def test_nothing_is_deleted_without_being_asked(monkeypatch, capsys) -> None:
    store = {"/c/theirs.ics": SCRUBBED_ENTRY}
    calendar = Calendar("https://host/c/", "u", "p")
    talking(calendar, server_holding(store, page=10))
    code = run(events_tool, ["https://host/c/", "-u", "u"], monkeypatch, calendar)
    assert code == 0
    assert store, "a dry run must change nothing"
    assert "dry run" in capsys.readouterr().out


def test_emptying_stops_rather_than_confirming(monkeypatch, capsys) -> None:
    store = {"/c/a.ics": MY_ENTRY}
    calendar = Calendar("https://host/c/", "u", "p")
    talking(calendar, server_holding(store, page=10))
    code = run(events_tool, ["https://host/c/", "-u", "u", "--everything", "--delete"],
               monkeypatch, calendar, answer="no")
    assert code == 1
    assert store, "a mistyped confirmation must change nothing"


def test_a_pass_that_deletes_nothing_ends_the_run(monkeypatch, capsys) -> None:
    """Otherwise a server refusing everything would be asked forever."""
    store = {f"/c/{i}.ics": MY_ENTRY for i in range(3)}
    calendar = Calendar("https://host/c/", "u", "p", page=1)

    def stubborn(method, path, body):
        if method == "DELETE":
            return (403, b"")
        return server_holding(store, page=1)(method, path, body)

    talking(calendar, stubborn)
    code = run(events_tool, ["https://host/c/", "-u", "u", "--everything", "--delete"],
               monkeypatch, calendar, answer="1")
    assert code == 1
    assert store == {f"/c/{i}.ics": MY_ENTRY for i in range(3)}


# --------------------------------------------------------------------------
# Deleting whole calendars


def collection(href: str, name: str, *kinds: str) -> str:
    marks = "".join(f"<C:{kind}/>" for kind in kinds)
    return (
        f"<D:response><D:href>{href}</D:href><D:propstat><D:prop>"
        f"<D:resourcetype><D:collection/>{marks}</D:resourcetype>"
        f"<D:displayname>{name}</D:displayname></D:prop></D:propstat></D:response>"
    )


HOME = multistatus(
    collection("/dav/cal/you/default", "Personal", "calendar")
    + collection("/dav/cal/you/inbox/", "Inbox", "calendar", "schedule-inbox")
    + collection("/dav/cal/you/outbox/", "Outbox", "calendar", "schedule-outbox")
    + collection("/dav/cal/you/ticket-7067/", "ticket 7067", "calendar")
    + collection("/dav/cal/you/6e37/", "my 3rd calendar", "calendar")
)


def test_scheduling_collections_are_not_calendars() -> None:
    """They are marked as calendars and deleting them breaks invitations."""
    account = Account("https://host/dav/cal/you/", "u", "p")
    talking(account, lambda *_: (207, HOME))
    found, _ = account.calendars()
    assert [name for _, name in found] == ["Personal", "ticket 7067", "my 3rd calendar"]


def test_the_default_is_kept_when_the_server_says_which_it_is(monkeypatch, capsys) -> None:
    account = Account("https://host/dav/cal/you/", "u", "p")
    told = multistatus(
        collection("/dav/cal/you/default", "Personal", "calendar").replace(
            "</D:prop>",
            "<C:schedule-default-calendar-URL><D:href>/dav/cal/you/default</D:href>"
            "</C:schedule-default-calendar-URL></D:prop>",
        )
        + collection("/dav/cal/you/6e37/", "my 3rd calendar", "calendar")
    )
    talking(account, lambda *_: (207, told))
    run(calendars_tool, ["https://host/dav/cal/you/", "-u", "u"], monkeypatch, account)
    assert "kept, it is the default" in capsys.readouterr().out


def test_the_default_is_kept_when_the_server_will_not_say(monkeypatch, capsys) -> None:
    """Stalwart advertises nothing, so the address is all there is to go on.

    Guessing is tolerable only because guessing wrong keeps a calendar rather
    than deleting one, and the server's own refusal is still the real backstop.
    """
    account = Account("https://host/dav/cal/you/", "u", "p")
    deleted: list[str] = []

    def reply(method, path, body):
        if method == "DELETE":
            deleted.append(path)
            return (204, b"")
        return (207, HOME)

    talking(account, reply)
    code = run(calendars_tool,
               ["https://host/dav/cal/you/", "-u", "u", "--keep", "ticket 7067", "--delete"],
               monkeypatch, account, answer="1")
    assert code == 0
    assert deleted == ["/dav/cal/you/6e37/"]


def test_a_refusal_names_the_default_rather_than_an_error_code() -> None:
    account = Account("https://host/dav/cal/you/", "u", "p")
    refusal = b'<D:error xmlns:D="DAV:"><A:default-calendar-needed/></D:error>'
    talking(account, lambda *_: (403, refusal))
    gone, why = account.delete_calendar("/dav/cal/you/default")
    assert not gone
    assert "default calendar" in why


def test_pointing_at_one_calendar_says_so(monkeypatch, capsys) -> None:
    """A calendar has no calendars in it, and that is a mistake worth naming."""
    account = Account("https://host/dav/cal/you/default/", "u", "p")
    talking(account, lambda *_: (207, multistatus()))
    code = run(calendars_tool, ["https://host/dav/cal/you/default/", "-u", "u"],
               monkeypatch, account)
    assert code == 1
    assert "take the last part off it" in capsys.readouterr().out


# --------------------------------------------------------------------------
# Making a calendar


def run_make(argv, monkeypatch, account, answer="", terminal=True) -> int:
    return run(make_tool, argv, monkeypatch, account, answer=answer, terminal=terminal)


ONE_CALENDAR = multistatus(collection("/dav/cal/you/default", "Personal", "calendar"))


def test_the_address_comes_from_the_name() -> None:
    """So a calendar made for a ticket is still identifiable a month later."""
    assert address_for("ticket 7067") == "ticket-7067"
    assert address_for("Réunion budgétaire!") == "r-union-budg-taire"
    assert address_for("  ") == "calendar"


def test_a_calendar_is_made_where_the_name_says(monkeypatch) -> None:
    account = Maker("https://host/dav/cal/you/", "u", "p")
    calls = talking(account, lambda method, *_: (207, ONE_CALENDAR) if method == "PROPFIND" else (201, b""))
    assert run_make(["https://host/dav/cal/you/", "ticket 7067", "-u", "u"], monkeypatch, account) == 0
    assert ("MKCALENDAR", "/dav/cal/you/ticket-7067/") in calls


def test_the_address_can_be_given_instead(monkeypatch) -> None:
    account = Maker("https://host/dav/cal/you/", "u", "p")
    calls = talking(account, lambda method, *_: (207, ONE_CALENDAR) if method == "PROPFIND" else (201, b""))
    run_make(["https://host/dav/cal/you/", "ticket 7067", "-u", "u", "--path", "scratch"],
             monkeypatch, account)
    assert ("MKCALENDAR", "/dav/cal/you/scratch/") in calls


def test_a_name_that_is_markup_does_not_break_the_request(monkeypatch) -> None:
    """Someone will call a calendar "R&D <test>", and it must still be valid XML."""
    account = Maker("https://host/dav/cal/you/", "u", "p")
    sent = []

    def reply(method, path, body):
        sent.append(body)
        return (207, ONE_CALENDAR) if method == "PROPFIND" else (201, b"")

    talking(account, reply)
    run_make(["https://host/dav/cal/you/", "R&D <test>", "-u", "u"], monkeypatch, account)
    made = sent[-1]
    assert "R&amp;D &lt;test&gt;" in made
    ET.fromstring(made)


def test_an_address_already_in_use_is_left_alone(monkeypatch, capsys) -> None:
    """Nothing here overwrites a calendar, because its contents would go too."""
    account = Maker("https://host/dav/cal/you/", "u", "p")
    calls = talking(account, lambda *_: (207, HOME))
    code = run_make(["https://host/dav/cal/you/", "ticket 7067", "-u", "u"], monkeypatch, account)
    assert code == 1
    assert "already there" in capsys.readouterr().out
    assert not [method for method, _ in calls if method == "MKCALENDAR"]


def test_a_name_already_in_use_is_left_alone(monkeypatch, capsys) -> None:
    """Two calendars with one name are indistinguishable in Thunderbird's list."""
    account = Maker("https://host/dav/cal/you/", "u", "p")
    calls = talking(account, lambda *_: (207, HOME))
    code = run_make(["https://host/dav/cal/you/", "TICKET 7067", "-u", "u", "--path", "other"],
                    monkeypatch, account)
    assert code == 1
    assert "already has a calendar called" in capsys.readouterr().out
    assert not [method for method, _ in calls if method == "MKCALENDAR"]


def test_pointing_at_one_calendar_says_so_here_too(monkeypatch, capsys) -> None:
    account = Maker("https://host/dav/cal/you/default/", "u", "p")
    talking(account, lambda *_: (207, multistatus()))
    code = run_make(["https://host/dav/cal/you/default/", "scratch", "-u", "u"], monkeypatch, account)
    assert code == 1
    assert "take the last part off it" in capsys.readouterr().out


def test_a_server_that_says_it_exists_is_reported(monkeypatch, capsys) -> None:
    """405 is the server contradicting the listing, and it must not read as success."""
    account = Maker("https://host/dav/cal/you/", "u", "p")
    talking(account, lambda method, *_: (207, ONE_CALENDAR) if method == "PROPFIND" else (405, b""))
    code = run_make(["https://host/dav/cal/you/", "ticket 7067", "-u", "u"], monkeypatch, account)
    assert code == 1
    assert "already at that address" in capsys.readouterr().err


def test_a_bad_password_says_to_use_an_app_password(monkeypatch, capsys) -> None:
    account = Maker("https://host/dav/cal/you/", "u", "p")
    talking(account, lambda method, *_: (207, ONE_CALENDAR) if method == "PROPFIND" else (401, b""))
    code = run_make(["https://host/dav/cal/you/", "ticket 7067", "-u", "u"], monkeypatch, account)
    assert code == 1
    assert "app password" in capsys.readouterr().err


# --------------------------------------------------------------------------
# Signing in

# The environment and a question do the same job here, and both matter. Reading
# CALDAV_USER is what stops the testing loop repeating -u on every command;
# asking for it is what stops a forgotten export becoming an unexplained 401.


@pytest.mark.parametrize(
    "tool,argv",
    [
        (events_tool, ["https://host/c/"]),
        (calendars_tool, ["https://host/dav/cal/you/"]),
        (make_tool, ["https://host/dav/cal/you/", "scratch"]),
    ],
    ids=["events", "calendars", "make"],
)
def test_all_three_take_the_credentials_from_the_environment(tool, argv, monkeypatch) -> None:
    kind = {events_tool: Calendar, calendars_tool: Account, make_tool: Maker}[tool]
    calendar = kind(argv[0], "ignored", "ignored")
    talking(calendar, lambda *_: (207, multistatus()))
    monkeypatch.setenv("CALDAV_USER", "you@example.com")
    monkeypatch.setenv("CALDAV_PASSWORD", "secret")
    _, used = signed_in_as(tool, argv, monkeypatch, calendar)
    assert used == [("you@example.com", "secret")]


def test_the_flag_beats_the_environment(monkeypatch) -> None:
    """Otherwise an export you have forgotten about silently wins an argument."""
    calendar = Calendar("https://host/c/", "u", "p")
    talking(calendar, lambda *_: (207, multistatus()))
    monkeypatch.setenv("CALDAV_USER", "stale@example.com")
    monkeypatch.setenv("CALDAV_PASSWORD", "secret")
    _, used = signed_in_as(
        events_tool, ["https://host/c/", "-u", "you@example.com"], monkeypatch, calendar
    )
    assert used == [("you@example.com", "secret")]


def test_the_username_is_asked_for_when_nothing_else_says(monkeypatch) -> None:
    calendar = Calendar("https://host/c/", "u", "p")
    talking(calendar, lambda *_: (207, multistatus()))
    monkeypatch.setenv("CALDAV_PASSWORD", "secret")
    _, used = signed_in_as(
        events_tool, ["https://host/c/"], monkeypatch, calendar, answer=["you@example.com"]
    )
    assert used == [("you@example.com", "secret")]


def test_no_username_stops_rather_than_signing_in_as_nobody(monkeypatch, capsys) -> None:
    """An empty answer used to be a required flag, so it must not become a 401."""
    calendar = Calendar("https://host/c/", "u", "p")
    code, used = signed_in_as(
        events_tool, ["https://host/c/"], monkeypatch, calendar, answer=[""]
    )
    assert code == 1
    assert not used, "nothing should be sent without a username"
    assert "CALDAV_USER" in capsys.readouterr().err


def test_no_app_password_says_where_to_get_one(monkeypatch, capsys) -> None:
    calendar = Calendar("https://host/c/", "u", "p")
    monkeypatch.setattr(asking_tool, "getpass", lambda *_: "")
    code, used = signed_in_as(events_tool, ["https://host/c/", "-u", "u"], monkeypatch, calendar)
    assert code == 1
    assert not used
    assert "app password" in capsys.readouterr().err


def test_confirm_gives_up_before_asking_for_a_password(monkeypatch, capsys) -> None:
    """Learning nobody can answer is much better news before you type a password."""
    calendar = Calendar("https://host/c/", "u", "p")
    asked = []
    monkeypatch.setattr(asking_tool, "getpass", lambda *_: asked.append(1) or "p")
    code, used = signed_in_as(
        events_tool, ["https://host/c/", "--confirm"], monkeypatch, calendar, terminal=False
    )
    assert code == 1
    assert not asked and not used
    assert "not a terminal" in capsys.readouterr().err


@pytest.mark.parametrize(
    "tool,argv",
    [
        (events_tool, ["https://host/c/", "-u", "u"]),
        (calendars_tool, ["https://host/dav/cal/you/", "-u", "u"]),
        (make_tool, ["https://host/dav/cal/you/", "scratch", "-u", "u"]),
    ],
    ids=["events", "calendars", "make"],
)
def test_confirm_and_yes_cannot_both_be_given(tool, argv) -> None:
    """They are opposite answers to the same question, so asking for both is a mistake."""
    with pytest.raises(SystemExit) as raised:
        tool.main(argv + ["--confirm", "--yes"])
    assert raised.value.code == 2


# --------------------------------------------------------------------------
# Being asked, and not being asked


def dated(summary: str, start: str, uid: str = "u") -> str:
    """One entry the scrubber would recognise as its own, at a time you can name."""
    return (
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\n"
        f"UID:{uid}\r\nSUMMARY:{summary}\r\nDTSTART:{start}\r\n"
        "END:VEVENT\r\nEND:VCALENDAR"
    )


SCRUBBED_AT = {
    f"/c/{day}.ics": dated("Anonymized Data", f"2026030{day}T090000Z", uid=str(day))
    for day in (1, 2, 3)
}


def test_an_entry_says_what_it_is_and_when(monkeypatch) -> None:
    """The address is a server-generated name and tells you nothing you recognise."""
    assert described(dated("Team standup", "20260304T090000Z")) == (
        "Team standup  --  2026-03-04 09:00"
    )
    assert described(dated("Sports day", "20260304")) == "Sports day  --  2026-03-04"
    assert described(dated("Lunch\\, then gym", "20260304")) == "Lunch, then gym  --  2026-03-04"
    assert described(dated("Weekly", "whenever")) == "Weekly  --  whenever"
    assert described("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:x\r\nEND:VEVENT\r\nEND:VCALENDAR") == (
        "(no title)  --  no start time"
    )
    assert described(dated("x" * 80, "20260304")).startswith("x" * 57 + "...")


def test_confirming_keeps_what_you_say_no_to(monkeypatch, capsys) -> None:
    store = dict(SCRUBBED_AT)
    calendar = Calendar("https://host/c/", "u", "p")
    talking(calendar, server_holding(store, page=10))
    code = run(events_tool, ["https://host/c/", "-u", "u", "--delete", "--confirm"],
               monkeypatch, calendar, answer=["y", "n", "y"])
    assert code == 0
    assert list(store) == ["/c/2.ics"]
    out = capsys.readouterr().out
    assert "2026-03-01 09:00" in out
    assert "1 left alone" in out


def test_all_the_rest_stops_the_asking(monkeypatch, capsys) -> None:
    """The answer that makes this safe to turn on without knowing how many there are.

    Two answers for three entries: a third question would run the queue dry and
    end the run, so this passing is the evidence that it stopped asking.
    """
    store = dict(SCRUBBED_AT)
    calendar = Calendar("https://host/c/", "u", "p")
    talking(calendar, server_holding(store, page=10))
    code = run(events_tool, ["https://host/c/", "-u", "u", "--delete", "--confirm"],
               monkeypatch, calendar, answer=["y", "a"])
    assert code == 0
    assert store == {}


def test_stopping_is_not_a_failure(monkeypatch, capsys) -> None:
    store = dict(SCRUBBED_AT)
    calendar = Calendar("https://host/c/", "u", "p")
    talking(calendar, server_holding(store, page=10))
    code = run(events_tool, ["https://host/c/", "-u", "u", "--delete", "--confirm"],
               monkeypatch, calendar, answer=["y", "q"])
    assert code == 0, "you were asked, and stopping was one of the answers"
    assert list(store) == ["/c/2.ics", "/c/3.ics"]
    out = capsys.readouterr().out
    assert "Stopped where you asked" in out
    assert "unsubscribe" in out


def test_a_declined_entry_is_not_offered_again_next_pass(monkeypatch, capsys) -> None:
    """A capped calendar comes round again, and so would a question you have answered.

    Three answers for three entries across two passes. The entry declined in the
    first pass is listed again in the second, and asking about it would run the
    queue dry before the last entry was reached.
    """
    store = dict(SCRUBBED_AT)
    calendar = Calendar("https://host/c/", "u", "p", page=2)
    talking(calendar, server_holding(store, page=2))
    code = run(events_tool, ["https://host/c/", "-u", "u", "--delete", "--confirm"],
               monkeypatch, calendar, answer=["n", "y", "y"])
    assert code == 0
    assert list(store) == ["/c/1.ics"]
    assert "1 left alone" in capsys.readouterr().out


def test_emptying_reads_only_the_entry_it_is_asking_about(monkeypatch, capsys) -> None:
    """--everything reads nothing, so a question has to fetch its own entry.

    One request per question, which is nothing next to the time you take to
    answer it, and answering "all the rest" goes back to reading nothing at all.
    """
    store = {f"/c/{i}.ics": MY_ENTRY for i in range(1, 4)}
    calendar = Calendar("https://host/c/", "u", "p")
    reads = []
    server = server_holding(store, page=10)

    def counting(method, path, body):
        if "calendar-multiget" in body:
            reads.append(body.count("<D:href>"))
        return server(method, path, body)

    talking(calendar, counting)
    code = run(events_tool, ["https://host/c/", "-u", "u", "--everything", "--delete", "--confirm"],
               monkeypatch, calendar, answer=["y", "a"])
    assert code == 0
    assert store == {}
    assert reads == [1, 1], "one entry per question, and none once it stopped asking"
    assert "Dentist" in capsys.readouterr().out


def test_confirming_each_entry_replaces_typing_the_number(monkeypatch, capsys) -> None:
    """Being asked about all of them and then about each of them is one question too many.

    The single answer here is the per-entry one. If the bulk confirmation still
    ran it would take "y" for the count, not match, and delete nothing.
    """
    store = {"/c/a.ics": MY_ENTRY}
    calendar = Calendar("https://host/c/", "u", "p")
    talking(calendar, server_holding(store, page=10))
    code = run(events_tool, ["https://host/c/", "-u", "u", "--everything", "--delete", "--confirm"],
               monkeypatch, calendar, answer=["y"])
    assert code == 0
    assert store == {}


def test_yes_empties_a_calendar_without_asking(monkeypatch, capsys) -> None:
    """The answer given would refuse the confirmation, so reaching empty proves it never ran."""
    store = {"/c/a.ics": MY_ENTRY}
    calendar = Calendar("https://host/c/", "u", "p")
    talking(calendar, server_holding(store, page=10))
    code = run(events_tool, ["https://host/c/", "-u", "u", "--everything", "--delete", "--yes"],
               monkeypatch, calendar, answer="no")
    assert code == 0
    assert store == {}


def test_yes_does_not_mean_delete(monkeypatch, capsys) -> None:
    """Skipping the question is not the same as answering it, and a dry run stays one."""
    store = {"/c/theirs.ics": SCRUBBED_ENTRY}
    calendar = Calendar("https://host/c/", "u", "p")
    talking(calendar, server_holding(store, page=10))
    code = run(events_tool, ["https://host/c/", "-u", "u", "--yes"], monkeypatch, calendar)
    assert code == 0
    assert store, "--yes answers a question; it does not ask for the deletion"
    assert "dry run" in capsys.readouterr().out


def test_confirming_picks_which_calendars_go(monkeypatch, capsys) -> None:
    account = Account("https://host/dav/cal/you/", "u", "p")
    deleted: list[str] = []

    def reply(method, path, body):
        if method == "DELETE":
            deleted.append(path)
            return (204, b"")
        return (207, HOME)

    talking(account, reply)
    code = run(calendars_tool, ["https://host/dav/cal/you/", "-u", "u", "--delete", "--confirm"],
               monkeypatch, account, answer=["n", "y"])
    assert code == 0
    assert deleted == ["/dav/cal/you/6e37/"]
    assert "1 calendar left alone" in capsys.readouterr().out


def test_stopping_leaves_the_calendars_after_it(monkeypatch, capsys) -> None:
    account = Account("https://host/dav/cal/you/", "u", "p")
    deleted: list[str] = []

    def reply(method, path, body):
        if method == "DELETE":
            deleted.append(path)
            return (204, b"")
        return (207, HOME)

    talking(account, reply)
    code = run(calendars_tool, ["https://host/dav/cal/you/", "-u", "u", "--delete", "--confirm"],
               monkeypatch, account, answer=["q"])
    assert code == 0
    assert deleted == []
    assert "Stopped where you asked" in capsys.readouterr().out


def test_yes_deletes_the_calendars_without_asking(monkeypatch, capsys) -> None:
    account = Account("https://host/dav/cal/you/", "u", "p")
    deleted: list[str] = []

    def reply(method, path, body):
        if method == "DELETE":
            deleted.append(path)
            return (204, b"")
        return (207, HOME)

    talking(account, reply)
    code = run(calendars_tool,
               ["https://host/dav/cal/you/", "-u", "u", "--keep", "ticket 7067",
                "--delete", "--yes"],
               monkeypatch, account, answer="no")
    assert code == 0
    assert deleted == ["/dav/cal/you/6e37/"]


def test_confirming_shows_the_address_before_making_it(monkeypatch, capsys) -> None:
    """The address is worked out from the name, and is what you subscribe to later."""
    account = Maker("https://host/dav/cal/you/", "u", "p")
    calls = talking(
        account, lambda method, *_: (207, ONE_CALENDAR) if method == "PROPFIND" else (201, b"")
    )
    code = run_make(["https://host/dav/cal/you/", "ticket 7067", "-u", "u", "--confirm"],
                    monkeypatch, account, answer="y")
    assert code == 0
    assert ("MKCALENDAR", "/dav/cal/you/ticket-7067/") in calls
    assert "/dav/cal/you/ticket-7067/" in capsys.readouterr().out


def test_declining_makes_nothing(monkeypatch, capsys) -> None:
    account = Maker("https://host/dav/cal/you/", "u", "p")
    calls = talking(
        account, lambda method, *_: (207, ONE_CALENDAR) if method == "PROPFIND" else (201, b"")
    )
    code = run_make(["https://host/dav/cal/you/", "ticket 7067", "-u", "u", "--confirm"],
                    monkeypatch, account, answer="n")
    assert code == 1, "the calendar you asked for does not exist, so nothing may follow this"
    assert not [method for method, _ in calls if method == "MKCALENDAR"]
    assert "Nothing made" in capsys.readouterr().out
