#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Take the personal information out of a calendar file.

Rewrites an .ics file so that nothing identifying is left -- what the meetings
were called, who was in them, where they were, what was attached -- while every
date, repeat rule, alarm and component stays exactly as it was. That is the
point: the result still reproduces the bug, but it is safe to attach to a
public bug report.

    uv run anonymize_ics.py Calendar.ics -o scrubbed.ics
    uv run anonymize_ics.py Calendar.ics > scrubbed.ics
    uv run anonymize_ics.py --check scrubbed.ics

Written for a support ticket carrying a 1,338-event calendar:
https://tbpro.zendesk.com/agent/tickets/7067
"""

from __future__ import annotations

import argparse
import re
import sys
import uuid

# Properties whose value is replaced. Everything absent from this set is copied
# through untouched, which is what keeps the scrubbed calendar a usable copy of
# the original: dates, repeat rules, alarms and component structure all survive.
TEXT_FIELDS = frozenset({"SUMMARY", "DESCRIPTION", "LOCATION", "COMMENT", "CONTACT", "RESOURCES"})
CATEGORY_FIELDS = frozenset({"CATEGORIES"})
CALENDAR_NAME_FIELDS = frozenset({"NAME", "X-WR-CALNAME", "X-WR-CALDESC"})
PERSON_FIELDS = frozenset({"ORGANIZER", "ATTENDEE"})
URI_FIELDS = frozenset({"URL", "ATTACH"})
LOCATION_FIELDS = frozenset({"GEO"})
UID_FIELDS = frozenset({"UID"})

SCRUBBED = (
    TEXT_FIELDS
    | CATEGORY_FIELDS
    | CALENDAR_NAME_FIELDS
    | PERSON_FIELDS
    | URI_FIELDS
    | LOCATION_FIELDS
    | UID_FIELDS
)

# Parameters kept on a scrubbed property. Everything else goes, because CN,
# SENT-BY, DIR, ALTREP, MEMBER, DELEGATED-FROM, DELEGATED-TO, EMAIL and the X-
# parameters all carry names, addresses or links. Blanking a property's value
# and leaving CN="Smith, Jim" beside it scrubs nothing.
SAFE_PARAMETERS = frozenset({
    "VALUE",
    "ENCODING",
    "FMTTYPE",
    "LANGUAGE",
    "TZID",
    "RELATED",
    "ROLE",
    "PARTSTAT",
    "RSVP",
    "CUTYPE",
    "RANGE",
})

TEXT_PLACEHOLDER = "Anonymized Data"
CATEGORY_PLACEHOLDER = "ANONYMIZED"
CALENDAR_NAME_PLACEHOLDER = "Anonymized Calendar"
URI_PLACEHOLDER = "https://example.com/anonymized"
BINARY_PLACEHOLDER = "QW5vbnltaXplZA=="  # base64 for "Anonymized"
COORDINATES_PLACEHOLDER = "0.0;0.0"
PSEUDONYM_DOMAIN = "example.com"

# A line is NAME, then any number of ;PARAMETER=VALUE, then a colon and the
# value. A parameter value may be quoted, and a quoted one may contain a colon,
# so the colon that ends the parameters is the first unquoted one.
CONTENT_LINE = re.compile(
    r'^(?P<name>[A-Za-z0-9-]+)(?P<parameters>(?:;(?:"[^"]*"|[^":])*)*):(?P<value>.*)$',
    re.DOTALL,
)
PARAMETER = re.compile(r';(?P<name>[A-Za-z0-9-]+)=(?P<value>(?:"[^"]*"|[^";:])*)')
CONTINUATION = re.compile(r"\r?\n[ \t]")
# No "=" in the local part, so that CN=someone@example.org is reported once as
# the address it contains rather than twice, the second time as itself.
LOOKS_LIKE_AN_ADDRESS = re.compile(r"[\w.+!#$%&'*?^`{|}~-]+@[\w.-]+\.\w+")
ALREADY_A_PSEUDONYM = re.compile(rf"person\d+@{re.escape(PSEUDONYM_DOMAIN)}\Z")

# RFC 5545 asks for at most 75 octets per line, not per character, and a line
# may be broken only between characters.
LINE_LIMIT = 75


def _unfold(text: str) -> list[str]:
    """Return the logical lines, joining continuations back onto their line.

    A calendar file wraps long values by starting the next line with a space or
    a tab. Anything that reads a calendar one physical line at a time sees only
    the first 75 characters of a long description and misses the rest.
    """
    joined = CONTINUATION.sub("", text)
    return [line for line in joined.replace("\r\n", "\n").split("\n") if line]


def _fold(line: str) -> list[str]:
    """Wrap one logical line to the length a calendar file allows."""
    if len(line.encode("utf-8")) <= LINE_LIMIT:
        return [line]

    wrapped: list[str] = []
    current = ""
    used = 0
    for character in line:
        width = len(character.encode("utf-8"))
        if used + width > LINE_LIMIT:
            wrapped.append(current)
            # The leading space marks the continuation and counts toward the limit.
            current = " "
            used = 1
        current += character
        used += width
    wrapped.append(current)
    return wrapped


def _kept_parameters(parameters: str) -> str:
    """Drop the parameters that name a person or point at one."""
    return "".join(
        f';{found.group("name").upper()}={found.group("value")}'
        for found in PARAMETER.finditer(parameters)
        if found.group("name").upper() in SAFE_PARAMETERS
    )


def _pseudonym(value: str, people: dict[str, str]) -> str:
    """Replace one calendar address with a stand-in, the same one every time.

    Each distinct address gets its own stand-in and keeps it for the whole file,
    so a meeting with three people still has three people in it and someone who
    appears in forty meetings is still recognisably one person. Collapsing
    everybody onto a single address would change what the calendar means.
    """
    scheme, separator, address = value.partition(":")
    if not separator:
        scheme, address = "", value

    # An organizer with no address at all is a real thing that exporters emit,
    # and inventing one would be inventing a person who was never there.
    if not address:
        return value

    if ALREADY_A_PSEUDONYM.match(address):
        return value

    stand_in = people.setdefault(address.lower(), f"person{len(people) + 1}")
    if "@" in address:
        stand_in = f"{stand_in}@{PSEUDONYM_DOMAIN}"
    return f"{scheme}{separator}{stand_in}"


def _scrubbed_value(
    name: str,
    parameters: str,
    value: str,
    uids: dict[str, str],
    people: dict[str, str],
    *,
    replace_uids: bool,
) -> str:
    if name in UID_FIELDS:
        if not replace_uids:
            return value
        # A new random identifier rather than a numbered one: a scrubbed
        # calendar gets imported into real profiles, where a predictable
        # identifier could collide with an entry that is already there.
        return uids.setdefault(value, str(uuid.uuid4()).upper())
    if name in PERSON_FIELDS:
        return _pseudonym(value, people)
    if name in URI_FIELDS:
        upper = parameters.upper()
        if "VALUE=BINARY" in upper or "ENCODING=BASE64" in upper:
            return BINARY_PLACEHOLDER
        return URI_PLACEHOLDER
    if name in LOCATION_FIELDS:
        return COORDINATES_PLACEHOLDER
    if name in CATEGORY_FIELDS:
        return CATEGORY_PLACEHOLDER
    if name in CALENDAR_NAME_FIELDS:
        return CALENDAR_NAME_PLACEHOLDER
    return TEXT_PLACEHOLDER


def _scrub(
    line: str,
    uids: dict[str, str],
    people: dict[str, str],
    report: dict,
    *,
    replace_uids: bool = True,
) -> str:
    """Return one logical line with anything identifying taken out of it."""
    match = CONTENT_LINE.match(line)
    if match is None:
        return line

    name = match.group("name").upper()
    if name not in SCRUBBED:
        return line

    parameters = _kept_parameters(match.group("parameters"))
    dropped = len(PARAMETER.findall(match.group("parameters"))) - len(
        PARAMETER.findall(parameters)
    )
    value = _scrubbed_value(
        name,
        parameters,
        match.group("value"),
        uids,
        people,
        replace_uids=replace_uids,
    )

    scrubbed = f"{name}{parameters}:{value}"
    if scrubbed != line:
        report["properties"] += 1
        report["parameters"] += dropped
    return scrubbed


def anonymize(text: str, *, replace_uids: bool = True) -> tuple[str, dict]:
    """Scrub a calendar. Returns the new file and a note of what was done."""
    uids: dict[str, str] = {}
    people: dict[str, str] = {}
    report: dict = {"properties": 0, "parameters": 0, "uids": uids, "people": people}

    lines: list[str] = []
    left_alone: set[str] = set()
    for line in _unfold(text):
        match = CONTENT_LINE.match(line)
        if match is not None:
            name = match.group("name").upper()
            if name.startswith("X-") and name not in SCRUBBED:
                left_alone.add(name)
        lines.extend(_fold(_scrub(line, uids, people, report, replace_uids=replace_uids)))

    report["left_alone"] = sorted(left_alone)
    # Calendar files end every line, including the last one, with CRLF.
    return "\r\n".join(lines) + "\r\n", report


def audit(text: str) -> list[str]:
    """Report anything identifying still in a calendar, in plain words.

    Clean means scrubbing the file again would change nothing, so there is one
    definition of clean rather than two that can drift apart. Identifiers are
    excluded from the comparison because scrubbing always replaces those.
    """
    findings: list[str] = []
    people: dict[str, str] = {}
    report: dict = {"properties": 0, "parameters": 0}

    for number, line in enumerate(_unfold(text), start=1):
        if _scrub(line, {}, people, report, replace_uids=False) != line:
            name = CONTENT_LINE.match(line).group("name").upper()
            findings.append(f"line {number}: {name} still holds something that identifies someone")

    for address in sorted(set(LOOKS_LIKE_AN_ADDRESS.findall(text))):
        if not ALREADY_A_PSEUDONYM.match(address):
            findings.append(f"the address {address} is still in this file")

    return findings


def _count(number: int, singular: str, plural: str | None = None) -> str:
    return f"{number} {singular if number == 1 else plural or singular + 's'}"


def _read(path: str | None) -> str:
    """Read a calendar without letting Python rewrite its line endings."""
    if path:
        with open(path, encoding="utf-8", newline="") as handle:
            return handle.read()
    return sys.stdin.buffer.read().decode("utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "path",
        nargs="?",
        help="the calendar file to read (default: read stdin)",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="where to write the scrubbed calendar (default: write to stdout)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="report what is still identifying in a calendar, and change nothing",
    )
    args = parser.parse_args(argv)

    if args.check and args.output:
        parser.error("--check only reports; it has nothing to write, so drop -o")

    try:
        text = _read(args.path)
    except OSError as error:
        print(f"Could not read that calendar: {error}", file=sys.stderr)
        return 1
    except UnicodeDecodeError:
        print("That file is not a calendar saved as UTF-8 text.", file=sys.stderr)
        return 1

    if args.check:
        findings = audit(text)
        for finding in findings:
            print(finding)
        if not findings:
            print("Nothing identifying left in this calendar.")
        return 1 if findings else 0

    scrubbed, report = anonymize(text)

    try:
        if args.output:
            with open(args.output, "w", encoding="utf-8", newline="") as handle:
                handle.write(scrubbed)
        else:
            sys.stdout.buffer.write(scrubbed.encode("utf-8"))
    except OSError as error:
        print(f"Could not write the scrubbed calendar: {error}", file=sys.stderr)
        return 1

    # To stderr, so the scrubbed calendar itself can be piped onward.
    print(
        f"Took the details out of {_count(report['properties'], 'field')}, "
        f"replaced {_count(len(report['uids']), 'identifier')}, "
        f"gave {_count(len(report['people']), 'person', 'people')} stand-in addresses, "
        f"and dropped {_count(report['parameters'], 'name')} attached to them.",
        file=sys.stderr,
    )
    if report["left_alone"]:
        left = ", ".join(report["left_alone"])
        print(f"Left alone, because they are not standard calendar fields: {left}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
