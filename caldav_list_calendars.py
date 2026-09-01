#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Say what an account's default calendar is called, and what else is on it.

Until now the only way to learn that was to run caldav_delete_calendars.py as a
dry run and read what it said it was keeping -- a deleting tool run for its
listing. This is the same question asked by a tool that cannot delete anything:
it sends PROPFIND and nothing else.

    uv run caldav_list_calendars.py HOME -u you@example.com          # the default's name
    uv run caldav_list_calendars.py HOME -u you@example.com --all    # all of them

HOME is the address of the account's calendars, which is the calendar Location
from Thunderbird's Properties dialog with the last part taken off:

    https://mail.example.com/dav/cal/you@example.com/some-calendar/  <- one calendar
    https://mail.example.com/dav/cal/you@example.com/                <- HOME

With no flags it prints one line, the name of the default calendar, so it can go
straight into a variable:

    DEFAULT=$(uv run caldav_list_calendars.py "$CAL_HOME")

Everything else it has to tell you -- that the default was worked out rather
than advertised, that a calendar has no name -- goes to standard error, so that
line stays a name and nothing else.

--all prints every calendar with its address and marks the default. Both come
from one request; the flag changes what is printed, not what is asked.

-u can be left off if CALDAV_USER is set, as CALDAV_PASSWORD already works for
the password.

**Which calendar is the default is usually a guess.** Servers are supposed to
advertise it as schedule-default-calendar-URL and Thundermail's Stalwart
advertises it nowhere, so the answer comes from the address ending in /default
and says so when it does. The one certain test -- DELETE it and see whether the
server refuses -- is not a thing a tool that only lists may do.
"""

from __future__ import annotations

import argparse
import sys

from anonymize_ics import _count
from caldav_account import UNNAMED, Account, _path_of, default_among
from caldav_asking import Refused, add_credentials, credentials

# What the note about a guessed default says, wherever it is printed. Worth
# keeping in one place: a name that turns out to be the wrong calendar's is the
# whole cost of getting this wrong, so it is never printed without the caveat.
GUESSED = (
    "The server did not say which calendar is its default, so that was worked out from\n"
    "the addresses: this is the one whose address ends in /default."
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("home", help="the address of the account's calendars")
    add_credentials(parser)
    parser.add_argument(
        "--all",
        action="store_true",
        help="list every calendar with its address, rather than naming the default",
    )
    args = parser.parse_args(argv)

    # credentials() rather than ready(), which the other tools use: what ready()
    # adds is checking there is somebody at the keyboard before a password is
    # typed, and that is for tools with a question to ask afterwards. This one
    # changes nothing, so it has none.
    try:
        user, password = credentials(args.user)
    except Refused as why:
        print(str(why), file=sys.stderr)
        return 1

    try:
        account = Account(args.home, user, password)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1

    try:
        calendars, advertised = account.calendars()
        if not calendars:
            print(
                f"No calendars under {account.path}.\n"
                "That address is probably one calendar rather than the account's calendars --\n"
                "take the last part off it and try again.",
                file=sys.stderr,
            )
            return 1

        default, said = default_among(calendars, advertised)
        if args.all:
            return _list_them(account, calendars, default, said)
        return _name_the_default(calendars, default, said)
    finally:
        account.close()


def _name_the_default(
    calendars: list[tuple[str, str]], default: str | None, said: bool
) -> int:
    """Print the default calendar's name on its own, and everything else elsewhere."""
    if not default:
        print(
            f"Could not tell which of these {_count(len(calendars), 'calendar')} is the default.\n"
            "The server does not advertise one and none of their addresses ends in /default.\n"
            "Run it again with --all to see them, and take the name from Thunderbird's list.",
            file=sys.stderr,
        )
        return 1

    name = next((name for href, name in calendars if _path_of(href) == default), None)
    if name is None:
        # The default the server named is not one of the calendars it listed,
        # which is the server contradicting itself rather than an answer.
        print(
            f"The server says its default calendar is {default}, which is not one of the\n"
            f"{_count(len(calendars), 'calendar')} it listed. Run it again with --all.",
            file=sys.stderr,
        )
        return 1

    if not said:
        print(GUESSED, file=sys.stderr)
    if name == UNNAMED:
        print(f"The server gave {default} no name of its own.", file=sys.stderr)
    print(name)
    return 0


def _list_them(
    account: Account, calendars: list[tuple[str, str]], default: str | None, said: bool
) -> int:
    """Print every calendar, name first, with the default marked."""
    print(f"{_count(len(calendars), 'calendar')} under {account.path}:\n")
    width = max(len(name) for _, name in calendars)
    for href, name in calendars:
        path = _path_of(href)
        mark = "  <- default" if default and path == default else ""
        print(f"  {name.ljust(width)}  {path}/{mark}")

    if default and not said:
        print(f"\n{GUESSED}")
    elif not default:
        print(
            "\nNone of these is marked as the default: the server does not advertise one and\n"
            "no address ends in /default. Thunderbird's calendar list is the place to look."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
