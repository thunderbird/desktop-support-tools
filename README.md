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

## The web page

**https://thunderbird.github.io/desktop-support-tools/**

The same check, in a browser. Paste, read the verdicts, close the tab.

It covers **Thundermail** accounts, and says so in its heading rather than
leaving you to work it out from a column of "Not checked" verdicts. Paste a
whole dump regardless: every account is listed, and accounts belonging to other
providers are reported as unchecked rather than judged against settings nobody
has verified.

The scope wording is generated from `settings.json`, so adding a provider
rewords the page by itself. Nothing in the markup names a provider.

To run it locally:

```sh
python3 -m http.server 8000    # then open http://localhost:8000/
```

It is static and client-side only: no build step, no backend, no storage.
Nothing you paste is uploaded, because there is nowhere for it to go. Opening
`index.html` from the filesystem will not work — the page fetches
`settings.json`, so it needs to be served over HTTP.

The page shares `settings.json` with the CLI, and its parser and verdict engine
are ports of the Python ones, checked against the same fixtures.

## Tests

```sh
uv run --with pytest pytest -q    # Python
node --test                       # JavaScript
```

Each fixture in `fixtures/` has two companions: `.expected.json` for what it
parses to, and `.verdict.json` for how it is judged. Both implementations are
asserted against both files, which is what keeps the CLI and the web page
answering the same question the same way. Running only one suite proves only
half of that.
