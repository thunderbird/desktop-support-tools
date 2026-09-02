#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Rename a calendar, which is the only way to shorten a name a server chose.

Thundermail names your default calendar after your account -- "Nemo Thundermail
Calendar (nemo@thundermail.com)" -- and subscribers have said it is too long. It
is also the one place these tools print an email address. Nothing in Thunderbird
or in Thundermail's web interface renames a calendar, but the server has no
objection: PROPPATCH on the collection returned 207 with a 200 propstat against
a real account's *default* calendar, which is the one calendar it refuses to let
anybody delete.

    uv run caldav_rename_calendar.py HOME --only "long name" --to "Calendar"
    uv run caldav_rename_calendar.py HOME --only long-name --to "Calendar" --rename

HOME is the address of the account's calendars, which is the calendar Location
from Thunderbird's Properties dialog with the last part taken off:

    https://mail.example.com/dav/cal/you@example.com/some-calendar/  <- one calendar
    https://mail.example.com/dav/cal/you@example.com/                <- HOME

--only picks the calendar, by the name Thunderbird shows or by the last part of
its address, and --to is what it should be called instead. If it matches more
than one calendar -- a display name and another calendar's address can be a
character apart -- it lists them and renames nothing. It reports and changes
nothing until you add --rename. --confirm shows the old name and the new one and
asks first; --yes asks nothing.

-u can be left off if CALDAV_USER is set, as CALDAV_PASSWORD already works for
the password.

**The old name is printed before it is replaced, and again afterwards.** A
rename is reversible only while somebody still knows what the name was, and this
is about to overwrite the only copy of it. Write it down.

Renaming does not move the calendar: the address stays as it was, so anything
subscribed to it stays subscribed. Thunderbird may go on showing the old name
from its own cache until it next looks.
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit
from xml.sax.saxutils import escape

from caldav_account import DAV, Account, _path_of
from caldav_asking import Refused, add_confirmation, add_credentials, agreed, ready
from caldav_make_calendar import _because

# Renaming a collection is one property. RFC 4918 puts it in a propertyupdate,
# and there is deliberately no JavaScript twin of this body: the add-on does not
# rename anything, because a rename replaces the only copy of the old name and a
# published build carries no operation like that.
RENAME = """<?xml version="1.0" encoding="utf-8"?>
<D:propertyupdate xmlns:D="DAV:">
  <D:set><D:prop><D:displayname>{name}</D:displayname></D:prop></D:set>
</D:propertyupdate>
"""


class Renamer(Account):
    """An account whose calendars you can rename, over the connection Account opens."""

    def rename(self, href: str, name: str) -> tuple[bool, str]:
        """Rename one calendar. Returns whether it happened, and why not."""
        # A calendar is a collection, so its address ends in a slash, and a
        # listing is free to hand one back without it. Servers differ on whether
        # they forgive that; none mind an extra.
        path = urlsplit(href).path or href
        status, body = self.request(
            "PROPPATCH",
            path if path.endswith("/") else f"{path}/",
            RENAME.format(name=escape(name)),
        )
        if status == 401:
            return False, "the server would not accept that username and password"
        if status == 403:
            return False, "the server would not allow it" + _because(body)
        if status == 404:
            return False, "there is nothing at that address any more"
        if status not in (200, 204, 207):
            return False, f"HTTP {status}" + _because(body)
        # A 207 is the envelope, not the answer. The server is free to say
        # "multi-status" and then refuse the property inside it, and reading only
        # the outer code would report that refusal as a rename.
        return _property_refused(body)


def _property_refused(body: bytes) -> tuple[bool, str]:
    """Whether every propstat in the reply succeeded, and what to say if not."""
    try:
        root = ET.fromstring(body) if body.strip() else None
    except ET.ParseError:
        return False, "the server's reply was not readable XML"
    if root is None:
        return True, ""  # 200 or 204 with nothing to say is a plain success.

    said = [
        (element.text or "").strip()
        for element in root.iter(f"{{{DAV}}}status")
        if (element.text or "").strip()
    ]
    refusals = [line for line in said if not _is_success(line)]
    if refusals:
        return False, f"the server refused the new name ({'; '.join(refusals)})"
    if not said:
        return False, "the server answered without saying whether it renamed anything"
    return True, ""


def _is_success(line: str) -> bool:
    """Whether an HTTP status line in a propstat is a 2xx."""
    parts = line.split()
    return len(parts) > 1 and parts[1].startswith("2")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("home", help="the address of the account's calendars")
    add_credentials(parser)
    add_confirmation(
        parser,
        asks="show the old name and the new one and ask before renaming",
        skips="do not ask; nothing here asks unless you pass --confirm",
    )
    parser.add_argument(
        "--only",
        required=True,
        metavar="NAME",
        help="which calendar, by name or by the last part of its address",
    )
    parser.add_argument(
        "--to",
        required=True,
        metavar="NAME",
        help="what it should be called instead",
    )
    parser.add_argument(
        "--rename",
        action="store_true",
        help="actually rename; without this it only reports what it would rename",
    )
    args = parser.parse_args(argv)

    wanted = args.only.strip("/").casefold()
    name = args.to.strip()
    if not name:
        print("The calendar needs a name. Give --to something.", file=sys.stderr)
        return 1

    try:
        user, password = ready(args)
    except Refused as why:
        print(str(why), file=sys.stderr)
        return 1

    try:
        account = Renamer(args.home, user, password)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1

    try:
        calendars, _ = account.calendars()
        if not calendars:
            print(
                f"No calendars under {account.path}.\n"
                "That address is probably one calendar rather than the account's calendars --\n"
                "take the last part off it and try again."
            )
            return 1

        # Every match, not the first: a display name and an address can be one
        # character apart -- "renametest" and "rename-test" were on one real
        # account, pointing at different calendars -- and picking whichever came
        # first would rename the one nobody meant.
        found = [
            (href, existing, _path_of(href))
            for href, existing in calendars
            if wanted in {existing.casefold(), _path_of(href).rsplit("/", 1)[-1].casefold()}
        ]

        if len(found) > 1:
            print(f"{args.only!r} matches {len(found)} calendars:\n")
            for _, existing, path in found:
                print(f"  {existing}")
                print(f"    {path}")
            print(
                "\nNothing was renamed. Name one of them exactly -- the display name and the\n"
                "last part of the address are both accepted, and here they disagree."
            )
            return 1

        picked = found[0] if found else None
        if picked is None:
            print(f"Nothing here is called {args.only!r}. These are:\n")
            for href, existing in calendars:
                print(f"  {existing}")
                print(f"    {_path_of(href)}")
            print("\nNothing was renamed.")
            return 1

        href, was, path = picked

        # Two calendars with one name are indistinguishable in Thunderbird's
        # list, which is the same reason caldav_make_calendar.py refuses it.
        for other, existing in calendars:
            if other != href and existing.casefold() == name.casefold():
                print(f"This account already has a calendar called {existing!r}, at")
                print(f"  {_path_of(other)}")
                print("Two calendars with one name are indistinguishable in Thunderbird's list,")
                print("so pick another name.")
                return 1

        if was == name:
            print(f"{path} is already called {name!r}. Nothing to do.")
            return 0

        # Printed before anything is sent, and again at the end. This is about
        # to overwrite the only copy of the old name.
        print(f"{path}")
        print(f"  is called  {was!r}")
        print(f"  would be   {name!r}")

        if not args.rename:
            print("\nThis was a dry run; add --rename to go ahead.")
            return 0

        if args.confirm and not args.yes:
            print()
            if not agreed(f"Rename {was!r} to {name!r}?"):
                print("Nothing renamed.")
                return 1

        renamed, why = account.rename(href, name)
        if not renamed:
            print(f"\nCould not rename it: {why}.", file=sys.stderr)
            if "username and password" in why:
                print(
                    "CalDAV here needs an app password; an OAuth2 account password will not do.",
                    file=sys.stderr,
                )
            return 1

        print(f"\nRenamed. It was {was!r}, and is now {name!r}.")
        print("Write the old name down if you might want it back -- nothing here remembers it.")
        print("Thunderbird may go on showing the old name until it next looks.")
        return 0
    finally:
        account.close()


if __name__ == "__main__":
    sys.exit(main())
