#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""What every CalDAV tool here needs before it can do its own job: a connection
to the server, and the list of calendars on the account.

Neither of those is about deleting anything, but both of them used to live in a
tool that does. That was fine while every tool in the set changed something --
each one had to sign in, list, and only then delete, import or create. A tool
that only looks is the case that made it wrong: caldav_list_calendars.py would
have had to import two deleting tools to ask the server a question.

So the transport is Connection, the listing is Account, and the tools that
change something add their own verb on top:

    Connection      one server, one collection, one connection kept open
      Account       the calendars on it, and which one is the default
        Deleter     ... and DELETE, in caldav_delete_calendars.py
        Maker       ... and MKCALENDAR, in caldav_make_calendar.py
      Calendar      one calendar's entries, in caldav_delete_events.py
        Importer    ... and PUT, in caldav_import_ics.py

The credentials that open one of these come from caldav_asking.py, which is the
other half of what the tools share.
"""

from __future__ import annotations

import base64
import http.client
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

DAV = "DAV:"
CALDAV = "urn:ietf:params:xml:ns:caldav"

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

# What a calendar with no name is called, so it still has something to print and
# something to match --keep against.
UNNAMED = "(unnamed)"


def _path_of(href: str) -> str:
    """A href as a bare path, however the server chose to write it.

    Three shapes arrive: a path, a full URL, and a network-path reference --
    //host/path, which is a host without a scheme. urlsplit reduces all three to
    the path, and that is load-bearing rather than incidental: a host named in a
    *reply* is not one to act on, and the JavaScript twin had a hole here
    exactly because it only recognised the second shape.
    """
    return (urlsplit(href).path or href).rstrip("/")


class Connection:
    """One collection on a CalDAV server, over a connection kept open."""

    def __init__(self, url: str, user: str, password: str, timeout: float = 60):
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            raise ValueError(f"{url} is not an http or https address")
        if not parts.path:
            raise ValueError(f"{url} has no path, so it cannot be a calendar")
        self.secure = parts.scheme == "https"
        self.host = parts.hostname or ""
        self.port = parts.port
        self.timeout = timeout
        # A calendar is a collection, so its address ends in a slash. Servers
        # differ on whether they forgive a missing one; none mind an extra.
        self.path = parts.path if parts.path.endswith("/") else parts.path + "/"
        credentials = f"{user}:{password}".encode()
        self.authorization = "Basic " + base64.b64encode(credentials).decode("ascii")
        self.connection: http.client.HTTPConnection | None = None

    def address(self) -> str:
        """The calendar's address as the tool understands it, to print back at you.

        Assembled from the parts rather than echoed, because what a request goes
        to is the path with its trailing slash rather than whatever was typed.
        Which means the scheme and the port are assembled too, and neither is a
        detail to lose: a local server on some port is exactly where a
        reproduction that must not touch production goes.
        """
        port = f":{self.port}" if self.port else ""
        return f"{'https' if self.secure else 'http'}://{self.host}{port}{self.path}"

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


class Account(Connection):
    """An account's calendars, reached over the connection Connection opens."""

    #: Where the last listing said the account's principal is, as a path.
    principal: str | None = None

    def calendars(self) -> tuple[list[tuple[str, str]], str | None]:
        """Every calendar as its address and name, and which one the server calls default.

        The second half is None far more often than the specification suggests:
        it is what the server *said*, and Stalwart says nothing. Pass both to
        default_among() rather than reading the None as "there isn't one".
        """
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
            found.append((href, name or UNNAMED))

        # Kept rather than returned, to leave calendars() a pair: every caller
        # wants the calendars and the default, and only the tests and the
        # add-on's twin care where the principal was.
        self.principal = _path_of(principal) if principal else None

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


def default_among(
    calendars: list[tuple[str, str]], advertised: str | None
) -> tuple[str | None, bool]:
    """Which calendar is the default, and whether the server actually said so.

    Thundermail's Stalwart advertises schedule-default-calendar-URL nowhere --
    not on the home, not on the principal -- so on the server these tools were
    written for the answer always comes from the address ending in /default.
    That is a guess, and every caller has to be able to tell the callers apart:
    guessing wrong means keeping a calendar you meant to delete, which is
    harmless, or naming the wrong calendar as your default, which is not.

    The name is deliberately not consulted. A calendar can be called anything,
    including "Default", and the one thing a display name never tells you is
    which calendar the account will fall back to.
    """
    if advertised:
        return advertised, True
    for href, _ in calendars:
        path = _path_of(href)
        if path.rsplit("/", 1)[-1].casefold() == "default":
            return path, False
    return None, False
