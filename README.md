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

Turns Thunderbird's Troubleshooting Information (**Thunderbird app menu ☰ → Help
→ Troubleshooting Information → Copy text to clipboard**) into structured JSON
describing each mail account's incoming and outgoing server settings.

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

## anonymize_ics.py

Takes the personal information out of a calendar file, so you can attach one to
a bug report. Written for
[ticket 7067](https://tbpro.zendesk.com/agent/tickets/7067), where the only way
to reproduce the problem was a 1,338-event calendar nobody could share.

```sh
uv run anonymize_ics.py Calendar.ics -o scrubbed.ics
uv run anonymize_ics.py Calendar.ics > scrubbed.ics
uv run anonymize_ics.py --check scrubbed.ics
```

`--check` reads a calendar, reports anything identifying still in it, and
changes nothing — use it on a file someone else scrubbed before you pass it on.
Exit status is `0` when the calendar is clean and `1` when it is not, or when
the file could not be read. Scrubbing writes the calendar to stdout unless you
give it `-o`, and its summary to stderr, so it can be piped.

**What it removes:** what the entries were called, their descriptions, where
they were, who organised them and who attended, the names attached to those
addresses, attachments, links, categories, coordinates, and the calendar's own
name.

**What it keeps, deliberately:** every date and time, repeat rules and their
exceptions, cancelled and tentative statuses, free/busy and privacy flags,
alarms and their triggers, time zone definitions, and the order and nesting of
everything. That is the whole point — a calendar with the details taken out
still reproduces the bug the original did, which a hand-trimmed one usually
does not.

### Everyone keeps their own stand-in

Each address becomes `person1@example.com`, `person2@example.com` and so on,
and keeps the same stand-in throughout the file. A meeting with three people
still has three people in it, someone who chairs a meeting they also attend is
still one person, and somebody who turns up in forty entries is still
recognisably one person. Putting everybody on a single address would quietly
change what the calendar says.

The trade to know about: this keeps the shape of who-met-whom, without the
names. That is far less than the original told you and it is the right balance
for a bug report, but it is not the same as erasing the pattern entirely.

An organiser with no address stays empty rather than being given one. Exchange
exports quite often look like that, and inventing a person who was never there
is its own kind of wrong answer.

## Making and deleting a test calendar by hand

Reproducing a calendar bug means making a calendar, filling it with a scrubbed
copy of somebody's data, and throwing it away afterwards — over and over, and
never against a calendar you actually use. Two `curl` requests do the making and
the throwing away, with no client in the way to cache, retry or reinterpret
anything.

There are scripts for all of it, and they are the better way round if you are
doing this more than once — they list what is already there before they touch
anything:

```sh
HOME_URL='https://mail.thundermail.com/dav/cal/nemo%40thundermail.com/'
uv run caldav_make_calendar.py "$HOME_URL" "ticket 7067" -u nemo@thundermail.com
uv run caldav_delete_calendars.py "$HOME_URL" -u nemo@thundermail.com   # dry run
uv run caldav_delete_events.py "${HOME_URL}ticket-7067/" -u nemo@thundermail.com --everything
```

`caldav_make_calendar.py` works the calendar's address out from its name
(`ticket 7067` → `.../ticket-7067/`, or give `--path`), and refuses if the
account already has a calendar at that address or under that name. The `curl`
below is the same request with nothing in the way, which is what you want when
it is the server's behaviour you are investigating.

### What you need first

**An app password, not your account password.** These requests authenticate with
HTTP Basic, so an account that signs in with OAuth2 will reject the password you
type into Thunderbird. Make an app password in your provider's settings.

**The address of the account's calendars.** In Thunderbird, right-click the
calendar → **Properties** → **Location** gives one calendar's address; take the
last segment off for the account's calendar home. An `@` in the path has to be
written `%40`:

```sh
CAL_HOME='https://mail.thundermail.com/dav/cal/nemo%40thundermail.com/'
CAL_USER='nemo@thundermail.com'
```

The examples below use `-u "$CAL_USER"`, which prompts for the password. Every URL
needs its **trailing slash** — a calendar is a collection, and some servers
answer differently without it.

### See what is there

```sh
curl -s -u "$CAL_USER" -X PROPFIND -H 'Depth: 1' \
  -H 'Content-Type: application/xml; charset=utf-8' \
  --data-binary '<?xml version="1.0" encoding="utf-8"?>
<D:propfind xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:prop>
    <D:displayname/>
    <D:resourcetype/>
    <C:schedule-default-calendar-URL/>
  </D:prop>
</D:propfind>' \
  "$CAL_HOME"
```

The reply is one `<response>` per collection: its address in `<href>`, its name
in `<displayname>`, and what it is in `<resourcetype>`. Two of them are the
scheduling **inbox** and **outbox** rather than calendars — leave those alone,
deleting them breaks the account.

`<schedule-default-calendar-URL>` is asked for because it is meant to name the
default calendar, the one never to test against. **Thundermail does not answer
it** (checked 2026-08-01), so there the default is identifiable only by its
`/default/` path segment, and by its refusing to be deleted. Other servers do
answer it, which is why it stays in the request.

### Make one

```sh
curl -i -u "$CAL_USER" -X MKCALENDAR \
  -H 'Content-Type: application/xml' \
  --data '<?xml version="1.0" encoding="utf-8"?>
<C:mkcalendar xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:set><D:prop><D:displayname>ticket 7067</D:displayname></D:prop></D:set>
</C:mkcalendar>' \
  "${CAL_HOME}ticket-7067/"
```

**`201 Created`** is the answer you want. Run against Thundermail on 2026-08-01,
exactly as written. The last path segment — `ticket-7067` — is the calendar's
address and you choose it; `<displayname>` is the separate, human-readable name
Thunderbird shows in its calendar list. Naming the address after the ticket is
what makes an abandoned test calendar identifiable a month later.

RFC 4791 allows more properties in that body —
`<C:supported-calendar-component-set>`, to make a calendar that takes events but
not tasks, for one. None of them have been tried here, so add them knowing that;
the two-line body above is the part that is known to work.

Entries go in one `PUT` per entry, to `<calendar>/<something>.ics`; Thunderbird's
**Import** does exactly that, one request per event. There is a worked example,
including reading the `ETag` back, in
[issue #13](https://github.com/thunderbird/desktop-support-tools/issues/13).

Thunderbird will not notice the new calendar by itself. Subscribe to it the
normal way: **New Calendar → On the Network**, which lists what the account has.

### Delete one

```sh
curl -i -u "$CAL_USER" -X DELETE "${CAL_HOME}ticket-7067/"
```

**`204 No Content`** means it is gone — also confirmed against Thundermail on
2026-08-01. Everything in it goes with it, and nothing here can bring any of it
back: there is no confirmation step and no undo, so read the URL twice before you
press return. This is the same request `caldav_delete_calendars.py` makes, minus
the part where it shows you the list first.

**Never point this at the default calendar.** Thundermail refuses with
`<A:default-calendar-needed/>`, which is the good outcome, but the account's
scheduling target is not a thing to gamble on. Empty it instead:

```sh
uv run caldav_delete_events.py "${CAL_HOME}default/" -u "$CAL_USER" --everything
```

Afterwards, remove the calendar from Thunderbird too. It keeps its own cached
copy and will happily go on showing a calendar the server no longer has.

### On Windows, without WSL

`curl.exe` is part of Windows 10 1803 and later, and Windows 11, so the requests
above need no extra software. Two things change:

**Type `curl.exe`, not `curl`.** Windows PowerShell 5.1 — the one that is there
by default — uses `curl` as another name for `Invoke-WebRequest`, which takes
entirely different arguments and will reject `-X` and `-u` with errors that do
not explain themselves. PowerShell 7 dropped that name, but do not count on
which one you are in front of.

**Put the XML in a file.** The examples above are written for a Unix shell, where
a quoted block can run over several lines; neither PowerShell nor `cmd` will take
that. Save the body as `mkcalendar.xml` and point curl at it, which then reads
the same way on every platform:

```
curl.exe -i -u nemo@thundermail.com -X MKCALENDAR ^
  -H "Content-Type: application/xml" ^
  --data-binary "@mkcalendar.xml" ^
  "https://mail.thundermail.com/dav/cal/nemo%40thundermail.com/ticket-7067/"
```

The `DELETE` has no body, so apart from the name and the double quotes it is
unchanged.

> **Unverified:** these two adjustments follow from how Windows shells and
> `curl.exe` are documented to behave, but the requests in this section have only
> been run from macOS. Correct this line once somebody has run them on Windows.

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

The calendar fixtures work the other way round: `ics-*.ics` with an
`.expected.ics` companion holding the scrubbed result. Each one exists because
an earlier version of the scrubber got that case wrong, and the tests assert
that the identifying strings planted in the inputs are absent from the outputs
— so a regression shows up as leaked data, not as a golden file that quietly
needs updating.
