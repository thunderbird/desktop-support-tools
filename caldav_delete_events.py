#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Take a scrubbed calendar back off a CalDAV server.

Importing a scrubbed calendar into a real account is how you reproduce a
calendar bug, and afterwards you are left with hundreds of made-up entries in a
calendar you actually use. Deleting the whole calendar is the easy way out, but
servers refuse that for the default calendar, and it would take your own
entries with it anyway.

So this deletes only what the import added, and there are three ways to say
which entries those were:

    marks   what the scrubber wrote is recognisable -- "Anonymized Data" as a
            title, person1@example.com as an attendee. Needs no file at all.
            Start here.

    ids     the identifiers in the scrubbed file. Exact, but only if you still
            have the very file you imported: the scrubber invents a fresh
            identifier every run, so a second scrub of the same calendar has
            nothing in common with the first.

    dates   the times in the *original*, unscrubbed calendar. Scrubbing leaves
            every date, repeat rule and recurrence exactly as it was, so these
            still match after it. Use this when the ticket's calendar is all
            you kept.

    uv run caldav_delete_events.py URL -u you@example.com
    uv run caldav_delete_events.py URL -u you@example.com --ids scrubbed.ics
    uv run caldav_delete_events.py URL -u you@example.com --dates Calendar.ics

It reports and changes nothing until you add --delete. Run two of the three
first and check they agree on the count before deleting anything.

Matching either takes an entry or leaves it, and sometimes you want to look at
the ones you do not recognise. --delete --confirm asks about each entry -- what
it says and when it is -- and takes yes, no, all the rest, or stop here. "All
the rest" is the useful one: look at the first few, satisfy yourself the right
entries are being picked, and stop being asked.

-u can be left off if CALDAV_USER is set, as CALDAV_PASSWORD already works for
the password.

On a test account none of that care is worth anything, because there is nothing
in the calendar worth keeping. Empty it instead:

    uv run caldav_delete_events.py URL -u you@example.com --everything

That is the right answer whenever a calendar has accumulated several rounds of
testing, hand-made entries among them: matching can only find entries it knows
how to recognise, and by then nobody remembers what made them all. Never point
it at a calendar you use.

URL is the calendar's address: in Thunderbird, right-click the calendar, choose
Properties, and copy the Location field.
"""

from __future__ import annotations

import argparse
import base64
import http.client
import sys
import time
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit
from xml.sax.saxutils import escape

from caldav_asking import (
    NO,
    QUIT,
    Asking,
    Refused,
    add_confirmation,
    add_credentials,
    ready,
)
from anonymize_ics import (
    ALREADY_A_PSEUDONYM,
    BINARY_PLACEHOLDER,
    CALENDAR_NAME_PLACEHOLDER,
    CATEGORY_PLACEHOLDER,
    COORDINATES_PLACEHOLDER,
    PERSON_FIELDS,
    SCRUBBED,
    TEXT_PLACEHOLDER,
    URI_PLACEHOLDER,
    _unfold,
)

DAV = "DAV:"
CALDAV = "urn:ietf:params:xml:ns:caldav"

# The entries a calendar file holds. Anything else -- the timezone definitions,
# the calendar's own headers -- is not something that was imported as an entry.
ENTRY_TYPES = frozenset({"VEVENT", "VTODO", "VJOURNAL"})

# What "dates" matches on. Every one of these is copied through by the scrubber
# untouched, which is the whole reason this works: the original calendar and the
# scrubbed one that came from it agree on all of them exactly.
#
# DTSTAMP, CREATED and LAST-MODIFIED are deliberately absent even though they
# also survive scrubbing. A server is allowed to rewrite them when it stores an
# entry, and one that does would leave nothing matching at all.
TIME_FIELDS = frozenset({
    "DTSTART",
    "DTEND",
    "DURATION",
    "RRULE",
    "RDATE",
    "EXDATE",
    "RECURRENCE-ID",
})

# The values the scrubber leaves behind. Seeing one in an entry means the
# scrubber wrote it, because these are not things a real calendar contains.
MARKS = frozenset({
    TEXT_PLACEHOLDER,
    CATEGORY_PLACEHOLDER,
    CALENDAR_NAME_PLACEHOLDER,
    URI_PLACEHOLDER,
    BINARY_PLACEHOLDER,
    COORDINATES_PLACEHOLDER,
})

# Asks only what is in the calendar, not what each entry says. A server may cap
# how much it will hand back in one reply -- Stalwart stops at 2000 -- and a
# capped reply looks exactly like a small calendar, so ask for the cheap list
# first and fetch the entries themselves against it.
LISTING = """<?xml version="1.0" encoding="utf-8"?>
<D:propfind xmlns:D="DAV:">
  <D:prop>
    <D:getetag/>
    <D:resourcetype/>
  </D:prop>
</D:propfind>
"""

# Asks for named entries, so the number of them is ours to choose rather than
# the server's. The version tag is what makes the deletion safe: handing it back
# means the server refuses if the entry changed in the meantime.
FETCH_HEAD = """<?xml version="1.0" encoding="utf-8"?>
<C:calendar-multiget xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:prop>
    <D:getetag/>
    <C:calendar-data/>
  </D:prop>
"""
FETCH_TAIL = "</C:calendar-multiget>\n"

# Asks for the list a page at a time. A plain listing has no way to say "now the
# next thousand", so a server that will not hand over a big calendar in one
# reply leaves this as the only way to see all of it. Not every server offers
# it, hence the fallback.
SYNC = """<?xml version="1.0" encoding="utf-8"?>
<D:sync-collection xmlns:D="DAV:">
  <D:sync-token>{token}</D:sync-token>
  <D:sync-level>1</D:sync-level>
  <D:limit><D:nresults>{page}</D:nresults></D:limit>
  <D:prop><D:getetag/></D:prop>
</D:sync-collection>
"""

# How many entries to ask for at once. Small enough to stay under any server's
# cap, large enough that a calendar of a few thousand is a handful of requests.
BATCH = 200

# How many to ask a paged listing for at a time. Generous on purpose: a server
# that caps below this hands back its own maximum, and a bigger page means fewer
# passes on the servers that make us work through a calendar in passes.
PAGE = 2000


def _name_of(line: str) -> str:
    """The property name of one logical line, without its parameters."""
    return line.partition(":")[0].split(";")[0].strip().upper()


def _value_of(line: str) -> str:
    return line.partition(":")[2].strip()


def entries_in(text: str) -> list[tuple[str, list[str]]]:
    """The entries in a calendar, each as its type and its own lines.

    Nested parts are left out. An alarm carries a DURATION of its own and that
    duration is the alarm's, not the meeting's; folding it in would make two
    identical meetings with different reminders look like different meetings.
    """
    entries: list[tuple[str, list[str]]] = []
    depth = 0
    inside: int | None = None
    for line in _unfold(text):
        name = _name_of(line)
        if name == "BEGIN":
            depth += 1
            if inside is None and _value_of(line).upper() in ENTRY_TYPES:
                inside = depth
                entries.append((_value_of(line).upper(), []))
            continue
        if name == "END":
            if inside is not None and depth == inside:
                inside = None
            depth = max(0, depth - 1)
            continue
        if inside is not None and depth == inside:
            entries[-1][1].append(line)
    return entries


def ids_in(text: str) -> set[str]:
    """Every identifier in a calendar.

    Unfolds first, because a long value is split across lines and reading the
    file a physical line at a time would truncate it -- the same trap the
    scrubber hit.
    """
    return {
        _value_of(line)
        for _, lines in entries_in(text)
        for line in lines
        if _name_of(line) == "UID" and _value_of(line)
    }


def times_in(text: str) -> set[str]:
    """A summary of each entry's times, as something comparable.

    Two calendars that are the same calendar before and after scrubbing produce
    the same set of these; nothing else does, short of a genuine coincidence.
    """
    summaries = set()
    for kind, lines in entries_in(text):
        parts = sorted(line.strip() for line in lines if _name_of(line) in TIME_FIELDS)
        if parts:
            summaries.add("\x1f".join([kind, *parts]))
    return summaries


def _plain(value: str) -> str:
    """An iCalendar text value as text: the escapes undone, and cut to one line."""
    for escaped, plain in (("\\n", " "), ("\\N", " "), ("\\,", ","), ("\\;", ";"), ("\\\\", "\\")):
        value = value.replace(escaped, plain)
    value = " ".join(value.split())
    return value if len(value) <= 60 else value[:57] + "..."


def _readable(stamp: str) -> str:
    """A calendar time as something you can take in at a glance."""
    digits = stamp.rstrip("Z")
    if len(digits) == 8 and digits.isdigit():
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    if len(digits) == 15 and digits[8] == "T":
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]} {digits[9:11]}:{digits[11:13]}"
    return stamp  # a form we do not recognise is still better shown than hidden


def described(data: str) -> str:
    """One line saying what an entry is, for when you are being asked about it.

    What it says and when it happens, because those are what you recognise. The
    address is not: server-generated names say nothing about which entry it is.
    """
    for _, lines in entries_in(data):
        title = when = ""
        for line in lines:
            name = _name_of(line)
            if name == "SUMMARY" and not title:
                title = _plain(_value_of(line))
            elif name == "DTSTART" and not when:
                when = _readable(_value_of(line))
        return f"{title or '(no title)'}  --  {when or 'no start time'}"
    return "(nothing readable in it)"


def looks_scrubbed(text: str) -> bool:
    """Whether the scrubber's handiwork is visible in this entry."""
    for _, lines in entries_in(text):
        for line in lines:
            name = _name_of(line)
            value = _value_of(line)
            if name in SCRUBBED and value in MARKS:
                return True
            if name in PERSON_FIELDS and ALREADY_A_PSEUDONYM.search(value):
                return True
    return False


class Calendar:
    """One calendar on a CalDAV server, over a connection kept open."""

    def __init__(
        self,
        url: str,
        user: str,
        password: str,
        timeout: float = 60,
        page: int = PAGE,
        batch: int = BATCH,
    ):
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            raise ValueError(f"{url} is not an http or https address")
        if not parts.path:
            raise ValueError(f"{url} has no path, so it cannot be a calendar")
        self.secure = parts.scheme == "https"
        self.host = parts.hostname or ""
        self.port = parts.port
        self.timeout = timeout
        self.page = page
        self.batch = batch
        # A calendar is a collection, so its address ends in a slash. Servers
        # differ on whether they forgive a missing one; none mind an extra.
        self.path = parts.path if parts.path.endswith("/") else parts.path + "/"
        credentials = f"{user}:{password}".encode()
        self.authorization = "Basic " + base64.b64encode(credentials).decode("ascii")
        self.connection: http.client.HTTPConnection | None = None

    def _connect(self) -> http.client.HTTPConnection:
        if self.connection is None:
            opener = http.client.HTTPSConnection if self.secure else http.client.HTTPConnection
            self.connection = opener(self.host, self.port, timeout=self.timeout)
        return self.connection

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def request(
        self, method: str, path: str, body: str | None = None, headers: dict | None = None
    ) -> tuple[int, bytes]:
        """Make one request, reconnecting once if the server dropped us.

        Hundreds of deletions go down a single connection and servers close idle
        ones, so a dropped connection is routine rather than a failure.
        """
        head = {"Authorization": self.authorization, "User-Agent": "desktop-support-tools"}
        if body is not None:
            head["Content-Type"] = "application/xml; charset=utf-8"
        head.update(headers or {})
        payload = body.encode("utf-8") if body is not None else None

        for attempt in (1, 2):
            try:
                connection = self._connect()
                connection.request(method, path, body=payload, headers=head)
                response = connection.getresponse()
                return response.status, response.read()
            except (http.client.HTTPException, OSError):
                self.close()
                if attempt == 2:
                    raise
        raise AssertionError("unreachable")

    def _multistatus(self, method: str, body: str, depth: str) -> tuple[ET.Element, bool]:
        """Make a request that returns a list, and say whether it was cut short.

        A server that will not hand back everything says so rather than quietly
        stopping, and taking a short reply for a short calendar is how you end
        up deleting half of what you meant to.
        """
        status, raw = self.request(method, self.path, body, {"Depth": depth})
        if status == 401:
            raise SystemExit(
                "The server would not accept that username and password.\n"
                "CalDAV here needs an app password; an OAuth2 account password will not do.\n"
                "Some servers also want the bare username rather than the full address."
            )
        if status == 404:
            raise SystemExit(f"There is no calendar at {self.path} -- check the Location field.")
        if status != 207:
            raise SystemExit(f"Asking the server about the calendar failed: HTTP {status}")
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as error:
            raise SystemExit(f"The server's reply was not readable XML: {error}") from None

        cut_short = root.find(f".//{{{DAV}}}number-of-matches-within-limits") is not None
        return root, cut_short

    def _entry_addresses(self, root: ET.Element) -> list[tuple[str, str]]:
        """Each entry's address and version tag, from a listing."""
        found = []
        for response in root.findall(f"{{{DAV}}}response"):
            href = (response.findtext(f"{{{DAV}}}href") or "").strip()
            if not href or href.rstrip("/") == self.path.rstrip("/"):
                continue  # the calendar itself
            if response.find(f".//{{{DAV}}}resourcetype/{{{DAV}}}collection") is not None:
                continue  # something nested, not an entry
            # A paged listing also reports what has gone away since last time.
            # On a first look there is no last time, so anything not present now
            # is not something to go and delete.
            if " 404 " in (response.findtext(f"{{{DAV}}}status") or " 200 "):
                continue
            found.append((href, (response.findtext(f".//{{{DAV}}}getetag") or "").strip()))
        return found

    def _paged_addresses(self) -> tuple[list[tuple[str, str]], bool] | None:
        """Page through the calendar, or None if the server will not do that.

        Returns the addresses and whether more are expected. This is a
        synchronisation protocol borrowed as a paging one, and the two are not
        the same thing: the token means "you are up to date as of here", so a
        server that truncates a page and then treats the token as caught-up
        answers the next request with nothing at all. Silence is therefore not
        evidence of having seen the whole calendar -- a page that came back
        exactly at the limit is evidence of the opposite.
        """
        token = ""
        found: list[tuple[str, str]] = []
        seen: set[str] = set()
        was_full = False
        while True:
            body = SYNC.format(token=escape(token), page=self.page)
            try:
                root, cut_short = self._multistatus("REPORT", body, "1")
            except SystemExit:
                return None  # no paged listing here; the caller falls back
            listed = self._entry_addresses(root)
            fresh = [entry for entry in listed if entry[0] not in seen]
            seen.update(href for href, _ in fresh)
            found.extend(fresh)
            if listed:
                was_full = len(listed) >= self.page
            print(f"\rListed {len(found)} entries...", end="", file=sys.stderr, flush=True)
            following = (root.findtext(f"{{{DAV}}}sync-token") or "").strip()
            # No new token, an unchanged one, or an empty page all mean the
            # server has nothing further to say, and looping on any of them
            # would never end.
            if not following or following == token or not fresh:
                print(file=sys.stderr)
                return found, was_full or cut_short
            token = following

    def addresses(self) -> tuple[list[tuple[str, str]], bool]:
        """Every entry's address, and whether more of them are expected."""
        paged = self._paged_addresses()
        if paged is not None:
            return paged
        root, cut_short = self._multistatus("PROPFIND", LISTING, "1")
        return self._entry_addresses(root), cut_short

    def _fetch(self, batch: list[tuple[str, str]]) -> list[tuple[str, str, str]]:
        """One request for named entries, as address, version tag and text."""
        asked = "".join(f"  <D:href>{escape(href)}</D:href>\n" for href, _ in batch)
        root, _ = self._multistatus("REPORT", FETCH_HEAD + asked + FETCH_TAIL, "0")
        found = []
        for response in root.findall(f"{{{DAV}}}response"):
            href = (response.findtext(f"{{{DAV}}}href") or "").strip()
            etag = (response.findtext(f".//{{{DAV}}}getetag") or "").strip()
            data = response.findtext(f".//{{{CALDAV}}}calendar-data") or ""
            if href and data:
                found.append((href, etag, data))
        return found

    def contents(self, addresses: list[tuple[str, str]]) -> list[tuple[str, str, str]]:
        """Fetch named entries, as address, version tag and text, in batches."""
        found: list[tuple[str, str, str]] = []
        for start in range(0, len(addresses), self.batch):
            found.extend(self._fetch(addresses[start : start + self.batch]))
            print(
                f"\rRead {len(found)} of {len(addresses)} entries...",
                end="",
                file=sys.stderr,
                flush=True,
            )
        print(file=sys.stderr)
        return found

    def content_of(self, href: str, etag: str) -> str:
        """One entry's text, and quietly, because it is wanted for a question.

        Under --everything nothing is read at all, deliberately, so a per-entry
        question has nothing to show you unless it asks for that entry itself.
        One request per question is nothing next to the time you take to answer
        it, and "all the rest" goes straight back to reading nothing.
        """
        found = self._fetch([(href, etag)])
        return found[0][2] if found else ""

    def delete(self, href: str, etag: str) -> tuple[bool, str]:
        """Delete one entry. Returns whether it is now gone, and why not."""
        # href arrives from the server already escaped; escaping it again would
        # ask for a different, non-existent entry.
        path = urlsplit(href).path or href
        headers = {"If-Match": etag} if etag else {}
        status, _ = self.request("DELETE", path, headers=headers)
        if status in (200, 202, 204):
            return True, ""
        if status == 404:
            return True, ""  # already gone, which is the outcome we wanted
        if status == 412:
            return False, "changed on the server since we listed it"
        if status == 423:
            return False, "locked by something else"
        return False, f"HTTP {status}"


def _read(path: str) -> str:
    with open(path, encoding="utf-8", newline="") as handle:
        return handle.read()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog="With neither --ids nor --dates, it matches what the scrubber left behind.",
    )
    parser.add_argument("url", help="the calendar's address, from Thunderbird's Properties dialog")
    add_credentials(parser)
    add_confirmation(
        parser,
        asks="ask about each entry: yes, no, all the rest, or stop here",
        skips="do not ask to confirm at all; this does not imply --delete",
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--ids",
        metavar="SCRUBBED.ICS",
        help="match the identifiers in the scrubbed file you imported",
    )
    source.add_argument(
        "--dates",
        metavar="ORIGINAL.ICS",
        help="match the times in the original calendar, scrubbed or not",
    )
    parser.add_argument(
        "--everything",
        action="store_true",
        help="delete every entry in the calendar, for a test account with nothing worth keeping",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="actually delete; without this it only reports what it would delete",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help="wait between deletions, if the server is rate-limiting you",
    )
    parser.add_argument(
        "--page",
        type=int,
        default=PAGE,
        metavar="N",
        help=(
            f"how many entries to list at a time (default {PAGE}). Set this below the"
            " number reported to find out whether the server is capping the list"
        ),
    )
    parser.add_argument(
        "--batch",
        type=int,
        default=BATCH,
        metavar="N",
        help=f"how many entries to read at a time (default {BATCH})",
    )
    args = parser.parse_args(argv)

    wanted_ids: set[str] = set()
    wanted_times: set[str] = set()
    source_path = args.ids or args.dates
    if source_path:
        try:
            text = _read(source_path)
        except OSError as error:
            print(f"Could not read that calendar: {error}", file=sys.stderr)
            return 1
        except UnicodeDecodeError:
            print("That file is not a calendar saved as UTF-8 text.", file=sys.stderr)
            return 1
        wanted_ids = ids_in(text) if args.ids else set()
        wanted_times = times_in(text) if args.dates else set()
        if not wanted_ids and not wanted_times:
            print(f"{source_path} has nothing in it to match on.", file=sys.stderr)
            return 1

    try:
        user, password = ready(args)
    except Refused as why:
        print(str(why), file=sys.stderr)
        return 1

    try:
        calendar = Calendar(args.url, user, password, page=args.page, batch=args.batch)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1

    try:
        total_deleted = 0
        kept = 0
        failures: list[str] = []
        first = True
        stopped = False
        asking = Asking(args.confirm)

        # A server that will only show part of the calendar at a time is worked
        # through in passes: delete what this pass can see, look again, repeat.
        # Deleting is what makes the next pass show something new, so this only
        # converges while entries are actually going away.
        while True:
            addresses, partial = calendar.addresses()

            # Emptying a calendar needs no reason to delete any particular
            # entry, so reading them all first would be a download of the whole
            # calendar, every pass, to learn nothing that changes the outcome.
            # The listing already carries the version tags the deletion needs.
            by_marks, by_ids, by_times = [], [], []
            if args.everything:
                contents = []
                matched = list(addresses)
            else:
                contents = calendar.contents(addresses)
                # Every way of matching is worked out every time, whichever one
                # is driving the deletion. Two of them agreeing is the only
                # evidence there is that either is right.
                for href, etag, data in contents:
                    if looks_scrubbed(data):
                        by_marks.append((href, etag))
                    if wanted_ids and ids_in(data) & wanted_ids:
                        by_ids.append((href, etag))
                    if wanted_times and times_in(data) & wanted_times:
                        by_times.append((href, etag))
                matched = by_ids if args.ids else by_times if args.dates else by_marks

            # What you have already said no to is not offered again. A pass lists
            # what the last one did not delete, so without this you would be asked
            # the same question every pass until you gave in or gave up.
            if asking.declined:
                matched = [pair for pair in matched if pair[0] not in asking.declined]
            known = {href: data for href, _, data in contents}

            if first:
                held = f"at least {len(addresses)}" if partial else f"{len(addresses)}"
                print(f"The calendar holds {held} entries.")
                if partial:
                    print(
                        "The server will not list more than that at a time and offers no way to\n"
                        "ask for the next page, so this works through it a pass at a time and the\n"
                        "counts below are for this pass only."
                    )

                counts = {}
                if not args.everything:
                    if len(contents) != len(addresses):
                        missing = len(addresses) - len(contents)
                        print(f"{missing} the server would not hand over. Those are left alone.")
                    counts["placeholders"] = len(by_marks)
                    print(f"  {len(by_marks):5} still carry the scrubber's placeholders")
                    if wanted_ids:
                        counts["identifiers"] = len(by_ids)
                        print(f"  {len(by_ids):5} carry an identifier from that file,"
                              f" which describes {len(wanted_ids)}")
                    if wanted_times:
                        counts["times"] = len(by_times)
                        print(f"  {len(by_times):5} happen at the same times as one,"
                              f" which describes {len(wanted_times)}")

                if len(set(counts.values())) > 1 and not args.everything:
                    print(
                        "\nThose disagree, so at least one of them is missing entries. Trust the\n"
                        "placeholder count: it reads what the scrubber wrote into the entry, and\n"
                        "that survives Thunderbird rewriting the entry on its way to the server.\n"
                        "Matching on times compares the text of each line, which rewriting changes."
                    )

                if not matched:
                    print("\nNothing to do.")
                    return 0

                if args.everything:
                    print("\nEverything: all of them would be deleted, nothing left alone.")
                else:
                    how = "identifiers" if args.ids else "times" if args.dates else "placeholders"
                    print(f"\nMatching on {how}: {len(matched)} would be deleted, "
                          f"{len(addresses) - len(matched)} left alone.")
                if not args.delete:
                    print("\nThis was a dry run. Add --delete to go ahead.")
                    return 0

                # Emptying a calendar is worth one more deliberate step than
                # deleting a recognised part of it, and typing the number means
                # having read it. --confirm replaces that step rather than adding
                # to it: you are about to be asked about every entry in turn, so
                # asking about all of them first is a question already answered.
                if args.everything and not args.yes and not args.confirm:
                    print(f"\nThis empties {calendar.path} on {calendar.host} completely,")
                    print("including anything you made by hand. It cannot be undone from here.")
                    try:
                        if input(f"Type {len(matched)} to confirm: ").strip() != str(len(matched)):
                            print("Nothing deleted.")
                            return 1
                    except EOFError:
                        print("\nNothing deleted.")
                        return 1
                first = False
            elif not matched:
                break

            print()
            deleted = 0
            counted = False
            for number, (href, etag) in enumerate(matched, start=1):
                asked = asking.on
                if asked:
                    data = known.get(href) or calendar.content_of(href, etag)
                    answer = asking.about(described(data) if data else href)
                    if answer == QUIT:
                        stopped = True
                        break
                    if answer == NO:
                        asking.declined.add(href)
                        kept += 1
                        continue

                gone, why = calendar.delete(href, etag)
                if gone:
                    deleted += 1
                else:
                    failures.append(f"  {href}: {why}")

                # A running count rewritten in place is the right report for
                # hundreds going by, and the wrong one next to a question: the
                # prompt you are answering would be overwritten as you type.
                if asked:
                    print(f"  {'deleted' if gone else 'kept -- ' + why}")
                else:
                    counted = True
                    print(
                        f"\rDeleted {total_deleted + deleted} entries...",
                        end="",
                        file=sys.stderr,
                        flush=True,
                    )
                    # You answering is the delay, so it only applies once nobody
                    # is being asked anything.
                    if args.delay and number < len(matched):
                        time.sleep(args.delay)
            if counted:
                print(file=sys.stderr)
            total_deleted += deleted

            # Nothing went away, so another pass would see the same entries and
            # fail on them again.
            if stopped or not partial or deleted == 0:
                break

        print(f"Deleted {total_deleted} entries.")
        if kept:
            print(f"{kept} left alone, because you said no.")
        if stopped:
            # Not a failure. You were asked, and stopping was one of the answers.
            print("Stopped where you asked; everything after that is untouched.")
        if failures:
            print(f"\n{len(failures)} could not be deleted:")
            print("\n".join(failures[:20]))
            if len(failures) > 20:
                print(f"  ... and {len(failures) - 20} more")
            print("Running this again will retry them.")
            return 1
        print("Now unsubscribe and re-add the calendar in Thunderbird, or its copy will linger.")
        return 0
    finally:
        calendar.close()


if __name__ == "__main__":
    sys.exit(main())
