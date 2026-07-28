#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Check the mail account settings in Thunderbird Troubleshooting Information.

Reads a full dump, or just the pasted Accounts lines, and reports one verdict
per account: which settings are wrong, what to change them to, and where in
Thunderbird to do it.

    uv run check_settings.py fixtures/thundermail-correct.txt
    pbpaste | uv run check_settings.py --account account3
"""

from __future__ import annotations

import argparse
import json
import sys

from troubleshooting_info import parse
from verdicts import FAIL, NOT_APPLICABLE, PASS, UNKNOWN, WARN, check, load_settings

# Aligned so the outcome column scans vertically in a terminal.
_MARKERS = {
    PASS: "PASS",
    WARN: "WARN",
    FAIL: "FAIL",
    UNKNOWN: "  ? ",
    NOT_APPLICABLE: "  - ",
}


def _render_account(account: dict, out) -> None:
    heading = f"Account {account['position']} — {account['label']}"
    provider = account["provider"]
    if provider:
        heading += f"  [{provider['displayName']}]"
        if not provider["verified"]:
            heading += " (unverified)"
    out.write(f"\n{heading}\n")
    out.write(f"{'=' * len(heading)}\n")

    for note in account["notes"]:
        out.write(f"  note: {note}\n")

    for server in account["servers"]:
        role = server["role"]
        if server.get("ordinal"):
            role = f"{role} {server['ordinal']}"
        out.write(f"\n  {role}: {server['label']}\n")
        for entry in server.get("checks", []):
            out.write(f"    [{_MARKERS[entry['outcome']]}] {entry['message']}\n")
            if entry.get("remediation"):
                out.write(f"           fix: {entry['remediation']}\n")
            if entry.get("provenance"):
                out.write(f"           why: {entry['provenance']}\n")


def render(result: dict, out) -> None:
    for warning in result["warnings"]:
        out.write(f"warning: {warning}\n")

    for account in result["accounts"]:
        _render_account(account, out)

    if len(result["accounts"]) > 1:
        # Deliberately no overall verdict and no count of accounts. A dump can
        # contain accounts the user abandoned, and the Smart Mailboxes
        # pseudo-account never appears at all, so any roll-up or total would be
        # misleading.
        out.write(
            "\nEach account is judged on its own. If one of these is a mailbox "
            "you no longer use, ignore its verdict.\n"
        )

    if result["accounts"]:
        out.write(
            "\nIf an account you deleted still appears here, quit Thunderbird, "
            "reopen it, and copy the troubleshooting information again.\n"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "path",
        nargs="?",
        help="file containing the troubleshooting text (default: read stdin)",
    )
    parser.add_argument(
        "--account",
        help="check only this account, by key (account3) or position (3)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="emit the verdicts as JSON instead of text",
    )
    parser.add_argument(
        "--settings",
        help="path to an alternative settings.json",
    )
    args = parser.parse_args(argv)

    if args.path:
        with open(args.path, encoding="utf-8") as handle:
            text = handle.read()
    else:
        text = sys.stdin.read()

    result = check(parse(text), load_settings(args.settings), account=args.account)

    if args.as_json:
        json.dump(result, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        render(result, sys.stdout)

    # Only "nothing to report on" is a non-zero status. A failing account must
    # never set the exit code: verdicts are per-account, and one abandoned
    # mailbox would otherwise fail the whole run.
    return 0 if result["accounts"] else 1


if __name__ == "__main__":
    sys.exit(main())
