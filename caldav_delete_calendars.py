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

    uv run caldav_delete_calendars.py HOME --only "ticket 7067" --delete

--only is the one to reach for when you know which calendar you mean: it takes
the name Thunderbird shows or the last part of the address, may be given more
than once, and everything not named is left alone. A name that matches nothing
stops the run rather than deleting nothing quietly, and **a name that matches
more than one calendar stops it too** -- a display name and somebody else's
address can be a character apart, and picking one of them is not this tool's
decision to make. It contradicts --keep, so no command may have both.

It reports and changes nothing until you add --delete. With --delete it asks you
to type the number of calendars first; --delete --confirm asks about each one
instead, and takes yes, no, all the rest, or stop here. --yes asks nothing.

-u can be left off if CALDAV_USER is set, as CALDAV_PASSWORD already works for
the password.

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
from urllib.parse import urlsplit

from anonymize_ics import _count
from caldav_account import Account, _path_of, default_among
from caldav_asking import (
    NO,
    QUIT,
    Asking,
    Refused,
    add_confirmation,
    add_credentials,
    ready,
)


class Deleter(Account):
    """An account you can take a calendar off, over the connection Account opens."""

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
    add_credentials(parser)
    add_confirmation(
        parser,
        asks="ask about each calendar: yes, no, all the rest, or stop here",
        skips="do not ask to confirm at all; this does not imply --delete",
    )
    # The two ways of picking contradict each other, so no command may have
    # both: --keep says what to spare out of everything, --only says what to
    # take out of nothing.
    picking = parser.add_mutually_exclusive_group()
    picking.add_argument(
        "--keep",
        action="append",
        default=[],
        metavar="NAME",
        help="a calendar to leave alone, by name; may be given more than once",
    )
    picking.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="NAME",
        help="delete only this one, by name or by the last part of its address;"
        " may be given more than once",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="actually delete; without this it only reports what it would delete",
    )
    args = parser.parse_args(argv)

    try:
        user, password = ready(args)
    except Refused as why:
        print(str(why), file=sys.stderr)
        return 1

    try:
        account = Deleter(args.home, user, password)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1

    try:
        calendars, advertised = account.calendars()
        if not calendars:
            print(
                f"No calendars under {account.path}.\n"
                "That address is probably one calendar rather than the account's calendars --\n"
                "take the last part off it and try again."
            )
            return 1

        default, said = default_among(calendars, advertised)
        keep = {name.casefold() for name in args.keep}
        # Named by their display name or by the last part of their address,
        # because both are in front of you: the name is what Thunderbird shows
        # and the address is what this tool and the add-on print.
        wanted = {name.strip("/").casefold() for name in args.only}
        # Which calendars each --only matched. A display name and an address can
        # be one character apart -- "renametest" and "rename-test" were on one
        # real account, pointing at different calendars -- so how many a name
        # matched has to be known before anything is deleted, not after.
        matched: dict[str, list[tuple[str, str]]] = {name: [] for name in wanted}
        for href, name in calendars:
            goes_by = {name.casefold(), _path_of(href).rsplit("/", 1)[-1].casefold()}
            for value in goes_by & wanted:
                matched[value].append((href, name))
        ambiguous = {value for value, found in matched.items() if len(found) > 1}

        doomed = []
        print(f"{_count(len(calendars), 'calendar')} under {account.path}:\n")
        for href, name in calendars:
            path = _path_of(href)
            goes_by = {name.casefold(), path.rsplit("/", 1)[-1].casefold()}
            # Guessing the default from its address is normally a bad idea, but
            # the only cost of guessing wrong here is keeping a calendar you
            # meant to delete, and the server refusing remains the real
            # protection.
            if default and path == default:
                why = ("kept, it is the default" if said
                       else "kept, its address says default and the server would not say")
            elif name.casefold() in keep:
                why = "kept, you asked"
            elif goes_by & ambiguous:
                why = "kept, that name matches more than one calendar"
            elif wanted and not (goes_by & wanted):
                why = "kept, not one you named"
            else:
                why = "would be deleted"
                doomed.append((href, name))
            print(f"  {name}")
            print(f"    {path}  --  {why}")

        if not said:
            print(
                "\nThe server did not say which calendar is its default, so that was worked out\n"
                "from the addresses. Any calendar above that turns out to be the default anyway\n"
                "will refuse and be reported, not deleted."
            )

        # More than one match is not a licence to delete both. The point of
        # --only is that you know which calendar you mean, so if the name does
        # not say, nothing goes until you have said it another way.
        if ambiguous:
            print()
            for value in ambiguous:
                print(f"{value!r} matches {_count(len(matched[value]), 'calendar')}:")
                for href, name in matched[value]:
                    print(f"  {name}")
                    print(f"    {_path_of(href)}")
            print(
                "\nNothing was deleted. Name one of them exactly -- the display name and the\n"
                "last part of the address are both accepted, and here they disagree."
            )
            return 1

        # A name that matches nothing is a typo, and a typo that quietly deletes
        # nothing is one you make again. Say which, and say it before anything
        # is deleted rather than after.
        missing = [name for name in args.only if not matched[name.strip("/").casefold()]]
        if missing:
            print(f"\nNothing here is called {', '.join(repr(name) for name in missing)}.")
            print("Nothing was deleted. The names above are what there is to choose from.")
            return 1

        if wanted and not doomed:
            # Everything named turned out to be the default, or spared for some
            # other reason listed above. You asked for a deletion and did not
            # get one, so this is not a quiet success.
            print("\nNothing you named can be deleted, for the reasons above.")
            return 1

        if not doomed:
            print("\nNothing to do.")
            return 0

        if not args.delete:
            print(f"\n{_count(len(doomed), 'calendar')} would be deleted."
                  " This was a dry run; add --delete to go ahead.")
            return 0

        # Typing the number means having read it. --confirm replaces that step
        # rather than adding to it: being asked about all of them and then about
        # each of them is one question too many.
        if not args.yes and not args.confirm:
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
        asking = Asking(args.confirm)
        deleted, kept, stopped, failures = 0, 0, False, []
        for href, name in doomed:
            if asking.on:
                answer = asking.about(f"{name}  --  {_path_of(href)}, contents and all")
                if answer == QUIT:
                    stopped = True
                    break
                if answer == NO:
                    kept += 1
                    print(f"  kept     {name} -- you said no")
                    continue
            gone, why = account.delete_calendar(href)
            if gone:
                deleted += 1
                print(f"  deleted  {name}")
            else:
                failures.append(f"  {name}: {why}")
                print(f"  kept     {name} -- {why}")

        print(f"\nDeleted {_count(deleted, 'calendar')}.")
        if kept:
            print(f"{_count(kept, 'calendar')} left alone, because you said no.")
        if stopped:
            # Not a failure. You were asked, and stopping was one of the answers.
            print("Stopped where you asked; everything after that is untouched.")
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
