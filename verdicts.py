# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Judge parsed account settings against ``settings.json``.

Takes the output of :func:`troubleshooting_info.parse` and produces one verdict
*per account*. Never an overall verdict: a dump routinely contains accounts the
user abandoned years ago, and nothing in it marks an account as dead, so a
single roll-up would tell someone to fix a mailbox they no longer use.

Outcomes extend the three severity levels in ``settings.json`` with two that
describe the *absence* of a judgement rather than its result:

``pass`` / ``warn`` / ``fail``
    A provider we catalogue, checked. ``warn`` exists so that a working
    app-password account is not reported as broken.
``unknown``
    No expected settings are catalogued for this host, or Thunderbird could not
    read the account. Not a fault, and not a pass either -- saying "looks fine"
    about a provider we have never verified is exactly the confidently-worded
    wrong answer CLAUDE.md warns about.
``notApplicable``
    Local Folders and friends. Not a mail server, nothing to check.

Provider detection is by incoming hostname, never by email address. That needs
no input from the user, still works for custom domains -- the RFC 6186 SRV
target is the same host -- and avoids ever asking someone to paste an address.
"""

from __future__ import annotations

import json
from pathlib import Path

SETTINGS_PATH = Path(__file__).resolve().parent / "settings.json"

PASS = "pass"
WARN = "warn"
FAIL = "fail"
UNKNOWN = "unknown"
NOT_APPLICABLE = "notApplicable"

# Worse outcomes win when rolling several checks up into one account verdict.
_SEVERITY = {PASS: 0, WARN: 1, FAIL: 2}

# Thunderbird's own pseudo-account protocol for Local Folders. It has no server,
# so every network-facing check is meaningless for it -- including the global
# cleartext-over-plain rule, which would otherwise fire on Local Folders'
# (plain, passwordCleartext) and report a security defect on a local mailbox.
_LOCAL_PROTOCOL = "none"

_OUTGOING_PROTOCOL = "smtp"


def load_settings(path: Path | str | None = None) -> dict:
    """Load ``settings.json``, the source of truth both front-ends share."""
    return json.loads(Path(path or SETTINGS_PATH).read_text(encoding="utf-8"))


def _real_keys(obj: dict):
    """Keys minus the ``$``-prefixed documentation ones. See settings.json."""
    return (key for key in obj if not key.startswith("$"))


def _worst(outcomes) -> str | None:
    ranked = [o for o in outcomes if o in _SEVERITY]
    if not ranked:
        return None
    return max(ranked, key=lambda o: _SEVERITY[o])


def _socket_label(settings: dict, value: str | None) -> str | None:
    if value is None:
        return None
    return settings["ui"]["socketTypeLabels"].get(value, value)


def _auth_label(settings: dict, value: str | None) -> str | None:
    if value is None:
        return None
    choice = settings["ui"]["authMethodChoices"].get(value)
    if choice is None:
        return value
    return choice["label"]


def _describe_server(protocol: str | None, host: str | None, port: int | None) -> str:
    """Identify a server to a human.

    Accounts are identified by protocol, host and port -- never by key, which
    appears nowhere in Thunderbird's UI, and never by name, which this tool
    discards.
    """
    where = host or "(server unknown)"
    if port is not None:
        where = f"{where}:{port}"
    return f"{protocol or '?'} {where}"


def _find_provider(settings: dict, *hosts: str | None) -> dict | None:
    """Match the first host that any catalogued provider claims."""
    for host in hosts:
        if not host:
            continue
        needle = host.strip().lower()
        for provider in settings["providers"]:
            if needle in {h.lower() for h in provider["match"]["hosts"]}:
                return provider
    return None


def _expected_summary(settings: dict, servers: list[dict]) -> str:
    """Render acceptable (port, socketType) pairs as prose.

    Rendered as alternatives because several can be correct at once: Thundermail
    SMTP is valid on both 587/STARTTLS and 465/SSL, and calling either one wrong
    would misdiagnose every account configured the other way.
    """
    parts = [
        f"{server['host']}:{server['port']} with "
        f"{settings['ui']['fieldLabels']['socketType']} "
        f"{_socket_label(settings, server['socketType'])}"
        for server in servers
    ]
    if len(parts) == 1:
        return parts[0]
    return " — or — ".join(parts)


def _preferred_server(servers: list[dict]) -> dict:
    for server in servers:
        if server.get("preferredForRemediation"):
            return server
    return servers[0]


def _check_server_settings(
    settings: dict, protocol_settings: dict, host, port, socket_type
) -> dict:
    """Judge (host, port, socketType) as a unit, not field by field."""
    servers = protocol_settings["servers"]
    direction = protocol_settings["direction"]
    location = settings["ui"]["locations"][direction]

    actual = {
        "host": host,
        "port": port,
        "socketType": socket_type,
        "socketTypeLabel": _socket_label(settings, socket_type),
    }

    matched = next(
        (
            server
            for server in servers
            if host
            and host.lower() == server["host"].lower()
            and port == server["port"]
            and socket_type == server["socketType"]
        ),
        None,
    )

    if matched is not None:
        return {
            "check": "server",
            "outcome": PASS,
            "actual": actual,
            "message": "Server, port and connection security are correct.",
            "provenance": matched["provenance"],
        }

    # A dump omits the port entirely when it is -1, meaning "use the default for
    # this connection security". We cannot tell which port that resolves to, so
    # this is reported rather than judged.
    if port is None:
        return {
            "check": "server",
            "outcome": UNKNOWN,
            "actual": actual,
            "message": (
                "This account has no explicit port, so Thunderbird is using the "
                "default for its connection security. The dump does not say "
                "which port that is."
            ),
            "expected": _expected_summary(settings, servers),
        }

    preferred = _preferred_server(servers)
    return {
        "check": "server",
        "outcome": FAIL,
        "actual": actual,
        "expected": _expected_summary(settings, servers),
        "message": (
            f"Expected {_expected_summary(settings, servers)}, "
            f"but this account has "
            f"{_describe_server(None, host, port)} with "
            f"{settings['ui']['fieldLabels']['socketType']} "
            f"{_socket_label(settings, socket_type)}."
        ),
        "remediation": (
            f"In {location}, set "
            f"{settings['ui']['fieldLabels']['host']} to {preferred['host']}, "
            f"{settings['ui']['fieldLabels']['port']} to {preferred['port']}, and "
            f"{settings['ui']['fieldLabels']['socketType']} to "
            f"{_socket_label(settings, preferred['socketType'])}."
        ),
        "provenance": preferred["provenance"],
    }


def _check_auth_method(settings: dict, protocol_settings: dict, auth_method) -> dict:
    auth = protocol_settings["authMethods"]
    direction = protocol_settings["direction"]
    location = settings["ui"]["locations"][direction]
    recommended = auth["recommended"]

    actual = {
        "authMethod": auth_method,
        "authMethodLabel": _auth_label(settings, auth_method),
    }

    if auth_method is None:
        return {
            "check": "authMethod",
            "outcome": UNKNOWN,
            "actual": actual,
            "message": "The dump does not show an authentication method for this server.",
        }

    accepted = auth["accepted"].get(auth_method)
    entry = accepted if accepted is not None else auth["otherwise"]
    outcome = entry["verdict"]

    result = {
        "check": "authMethod",
        "outcome": outcome,
        "actual": actual,
        "message": entry["note"],
    }
    if outcome != PASS:
        result["remediation"] = (
            f"In {location}, set "
            f"{settings['ui']['fieldLabels']['authMethod']} to "
            f"{_auth_label(settings, recommended)}."
        )
    return result


def _check_rules(settings: dict, socket_type, auth_method) -> list[dict]:
    """Provider-independent rules, applied even when the provider is unknown.

    A cleartext password over a plain socket is a defect whoever the provider
    is, so this runs for hosts we have no catalogue entry for.
    """
    fired = []
    for rule in settings["rules"]:
        when = rule["when"]
        if when.get("socketType", socket_type) != socket_type:
            continue
        if when.get("authMethod", auth_method) != auth_method:
            continue
        fired.append(
            {
                "check": "rule",
                "rule": rule["id"],
                "outcome": rule["verdict"],
                "message": rule["message"],
                "remediation": rule["remediation"],
            }
        )
    return fired


def _check_one_server(
    settings: dict, provider: dict | None, protocol: str, record: dict, role: str
) -> dict:
    """Judge one incoming or outgoing server."""
    host = record.get("host")
    port = record.get("port")
    socket_type = record.get("socketType")
    auth_method = record.get("authMethod")

    result = {
        "role": role,
        "protocol": protocol,
        "label": _describe_server(protocol, host, port),
        "checks": [],
    }

    if provider is None:
        known = ", ".join(p["displayName"] for p in settings["providers"])
        result["checks"].append(
            {
                "check": "provider",
                "outcome": UNKNOWN,
                "message": (
                    f"No expected settings are catalogued for "
                    f"{host or 'this server'}. Covered so far: {known}."
                ),
            }
        )
        # Rules are provider-independent and run even here: a cleartext
        # password over a plain socket is a defect whoever the provider is.
        result["checks"].extend(_check_rules(settings, socket_type, auth_method))
        result["outcome"] = _worst(c["outcome"] for c in result["checks"]) or UNKNOWN
        return result

    protocol_settings = provider["protocols"].get(protocol)

    if protocol_settings is None:
        result["checks"].append(
            {
                "check": "protocol",
                "outcome": UNKNOWN,
                "message": (
                    f"{provider['displayName']} has no catalogued settings for "
                    f"{protocol}."
                ),
            }
        )
    elif not protocol_settings["supported"]:
        # No correct settings exist to compare against, so per-field mismatches
        # would be nonsense. Say what the user should do instead.
        result["checks"].append(
            {
                "check": "protocol",
                "outcome": protocol_settings["verdict"],
                "message": protocol_settings["message"],
                "remediation": protocol_settings["remediation"],
                "provenance": protocol_settings["provenance"],
            }
        )
    else:
        result["checks"].append(
            _check_server_settings(
                settings, protocol_settings, host, port, socket_type
            )
        )
        result["checks"].append(
            _check_auth_method(settings, protocol_settings, auth_method)
        )

    # Rules come last so the provider-specific finding leads: "POP isn't
    # supported" is the headline, "and the password is in the clear" the
    # supporting detail, not the other way round.
    result["checks"].extend(_check_rules(settings, socket_type, auth_method))

    result["outcome"] = _worst(c["outcome"] for c in result["checks"]) or UNKNOWN
    return result


def check_account(settings: dict, account: dict, position: int) -> dict:
    """Judge one parsed account."""
    incoming = account.get("incoming")
    outgoing = account.get("outgoing") or []

    result = {
        "position": position,
        "key": account.get("key"),
        "provider": None,
        "servers": [],
        "notes": [],
    }

    incoming_protocol = incoming.get("protocol") if incoming else None
    incoming_host = incoming.get("host") if incoming else None

    if incoming is not None:
        result["notes"].extend(incoming.get("warnings", []))

    # Local Folders is not a mail server. Judging it would fire the
    # cleartext-over-plain rule on a mailbox that never touches the network.
    if incoming_protocol == _LOCAL_PROTOCOL and not outgoing:
        result["outcome"] = NOT_APPLICABLE
        result["label"] = _describe_server(incoming_protocol, incoming_host, None)
        result["notes"].append(
            "Local Folders is stored on this computer and has no server settings to check."
        )
        return result

    outgoing_host = next((o.get("host") for o in outgoing if o.get("host")), None)
    provider = _find_provider(settings, incoming_host, outgoing_host)
    if provider is not None:
        result["provider"] = {
            "id": provider["id"],
            "displayName": provider["displayName"],
            "verified": provider["verified"],
        }

    if incoming is not None and incoming_protocol is not None:
        result["servers"].append(
            _check_one_server(settings, provider, incoming_protocol, incoming, "incoming")
        )
    elif incoming is not None:
        # accounts.js emits a placeholder record when reading the incoming
        # server throws. That is a finding about the account, not a parse error.
        result["servers"].append(
            {
                "role": "incoming",
                "protocol": None,
                "label": "incoming server unreadable",
                "outcome": UNKNOWN,
                "checks": [
                    {
                        "check": "account",
                        "outcome": UNKNOWN,
                        "message": (
                            "Thunderbird could not read this account's incoming "
                            "server, so there are no settings to check. That is "
                            "a fault in the account itself rather than in its "
                            "configuration."
                        ),
                        "remediation": (
                            "Quit Thunderbird, reopen it, and copy the "
                            "troubleshooting information again. If the account "
                            "is still listed like this, it is damaged and worth "
                            "removing and adding again."
                        ),
                    }
                ],
            }
        )

    for ordinal, record in enumerate(outgoing, start=1):
        checked = _check_one_server(
            settings, provider, _OUTGOING_PROTOCOL, record, "outgoing"
        )
        if len(outgoing) > 1:
            # An account with several identities has several outgoing servers,
            # and they are routinely the same host and port. The identity name
            # that would tell them apart is private and discarded, so order is
            # the only handle -- and without it a warning on the second one
            # reads as if it were about the first.
            checked["ordinal"] = ordinal
        result["servers"].append(checked)

    if len(outgoing) > 1:
        result["notes"].append(
            f"This account has {len(outgoing)} outgoing servers, listed in the "
            f"order Thunderbird reports them. They are identified by number "
            f"because the identity names that would distinguish them are "
            f"private, and this tool discards them."
        )

    result["label"] = (
        result["servers"][0]["label"] if result["servers"] else "no servers"
    )
    result["outcome"] = (
        _worst(server["outcome"] for server in result["servers"])
        or (UNKNOWN if result["servers"] else NOT_APPLICABLE)
    )
    return result


def check(parsed: dict, settings: dict | None = None, account: str | None = None) -> dict:
    """Judge every account in a parsed dump.

    ``account`` optionally selects one, by key (``account3``) or by 1-based
    position in the dump. Which account to check is an input, not an inference:
    when several match a provider, guessing wrong is worse than reporting both.
    """
    settings = settings if settings is not None else load_settings()

    results = [
        check_account(settings, parsed_account, position)
        for position, parsed_account in enumerate(parsed.get("accounts", []), start=1)
    ]

    warnings = list(parsed.get("warnings", []))

    if account is not None:
        selected = [
            r
            for r in results
            if r["key"] == account or str(r["position"]) == str(account)
        ]
        if not selected:
            warnings.append(f"no account matched {account!r}; showing all accounts")
        else:
            results = selected

    return {
        "input": parsed.get("input", {}),
        "app": parsed.get("app", {}),
        "accounts": results,
        "warnings": warnings,
    }
