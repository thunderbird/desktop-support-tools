#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""What the CalDAV tools ask you: who you are, and whether you meant it.

All four sign in the same way and all four do things worth being sure about, so
the asking lives here once rather than four times over.

The app password comes from CALDAV_PASSWORD or a prompt, which is what keeps it
out of your shell history. The username works the same way for a different
reason: it is not a secret, but reproducing a calendar bug is half a dozen
commands against one account, and repeating -u on every one of them is noise.

    export CALDAV_USER='you@example.com'
    export CALDAV_PASSWORD='...'        # or leave it unset and be asked

Confirmation is two flags that contradict each other, so no command may have
both. --confirm asks before acting, even where the action is not destructive.
--yes does not ask, even where it is. Neither of them ever implies --delete: a
dry run stays a dry run.
"""

from __future__ import annotations

import argparse
import sys
from getpass import getpass
from os import environ

# What Asking.about() answers with. "All the rest" is not among them: it answers
# YES for the thing in hand and stops the asking, so the caller never sees it.
YES = "yes"
NO = "no"
QUIT = "quit"


class Refused(Exception):
    """There is nobody here to ask, or there is and they said no."""


def add_credentials(parser: argparse.ArgumentParser) -> None:
    """The one flag both credentials share, worded the same way in all three tools."""
    parser.add_argument(
        "-u",
        "--user",
        help="the username to sign in with; CALDAV_USER, or a question, if left off",
    )


def add_confirmation(parser: argparse.ArgumentParser, asks: str, skips: str) -> None:
    """--confirm and --yes, which cannot both be given because they disagree."""
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--confirm", action="store_true", help=asks)
    group.add_argument("--yes", action="store_true", help=skips)


def interactive() -> bool:
    """Whether there is somebody at the keyboard to answer a question."""
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except ValueError:  # stdin has been closed under us
        return False


def ready(args: argparse.Namespace) -> tuple[str, str]:
    """The credentials, once it is clear that anything needing asking can be asked.

    The terminal comes first on purpose. Finding out that the questions you were
    going to be asked cannot be asked is much better news before you have typed a
    password than after.
    """
    if args.confirm and not interactive():
        raise Refused(
            "--confirm has questions for you, and this is not a terminal, so there is\n"
            "nothing here to answer them. Run it in a terminal, or drop --confirm."
        )
    return credentials(args.user)


def credentials(user: str | None) -> tuple[str, str]:
    """The username and app password: from the flag, the environment, or you."""
    user = (user or environ.get("CALDAV_USER") or _asked("Username: ")).strip()
    if not user:
        raise Refused("No username. Pass -u, set CALDAV_USER, or type one when asked.")

    password = environ.get("CALDAV_PASSWORD") or _asked(f"App password for {user}: ", secret=True)
    if not password:
        raise Refused(
            "No app password. Set CALDAV_PASSWORD, or type one when asked.\n"
            "These requests sign in with HTTP Basic, so they need an app password from\n"
            "your provider's settings rather than the password you type into Thunderbird."
        )
    return user, password


def _asked(question: str, secret: bool = False) -> str:
    """Ask for one thing, treating end of input as nothing rather than a traceback."""
    try:
        return (getpass(question) if secret else input(question)).strip()
    except EOFError:
        print(file=sys.stderr)
        return ""


def agreed(question: str) -> bool:
    """A yes-or-no question where everything except yes is no, end of input included."""
    try:
        return input(f"{question} [y/N]: ").strip().lower() in ("y", "yes")
    except EOFError:
        print()
        return False


class Asking:
    """Asks about each thing in turn, until you say to stop being asked.

    "All the rest" is what makes this safe to turn on when you do not know how
    many things there are: look at the first few, satisfy yourself the right ones
    are being picked, and stop being asked. So it has to outlive the pass it was
    given in -- caldav_delete_events.py works through a capped calendar in passes,
    and being asked all over again on the next pass would defeat the point.

    Saying no is remembered for the same reason. A later pass lists what it did
    not delete, and offering it again would be asking a question you have
    already answered.
    """

    def __init__(self, on: bool, verb: str = "Delete"):
        self.on = on
        self.verb = verb
        self.declined: set[str] = set()

    def about(self, description: str) -> str:
        """What to do with one thing: YES, NO or QUIT.

        Answering with nothing at all asks again rather than assuming either way,
        because a stray return is not consent to delete something.
        """
        if not self.on:
            return YES
        print(f"  {description}")
        while True:
            try:
                answer = input(
                    f"  {self.verb}? [y]es [n]o [a]ll the rest [q]uit: "
                ).strip().lower()
            except EOFError:
                print()
                return QUIT
            if answer in ("y", "yes"):
                return YES
            if answer in ("n", "no"):
                return NO
            if answer in ("a", "all"):
                self.on = False
                print("  Not asking again.")
                return YES
            if answer in ("q", "quit"):
                return QUIT
            print("  Answer y, n, a or q.")
