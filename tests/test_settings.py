# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Structural tests for ``settings.json``.

``settings.json`` is the single source of truth both front-ends read, so its
contents are compared against parser *output*. A ``socketType`` spelled
``ssl`` instead of ``SSL`` would not raise anything -- it would simply never
match, and every account would be reported as misconfigured. These tests make
that class of typo fail the suite instead.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from troubleshooting_info import AUTH_METHODS, SOCKET_TYPES  # noqa: E402

SETTINGS_PATH = Path(__file__).resolve().parent.parent / "settings.json"
VERDICTS = {"pass", "warn", "fail"}
DIRECTIONS = {"incoming", "outgoing"}


@pytest.fixture(scope="module")
def settings() -> dict:
    return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))


def keys(obj: dict) -> set[str]:
    """Real keys, minus the ``$``-prefixed documentation ones.

    JSON has no comment syntax, so ``settings.json`` carries its reasoning in
    ``$comment`` keys. Consumers must skip them; so must these tests.
    """
    return {key for key in obj if not key.startswith("$")}


def supported_protocols(settings: dict):
    """Yield (provider id, protocol name, protocol body) for supported protocols."""
    for provider in settings["providers"]:
        for name, protocol in provider["protocols"].items():
            if protocol["supported"]:
                yield provider["id"], name, protocol


def unsupported_protocols(settings: dict):
    for provider in settings["providers"]:
        for name, protocol in provider["protocols"].items():
            if not protocol["supported"]:
                yield provider["id"], name, protocol


def test_provider_ids_are_unique(settings: dict) -> None:
    ids = [provider["id"] for provider in settings["providers"]]
    assert len(ids) == len(set(ids))


def test_socket_types_are_spellings_the_parser_emits(settings: dict) -> None:
    for provider_id, name, protocol in supported_protocols(settings):
        for server in protocol["servers"]:
            assert server["socketType"] in SOCKET_TYPES, (
                f"{provider_id}/{name}: {server['socketType']!r} is not a "
                f"socketType the parser can produce"
            )


def test_auth_methods_are_spellings_the_parser_emits(settings: dict) -> None:
    for provider_id, name, protocol in supported_protocols(settings):
        auth = protocol["authMethods"]
        for method in auth["accepted"]:
            assert method in AUTH_METHODS, (
                f"{provider_id}/{name}: {method!r} is not an authMethod the "
                f"parser can produce"
            )
        assert auth["recommended"] in auth["accepted"]


def test_rules_reference_real_vocabulary(settings: dict) -> None:
    for rule in settings["rules"]:
        when = rule["when"]
        if "socketType" in when:
            assert when["socketType"] in SOCKET_TYPES
        if "authMethod" in when:
            assert when["authMethod"] in AUTH_METHODS
        assert rule["verdict"] in VERDICTS


def test_ui_labels_cover_the_whole_vocabulary(settings: dict) -> None:
    """A new enum value in the parser must not leave the UI unclassified.

    Remediation names the value to select in Thunderbird, so an unlabelled
    value would mean telling someone to pick a raw internal name. A ``null``
    label is a deliberate classification, not a gap: it records that no
    dropdown offers the choice at all, so remediation must steer to a
    different method instead of naming this one.
    """
    socket_labels = settings["ui"]["socketTypeLabels"]
    choices = settings["ui"]["authMethodChoices"]

    assert keys(socket_labels) == set(SOCKET_TYPES)
    assert keys(choices) == set(AUTH_METHODS)

    for label in (socket_labels[key] for key in keys(socket_labels)):
        assert isinstance(label, str) and label.strip()

    for name in keys(choices):
        choice = choices[name]
        offered = choice["offeredIn"]
        assert set(offered) <= DIRECTIONS
        if choice["label"] is None:
            assert offered == [], f"{name}: unlabelled but marked as offered"
        else:
            assert choice["label"].strip()
            assert offered, f"{name}: labelled but offered in no dialog"


def test_every_protocol_names_a_dialog(settings: dict) -> None:
    """``direction`` selects which Account Settings dialog remediation names."""
    locations = settings["ui"]["locations"]
    assert keys(locations) == DIRECTIONS

    for provider in settings["providers"]:
        for name, protocol in provider["protocols"].items():
            assert protocol["direction"] in DIRECTIONS, name


def test_accepted_auth_methods_exist_in_that_dialog(settings: dict) -> None:
    """A method must be selectable *in the dialog remediation will name*.

    The two dropdowns are not the same list: incoming offers ``TLS
    Certificate`` and no ``No authentication``, the SMTP Server dialog the
    reverse. Accepting a method absent from the relevant dialog would produce
    remediation telling someone to choose something that is not in the menu.
    """
    choices = settings["ui"]["authMethodChoices"]
    for provider_id, name, protocol in supported_protocols(settings):
        direction = protocol["direction"]
        for method in protocol["authMethods"]["accepted"]:
            assert direction in choices[method]["offeredIn"], (
                f"{provider_id}/{name} accepts {method!r}, which the "
                f"{direction} dropdown does not offer"
            )


def test_verdicts_are_known_levels(settings: dict) -> None:
    assert keys(settings["verdicts"]) == VERDICTS

    for _, _, protocol in supported_protocols(settings):
        auth = protocol["authMethods"]
        for entry in auth["accepted"].values():
            assert entry["verdict"] in VERDICTS
        assert auth["otherwise"]["verdict"] in VERDICTS

    for _, _, protocol in unsupported_protocols(settings):
        assert protocol["verdict"] in VERDICTS


def test_ports_are_plausible(settings: dict) -> None:
    for _, _, protocol in supported_protocols(settings):
        for server in protocol["servers"]:
            port = server["port"]
            assert isinstance(port, int) and not isinstance(port, bool)
            assert 1 <= port <= 65535


def test_match_hosts_are_lowercase(settings: dict) -> None:
    """Hosts are compared against parsed hostnames case-insensitively."""
    for provider in settings["providers"]:
        hosts = provider["match"]["hosts"]
        assert hosts
        assert all(host == host.lower() for host in hosts)


def test_every_server_carries_provenance(settings: dict) -> None:
    """CLAUDE.md's 'flag unverified inferences' rule, enforced."""
    for _, _, protocol in supported_protocols(settings):
        for server in protocol["servers"]:
            assert server["provenance"].strip()


def test_unsupported_protocols_explain_themselves(settings: dict) -> None:
    """"POP isn't supported" needs to say what to do instead, not just fail."""
    for _, _, protocol in unsupported_protocols(settings):
        assert protocol["message"].strip()
        assert protocol["remediation"].strip()
        assert "servers" not in protocol


def test_at_most_one_preferred_server_per_protocol(settings: dict) -> None:
    for _, _, protocol in supported_protocols(settings):
        preferred = [
            server
            for server in protocol["servers"]
            if server.get("preferredForRemediation")
        ]
        assert len(preferred) <= 1


def test_thundermail_smtp_accepts_both_587_and_465(settings: dict) -> None:
    """The specific false-failure this schema exists to prevent.

    Thundermail publishes ``_submission._tcp`` -> 587, so autoconfigured
    accounts land on 587/alwaysSTARTTLS, while the Thundermail UI tells users
    to type 465/SSL. Encoding only one would report every account configured
    the other way as broken.
    """
    thundermail = next(
        provider
        for provider in settings["providers"]
        if provider["id"] == "thundermail"
    )
    pairs = {
        (server["port"], server["socketType"])
        for server in thundermail["protocols"]["smtp"]["servers"]
    }
    assert (587, "alwaysSTARTTLS") in pairs
    assert (465, "SSL") in pairs


def test_thundermail_does_not_offer_pop_or_jmap(settings: dict) -> None:
    thundermail = next(
        provider
        for provider in settings["providers"]
        if provider["id"] == "thundermail"
    )
    assert thundermail["protocols"]["pop3"]["supported"] is False
    assert thundermail["protocols"]["jmap"]["supported"] is False
