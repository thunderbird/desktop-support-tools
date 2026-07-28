# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Parse Thunderbird Desktop Troubleshooting Information into account records.

Thunderbird's ``about:support`` page (Help -> Troubleshooting Information) has
only a "Copy text to clipboard" button -- the JSON "Copy raw data to clipboard"
button that Firefox has is commented out in comm-central, marked "Not used on
TB". So text is the only input format available to us.

The Accounts section is serialised by ``getAccountsText()`` in
``mail/components/about-support/content/accounts.js``, which emits::

    account1:
      INCOMING: account1, , (imap) mail.thundermail.com:993, 3, 10
      OUTGOING: , mail.thundermail.com:465, 3, 10, true

Both line types have a *fixed* field count, and in each only one field is
free-form text that may itself contain a comma -- ``name`` for INCOMING and
``identityName`` for OUTGOING. Both of those are exactly the fields Thunderbird
marks private, and both are the ones we throw away. So we parse positionally
from whichever end is fixed and let the free-form field absorb the slack. No
guessing required.

Fields are joined with ", " but we split on "," and strip, because ``export.js``
runs ``text.replace(/[ \\t]+\\n/g, "\\n")`` over the whole page. A line whose
last field is empty therefore ends in "," rather than ", ", and splitting on
", " would yield one field too few and shift every field along.

Privacy: ``hostDetails``, ``socketType`` and ``authMethod`` are public in
Thunderbird's own sense -- they survive the "Show private data" checkbox being
off -- so everything needed to judge a configuration is always present. The two
private fields are discarded unconditionally, whether or not the user pasted
them, so no personally identifying data is ever returned by this module.
"""

from __future__ import annotations

import re

# socketType and authMethod arrive in one of two forms depending on the
# Thunderbird version, and we normalise both to the name.
#
# accounts.js means to write a language-neutral *name*, which it looks up in a
# table built from ``Object.entries(Ci.nsMsgSocketType)``. When that enumeration
# yields nothing the lookup misses and the code falls back to the raw integer:
# ``aIndex in gSocketTypes ? gSocketTypes[aIndex] : aIndex``. Thunderbird 153
# takes that fallback, so a current dump reads ", 3, 10" where the source
# suggests ", SSL, OAuth2". Confirmed against real 153.0 dumps on both macOS and
# Windows 11, so it is the build that decides this, not the platform.
#
# Numbers are from nsMsgSocketType / nsMsgAuthMethod in
# mailnews/base/public/MailNewsTypes2.idl.
_SOCKET_TYPE_BY_NUMBER = {
    0: "plain",
    # trySTARTTLS has been removed from MailNewsTypes2.idl, but an old profile
    # can still carry the stored value, so keep decoding it.
    1: "trySTARTTLS",
    2: "alwaysSTARTTLS",
    3: "SSL",
}
_AUTH_METHOD_BY_NUMBER = {
    1: "none",
    2: "old",
    3: "passwordCleartext",
    4: "passwordEncrypted",
    5: "GSSAPI",
    6: "NTLM",
    7: "External",
    8: "secure",
    9: "anything",
    10: "OAuth2",
}

SOCKET_TYPES = tuple(_SOCKET_TYPE_BY_NUMBER.values())
AUTH_METHODS = tuple(_AUTH_METHOD_BY_NUMBER.values())

# accounts.js builds hostDetails as "(" + type + ") " + hostName + optional
# ":" + port. The port is omitted entirely when it is -1 (meaning "default").
_HOST_DETAILS_RE = re.compile(r"^\((?P<protocol>[^)]*)\)\s*(?P<hostport>.*)$")

# An account block starts with a bare "key:" line, emitted immediately before
# that account's INCOMING line.
_BARE_KEY_RE = re.compile(r"^(?P<key>[^\s:][^:]*):$")

_INTEGER_RE = re.compile(r"-?[0-9]+")

_INCOMING_PREFIX = "INCOMING:"
_OUTGOING_PREFIX = "OUTGOING:"

# App Basics rows serialise as "Label: value" (see generateTextForElement in
# export.js). These labels are *localised*, unlike the Accounts vocabularies
# above, so matching them is best-effort and English-only by design.
_APP_LABELS = {
    "Name": "name",
    "Version": "version",
    "Build ID": "buildId",
    "OS": "os",
    "User Agent": "userAgent",
}


def _split_fields(payload: str) -> list[str]:
    """Split an INCOMING/OUTGOING payload into stripped fields.

    See the module docstring on why this splits on "," rather than ", ".
    """
    return [field.strip() for field in payload.split(",")]


def _split_host_port(hostport: str) -> tuple[str | None, int | None]:
    """Split "host:port" into its parts. A missing port means "use the default"."""
    hostport = hostport.strip()
    if not hostport:
        return None, None
    host, sep, tail = hostport.rpartition(":")
    if sep and tail.isdigit():
        return (host.strip() or None), int(tail)
    return hostport, None


def _normalise_enum(
    value: str, field: str, by_number: dict[int, str]
) -> tuple[str | None, list[str]]:
    """Normalise a socketType/authMethod field to its name.

    Accepts either the name ("SSL") or the raw integer ("3"), since which one
    Thunderbird writes depends on the version. Returns the name, or the original
    value plus a warning when it cannot be decoded.
    """
    if not value:
        return None, []
    if value in by_number.values():
        return value, []
    # Deliberately ASCII-only and deliberately strict. "--3" used to reach
    # int() and raise; anything that is not exactly an optional minus followed
    # by ASCII digits is now reported as unrecognised instead. The JavaScript
    # parser applies the identical rule, which is why this is a regex rather
    # than isdigit() -- Python's isdigit() accepts non-ASCII digits that
    # JavaScript's \d does not, and the two must not disagree.
    if _INTEGER_RE.fullmatch(value):
        name = by_number.get(int(value))
        if name is not None:
            return name, []
        return value, [f"unrecognised {field} value: {value!r}"]
    return value, [f"unrecognised {field}: {value!r}"]


def _parse_incoming(fields: list[str]) -> dict:
    """Parse an INCOMING field list.

    Field order is ``key, name, hostDetails, socketType, authMethod``. Only
    ``name`` (private, and discarded) can contain ", ", so the last three
    fields are read from the right and the first from the left.
    """
    record: dict = {
        "protocol": None,
        "host": None,
        "port": None,
        "socketType": None,
        "authMethod": None,
        "warnings": [],
    }

    if len(fields) < 5:
        record["warnings"].append("incoming line has too few fields to parse")
        return record

    host_details = fields[-3].strip()
    record["socketType"], socket_warnings = _normalise_enum(
        fields[-2].strip(), "socketType", _SOCKET_TYPE_BY_NUMBER
    )
    record["authMethod"], auth_warnings = _normalise_enum(
        fields[-1].strip(), "authMethod", _AUTH_METHOD_BY_NUMBER
    )

    match = _HOST_DETAILS_RE.match(host_details)
    if match:
        record["protocol"] = match.group("protocol").strip() or None
        record["host"], record["port"] = _split_host_port(match.group("hostport"))
    elif host_details:
        record["warnings"].append(f"unrecognised server details: {host_details!r}")
    else:
        # accounts.js emits a placeholder record with an empty hostDetails when
        # reading the incoming server throws. A support person seeing this has
        # an account Thunderbird itself cannot read, which is a finding in its
        # own right rather than a parse failure.
        record["warnings"].append(
            "Thunderbird could not read this account's incoming server"
        )

    record["warnings"].extend(socket_warnings)
    record["warnings"].extend(auth_warnings)
    return record


def _parse_outgoing(fields: list[str]) -> dict:
    """Parse an OUTGOING field list.

    Field order is ``identityName, name, socketType, authMethod, isDefault``.
    Here the free-form private field is *first*, so all four trailing fields are
    read from the right.
    """
    record: dict = {
        "host": None,
        "port": None,
        "socketType": None,
        "authMethod": None,
        "isDefault": None,
        "warnings": [],
    }

    if len(fields) < 5:
        record["warnings"].append("outgoing line has too few fields to parse")
        return record

    record["host"], record["port"] = _split_host_port(fields[-4])
    record["socketType"], socket_warnings = _normalise_enum(
        fields[-3].strip(), "socketType", _SOCKET_TYPE_BY_NUMBER
    )
    record["authMethod"], auth_warnings = _normalise_enum(
        fields[-2].strip(), "authMethod", _AUTH_METHOD_BY_NUMBER
    )

    is_default = fields[-1].strip()
    if is_default in ("true", "false"):
        record["isDefault"] = is_default == "true"
    elif is_default:
        record["warnings"].append(f"unrecognised isDefault: {is_default!r}")

    record["warnings"].extend(socket_warnings)
    record["warnings"].extend(auth_warnings)
    return record


def _private_data_present(fields: list[str], index: int) -> bool:
    """Was a private field non-empty, i.e. did the user paste with it shown?"""
    return bool(fields[index].strip()) if len(fields) > index else False


def parse(text: str) -> dict:
    """Parse troubleshooting text into a structured, PII-free result.

    Accepts either a complete Troubleshooting Information dump or just the
    Accounts lines on their own, since support staff often ask for -- and users
    often send -- only the relevant fragment.
    """
    # Windows builds copy with CRLF line endings: createTextForElement in
    # export.js runs text.replace(/\n/g, "\r\n") over the whole document behind
    # a Windows-only check. Verified against a real Windows 11 / TB 153
    # clipboard capture -- 468 CRLF, no lone LF, including the newlines inside
    # multi-line Graphics values, as a whole-document substitution implies.
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    accounts: list[dict] = []
    app: dict = {}
    warnings: list[str] = []
    private_data_shown = False
    saw_non_account_content = False

    # The account key line is emitted immediately before that account's
    # INCOMING line, with nothing between them -- not even a blank line. So the
    # key candidate is cleared by *any* intervening line, which stops an
    # unrelated "Some Section:" heading elsewhere in the dump from being
    # mistaken for an account key.
    key_candidate: str | None = None

    for raw_line in lines:
        line = raw_line.strip()

        if not line:
            key_candidate = None
            continue

        if line.startswith(_INCOMING_PREFIX):
            fields = _split_fields(line[len(_INCOMING_PREFIX) :])
            if _private_data_present(fields, 1):
                private_data_shown = True
            accounts.append(
                {
                    "key": key_candidate,
                    "incoming": _parse_incoming(fields),
                    "outgoing": [],
                }
            )
            key_candidate = None
            continue

        if line.startswith(_OUTGOING_PREFIX):
            fields = _split_fields(line[len(_OUTGOING_PREFIX) :])
            if _private_data_present(fields, 0):
                private_data_shown = True
            if not accounts:
                # A fragment that begins mid-account. Keep the data rather than
                # discarding it; the caller can still judge the SMTP settings.
                accounts.append({"key": None, "incoming": None, "outgoing": []})
            accounts[-1]["outgoing"].append(_parse_outgoing(fields))
            key_candidate = None
            continue

        key_match = _BARE_KEY_RE.match(line)
        if key_match:
            key_candidate = key_match.group("key")
            continue

        key_candidate = None

        # Not an account line. Try App Basics, and note that this input has
        # content beyond the Accounts section.
        label, sep, value = line.partition(":")
        if sep and label.strip() in _APP_LABELS and value.strip():
            # First occurrence wins: App Basics is the first section in the
            # dump, and later sections (Calendars, Chat) reuse labels like
            # "Name" for unrelated things.
            app.setdefault(_APP_LABELS[label.strip()], value.strip())

        saw_non_account_content = True

    kind = "full" if saw_non_account_content else "fragment"

    if not accounts:
        warnings.append("no mail account information found in this input")
    elif kind == "full" and "version" not in app:
        # App Basics labels are localised, so a non-English dump parses fine for
        # accounts but yields no version. Anything version-dependent must treat
        # this as unknown rather than assume a value.
        warnings.append(
            "could not determine Thunderbird version "
            "(App Basics labels are localised; only English is recognised)"
        )
    if private_data_shown:
        warnings.append(
            "input contained private data (account or identity names); "
            "it was discarded and is not included in this result"
        )

    return {
        "input": {"kind": kind, "privateDataShown": private_data_shown},
        "app": app,
        "accounts": accounts,
        "warnings": warnings,
    }
