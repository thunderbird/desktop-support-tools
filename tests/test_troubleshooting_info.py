# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Tests for the troubleshooting-information parser.

The fixture files and their ``.expected.json`` companions are the shared
contract between the Python CLI and the JavaScript webapp -- the webapp's parser
will be asserted against these same files, which is what keeps the two
implementations from drifting apart.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from troubleshooting_info import parse  # noqa: E402

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

# Values that would identify a person, as they appear in the fixtures. None of
# these may survive parsing.
#
# "Local Folders" is deliberately absent: it is the Local Folders account's
# hostName, which lives in the public hostDetails field and is a fixed
# Thunderbird string rather than anything identifying. It legitimately survives.
PII_STRINGS = (
    "tester@example.com",
    "tester+lists@example.com",
    "Work, Personal",
)


def fixture_names() -> list[str]:
    return sorted(path.stem for path in FIXTURES.glob("*.txt"))


@pytest.mark.parametrize("name", fixture_names())
def test_matches_expected(name: str) -> None:
    text = (FIXTURES / f"{name}.txt").read_text(encoding="utf-8")
    expected = json.loads((FIXTURES / f"{name}.expected.json").read_text(encoding="utf-8"))
    assert parse(text) == expected


@pytest.mark.parametrize("name", fixture_names())
def test_crlf_input_parses_identically(name: str) -> None:
    """Windows builds copy with CRLF line endings."""
    text = (FIXTURES / f"{name}.txt").read_text(encoding="utf-8")
    assert parse(text.replace("\n", "\r\n")) == parse(text)


def test_no_pii_in_output() -> None:
    """Private fields must never reach the output, even when pasted."""
    text = (FIXTURES / "thundermail-private-shown.txt").read_text(encoding="utf-8")
    result = parse(text)
    assert result["input"]["privateDataShown"] is True

    serialised = json.dumps(result)
    for pii in PII_STRINGS:
        assert pii not in serialised, f"{pii!r} leaked into parser output"


def test_comma_in_account_name_does_not_shift_fields() -> None:
    """An account named "Work, Personal" must not push the real fields along."""
    incoming = parse(
        "  account1:\n"
        "    INCOMING: account1, Work, Personal, (imap) mail.thundermail.com:993, SSL, OAuth2\n"
    )["accounts"][0]["incoming"]

    assert incoming["host"] == "mail.thundermail.com"
    assert incoming["port"] == 993
    assert incoming["socketType"] == "SSL"
    assert incoming["authMethod"] == "OAuth2"
    assert incoming["warnings"] == []


def test_fragment_starting_with_outgoing_is_kept() -> None:
    """A paste that begins mid-account still yields its SMTP settings."""
    result = parse("    OUTGOING: , mail.thundermail.com:465, SSL, OAuth2, true\n")

    assert result["accounts"][0]["key"] is None
    assert result["accounts"][0]["incoming"] is None
    assert result["accounts"][0]["outgoing"][0]["port"] == 465


def test_unrelated_label_line_is_not_read_as_an_account_key() -> None:
    """Only the line directly above INCOMING can be the account key."""
    result = parse(
        "Some Section:\n"
        "\n"
        "    INCOMING: account1, , (imap) mail.thundermail.com:993, SSL, OAuth2\n"
    )
    assert result["accounts"][0]["key"] is None


def test_missing_version_is_reported_for_full_dumps() -> None:
    """Localised dumps parse accounts fine but yield no version, and say so."""
    result = parse(
        "Grundlagen der Anwendung\n"
        "\n"
        "  Version des Programms: 140.2.1\n"
        "\n"
        "  account1:\n"
        "    INCOMING: account1, , (imap) mail.thundermail.com:993, SSL, OAuth2\n"
    )

    assert result["app"] == {}
    assert any("could not determine Thunderbird version" in w for w in result["warnings"])
    assert result["accounts"][0]["incoming"]["host"] == "mail.thundermail.com"


def test_empty_input_reports_nothing_found() -> None:
    result = parse("")
    assert result["accounts"] == []
    assert result["warnings"] == ["no mail account information found in this input"]


def test_including_account_names_does_not_change_the_settings() -> None:
    """The privacy property that matters: PII cannot alter the verdict.

    Same profile, same Thunderbird, the "Include account names" checkbox the only
    difference. The account settings must come out byte-identical.
    """
    included = parse((FIXTURES / "tb153-macos-names-included.txt").read_text(encoding="utf-8"))
    hidden = parse((FIXTURES / "tb153-macos-names-hidden.txt").read_text(encoding="utf-8"))

    assert included["accounts"] == hidden["accounts"]
    assert included["input"]["privateDataShown"] is True
    assert hidden["input"]["privateDataShown"] is False


def test_numeric_enums_are_decoded() -> None:
    """Thunderbird 153 writes raw integers rather than names."""
    incoming = parse(
        "  account1:\n"
        "    INCOMING: account1, , (imap) mail.thundermail.com:993, 3, 10\n"
    )["accounts"][0]["incoming"]

    assert incoming["socketType"] == "SSL"
    assert incoming["authMethod"] == "OAuth2"
    assert incoming["warnings"] == []


def test_numeric_enums_survive_a_comma_in_the_identity_name() -> None:
    """Both real-world quirks at once: integers and a comma inside the PII field."""
    outgoing = parse(
        "  account1:\n"
        "    OUTGOING: Tanglao, Roland <someone@example.com>, "
        "mail.thundermail.com:465, 3, 10, true\n"
    )["accounts"][0]["outgoing"][0]

    assert outgoing["host"] == "mail.thundermail.com"
    assert outgoing["port"] == 465
    assert outgoing["socketType"] == "SSL"
    assert outgoing["authMethod"] == "OAuth2"
    assert outgoing["isDefault"] is True
    assert outgoing["warnings"] == []


def test_out_of_range_enum_value_is_reported() -> None:
    incoming = parse(
        "    INCOMING: account1, , (imap) mail.thundermail.com:993, 99, 10\n"
    )["accounts"][0]["incoming"]

    assert incoming["socketType"] == "99"
    assert incoming["warnings"] == ["unrecognised socketType value: '99'"]


def test_colon_bearing_noise_sections_are_ignored() -> None:
    """Later sections are full of "Label: value" lines that must not interfere."""
    base = (FIXTURES / "tb153-macos-names-hidden.txt").read_text(encoding="utf-8")
    noise = (
        "\n  Environment Variables\n\n"
        "    DISPLAY: /var/run/com.apple.launchd.yfbkZAUzlW/org.xquartz:0\n"
        "    XRE_BINARY_PATH:\n"
        "\n  Important Modified Preferences\n\n"
        "    extensions.lastAppVersion: 153.0\n"
        "\n  Graphics\n\n"
        "      Display0: 7680x3200@0Hz scales:2.000000|2.000000 SDR\n"
    )

    assert parse(base + noise) == parse(base)
