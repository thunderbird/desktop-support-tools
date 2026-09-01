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

## Making, filling and deleting a test calendar by hand

Reproducing a calendar bug means making a calendar, filling it with a scrubbed
copy of somebody's data, and throwing it away afterwards — over and over, and
never against a calendar you actually use. A `curl` request each does the making
and the throwing away, with no client in the way to cache, retry or reinterpret
anything.

There are scripts for all of it, and they are the better way round if you are
doing this more than once — they list what is already there before they touch
anything:

```sh
export CALDAV_USER='nemo@thundermail.com'
export CALDAV_PASSWORD='...'          # or leave it unset and be asked

CAL_HOME='https://mail.thundermail.com/dav/cal/nemo%40thundermail.com/'
uv run caldav_list_calendars.py "$CAL_HOME" --all     # what is there already
uv run caldav_make_calendar.py "$CAL_HOME" "ticket 7067"
uv run caldav_import_ics.py "${CAL_HOME}ticket-7067/" scrubbed.ics    # dry run
uv run caldav_delete_calendars.py "$CAL_HOME"                        # dry run
uv run caldav_delete_events.py "${CAL_HOME}ticket-7067/" --everything
```

All five take the username from `-u`, from `CALDAV_USER`, or by asking, in that
order, and the app password from `CALDAV_PASSWORD` or by asking. Exporting both
is worth doing because this is half a dozen commands against one account, and
leaving `CALDAV_PASSWORD` unset is worth doing because a password you type is a
password that is not in your shell history.

`caldav_list_calendars.py` is the one that only looks: with no flags it prints
the name of the account's default calendar and nothing else, so it can go into a
variable, and `--all` lists every calendar with its address. It sends `PROPFIND`
and never anything else.

`caldav_make_calendar.py` works the calendar's address out from its name
(`ticket 7067` → `.../ticket-7067/`, or give `--path`), and refuses if the
account already has a calendar at that address or under that name. The `curl`
below is the same request with nothing in the way, which is what you want when
it is the server's behaviour you are investigating.

### Being asked, or not being asked

Both cleanup scripts report and change nothing until you add `--delete`, and
`caldav_import_ics.py` sends nothing until you add `--upload`. Then they ask you
to type the number of things about to go. Two flags change that, and no command
takes both:

| | what it does |
|---|---|
| *neither* | type the number of things to confirm all of them at once |
| `--confirm` | ask about each one instead: `y` yes, `n` no, `a` all the rest, `q` stop here |
| `--yes` | ask nothing |

Neither flag implies `--delete` or `--upload`, so a dry run stays a dry run
either way.

`--confirm` is for the part of a reproduction where you do not recognise
everything that matched:

```
  Anonymized Data  --  2026-03-04 09:00
  Delete? [y]es [n]o [a]ll the rest [q]uit: a
  Not asking again.
```

`a` is the one that makes it usable on a calendar of hundreds — look at the first
few, satisfy yourself the right entries are being picked, and stop being asked.
`q` stops there and keeps everything you have not answered for, which is not a
failure and does not stop you running it again. Anything you answer `n` to stays
answered: a server that will only list part of a calendar at a time gets worked
through in passes, and you are not asked twice about the same entry.

The import asks the same three-way question, worded `Send?`, and `a` does the
same job there — look at the first few entries, satisfy yourself the right file is
going up, and stop being asked.

`--yes` is for the other half of the same loop, where you have run the cleanup
five times already and typing the count is friction rather than safety. On
`caldav_make_calendar.py` it does nothing, since nothing there asks unless you
pass `--confirm`; it is accepted so that a loop can pass the same flag to all
four of the tools that change something. `caldav_list_calendars.py` takes
neither flag, because it changes nothing and so has nothing to ask you about.

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
```

`CAL_HOME` is a shell variable of your own, unlike `CALDAV_USER` and
`CALDAV_PASSWORD`, which the scripts read. The `curl` examples below use
`-u "$CALDAV_USER"`, which prompts for the password rather than putting it where
other people logged into the machine can read it off the process list. Every URL
needs its **trailing slash** — a calendar is a collection, and some servers
answer differently without it.

### See what is there

`caldav_list_calendars.py` answers two questions, and which one it answers is
the only thing `--all` changes. Neither sends anything but `PROPFIND`.

**What is my default calendar called?** — the one you must never test against:

```sh
$ uv run caldav_list_calendars.py "$CAL_HOME"
App password for nemo@thundermail.com:
The server did not say which calendar is its default, so that was worked out from
the addresses: this is the one whose address ends in /default.
Nemo Thundermail Calendar (nemo@thundermail.com)
```

The name goes to standard output on a line of its own and everything else goes
to standard error, so the name is all a variable catches:

```sh
DEFAULT=$(uv run caldav_list_calendars.py "$CAL_HOME")
```

If it cannot tell which calendar is the default it says so and exits non-zero,
rather than handing back an empty line for a shell to carry on with.

**What else is on the account?** — the leftovers of every reproduction you have
run, which is what you want before making another calendar or cleaning up:

```sh
$ uv run caldav_list_calendars.py "$CAL_HOME" --all
2 calendars under /dav/cal/nemo%40thundermail.com/:

  ticket 7067                                       .../ticket-7067/
  Nemo Thundermail Calendar (nemo@thundermail.com)  .../default/  <- default
```

Note what that default calendar is called: on Thundermail the name is generated
from the account, **so it contains your email address**. It is your own address
on your own screen, but it is also the one output of these tools that is a
person — worth a second look before it goes into a bug or a support thread.

Both come from one request, which is also worth sending with nothing in the way:

```sh
curl -s -u "$CALDAV_USER" -X PROPFIND -H 'Depth: 1' \
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
it** (checked 2026-08-01, and again on 2026-09-01), so there the default is identifiable only by its
`/default/` path segment, and by its refusing to be deleted. Other servers do
answer it, which is why it stays in the request.

That is also why `caldav_list_calendars.py` tells you which of the two answers
you are getting: a default the server named is a fact, and a default recognised
by its address is a guess that happens to be right on every server tried so far.
The guess is the sort of thing to say out loud, since acting on the wrong one
means testing against the calendar you meant to leave alone.

### Make one

```sh
curl -i -u "$CALDAV_USER" -X MKCALENDAR \
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

Thunderbird will not notice the new calendar by itself. Subscribe to it the
normal way: **New Calendar → On the Network**, which lists what the account has.

### Fill one

```sh
uv run caldav_import_ics.py "${CAL_HOME}ticket-7067/" scrubbed.ics           # dry run
uv run caldav_import_ics.py "${CAL_HOME}ticket-7067/" scrubbed.ics --upload
```

**Thunderbird's Import is still the right way to do the first round.** It is the
code path the reporter went through, so a bug that lives there only shows up that
way. This is for the rounds after it — the fifth import of the same file, and the
ones where what you are watching is the server, which means keeping the client
out of the way.

Entries go in one `PUT` per entry, to `<calendar>/<something>.ics`; Thunderbird's
Import does exactly that, one request per event, because CalDAV has no "here is
the whole file" request. What travels in one request is one *entry*, which is not
the same as one component, and the difference is where a hand-rolled import goes
wrong:

- **A repeating entry and its changed occurrences share an identifier** and go up
  together. Sent separately, the exception becomes an entry of its own — two
  meetings in the calendar, one of them the occurrence that was meant to move.
- **A timezone definition is carried into every entry that names one**, since an
  entry naming a timezone its own request does not define is one a server may
  refuse. Where the file names a zone it never defines, the report says so.
- **`METHOD` is dropped.** It says what a calendar was sent *for* — `PUBLISH`,
  `REQUEST` — and RFC 4791 does not allow it on a stored entry. Exporters write it
  anyway.
- **An entry with no identifier is left out and reported**, not given one.

Each entry's address is worked out from its identifier, which is what makes the
loop repeatable: **nothing already in the calendar is overwritten.** The request
asks the server to refuse rather than replace, so a second run of the same file
reports the entries as already there and sends the rest. That is also the answer
to a server that starts rate-limiting you at entry 400 — run it again, or add
`--delay`. `--replace` overwrites deliberately, and asks you to type the count
first.

Afterwards the same file names what it imported:

```sh
uv run caldav_delete_events.py "${CAL_HOME}ticket-7067/" --ids scrubbed.ics
```

There is a worked `PUT` by hand, including reading the `ETag` back, in
[issue #13](https://github.com/thunderbird/desktop-support-tools/issues/13) — that
is the shape to reach for when it is one request you want rather than an import.

#### It refuses a calendar that still identifies people

```
$ uv run caldav_import_ics.py "$CAL" Calendar.ics
This calendar still identifies people, so it is not going to a server:
  line 41: SUMMARY still holds something that identifies someone
  the address someone@example.org is still in this file
  ... and 6 more findings

Scrub it first, and send the scrubbed copy:
  uv run anonymize_ics.py CALENDAR.ics -o scrubbed.ics
```

The check is `anonymize_ics.py --check`'s, reused rather than reimplemented, so
there is one definition of clean. It runs before the password is asked for,
because being told the file is not going anywhere is better news before you have
typed one.

`--unscrubbed` sends it anyway **and only to a server on this machine** —
loopback, a private address, or a `.local` name. Somebody else's calendar may be
reproduced against a local Stalwart in Docker, which can be wiped completely, and
never against production, where a `DELETE` afterwards does not reach the backups,
the server logs, or any other client subscribed to that account. See step 7 of
[issue #13](https://github.com/thunderbird/desktop-support-tools/issues/13) for
what to do instead: the scrubber's fidelity rule makes the list of things a scrub
could have changed an enumerable one, and every item on it can be tested
synthetically.

### Delete one

```sh
curl -i -u "$CALDAV_USER" -X DELETE "${CAL_HOME}ticket-7067/"
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
uv run caldav_delete_events.py "${CAL_HOME}default/" --everything
```

Afterwards, remove the calendar from Thunderbird too. It keeps its own cached
copy and will happily go on showing a calendar the server no longer has.

## The Firefox add-on

The same two safe operations — list your calendars, make a new one — from a
button in Firefox, for when a command line is the thing in the way.

```sh
uv run package_addon.py          # assembles build/firefox-addon/
uv run package_addon.py --zip    # ... and the archive you upload for signing
```

Then in Firefox: **about:debugging** → **This Firefox** → **Load Temporary
Add-on**, and pick `build/firefox-addon/manifest.json`. It is there until
Firefox restarts. Anything more permanent has to be signed, because release
Firefox will not install an unsigned extension.

The build step exists for one reason: an extension can only load files from
inside its own directory, and `caldav_account.js` is shared with the tests and
with `caldav_account.py`'s fixtures. Rather than keep two copies of it, the
packaging copies it in. Edit `firefox-addon/` in place; run the script again
after editing the shared file.

**Why an add-on and not a web page.** Thundermail's CalDAV sends no CORS
headers, so a page — even one served from your own machine — never gets to make
the request. An extension with a host permission is not subject to that. The ask
to Thundermail is tracked separately; the add-on does not wait on it.

**Keeping it open.** A popup is closed by the browser the moment it loses
focus, switching tabs included, and no setting changes that. So the popup offers
a button — **Keep this open in the sidebar** — which opens the same page in
Firefox's sidebar, where it stays put and keeps what you have typed. Move over
before you type, since nothing is carried across. The sidebar is Firefox's;
Chrome's equivalent is a different manifest key, and Chrome is not the target
yet.

**What it asks for, and when.** No permissions at install. The first time it
talks to a server, Firefox asks whether to let it talk to *that* server, because
the address is whatever you typed. It is never given the run of the web.
Confirmed working in Firefox on 2026-09-01: the prompt appears on the first
click, and the answer is remembered until you revoke it in Add-ons Manager.

**What it stores: nothing.** What you type lives in the popup and goes when the
popup closes — no extension storage, no sync, no cookies on the request, nothing
in a URL. Requests go from your browser straight to your own mail server. Your
browser may still offer to save the password, which is your browser rather than
this add-on, and the popup says so.

It sends `PROPFIND` and `MKCALENDAR` and nothing else. Deleting a calendar is
not in it, on purpose: that is the operation with no undo, and it wants a
confirmation flow designed rather than a button added.

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
