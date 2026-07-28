# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Tests for the verdict engine.

Every fixture has a ``.verdict.json`` companion as well as an
``.expected.json`` one. They are kept separate deliberately: ``.expected.json``
is the *parsing* contract and ``.verdict.json`` the *judgement* contract, and
the two change for unrelated reasons -- a new provider in ``settings.json``
rewrites every verdict while leaving parsing untouched. Both are the shared
contract the JavaScript implementation will be asserted against.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from troubleshooting_info import parse  # noqa: E402
from verdicts import (  # noqa: E402
    FAIL,
    NOT_APPLICABLE,
    PASS,
    UNKNOWN,
    WARN,
    check,
    load_settings,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def fixture_names() -> list[str]:
    return sorted(path.stem for path in FIXTURES.glob("*.txt"))


def verdict_for(name: str) -> dict:
    text = (FIXTURES / f"{name}.txt").read_text(encoding="utf-8")
    return check(parse(text), load_settings())


@pytest.mark.parametrize("name", fixture_names())
def test_matches_expected_verdict(name: str) -> None:
    expected = json.loads((FIXTURES / f"{name}.verdict.json").read_text(encoding="utf-8"))
    assert verdict_for(name) == expected


def test_correct_thundermail_account_passes() -> None:
    result = verdict_for("thundermail-correct")
    assert [account["outcome"] for account in result["accounts"]] == [PASS]


def test_autoconfigured_smtp_on_587_is_not_a_failure() -> None:
    """The false failure ``settings.json`` exists to prevent.

    Thundermail publishes ``_submission._tcp`` -> 587, so every autoconfigured
    account lands on 587/STARTTLS while the vendor UI documents 465/SSL.
    """
    result = verdict_for("tb153-macos-names-hidden")
    smtp_ports = {
        server["checks"][0]["actual"]["port"]
        for account in result["accounts"]
        for server in account["servers"]
        if server["role"] == "outgoing" and account["provider"]
    }
    assert {587, 465} <= smtp_ports

    thundermail = [
        account
        for account in result["accounts"]
        if account["provider"] and account["provider"]["id"] == "thundermail"
    ]
    assert thundermail
    assert all(account["outcome"] == PASS for account in thundermail)


def test_pop_reports_unsupported_rather_than_wrong_settings() -> None:
    """"There are no correct POP settings" is the point.

    A per-field comparison would invent expected values for a service that does
    not exist.
    """
    result = verdict_for("thundermail-pop3-plain")
    incoming = result["accounts"][0]["servers"][0]
    protocol_checks = [c for c in incoming["checks"] if c["check"] == "protocol"]

    assert len(protocol_checks) == 1
    assert protocol_checks[0]["outcome"] == FAIL
    assert "IMAP" in protocol_checks[0]["message"]
    assert not [c for c in incoming["checks"] if c["check"] == "server"]


def test_cleartext_over_plain_socket_fails_even_for_pop() -> None:
    result = verdict_for("thundermail-pop3-plain")
    incoming = result["accounts"][0]["servers"][0]
    rules = [c for c in incoming["checks"] if c["check"] == "rule"]

    assert [rule["rule"] for rule in rules] == ["cleartext-over-plain-socket"]
    assert rules[0]["outcome"] == FAIL


def test_local_folders_is_not_judged() -> None:
    """Local Folders is (plain, passwordCleartext) and touches no network.

    Running the cleartext-over-plain rule on it would report a security defect
    on a mailbox stored on the user's own disk.
    """
    result = verdict_for("tb153-macos-names-hidden")
    local = [
        account
        for account in result["accounts"]
        if account["outcome"] == NOT_APPLICABLE
    ]

    assert len(local) == 1
    assert local[0]["servers"] == []
    assert any("this computer" in note for note in local[0]["notes"])


def test_unknown_provider_is_not_reported_as_correct() -> None:
    """Gmail is not catalogued yet, and saying "looks fine" would be a lie."""
    result = verdict_for("tb153-windows-gmail")
    gmail = result["accounts"][0]

    assert gmail["provider"] is None
    assert gmail["outcome"] == UNKNOWN
    assert all(
        check_["outcome"] == UNKNOWN
        for server in gmail["servers"]
        for check_ in server["checks"]
    )


def test_unreadable_account_is_reported_not_skipped() -> None:
    result = verdict_for("account-read-error")
    broken = result["accounts"][1]

    assert broken["outcome"] == UNKNOWN
    assert broken["servers"][0]["checks"][0]["remediation"]


def test_app_password_warns_rather_than_fails() -> None:
    """A working app-password account must not be reported as broken."""
    result = verdict_for("thundermail-private-shown")
    account = result["accounts"][0]
    second_outgoing = account["servers"][2]

    assert account["outcome"] == WARN
    assert second_outgoing["outcome"] == WARN


def test_several_outgoing_servers_are_distinguishable() -> None:
    """Identical host and port, and the identity names are discarded."""
    result = verdict_for("thundermail-private-shown")
    outgoing = [s for s in result["accounts"][0]["servers"] if s["role"] == "outgoing"]

    assert len(outgoing) == 2
    assert outgoing[0]["label"] == outgoing[1]["label"]
    assert [server["ordinal"] for server in outgoing] == [1, 2]


def test_verdicts_are_per_account_never_rolled_up() -> None:
    """One abandoned mailbox must not condemn the others."""
    result = verdict_for("account-read-error")

    assert "outcome" not in result
    assert [account["outcome"] for account in result["accounts"]] == [PASS, UNKNOWN]


def test_account_selection_by_key_and_by_position() -> None:
    by_key = check(
        parse((FIXTURES / "tb153-macos-names-hidden.txt").read_text(encoding="utf-8")),
        load_settings(),
        account="account6",
    )
    by_position = check(
        parse((FIXTURES / "tb153-macos-names-hidden.txt").read_text(encoding="utf-8")),
        load_settings(),
        account="3",
    )

    assert len(by_key["accounts"]) == 1
    assert by_key == by_position


def test_unmatched_selection_falls_back_to_showing_everything() -> None:
    """Silently reporting nothing would look like a clean bill of health."""
    result = check(
        parse((FIXTURES / "tb153-macos-names-hidden.txt").read_text(encoding="utf-8")),
        load_settings(),
        account="account99",
    )

    assert len(result["accounts"]) == 4
    assert any("account99" in warning for warning in result["warnings"])


def test_no_pii_in_verdicts() -> None:
    """The verdict layer must not reintroduce what the parser discarded."""
    text = (FIXTURES / "thundermail-private-shown.txt").read_text(encoding="utf-8")
    rendered = json.dumps(check(parse(text), load_settings()))

    for secret in ("tester@example.com", "tester+lists@example.com", "Work, Personal"):
        assert secret not in rendered


def test_known_issue_explains_a_mismatch_rather_than_replacing_it() -> None:
    """The generic check says what is expected; the catalogue says what is wrong.

    Both are useful, so both appear. 465 with STARTTLS is a combination that
    cannot connect at all, which "expected 587/STARTTLS or 465/SSL" does not
    convey on its own.
    """
    result = verdict_for("thundermail-smtp-465-starttls")
    outgoing = result["accounts"][0]["servers"][1]
    kinds = [entry["check"] for entry in outgoing["checks"]]

    assert "server" in kinds
    assert "knownIssue" in kinds

    issue = next(e for e in outgoing["checks"] if e["check"] == "knownIssue")
    assert issue["issue"] == "implicit-tls-port-with-starttls"
    assert issue["outcome"] == FAIL
    assert issue["observed"] is False


def test_catalogue_catches_what_provider_detection_cannot() -> None:
    """A guessed hostname defeats provider matching, which is when it matters.

    Without the catalogue this account reports "not checked" -- the least
    useful possible answer for someone who has typed the wrong server name.
    """
    account = verdict_for("thundermail-guessed-hostnames")["accounts"][0]

    assert account["provider"] is None
    assert account["outcome"] == FAIL

    for server in account["servers"]:
        issues = [e for e in server["checks"] if e["check"] == "knownIssue"]
        assert [issue["issue"] for issue in issues] == ["guessed-thundermail-hostname"]
        assert "mail.thundermail.com" in issues[0]["remediation"]


def test_correct_configurations_trigger_no_catalogue_entries() -> None:
    """A catalogue that fires on working accounts is worse than no catalogue."""
    for name in ("thundermail-correct", "tb153-macos-names-hidden", "tb153-windows-gmail"):
        for account in verdict_for(name)["accounts"]:
            for server in account["servers"]:
                assert not [e for e in server["checks"] if e["check"] == "knownIssue"], name
