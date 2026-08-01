#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Delete the test calendars from an account, keeping the default one.

Reproducing a calendar bug means making a calendar, filling it with someone
else's scrubbed data, and throwing it away afterwards. The throwing away is the
part that gets skipped, so a test account collects calendars nobody can name any
more. Deleting a whole calendar is one request, where emptying one can be
thousands, so this is the cheap way to clear an account out.

    uv run caldav_delete_calendars.py HOME -u you@example.com
    uv run caldav_delete_calendars.py HOME -u you@example.com --delete

HOME is the address of the account's calendars, which is the calendar Location
from Thunderbird's Properties dialog with the last part taken off:

    https://mail.example.com/dav/cal/you@example.com/some-calendar/  <- one calendar
    https://mail.example.com/dav/cal/you@example.com/                <- HOME

It reports and changes nothing until you add --delete.

**The default calendar is never deleted**, and neither is anything you name with
--keep. Servers refuse to delete the default anyway, so this asks first rather
than making you read an error.

This deletes calendars, not entries. Everything in them goes too, and none of it
can be recovered from here. Use caldav_delete_events.py to empty a calendar you
want to keep.
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from getpass import getpass
from os import environ
from urllib.parse import urlsplit

from anonymize_ics import _count
from caldav_delete_events import CALDAV, DAV, Calendar

# What is in the account: every child collection, what kind it is, and what it
# is called. The kind matters -- an account's calendar home also holds the
# scheduling inbox and outbox, which are collections but are not calendars and
# must not be deleted.
LISTING = """<?xml version="1.0" encoding="utf-8"?>
<D:propfind xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:prop>
    <D:resourcetype/>
    <D:displayname/>
    <D:current-user-principal/>
    <C:schedule-default-calendar-URL/>
  </D:prop>
</D:propfind>
"""

# Which calendar the account treats as its default. Servers put this in
# different places, so it gets asked for wherever it might be.
DEFAULT = """<?xml version="1.0" encoding="utf-8"?>
<D:propfind xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:prop>
    <C:schedule-default-calendar-URL/>
  </D:prop>
</D:propfind>
"""


def _path_of(href: str) -> str:
    """A href as a bare path, however the server chose to write it."""
    return (urlsplit(href).path or href).rstrip("/")


class Account(Calendar):
    """An account's calendars, reached over the connection Calendar opens."""

    def calendars(self) -> tuple[list[tuple[str, str]], str | None]:
        """Every calendar as its address and name, and which one is default."""
        root, _ = self._multistatus("PROPFIND", LISTING, "1")

        default = None
        found = []
        principal = None
        for response in root.findall(f"{{{DAV}}}response"):
            href = (response.findtext(f"{{{DAV}}}href") or "").strip()
            kind = response.find(f".//{{{DAV}}}resourcetype")
            if principal is None:
                principal = response.findtext(
                    f".//{{{DAV}}}current-user-principal/{{{DAV}}}href"
                )
            if default is None:
                default = response.findtext(
                    f".//{{{CALDAV}}}schedule-default-calendar-URL/{{{DAV}}}href"
                )
            if not href or kind is None:
                continue
            # A calendar, and specifically not the scheduling inbox or outbox,
            # which are collections in the same place and would break the
            # account if they went.
            if kind.find(f"{{{CALDAV}}}calendar") is None:
                continue
            if kind.find(f"{{{CALDAV}}}schedule-inbox") is not None:
                continue
            if kind.find(f"{{{CALDAV}}}schedule-outbox") is not None:
                continue
            name = (response.findtext(f".//{{{DAV}}}displayname") or "").strip()
            found.append((href, name or "(unnamed)"))

        if default is None and principal:
            default = self._default_from(principal)
        return found, _path_of(default) if default else None

    def _default_from(self, principal: str) -> str | None:
        """Ask the account's principal which calendar is the default one."""
        here = self.path
        try:
            self.path = _path_of(principal) + "/"
            root, _ = self._multistatus("PROPFIND", DEFAULT, "0")
        except SystemExit:
            return None
        finally:
            self.path = here
        return root.findtext(f".//{{{CALDAV}}}schedule-default-calendar-URL/{{{DAV}}}href")

    def delete_calendar(self, href: str) -> tuple[bool, str]:
        """Delete one whole calendar. Returns whether it went, and why not."""
        status, body = self.request("DELETE", urlsplit(href).path or href)
        if status in (200, 202, 204, 404):
            return True, ""
        # The server saying this is the most reliable way to learn which
        # calendar is the default, whatever it did or did not advertise.
        if b"default-calendar-needed" in body:
            return False, "it is the account's default calendar"
        if status == 403:
            return False, "the server would not allow it"
        return False, f"HTTP {status}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("home", help="the address of the account's calendars")
    parser.add_argument("-u", "--user", required=True, help="the username to sign in with")
    parser.add_argument(
        "--keep",
        action="append",
        default=[],
        metavar="NAME",
        help="a calendar to leave alone, by name; may be given more than once",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="actually delete; without this it only reports what it would delete",
    )
    args = parser.parse_args(argv)

    # Prompting keeps the password out of your shell history.
    password = environ.get("CALDAV_PASSWORD") or getpass(f"App password for {args.user}: ")

    try:
        account = Account(args.home, args.user, password)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1

    try:
        calendars, default = account.calendars()
        if not calendars:
            print(
                f"No calendars under {account.path}.\n"
                "That address is probably one calendar rather than the account's calendars --\n"
                "take the last part off it and try again."
            )
            return 1

        keep = {name.casefold() for name in args.keep}
        doomed = []
        print(f"{_count(len(calendars), 'calendar')} under {account.path}:\n")
        for href, name in calendars:
            path = _path_of(href)
            if default and path == default:
                why = "kept, it is the default"
            elif name.casefold() in keep:
                why = "kept, you asked"
            # Guessing from an address is normally a bad idea, but the only cost
            # of guessing wrong here is keeping a calendar you meant to delete,
            # and the server refusing remains the real protection.
            elif not default and path.rsplit("/", 1)[-1].casefold() == "default":
                why = "kept, its address says default and the server would not say"
            else:
                why = "would be deleted"
                doomed.append((href, name))
            print(f"  {name}")
            print(f"    {path}  --  {why}")

        if not default:
            print(
                "\nThe server did not say which calendar is its default, so that was worked out\n"
                "from the addresses. Any calendar above that turns out to be the default anyway\n"
                "will refuse and be reported, not deleted."
            )

        if not doomed:
            print("\nNothing to do.")
            return 0

        if not args.delete:
            print(f"\n{_count(len(doomed), 'calendar')} would be deleted."
                  " This was a dry run; add --delete to go ahead.")
            return 0

        print(f"\nThis deletes {_count(len(doomed), 'calendar')} on {account.host},"
              " contents and all.")
        print("It cannot be undone from here.")
        try:
            if input(f"Type {len(doomed)} to confirm: ").strip() != str(len(doomed)):
                print("Nothing deleted.")
                return 1
        except EOFError:
            print("\nNothing deleted.")
            return 1

        print()
        deleted, failures = 0, []
        for href, name in doomed:
            gone, why = account.delete_calendar(href)
            if gone:
                deleted += 1
                print(f"  deleted  {name}")
            else:
                failures.append(f"  {name}: {why}")
                print(f"  kept     {name} -- {why}")

        print(f"\nDeleted {_count(deleted, 'calendar')}.")
        if failures:
            print(f"{_count(len(failures), 'calendar')} could not be deleted:")
            print("\n".join(failures))
            return 1
        print("Now unsubscribe from them in Thunderbird, or it will keep asking for them.")
        return 0
    finally:
        account.close()


if __name__ == "__main__":
    sys.exit(main())
