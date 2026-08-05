#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Put a scrubbed calendar onto a CalDAV server, one entry at a time.

Reproducing a calendar bug means making a calendar, filling it with a scrubbed
copy of somebody's data, and throwing it away afterwards. Thunderbird's Import
fills it, and where the bug is Thunderbird's that is the right way to do it: it
is the code path the reporter went through. This is for the rounds after that
one -- the fifth import of the same file, or the ones where Thunderbird is the
thing you are trying to keep out of the way, because what you are watching is
what the server does with the file.

    uv run caldav_import_ics.py URL scrubbed.ics -u you@example.com
    uv run caldav_import_ics.py URL scrubbed.ics -u you@example.com --upload

It reports and sends nothing until you add --upload. URL is the calendar's
address: in Thunderbird, right-click the calendar, choose Properties, and copy
the Location field. Make a calendar to point it at with caldav_make_calendar.py,
and never point it at one you use.

-u can be left off if CALDAV_USER is set, as CALDAV_PASSWORD already works for
the password.

**A calendar that still identifies somebody is refused.** The check is
anonymize_ics.py's, so there is one definition of clean rather than two: scrub
the file first and send the scrubbed copy. --unscrubbed sends it anyway and only
to a server on this machine, because a real calendar may be reproduced against a
local server and never against production -- a DELETE afterwards does not reach
the backups, the logs, or any other client subscribed to that account.

**Nothing already in the calendar is overwritten.** One entry per request, sent
so that the server refuses rather than replaces if that entry is already there;
those are reported as left alone rather than as failures. So a run interrupted
half way through can simply be run again, which is also the answer to a server
that starts rate-limiting you at entry 400. --replace overwrites deliberately,
and asks you to type the count first.

Entries are sent one per request, the way Thunderbird's Import does it, because
that is what CalDAV offers: a calendar collection has no "here is the whole
file" request. What travels in one request is one entry, which is not the same
as one component:

- **A repeating entry and its changed occurrences share an identifier**, and go
  in one request. Splitting them would leave the changed occurrence pointing at
  a series the server has never heard of.
- **Timezone definitions are carried into every entry that names one**, since an
  entry that refers to a timezone the request does not define is one a server may
  refuse.
- **METHOD is dropped.** It says what a calendar was sent *for* -- PUBLISH,
  REQUEST -- and RFC 4791 does not allow it on a stored entry. Exporters write
  it anyway.
- **An entry with no identifier is left out and reported**, rather than given
  one. Something the file does not say is not something this invents.

Each entry's address is worked out from its identifier, so afterwards the same
file names what it imported:

    uv run caldav_delete_events.py URL --ids scrubbed.ics
"""

from __future__ import annotations

import argparse
import ipaddress
import re
import sys
import time
from hashlib import sha256
from typing import NamedTuple
from urllib.parse import quote, urlsplit

from anonymize_ics import _count, _fold, _unfold, audit
from caldav_asking import (
    NO,
    QUIT,
    Asking,
    Refused,
    add_confirmation,
    add_credentials,
    agreed,
    ready,
)
from caldav_delete_events import (
    CALDAV,
    DAV,
    ENTRY_TYPES,
    Calendar,
    _name_of,
    _read,
    _value_of,
    described,
)
from caldav_make_calendar import _because

# What the calendar's own header carries into each entry. VERSION and PRODID are
# the file's own, not ours: which program wrote a calendar is part of what a
# reproduction reproduces. METHOD is deliberately absent -- see the module
# docstring.
HEADER_FIELDS = ("VERSION", "PRODID", "CALSCALE")

# What to say when the file did not. A calendar without VERSION is one a server
# may reject outright, and the file being unusual is not a reason to send
# something that cannot be stored.
DEFAULT_VERSION = "VERSION:2.0"
DEFAULT_PRODID = "PRODID:-//Thunderbird//desktop-support-tools//EN"

# Which timezone an entry's times are in, as a parameter on the time itself. A
# parameter value may be quoted, and a quoted one may contain the characters that
# would otherwise end it.
TZID_PARAMETER = re.compile(r';TZID=(?:"([^"]*)"|([^";:]*))', re.IGNORECASE)

# How long an entry's address may be before it is replaced with a digest of the
# identifier rather than the identifier itself. Servers and filesystems both stop
# accepting long path segments somewhere, and they disagree about where.
SEGMENT_LIMIT = 120

# Asks what the calendar is called and whether it is a calendar at all. Sending
# entries to the account's calendar home rather than to one of its calendars is
# the mistake this catches, and the server answers 207 to both.
HERE = """<?xml version="1.0" encoding="utf-8"?>
<D:propfind xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:prop>
    <D:displayname/>
    <D:resourcetype/>
  </D:prop>
</D:propfind>
"""

# How many findings, or failures, to print before summarising the rest. A real
# calendar can have a finding per event, and a thousand of them scrolled past is
# the same as none.
SHOWN = 10


def _row(number: int, singular: str, plural: str) -> str:
    """One counted line of the report, with the numbers under each other."""
    return f"  {number:5} {singular if number == 1 else plural}"


class Entry(NamedTuple):
    """One entry as it will be sent: what it is, where it goes, and its request."""

    uid: str
    segment: str
    body: str
    components: int


def components_in(text: str) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """The calendar's own header lines, and each of its components.

    A component keeps everything nested inside it -- an event's alarms travel
    with the event -- which is the opposite of what entries_in() wants, and the
    reason this is a second reader rather than that one.
    """
    header: list[str] = []
    components: list[tuple[str, list[str]]] = []
    current: list[str] | None = None
    kind = ""
    depth = 0
    started = 0

    for line in _unfold(text):
        name = _name_of(line)
        if name == "BEGIN":
            depth += 1
            if current is None and depth == 2:
                kind = _value_of(line).upper()
                started = depth
                current = [line]
            elif current is not None:
                current.append(line)
            continue
        if name == "END":
            if current is not None:
                current.append(line)
                if depth == started:
                    components.append((kind, current))
                    current = None
            depth = max(0, depth - 1)
            continue
        if current is not None:
            current.append(line)
        elif depth == 1:
            header.append(line)

    return header, components


def _first(lines: list[str], field: str) -> str:
    for line in lines:
        if _name_of(line) == field:
            return _value_of(line)
    return ""


def _timezones_named(lines: list[str]) -> set[str]:
    """Every timezone the lines refer to, however the parameter was written."""
    named = set()
    for line in lines:
        for quoted, bare in TZID_PARAMETER.findall(line):
            found = (quoted or bare).strip()
            if found:
                named.add(found)
    return named


def address_for(uid: str) -> str:
    """The last part of one entry's address, worked out from its identifier.

    Naming it after the identifier is what makes a second run of the same file
    land on the same addresses, so re-running finds the entries already there
    instead of storing everything twice under fresh names.

    An identifier may be anything at all, though -- a long one, or one full of
    characters no path can hold -- so one that does not survive being written
    into an address is replaced by a digest of itself, which is still the same
    every time.
    """
    escaped = quote(uid, safe="")
    if not escaped or len(escaped) > SEGMENT_LIMIT:
        escaped = sha256(uid.encode("utf-8")).hexdigest()[:32]
    return escaped + ".ics"


def _body(header: list[str], timezones: list[list[str]], entry: list[list[str]]) -> str:
    """One request's worth of calendar: the wrapper, the timezones, the entry."""
    lines = ["BEGIN:VCALENDAR"]
    lines.extend(header)
    for block in timezones:
        lines.extend(block)
    for component in entry:
        lines.extend(component)
    lines.append("END:VCALENDAR")

    # Refolded rather than copied line for line: what was read is the logical
    # lines, and a value that was wrapped in the file is wrapped again here,
    # possibly in a different place. The value itself is unchanged, which is what
    # a server stores and what the cleanup tools match on.
    wrapped: list[str] = []
    for line in lines:
        wrapped.extend(_fold(line))
    return "\r\n".join(wrapped) + "\r\n"


def entries_to_send(text: str) -> tuple[list[Entry], dict]:
    """Split a calendar into one request per entry, and say what was left out.

    Grouping is by identifier, because that is what the server keys an entry on:
    a repeating entry and its changed occurrences are one entry stored in one
    place, and sending them separately would store the exceptions as entries of
    their own.
    """
    header, components = components_in(text)
    kept_header = [line for line in header if _name_of(line) in HEADER_FIELDS]
    if not any(_name_of(line) == "VERSION" for line in kept_header):
        kept_header.insert(0, DEFAULT_VERSION)
    if not any(_name_of(line) == "PRODID" for line in kept_header):
        kept_header.append(DEFAULT_PRODID)

    report: dict = {
        "components": len(components),
        "dropped_method": any(_name_of(line) == "METHOD" for line in header),
        "nameless": 0,
        "overrides": 0,
        "not_entries": {},
        "undefined_timezones": set(),
        "mixed": [],
    }

    timezones: dict[str, list[str]] = {}
    for kind, lines in components:
        if kind == "VTIMEZONE":
            found = _first(lines, "TZID")
            if found:
                timezones[found] = lines

    grouped: dict[str, list[list[str]]] = {}
    kinds: dict[str, set[str]] = {}
    for kind, lines in components:
        if kind == "VTIMEZONE":
            continue
        if kind not in ENTRY_TYPES:
            report["not_entries"][kind] = report["not_entries"].get(kind, 0) + 1
            continue
        uid = _first(lines, "UID")
        if not uid:
            report["nameless"] += 1
            continue
        if _first(lines, "RECURRENCE-ID"):
            report["overrides"] += 1
        grouped.setdefault(uid, []).append(lines)
        kinds.setdefault(uid, set()).add(kind)

    sending: list[Entry] = []
    carried_anywhere: set[str] = set()
    for uid, group in grouped.items():
        if len(kinds[uid]) > 1:
            report["mixed"].append(uid)
        # The series before its exceptions. A changed occurrence is a change to
        # something, and a reader that meets it first has nothing to change.
        ordered = sorted(group, key=lambda lines: bool(_first(lines, "RECURRENCE-ID")))
        named = _timezones_named([line for lines in ordered for line in lines])
        report["undefined_timezones"].update(named - set(timezones))
        carried_anywhere.update(named & set(timezones))
        carried = [timezones[name] for name in timezones if name in named]
        sending.append(
            Entry(uid, address_for(uid), _body(kept_header, carried, ordered), len(ordered))
        )

    report["undefined_timezones"] = sorted(report["undefined_timezones"])
    # What is carried, not what is defined. A definition nothing refers to is not
    # sent at all, and counting it as carried would overstate what went up.
    report["timezones"] = len(carried_anywhere)
    report["unused_timezones"] = sorted(set(timezones) - carried_anywhere)
    return sending, report


def on_this_machine(host: str) -> bool:
    """Whether that host is a server here rather than one out on the internet.

    The escape hatch for an unscrubbed calendar is worth exactly as much as this
    check is: a name that resolves anywhere else is not one an unscrubbed
    calendar may be sent to, whoever asks.
    """
    name = (host or "").strip("[]").lower().rstrip(".")
    if name == "localhost" or name.endswith((".localhost", ".local", ".internal")):
        return True
    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        return False
    return address.is_loopback or address.is_private


class Importer(Calendar):
    """A calendar you can store entries in, over the connection Calendar opens."""

    def target(self) -> tuple[str, bool]:
        """What the calendar is called, and whether the server calls it one."""
        root, _ = self._multistatus("PROPFIND", HERE, "0")
        name = (root.findtext(f".//{{{DAV}}}displayname") or "").strip()
        kind = root.find(f".//{{{DAV}}}resourcetype")
        return name, kind is not None and kind.find(f"{{{CALDAV}}}calendar") is not None

    def store(self, segment: str, body: str, *, replace: bool) -> tuple[bool, bool, str]:
        """Store one entry. Returns whether it is there, whether it already was, and why not."""
        headers = {"Content-Type": "text/calendar; charset=utf-8"}
        if not replace:
            # "Only if there is nothing here", so the server refuses instead of
            # replacing an entry that is already in the calendar.
            headers["If-None-Match"] = "*"
        status, body_back = self.request("PUT", self.path + segment, body, headers)

        if status in (200, 201, 204):
            return True, False, ""
        if status == 412:
            # Only ever ours: nothing here sends If-Match, so the precondition
            # that failed is the one asking the server not to overwrite.
            return False, True, "already there"
        if status == 401:
            return False, False, "the server would not accept that username and password"
        if status == 403:
            return False, False, "the server would not allow it" + _because(body_back)
        if status in (404, 409):
            return False, False, "there is nothing at that address to store an entry in"
        if status == 415:
            return False, False, "the server would not take it as a calendar entry"
        if status in (413, 507):
            return False, False, "too big for the account, or the account is out of space"
        if status == 429:
            return False, False, "the server is rate-limiting; try --delay"
        return False, False, f"HTTP {status}" + _because(body_back)


def _refuse_unscrubbed(findings: list[str], host: str, unscrubbed: bool) -> str | None:
    """Why this calendar is not going to that server, if it is not."""
    if not findings:
        return None

    listed = "\n".join(f"  {finding}" for finding in findings[:SHOWN])
    if len(findings) > SHOWN:
        listed += f"\n  ... and {_count(len(findings) - SHOWN, 'more finding')}"

    if not unscrubbed:
        return (
            f"This calendar still identifies people, so it is not going to a server:\n{listed}\n\n"
            "Scrub it first, and send the scrubbed copy:\n"
            "  uv run anonymize_ics.py CALENDAR.ics -o scrubbed.ics\n\n"
            "--unscrubbed sends it anyway, and only to a server on this machine."
        )
    if not on_this_machine(host):
        return (
            f"This calendar still identifies people:\n{listed}\n\n"
            f"--unscrubbed does not apply to {host}, which is not a server on this machine.\n"
            "Somebody else's calendar may be reproduced against a local server -- Stalwart in\n"
            "Docker answers the same requests -- and never against production, where a DELETE\n"
            "afterwards does not reach the backups, the logs, or the other clients subscribed\n"
            "to that account."
        )
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog="Nothing already in the calendar is overwritten, so an interrupted run"
        " can simply be run again.",
    )
    parser.add_argument("url", help="the calendar's address, from Thunderbird's Properties dialog")
    parser.add_argument("path", help="the scrubbed calendar file to send")
    add_credentials(parser)
    add_confirmation(
        parser,
        asks="ask about each entry: yes, no, all the rest, or stop here",
        skips="do not ask to confirm at all; this does not imply --upload",
    )
    parser.add_argument(
        "--upload",
        action="store_true",
        help="actually send it; without this it only reports what it would send",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="overwrite an entry already in the calendar, rather than leaving it alone",
    )
    parser.add_argument(
        "--unscrubbed",
        action="store_true",
        help="send a calendar that still identifies people; only to a server on this machine",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help="wait between entries, if the server is rate-limiting you",
    )
    args = parser.parse_args(argv)

    try:
        text = _read(args.path)
    except OSError as error:
        print(f"Could not read that calendar: {error}", file=sys.stderr)
        return 1
    except UnicodeDecodeError:
        print("That file is not a calendar saved as UTF-8 text.", file=sys.stderr)
        return 1

    # Before the password, deliberately, as with --confirm: being told that this
    # file is not going anywhere is better news before you have typed one.
    refusal = _refuse_unscrubbed(
        audit(text), urlsplit(args.url).hostname or "", args.unscrubbed
    )
    if refusal:
        print(refusal, file=sys.stderr)
        return 1

    sending, report = entries_to_send(text)
    if not sending:
        print(f"There is nothing in {args.path} to send.", file=sys.stderr)
        if report["nameless"]:
            print(
                f"{_count(report['nameless'], 'entry', 'entries')} in it have no identifier,"
                " and nothing here invents one.",
                file=sys.stderr,
            )
        return 1

    try:
        user, password = ready(args)
    except Refused as why:
        print(str(why), file=sys.stderr)
        return 1

    try:
        calendar = Importer(args.url, user, password)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1

    try:
        name, is_calendar = calendar.target()
        where = f"{name!r}" if name else "the calendar"
        going = "Sending" if args.upload else "Would send"
        print(f"{going} {_count(len(sending), 'entry', 'entries')} to {where} at")
        print(f"  {calendar.address()}")

        print(f"\n{args.path} holds {_count(report['components'], 'component')}:")
        print(_row(len(sending), "entry, one request", "entries, one request each"))
        if report["overrides"]:
            print(_row(
                report["overrides"],
                "changed occurrence, sent with its own series",
                "changed occurrences, sent with their own series",
            ))
        if report["timezones"]:
            print(_row(
                report["timezones"],
                "timezone definition, carried into what names it",
                "timezone definitions, carried into what names them",
            ))
        if report["dropped_method"]:
            print("        METHOD, which says what the file was sent for and is not stored")
        for kind, many in sorted(report["not_entries"].items()):
            print(_row(
                many,
                f"{kind}, which is not an entry and is not sent",
                f"{kind}, which are not entries and are not sent",
            ))
        if report["unused_timezones"]:
            print(_row(
                len(report["unused_timezones"]),
                "timezone definition nothing refers to, which is not sent",
                "timezone definitions nothing refers to, which are not sent",
            ))
        if report["nameless"]:
            print(_row(
                report["nameless"],
                "with no identifier, left out; nothing here invents one",
                "with no identifier, left out; nothing here invents them",
            ))
        if report["undefined_timezones"]:
            named = ", ".join(report["undefined_timezones"][:SHOWN])
            print(f"\nTimezones are named that this file does not define: {named}.")
            print("A server is entitled to refuse those entries, and some do.")
        if report["mixed"]:
            print(
                f"\n{_count(len(report['mixed']), 'identifier')} belong to more than one kind of"
                " entry at once. That is not something a calendar is allowed to hold, and the"
                " server may well refuse it."
            )

        if not is_calendar:
            print(
                "\nThe server answered for that address but does not call it a calendar. The most\n"
                "likely reason is that it is the account's calendar home rather than one of its\n"
                "calendars -- entries stored there may be accepted and then never appear in\n"
                "Thunderbird. Check the Location field in the calendar's Properties dialog."
            )

        if not args.upload:
            print("\nThis was a dry run. Add --upload to send it.")
            return 0

        # Asked here rather than with the warning above, because a dry run sends
        # nothing and has nothing to ask about.
        if not is_calendar and not args.yes and not agreed("\nSend to it anyway?"):
            print("Nothing sent.")
            return 1

        # Overwriting is the one thing here that can lose something, so it is the
        # one thing that asks. --confirm replaces the question rather than adding
        # to it: you are about to be asked about every entry in turn.
        if args.replace and not args.yes and not args.confirm:
            print(f"\nThis replaces any of {len(sending)} entries already in {calendar.path}")
            print("on their identifiers, and what they held now cannot be recovered from here.")
            try:
                if input(f"Type {len(sending)} to confirm: ").strip() != str(len(sending)):
                    print("Nothing sent.")
                    return 1
            except EOFError:
                print("\nNothing sent.")
                return 1

        print()
        asking = Asking(args.confirm, verb="Send")
        stored, already, stopped = 0, 0, False
        failures: list[str] = []
        counted = False
        for number, entry in enumerate(sending, start=1):
            asked = asking.on
            if asked:
                answer = asking.about(described(entry.body))
                if answer == QUIT:
                    stopped = True
                    break
                if answer == NO:
                    continue

            there, was_there, why = calendar.store(
                entry.segment, entry.body, replace=args.replace
            )
            if there:
                stored += 1
            elif was_there:
                already += 1
            else:
                failures.append(f"  {entry.segment}: {why}")

            if asked:
                print(f"  {'sent' if there else 'already there' if was_there else why}")
            else:
                counted = True
                print(
                    f"\rSent {_count(stored, 'entry', 'entries')}...",
                    end="",
                    file=sys.stderr,
                    flush=True,
                )
                # You answering is the delay, so it only applies once nobody is
                # being asked anything.
                if args.delay and number < len(sending):
                    time.sleep(args.delay)
        if counted:
            print(file=sys.stderr)

        print(f"Sent {_count(stored, 'entry', 'entries')}.")
        if already:
            print(f"{_count(already, 'entry', 'entries')} already in the calendar, left alone.")
            print("Their identifiers are what says so, whatever they hold now, and --replace")
            print("is what overwrites them.")
        if stopped:
            # Not a failure. You were asked, and stopping was one of the answers.
            print("Stopped where you asked; nothing after that was sent.")
        if failures:
            print(f"\n{_count(len(failures), 'entry', 'entries')} could not be sent:")
            print("\n".join(failures[:SHOWN]))
            if len(failures) > SHOWN:
                print(f"  ... and {len(failures) - SHOWN} more")
            print("Running this again will retry them and leave the rest alone.")
            return 1
        if stored:
            print("\nNow subscribe to the calendar in Thunderbird, or refresh it if you already")
            print("have. Afterwards, this takes the same entries back off the server:")
            print(f"  uv run caldav_delete_events.py {args.url} --ids {args.path}")
        return 0
    finally:
        calendar.close()


if __name__ == "__main__":
    sys.exit(main())
