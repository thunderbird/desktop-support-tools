# desktop-support-tools

Small tools to help Thunderbird support staff and volunteers troubleshoot
Thunderbird **Desktop**. Each tool comes as a command-line script and a matching
web page, in the style of
[thunderbird/dns-scripts](https://github.com/thunderbird/dns-scripts).

Licensed under [MPL-2.0](LICENSE).

## Setup

Managed with [`uv`](https://docs.astral.sh/uv/):

```sh
uv sync
```

## parse_troubleshooting.py

Turns Thunderbird's Troubleshooting Information (**Help → Troubleshooting
Information → Copy text to clipboard**) into structured JSON describing each mail
account's incoming and outgoing server settings.

```sh
uv run parse_troubleshooting.py fixtures/thundermail-correct.txt
pbpaste | uv run parse_troubleshooting.py
```

You can paste the **whole** Troubleshooting Information or just the account lines
from the "Mail and News Accounts" section — both work.

Exit status is `0` when at least one account was found, `1` otherwise, so it can
be chained in a shell pipeline.

### Nothing personal is kept

Thunderbird marks the account and identity names — usually your email address —
as private data, and everything needed to check a configuration (server, port,
connection security, authentication method) is *not* private. So this tool
discards those two fields unconditionally, whether or not you copied with "Show
private data" ticked, and tells you when it did. Server settings are all it
keeps.

Nothing is uploaded and nothing is stored.

## check_settings.py

Checks those account settings against the provider's expected values and
reports what to change.

```sh
uv run check_settings.py fixtures/thundermail-correct.txt
pbpaste | uv run check_settings.py
pbpaste | uv run check_settings.py --account account3 --json
```

Verdicts are **per account**, never rolled up into one answer, because a dump
routinely lists accounts you no longer use and nothing in it marks them as dead.
Each account gets one of:

| | |
|---|---|
| `PASS` | Settings are correct. |
| `WARN` | Settings work, but are not what the provider recommends — an app password instead of OAuth2, say. Not a reason to change a working account. |
| `FAIL` | Settings are wrong, or cannot work. |
| `?` | No expected settings are catalogued for that server yet, or Thunderbird could not read the account. |
| `-` | Nothing to check, such as Local Folders. |

A failing account does **not** set the exit status — otherwise one abandoned
mailbox would fail the whole run. As with `parse_troubleshooting.py`, only
finding no accounts at all exits non-zero.

Thundermail is the only provider catalogued so far. Anything else is reported as
unchecked rather than as correct.

### Where the expected values come from

`settings.json` at the repo root, read by both this script and (in time) the web
page, so a provider is added once rather than twice. Each expected value carries
its provenance, which the output quotes: run the command above and the `why:`
lines are the reason the value is what it is.

## Tests

```sh
uv run --with pytest pytest -q
```

Fixtures in `fixtures/` each have an `.expected.json` companion. These are the
shared contract between the Python CLI and the forthcoming JavaScript web page,
which will be checked against the same files.
