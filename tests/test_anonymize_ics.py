# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Tests for the calendar scrubber.

Each ``ics-*.ics`` fixture is paired with an ``.expected.ics`` companion holding
the scrubbed result, and each fixture exists because an earlier version of the
scrubber got that case wrong. The identifying strings planted in the inputs are
asserted absent from the outputs, so a regression shows up as leaked data rather
than as a golden file that quietly needs updating.

Two properties matter as much as the scrubbing itself, and both are asserted
here: everything not scrubbed comes through byte-identical, and the result is
still a well-formed calendar. Neither is decoration -- a scrubbed calendar is
only worth having if it still reproduces the bug the original did.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from anonymize_ics import LINE_LIMIT, SCRUBBED, _fold, _unfold, anonymize, audit  # noqa: E402

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

# Identifying strings planted in the fixtures. None may survive scrubbing.
PII_STRINGS = (
    "Smith",
    "Jim",
    "jim.smith@example.org",
    "jane.smith@example.net",
    "george.smith@example.org",
    "GEORGE.SMITH@example.org",
    "George Smith",
    "adjointe@example.org",
    "contrat-smith.pdf",
    "jim.jpg",
    "360300RE-000023",
    "475 boulevard de l'Avenir",
    "Ressources humaines",
    "45.5712",
    "outlook.example.org",
    "intranet.example.org",
    "called them",
    "renouvellement du bail",
)

# The identifier is the one thing that is deliberately unpredictable, so the
# golden files hold a token in its place.
UID_LINE = re.compile(r"^(UID:)([^\r\n]+)", re.MULTILINE)


def fixture_names() -> list[str]:
    return sorted(
        path.stem
        for path in FIXTURES.glob("ics-*.ics")
        if not path.stem.endswith(".expected")
    )


def read(name: str) -> str:
    """Read a fixture as-is, without letting Python touch its line endings."""
    return (FIXTURES / name).read_bytes().decode("utf-8")


def normalise_uids(text: str) -> str:
    """Rewrite each distinct identifier to UID-1, UID-2 ... in first-seen order."""
    seen: dict[str, str] = {}

    def token(match: re.Match[str]) -> str:
        return match.group(1) + seen.setdefault(match.group(2), f"UID-{len(seen) + 1}")

    return UID_LINE.sub(token, text)


def properties(text: str) -> list[str]:
    return [line.split(":")[0].split(";")[0].upper() for line in _unfold(text)]


@pytest.mark.parametrize("name", fixture_names())
def test_matches_expected(name: str) -> None:
    scrubbed, _ = anonymize(read(f"{name}.ics"))
    assert normalise_uids(scrubbed) == read(f"{name}.expected.ics")


@pytest.mark.parametrize("name", fixture_names())
def test_nothing_identifying_survives(name: str) -> None:
    """The assertion that would have caught every bug these fixtures encode."""
    original = read(f"{name}.ics")
    scrubbed, _ = anonymize(original)

    for pii in PII_STRINGS:
        if pii in original:
            assert pii not in scrubbed, f"{pii!r} survived scrubbing {name}"


@pytest.mark.parametrize("name", fixture_names())
def test_structure_is_untouched(name: str) -> None:
    """Same properties, same order, and every unscrubbed line byte-identical.

    This is what makes a scrubbed calendar usable as a bug report: the dates,
    repeat rules and component structure that reproduce the problem are exactly
    the ones the original had.
    """
    original = read(f"{name}.ics")
    scrubbed, _ = anonymize(original)

    assert properties(original) == properties(scrubbed)

    kept = [line for line in _unfold(original) if line.split(":")[0].split(";")[0].upper() not in SCRUBBED]
    assert kept == [
        line for line in _unfold(scrubbed) if line.split(":")[0].split(";")[0].upper() not in SCRUBBED
    ]


@pytest.mark.parametrize("name", fixture_names())
def test_output_is_a_well_formed_calendar(name: str) -> None:
    scrubbed, _ = anonymize(read(f"{name}.ics"))

    assert scrubbed.startswith("BEGIN:VCALENDAR\r\n")
    assert scrubbed.endswith("END:VCALENDAR\r\n")
    assert "\n" not in scrubbed.replace("\r\n", ""), "a line ended without a carriage return"

    for line in scrubbed.split("\r\n")[:-1]:
        assert len(line.encode("utf-8")) <= LINE_LIMIT, f"line too long: {line!r}"

    # Folding has to be reversible, which it is not if a character was split in
    # half to make a line fit. Rewrapping the unfolded lines must reproduce the
    # file exactly.
    rewrapped = "\r\n".join(part for line in _unfold(scrubbed) for part in _fold(line)) + "\r\n"
    assert rewrapped == scrubbed
    for line in scrubbed.split("\r\n")[1:-1]:
        if line.startswith(" "):
            assert line != " ", "a continuation line carries no content"


@pytest.mark.parametrize("name", fixture_names())
def test_scrubbing_twice_changes_nothing(name: str) -> None:
    once, _ = anonymize(read(f"{name}.ics"))
    twice, _ = anonymize(once)
    assert normalise_uids(twice) == normalise_uids(once)
    assert audit(once) == []


@pytest.mark.parametrize("name", fixture_names())
def test_check_reports_an_unscrubbed_calendar(name: str) -> None:
    assert audit(read(f"{name}.ics")), f"{name} should have something to report"


def test_folded_values_are_scrubbed_whole() -> None:
    """A description wrapped over three lines is one value, not three.

    Reading the file a physical line at a time leaves the continuations behind,
    which is how ``SUMMARY:Anonymized Datas et auteurs.`` used to happen.
    """
    scrubbed, _ = anonymize(read("ics-folded-values.ics"))
    values = [line for line in _unfold(scrubbed) if line.startswith(("SUMMARY", "DESCRIPTION", "LOCATION"))]

    assert values == [
        "SUMMARY:Anonymized Data",
        "DESCRIPTION:Anonymized Data",
        "LOCATION:Anonymized Data",
    ]


def test_long_values_are_rewrapped_without_splitting_a_character() -> None:
    calendar = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\n"
        f"X-LONG:{'é' * 200}\r\n"
        "END:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    scrubbed, _ = anonymize(calendar)

    physical = scrubbed.split("\r\n")[:-1]
    assert max(len(line.encode("utf-8")) for line in physical) <= LINE_LIMIT
    assert f"X-LONG:{'é' * 200}" in _unfold(scrubbed)


def test_each_person_keeps_one_stand_in_address() -> None:
    """Three people stay three people, and the chair stays the organizer."""
    scrubbed, report = anonymize(read("ics-identities.ics"))

    assert len(report["people"]) == 3
    addresses = re.findall(r"(?:ORGANIZER|ATTENDEE)[^:]*:mailto:(\S+)", scrubbed)
    assert set(addresses) == {
        "person1@example.com",
        "person2@example.com",
        "person3@example.com",
    }

    # The organizer of the first event also attends it, and is the same person
    # in both lines. Collapsing everyone onto one address would hide that, and
    # collapsing nothing would invent a second person.
    assert "ORGANIZER:mailto:person1@example.com" in scrubbed
    assert "ATTENDEE;ROLE=CHAIR:mailto:person1@example.com" in scrubbed


def test_an_address_written_in_two_cases_is_one_person() -> None:
    scrubbed, report = anonymize(read("ics-identities.ics"))
    assert len(report["people"]) == 3
    assert "ORGANIZER:mailto:person3@example.com" in scrubbed


def test_an_organizer_with_no_address_stays_empty() -> None:
    """Exchange exports ``ORGANIZER:MAILTO:`` with nothing after it.

    Filling that in invents a person who was never there, and an absent
    organizer is exactly the sort of oddity a calendar bug turns on.
    """
    scrubbed, _ = anonymize(read("ics-identities.ics"))
    assert "ORGANIZER:MAILTO:\r\n" in scrubbed
    assert scrubbed.count("ORGANIZER:") == 3


def test_a_repeating_event_and_its_changed_occurrence_share_an_identifier() -> None:
    scrubbed, report = anonymize(read("ics-recurrence-override.ics"))
    uids = [line.removeprefix("UID:") for line in _unfold(scrubbed) if line.startswith("UID:")]

    assert len(uids) == 2
    assert uids[0] == uids[1], "the changed occurrence no longer points at its series"
    assert len(report["uids"]) == 1


def test_identifiers_are_replaced_rather_than_kept() -> None:
    original = read("ics-recurrence-override.ics")
    scrubbed, _ = anonymize(original)
    assert "weekly-standup@example.org" not in scrubbed


def test_a_name_beside_a_blanked_address_is_dropped() -> None:
    """Blanking ORGANIZER while leaving CN="Smith, Jim" scrubs nothing."""
    scrubbed, report = anonymize(read("ics-identifying-params.ics"))

    assert "CN=" not in scrubbed
    assert "SENT-BY=" not in scrubbed
    assert "ALTREP=" not in scrubbed
    assert "X-NOTE=" not in scrubbed
    assert report["parameters"] == 5

    # Parameters that describe rather than identify are kept, because they
    # affect how the entry behaves.
    assert "LANGUAGE=fr" in scrubbed
    assert "RSVP=TRUE" in scrubbed
    assert "PARTSTAT=NEEDS-ACTION" in scrubbed
    assert "TZID=America/Toronto" in scrubbed


def test_attachments_and_links_are_replaced() -> None:
    scrubbed, _ = anonymize(read("ics-attachments-and-urls.ics"))

    assert "CID:contrat-smith.pdf@01DEADBE.EF001122" not in scrubbed
    assert "ATTACH:https://example.com/anonymized" in scrubbed
    # A binary attachment has to stay decodable base64 or the file stops parsing.
    assert "ENCODING=BASE64:QW5vbnltaXplZA==" in scrubbed
    assert "URL:https://example.com/anonymized" in scrubbed
    assert "CATEGORIES:ANONYMIZED" in scrubbed
    assert "GEO:0.0;0.0" in scrubbed
    assert "NAME:Anonymized Calendar" in scrubbed


def test_check_names_what_it_found() -> None:
    findings = audit(read("ics-identities.ics"))
    assert any("jim.smith@example.org" in finding for finding in findings)
    assert any("ORGANIZER" in finding for finding in findings)


def test_lf_only_input_is_accepted() -> None:
    """Not every calendar arrives with the line endings the standard asks for."""
    original = read("ics-folded-values.ics")
    from_lf, _ = anonymize(original.replace("\r\n", "\n"))
    from_crlf, _ = anonymize(original)
    assert normalise_uids(from_lf) == normalise_uids(from_crlf)


def test_an_empty_calendar_is_not_a_crash() -> None:
    scrubbed, report = anonymize("")
    assert scrubbed == "\r\n"
    assert report["properties"] == 0
