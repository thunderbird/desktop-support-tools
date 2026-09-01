#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Assemble the Firefox add-on, which is the add-on plus one file from the root.

    uv run package_addon.py            # build/firefox-addon/, ready to load
    uv run package_addon.py --zip      # ... and build/thundermail-calendars.zip

An extension can only load files from inside its own directory, and
`caldav_account.js` is shared with the tests and with whatever front-end comes
next, so it lives at the repo root like the rest of the shared code. Copying it
by hand into the add-on would make two of it, which is the one thing the
fixtures rule exists to prevent -- so it gets copied at packaging time instead,
and `build/` is not in git.

Load the result with about:debugging -> This Firefox -> Load Temporary Add-on,
and pick `build/firefox-addon/manifest.json`. It goes away when Firefox
restarts; anything more permanent needs signing, which the README covers.

Run this again after editing `caldav_account.js`. The add-on directory itself
you can edit in place -- only the shared file is a copy.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "firefox-addon"
BUILD = HERE / "build" / "firefox-addon"

# The one file that lives outside the add-on and has to travel with it.
SHARED = ["caldav_account.js"]


def package(make_zip: bool) -> int:
    if not SOURCE.is_dir():
        print(f"There is no {SOURCE.name}/ to package.", file=sys.stderr)
        return 1

    if BUILD.exists():
        shutil.rmtree(BUILD)
    shutil.copytree(SOURCE, BUILD)
    for name in SHARED:
        shutil.copy2(HERE / name, BUILD / name)

    manifest = json.loads((BUILD / "manifest.json").read_text(encoding="utf-8"))
    version = manifest.get("version", "0")
    missing = sorted(_missing(BUILD, manifest))
    if missing:
        print("The manifest names files that are not there:", file=sys.stderr)
        print("\n".join(f"  {name}" for name in missing), file=sys.stderr)
        return 1

    print(f"{BUILD.relative_to(HERE)}/ is ready to load in about:debugging.")

    if make_zip:
        archive = BUILD.parent / f"thundermail-calendars-{version}.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            for path in sorted(BUILD.rglob("*")):
                if path.is_file():
                    bundle.write(path, path.relative_to(BUILD))
        print(f"{archive.relative_to(HERE)} is what you upload for signing.")
    return 0


def _missing(root: Path, manifest: dict) -> set[str]:
    """Every file the manifest points at that is not in the build.

    Cheap, and it catches the mistake this script exists to make possible: a new
    shared file imported by the popup and not added to SHARED, which works
    perfectly in the tests and breaks the moment the add-on is loaded.
    """
    named = set()

    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ("default_popup", "page", "service_worker"):
                    named.add(item)
                elif key in ("icons", "default_icon") and isinstance(item, dict):
                    named.update(item.values())
                else:
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(manifest)
    named.update(_imported_by(root, named))
    return {name for name in named if not (root / name).exists()}


def _imported_by(root: Path, pages: set[str]) -> set[str]:
    """What the popup's scripts import, one level deep, as written in the source."""
    found = set()
    for script in root.glob("*.js"):
        for line in script.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("} from \"./") or stripped.startswith("from \"./"):
                found.add(stripped.split('"./')[1].rstrip('";'))
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--zip", action="store_true", help="also write the archive you upload for signing"
    )
    return package(parser.parse_args(argv).zip)


if __name__ == "__main__":
    sys.exit(main())
