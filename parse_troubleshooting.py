#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Turn Thunderbird Troubleshooting Information into structured JSON.

Reads a full Troubleshooting Information dump, or just the pasted Accounts
lines, and writes PII-free account records to stdout.

    uv run parse_troubleshooting.py fixtures/thundermail-correct.txt
    pbpaste | uv run parse_troubleshooting.py
"""

from __future__ import annotations

import argparse
import json
import sys

from troubleshooting_info import parse


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "path",
        nargs="?",
        help="file containing the troubleshooting text (default: read stdin)",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="emit single-line JSON instead of indented",
    )
    args = parser.parse_args(argv)

    if args.path:
        with open(args.path, encoding="utf-8") as handle:
            text = handle.read()
    else:
        text = sys.stdin.read()

    result = parse(text)
    json.dump(
        result,
        sys.stdout,
        indent=None if args.compact else 2,
        sort_keys=True,
    )
    sys.stdout.write("\n")

    # Nothing to report on is the one case worth a non-zero status, so this can
    # be chained in a shell pipeline without silently succeeding on junk input.
    return 0 if result["accounts"] else 1


if __name__ == "__main__":
    sys.exit(main())
