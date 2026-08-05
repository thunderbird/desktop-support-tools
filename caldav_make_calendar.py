#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Make a calendar on a CalDAV server, which Thunderbird cannot do for you.

Reproducing a calendar bug means importing somebody's data into a calendar and
then throwing that calendar away, never into one you use. Thunderbird will not
make you one: its New Calendar dialog subscribes to calendars the server already
has, and nothing in it sends MKCALENDAR. So the making is done here, and the
throwing away by caldav_delete_calendars.py.

    uv run caldav_make_calendar.py HOME -u you@example.com "ticket 7067"

HOME is the address of the account's calendars, which is the calendar Location
from Thunderbird's Properties dialog with the last part taken off:

    https://mail.example.com/dav/cal/you@example.com/some-calendar/  <- one calendar
    https://mail.example.com/dav/cal/you@example.com/                <- HOME

The name is what Thunderbird shows in its list. The address is worked out from
it -- "ticket 7067" becomes .../ticket-7067/ -- unless you give --path. Name it
after the ticket: an address that says which bug it was for is the difference
between cleaning up later and guessing.

Nothing is overwritten. If the account already has a calendar at that address,
or one going by that name, this reports it and sends nothing. --confirm shows you
the address it worked out and asks before sending anything at all.

-u can be left off if CALDAV_USER is set, as CALDAV_PASSWORD already works for
the password.

Making a calendar does not put it in Thunderbird. Subscribe afterwards with New
Calendar -> On the Network, which lists what the account has.
"""

from __future__ import annotations

import argparse
import re
import sys
from xml.sax.saxutils import escape

from caldav_asking import Refused, add_confirmation, add_credentials, agreed, ready
from caldav_delete_calendars import Account, _path_of

# The body Thundermail's Stalwart answered 201 to on 2026-08-01. RFC 4791 allows
# more properties here -- supported-calendar-component-set, a description, a
# colour -- and none of them have been tried against a real server, so this asks
# for the one thing it needs and lets the server default the rest.
MAKE = """<?xml version="1.0" encoding="utf-8"?>
<C:mkcalendar xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:set><D:prop><D:displayname>{name}</D:displayname></D:prop></D:set>
</C:mkcalendar>
"""


def address_for(name: str) -> str:
    """The last part of a calendar's address, worked out from its name.

    Names have spaces, accents and punctuation in them and addresses should not,
    so this keeps the letters and digits and joins the rest with hyphens.
    """
    slug = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower()
    return slug or "calendar"


class Maker(Account):
    """An account you can add a calendar to, over the connection Account opens."""

    def make_calendar(self, segment: str, name: str) -> tuple[bool, str]:
        """Make one calendar. Returns whether it is there, and why not."""
        status, body = self.request(
            "MKCALENDAR", self.path + segment + "/", MAKE.format(name=escape(name))
        )
        if status in (200, 201):
            return True, ""
        if status == 401:
            return False, "the server would not accept that username and password"
        # 405 is what a server says when something is already at that address,
        # which is the one failure worth telling apart from the rest -- it means
        # the listing above missed it rather than that the request was wrong.
        if status == 405:
            return False, "something is already at that address"
        if status == 403:
            return False, "the server would not allow it" + _because(body)
        if status == 507:
            return False, "the account is out of space"
        return False, f"HTTP {status}" + _because(body)


def _because(body: bytes) -> str:
    """Whatever the server said about why, if it said anything readable."""
    text = re.sub(rb"<[^>]+>", b" ", body).decode("utf-8", "replace")
    text = " ".join(text.split())
    return f" ({text})" if text else ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("home", help="the address of the account's calendars")
    parser.add_argument("name", help="what the calendar is called, as Thunderbird shows it")
    add_credentials(parser)
    add_confirmation(
        parser,
        asks="show the address it worked out and ask before making anything",
        skips="do not ask; nothing here asks unless you pass --confirm",
    )
    parser.add_argument(
        "--path",
        metavar="SEGMENT",
        help="the last part of the calendar's address; worked out from the name if left off",
    )
    args = parser.parse_args(argv)

    name = args.name.strip()
    if not name:
        print("The calendar needs a name.", file=sys.stderr)
        return 1
    segment = (args.path or address_for(name)).strip("/")

    try:
        user, password = ready(args)
    except Refused as why:
        print(str(why), file=sys.stderr)
        return 1

    try:
        account = Maker(args.home, user, password)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1

    try:
        calendars, _ = account.calendars()
        if not calendars:
            print(
                f"No calendars under {account.path}.\n"
                "That address is probably one calendar rather than the account's calendars --\n"
                "take the last part off it and try again. If it really is the right address,\n"
                "the MKCALENDAR request in the README makes this one call directly."
            )
            return 1

        for href, existing in calendars:
            if _path_of(href).rsplit("/", 1)[-1].casefold() == segment.casefold():
                print(f"{account.path}{segment}/ is already there, called {existing!r}.")
                print("Give a different name, or --path, or delete that one first.")
                return 1
            if existing.casefold() == name.casefold():
                print(f"This account already has a calendar called {existing!r}, at")
                print(f"  {_path_of(href)}")
                print("Two calendars with one name are indistinguishable in Thunderbird's list,")
                print("so pick another name, or delete that one first.")
                return 1

        # The address is worked out from the name, and it is the one thing here
        # worth seeing before it exists: it is what you subscribe to and what you
        # match against when you come to clean up.
        if args.confirm:
            print(f"About to make {name!r} at")
            print(f"  {account.address()}{segment}/")
            if not agreed("Go ahead?"):
                print("Nothing made.")
                return 1

        made, why = account.make_calendar(segment, name)
        if not made:
            print(f"Could not make it: {why}.", file=sys.stderr)
            if "username and password" in why:
                print(
                    "CalDAV here needs an app password; an OAuth2 account password will not do.",
                    file=sys.stderr,
                )
            return 1

        print(f"Made {name!r} at")
        print(f"  {account.address()}{segment}/")
        print("\nSubscribe to it in Thunderbird with New Calendar -> On the Network,")
        print("and delete it afterwards with caldav_delete_calendars.py.")
        return 0
    finally:
        account.close()


if __name__ == "__main__":
    sys.exit(main())
